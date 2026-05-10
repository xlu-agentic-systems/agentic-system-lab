from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

from agentic_system_lab.observability import log_agent_event
from project4_agentic_project_copilot.app.agents import CopilotOrchestrator, FileQaAgent, SqlAgent, ToolAgent
from project4_agentic_project_copilot.app.database import CopilotDatabase
from project4_agentic_project_copilot.app.document_store import DocumentStore
from project4_agentic_project_copilot.app.embeddings import EmbeddingClient
from project4_agentic_project_copilot.app.llm import LlmClient, OpenAILlmClient
from project4_agentic_project_copilot.app.models import (
    ChatRequest,
    ChatResponse,
    Citation,
    DeleteDocumentResponse,
    DecisionLog,
    DocumentListResponse,
    PendingAction,
    SelectDocumentResponse,
    SessionContext,
    SqlResult,
    TraceDetail,
    TraceListResponse,
    ToolCall,
    ToolResult,
)
from project4_agentic_project_copilot.app.session_store import JsonSessionStore, append_turn
from project4_agentic_project_copilot.app.sql_safety import SqlSafetyError, validate_read_only_sql
from project4_agentic_project_copilot.app.tools import ProjectToolService, WRITE_TOOLS
from project4_agentic_project_copilot.app.trace_store import JsonlTraceStore, SqliteTraceStore


logger = logging.getLogger(__name__)


class ProjectCopilotService:
    def __init__(
        self,
        *,
        db: CopilotDatabase | None = None,
        llm_client: LlmClient | None = None,
        embedding_client: EmbeddingClient | None = None,
        session_store: JsonSessionStore | None = None,
        trace_store: JsonlTraceStore | SqliteTraceStore | None = None,
    ) -> None:
        self.db = db or CopilotDatabase()
        self.llm_client = llm_client or OpenAILlmClient()
        self.document_store = DocumentStore(self.db, embedding_client)
        self.session_store = session_store or JsonSessionStore()
        self.trace_store = trace_store or SqliteTraceStore(self.db)
        self.tools = ProjectToolService(self.db)
        self.orchestrator = CopilotOrchestrator(self.llm_client)
        self.sql_agent = SqlAgent(self.llm_client)
        self.tool_agent = ToolAgent(self.llm_client)
        self.file_qa_agent = FileQaAgent(self.llm_client)

    async def upload_file(self, *, filename: str, content_type: str, content: bytes, session_id: str | None = None):
        upload = await self.document_store.ingest_bytes(filename=filename, content_type=content_type, content=content)
        log_agent_event(
            logger,
            event="document_ingested",
            message="project4 document ingested",
            session_id=session_id,
            status="uploaded",
            attributes={
                "filename": filename,
                "content_type": content_type,
                "chunk_count": upload.chunk_count,
                "reindexed_chunk_count": upload.reindexed_chunk_count,
            },
        )
        if session_id:
            context = await self.session_store.load(session_id)
            context.current_document_id = upload.document_id
            context.current_document_filename = upload.filename
            append_turn(context, role="assistant", content=f"Uploaded {filename} and selected it as the current document.")
            await self.session_store.save(context)
            upload.context = context
        return upload

    async def list_documents(self) -> DocumentListResponse:
        return DocumentListResponse(documents=self.document_store.list_documents())

    async def list_traces(self, limit: int = 50) -> TraceListResponse:
        return TraceListResponse(traces=await self.trace_store.list_traces(limit=limit))

    async def get_trace(self, trace_id: str) -> TraceDetail:
        return await self.trace_store.get_trace(trace_id)

    async def select_document(self, *, session_id: str, document_id: str) -> SelectDocumentResponse:
        document = self.document_store.get_document(document_id)
        if document is None:
            raise ValueError(f"document {document_id} does not exist")
        context = await self.session_store.load(session_id)
        context.current_document_id = document.document_id
        context.current_document_filename = document.filename
        append_turn(context, role="assistant", content=f"Selected {document.filename} as the current document.")
        await self.session_store.save(context)
        return SelectDocumentResponse(document=document, context=context)

    async def delete_document(self, *, session_id: str | None, document_id: str) -> DeleteDocumentResponse:
        deleted, reindexed_count = await self.document_store.delete_document(document_id)
        log_agent_event(
            logger,
            event="document_deleted",
            message="project4 document deleted",
            session_id=session_id,
            status="deleted" if deleted else "not_found",
            attributes={"document_id": document_id, "reindexed_chunk_count": reindexed_count},
        )
        context = None
        if session_id:
            context = await self.session_store.load(session_id)
            if context.current_document_id == document_id:
                context.current_document_id = None
                context.current_document_filename = None
                append_turn(context, role="assistant", content="Deleted the selected document and cleared the current file.")
            await self.session_store.save(context)
        return DeleteDocumentResponse(
            document_id=document_id,
            deleted=deleted,
            reindexed_chunk_count=reindexed_count,
            context=context,
        )

    async def chat(self, request: ChatRequest) -> ChatResponse:
        context = await self.session_store.load(request.session_id)
        append_turn(context, role="user", content=request.message)
        log_agent_event(
            logger,
            event="user_message",
            message="project4 user message received",
            session_id=request.session_id,
            attributes={"message_length": len(request.message), "confirm_action": bool(request.confirm_action_id)},
        )
        if request.confirm_action_id:
            response = await self._confirm_action(request, context)
            await self._persist(request.message, response)
            self._log_response(request, response)
            return response

        response = self._preflight_response(request, context)
        if response is None:
            try:
                text = request.message.strip().lower()
                if _should_answer_current_document(text, context):
                    response = await self._answer_from_files(
                        request,
                        context,
                        "The user asked about the current session document.",
                        request.message,
                    )
                else:
                    decision = await self.orchestrator.decide(request.message, context)
                    log_agent_event(
                        logger,
                        event="agent_decision",
                        message="project4 copilot orchestrator decision",
                        agent="copilot_orchestrator",
                        session_id=request.session_id,
                        route=decision.route,
                        status=decision.route,
                        attributes={
                            "reason_length": len(decision.reasoning),
                            "tool_name": decision.tool_name,
                            "has_search_query": bool(decision.search_query),
                        },
                    )
                    if decision.route == "file_retrieval":
                        response = await self._answer_from_files(request, context, decision.reasoning, decision.search_query or request.message)
                    elif decision.route == "sql_query":
                        response = await self._answer_from_sql(request, context, decision.reasoning)
                    elif decision.route == "api_tool":
                        response = await self._handle_tool(request, context, decision)
                    elif decision.route == "context":
                        response = self._answer_from_context(request, context, decision.reasoning)
                    else:
                        response = self._clarify(request, context, decision.reasoning, decision.clarification_question)
            except Exception as exc:
                response = self._model_error_response(request, context, exc)

        await self._persist(request.message, response)
        self._log_response(request, response)
        return response

    def _log_response(self, request: ChatRequest, response: ChatResponse) -> None:
        log_agent_event(
            logger,
            event="final_response",
            message="project4 final response generated",
            session_id=request.session_id,
            route=response.route,
            status=response.route,
            attributes={
                "response_length": len(response.response),
                "citation_count": len(response.citations),
                "has_sql": bool(response.generated_sql),
                "has_tool_call": response.tool_call is not None,
            },
        )

    async def _answer_from_files(
        self,
        request: ChatRequest,
        context: SessionContext,
        reasoning: str,
        query: str,
    ) -> ChatResponse:
        document_id = context.current_document_id
        if not document_id:
            return self._clarify(
                request,
                context,
                "The user asked about files, but this session has no current document.",
                "I do not have a file selected for this session yet. Upload a markdown, text, or PDF file first, then ask about it.",
            )
        if not self.document_store.has_documents(document_id):
            context.current_document_id = None
            context.current_document_filename = None
            return self._clarify(
                request,
                context,
                "The session references a document that has no stored chunks.",
                "I cannot find the selected document chunks anymore. Upload the file again, then ask about it.",
            )
        if not self.document_store.has_documents():
            return self._clarify(
                request,
                context,
                "The user asked about files, but no uploaded document chunks are available.",
                "I do not have an uploaded file in this workspace yet. Upload a markdown, text, or PDF file first, then ask about it.",
            )
        chunks = await self.document_store.search(query, document_id=document_id)
        if not chunks:
            return self._response(
                request,
                context,
                response="I could not find relevant uploaded file content for that question.",
                route="file_retrieval",
                citations=[],
                log=DecisionLog(route="file_retrieval", reasoning=reasoning, selected_data_source="uploaded_files"),
            )
        answer = await self.file_qa_agent.answer(
            request.message,
            [chunk.model_dump(mode="json") for chunk in chunks],
            context,
        )
        cited_ids = set(answer.cited_chunk_ids)
        citations: list[Citation] = [
            Citation(**chunk.model_dump(exclude={"text"})) for chunk in chunks if not cited_ids or chunk.chunk_id in cited_ids
        ]
        if chunks:
            context.current_document_id = chunks[0].document_id
            context.current_document_filename = chunks[0].filename
        return self._response(
            request,
            context,
            response=answer.answer,
            route="file_retrieval",
            citations=citations,
            log=DecisionLog(route="file_retrieval", reasoning=reasoning, selected_data_source="uploaded_files"),
        )

    async def _answer_from_sql(self, request: ChatRequest, context: SessionContext, reasoning: str) -> ChatResponse:
        plan = await self.sql_agent.plan(request.message, self.db.schema_description(), context)
        try:
            safe_sql = validate_read_only_sql(plan.sql)
            rows = self.db.execute_read(safe_sql)
        except SqlSafetyError as exc:
            return self._response(
                request,
                context,
                response=f"I refused to run the generated SQL because it was not read-only: {exc}",
                route="sql_query",
                generated_sql=plan.sql,
                log=DecisionLog(route="sql_query", reasoning=reasoning, selected_data_source="task_database", generated_sql=plan.sql),
            )
        columns = list(rows[0].keys()) if rows else []
        sql_result = SqlResult(sql=safe_sql, columns=columns, rows=rows, explanation=plan.explanation)
        response_text = _explain_rows(rows)
        return self._response(
            request,
            context,
            response=response_text,
            route="sql_query",
            generated_sql=safe_sql,
            sql_result=sql_result,
            log=DecisionLog(route="sql_query", reasoning=reasoning, selected_data_source="task_database", generated_sql=safe_sql),
        )

    async def _handle_tool(self, request: ChatRequest, context: SessionContext, decision) -> ChatResponse:
        tool_call = await self.tool_agent.propose(request.message, decision, context)
        if tool_call.name in WRITE_TOOLS or tool_call.requires_confirmation:
            action_id = str(uuid.uuid4())
            pending = PendingAction(
                action_id=action_id,
                tool_call=tool_call,
                preview=self.tools.preview(tool_call),
            )
            context.pending_actions[action_id] = tool_call
            workflow_id = self._create_workflow_state(
                context=context,
                action_id=action_id,
                tool_call=tool_call,
                preview=pending.preview,
            )
            log_agent_event(
                logger,
                event="tool_execution",
                message="project4 project API tool requires confirmation",
                agent="tool_agent",
                session_id=request.session_id,
                tool_name=tool_call.name,
                status="requires_confirmation",
                attributes={"requires_confirmation": tool_call.requires_confirmation, "workflow_id": workflow_id},
            )
            return self._response(
                request,
                context,
                response=f"I can do that, but need confirmation first. Proposed action: {pending.preview}",
                route="api_tool",
                tool_call=tool_call,
                pending_action=pending,
                log=DecisionLog(
                    route="api_tool",
                    reasoning=decision.reasoning,
                    selected_data_source="project_api",
                    tool_name=tool_call.name,
                    workflow_id=workflow_id,
                ),
            )
        result = self.tools.execute(tool_call)
        log_agent_event(
            logger,
            event="tool_execution",
            message="project4 project API tool execution result",
            agent="tool_agent",
            session_id=request.session_id,
            tool_name=tool_call.name,
            status="executed" if result.ok else "failed",
            attributes={"ok": result.ok, "error": result.error, "requires_confirmation": tool_call.requires_confirmation},
        )
        self._update_context_from_tool(context, tool_call, result)
        return self._response(
            request,
            context,
            response=_explain_tool_result(result),
            route="api_tool",
            tool_call=tool_call,
            tool_result=result,
            log=DecisionLog(route="api_tool", reasoning=decision.reasoning, selected_data_source="project_api", tool_name=tool_call.name),
        )

    async def _confirm_action(self, request: ChatRequest, context: SessionContext) -> ChatResponse:
        action_id = request.confirm_action_id or ""
        tool_call = context.pending_actions.pop(action_id, None)
        if tool_call is None:
            result = ToolResult(name="search_tasks", ok=False, error="No pending action found for that confirmation id.")
            log_agent_event(
                logger,
                event="tool_execution",
                message="project4 missing pending action confirmation",
                agent="tool_agent",
                session_id=request.session_id,
                tool_name=result.name,
                status="blocked",
                attributes={"ok": result.ok, "error": result.error},
            )
            return self._response(
                request,
                context,
                response=result.error or "No pending action found.",
                route="api_tool",
                tool_result=result,
                log=DecisionLog(route="api_tool", reasoning="User attempted to confirm a missing pending action.", selected_data_source="project_api"),
            )
        result = self.tools.execute(tool_call)
        workflow_id = self._finish_workflow_state(context, action_id, result)
        log_agent_event(
            logger,
            event="tool_execution",
            message="project4 confirmed project API tool execution result",
            agent="tool_agent",
            session_id=request.session_id,
            tool_name=tool_call.name,
            status="executed" if result.ok else "failed",
            attributes={
                "ok": result.ok,
                "error": result.error,
                "requires_confirmation": tool_call.requires_confirmation,
                "workflow_id": workflow_id,
            },
        )
        self._update_context_from_tool(context, tool_call, result)
        return self._response(
            request,
            context,
            response=_explain_tool_result(result),
            route="api_tool",
            tool_call=tool_call,
            tool_result=result,
            log=DecisionLog(
                route="api_tool",
                reasoning="User confirmed a pending state-changing API action.",
                selected_data_source="project_api",
                tool_name=tool_call.name,
                workflow_id=workflow_id,
            ),
        )

    def _answer_from_context(self, request: ChatRequest, context: SessionContext, reasoning: str) -> ChatResponse:
        response = (
            f"Current project: {context.current_project_id or 'none'}. "
            f"Current task: {context.current_task_id or 'none'}. "
            f"Current document: {context.current_document_filename or context.current_document_id or 'none'}. "
            f"Current note: {context.current_note_id or 'none'}. "
            f"Current workflow: {context.current_workflow_id or 'none'}."
        )
        return self._response(
            request,
            context,
            response=response,
            route="context",
            log=DecisionLog(route="context", reasoning=reasoning, selected_data_source="session_context"),
        )

    def _clarify(
        self,
        request: ChatRequest,
        context: SessionContext,
        reasoning: str,
        question: str | None,
    ) -> ChatResponse:
        return self._response(
            request,
            context,
            response=question or "Should I search files, query the task database, or perform a task action?",
            route="clarify",
            log=DecisionLog(route="clarify", reasoning=reasoning),
        )

    def _preflight_response(self, request: ChatRequest, context: SessionContext) -> ChatResponse | None:
        text = request.message.strip().lower()
        if _is_greeting(text):
            return self._clarify(
                request,
                context,
                "Simple greeting handled without a model call.",
                "Hi. Upload a file or ask me about tasks, project data, or a task action.",
            )
        if _asks_about_files(text) and not context.current_document_id:
            return self._clarify(
                request,
                context,
                "The user asked about a file before any file was uploaded.",
                "I do not have an uploaded file in this workspace yet. Upload a markdown, text, or PDF file first, then ask about it.",
            )
        return None

    def _model_error_response(self, request: ChatRequest, context: SessionContext, exc: Exception) -> ChatResponse:
        return self._response(
            request,
            context,
            response=(
                "I could not complete the model call. Check OPENAI_API_KEY, OPENAI_MODEL, "
                "and network access, then try again."
            ),
            route="clarify",
            log=DecisionLog(route="clarify", reasoning=f"Model/API call failed: {type(exc).__name__}: {exc}"),
        )

    def _response(self, request: ChatRequest, context: SessionContext, **kwargs) -> ChatResponse:
        response_text = kwargs.pop("response")
        if "log" in kwargs:
            kwargs["decision_log"] = kwargs.pop("log")
        append_turn(context, role="assistant", content=response_text)
        return ChatResponse(session_id=request.session_id, response=response_text, context=context, **kwargs)

    async def _persist(self, user_message: str, response: ChatResponse) -> None:
        await self.session_store.save(response.context)
        response.trace_id = await self.trace_store.append_response(user_message, response)

    def _update_context_from_tool(self, context: SessionContext, tool_call: ToolCall, result: ToolResult) -> None:
        if not result.ok or not isinstance(result.result, dict):
            return
        if "project_id" in result.result:
            context.current_project_id = int(result.result["project_id"])
        if "task_id" in result.result:
            context.current_task_id = int(result.result["task_id"])
        if "note_id" in result.result:
            context.current_note_id = int(result.result["note_id"])

    def _create_workflow_state(
        self,
        *,
        context: SessionContext,
        action_id: str,
        tool_call: ToolCall,
        preview: str,
    ) -> str:
        workflow_id = f"wf_{uuid.uuid4().hex[:12]}"
        workflow_type = _workflow_type(tool_call)
        args = tool_call.args.clean()
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO productivity_workflows(
                  workflow_id,
                  session_id,
                  workflow_type,
                  status,
                  pending_action_id,
                  source_document_id,
                  source_note_id,
                  summary
                )
                VALUES (?, ?, ?, 'awaiting_review', ?, ?, ?, ?)
                """,
                (
                    workflow_id,
                    context.session_id,
                    workflow_type,
                    action_id,
                    args.get("source_document_id") or context.current_document_id,
                    args.get("note_id") or context.current_note_id,
                    preview,
                ),
            )
            conn.execute(
                """
                INSERT INTO workflow_steps(workflow_id, step_index, name, status, output_json)
                VALUES (?, 1, 'proposed_tool_action', 'awaiting_review', ?)
                """,
                (
                    workflow_id,
                    json.dumps(
                        {
                            "tool_name": tool_call.name,
                            "args": args,
                            "preview": preview,
                        },
                        sort_keys=True,
                    ),
                ),
            )
            conn.commit()
        context.current_workflow_id = workflow_id
        return workflow_id

    def _finish_workflow_state(self, context: SessionContext, action_id: str, result: ToolResult) -> str | None:
        rows = self.db.execute_read(
            """
            SELECT workflow_id
            FROM productivity_workflows
            WHERE session_id = ? AND pending_action_id = ? AND status = 'awaiting_review'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (context.session_id, action_id),
        )
        if not rows:
            return None
        workflow_id = rows[0]["workflow_id"]
        status = "completed" if result.ok else "failed"
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE productivity_workflows
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE workflow_id = ?
                """,
                (status, workflow_id),
            )
            conn.execute(
                """
                INSERT INTO workflow_steps(workflow_id, step_index, name, status, output_json)
                VALUES (?, 2, 'confirmed_tool_execution', ?, ?)
                """,
                (
                    workflow_id,
                    status,
                    result.model_dump_json(),
                ),
            )
            conn.commit()
        context.current_workflow_id = workflow_id
        return workflow_id


def _workflow_type(tool_call: ToolCall) -> str:
    if tool_call.name == "create_note":
        return "capture_personal_note"
    if tool_call.name == "create_task" and (
        tool_call.args.source_document_id or tool_call.args.note_id or tool_call.args.source_filename
    ):
        return "document_or_note_to_task"
    return f"{tool_call.name}_workflow"


def _explain_rows(rows: list[dict]) -> str:
    if not rows:
        return "The read-only query returned no rows."
    if len(rows) == 1 and len(rows[0]) == 1:
        key, value = next(iter(rows[0].items()))
        return f"The query returned {key}: {value}."
    preview = "; ".join(", ".join(f"{key}={value}" for key, value in row.items()) for row in rows[:5])
    suffix = "" if len(rows) <= 5 else f" Showing 5 of {len(rows)} rows."
    return f"The query returned {len(rows)} rows: {preview}.{suffix}"


def _explain_tool_result(result: ToolResult) -> str:
    if not result.ok:
        return f"The tool call failed: {result.error}"
    return f"Tool {result.name} executed successfully: {result.result}"


def _is_greeting(text: str) -> bool:
    return text in {"hi", "hello", "hey", "hi there", "hello there"}


def _asks_about_files(text: str) -> bool:
    return any(term in text for term in ("this file", "file", "document", "doc", "pdf", "markdown"))


def _should_answer_current_document(text: str, context: SessionContext) -> bool:
    if not context.current_document_id:
        return False
    if _asks_about_files(text):
        return True
    return _is_document_followup(text) and _recent_file_context(context)


def _is_document_followup(text: str) -> bool:
    normalized = text.strip(" ?!.").lower()
    if normalized in {
        "how about now",
        "what about now",
        "try now",
        "now",
        "ok now",
        "can you answer now",
        "can you do it now",
    }:
        return True
    return any(
        phrase in normalized
        for phrase in (
            "what does it say",
            "what is it doing",
            "tell me about it",
            "say something about it",
            "summarize it",
            "explain it",
            "describe it",
        )
    )


def _recent_file_context(context: SessionContext) -> bool:
    recent_text = " ".join(turn.content.lower() for turn in context.history[-6:])
    return any(term in recent_text for term in ("file", "document", "pdf", "markdown", "uploaded", "upload"))
