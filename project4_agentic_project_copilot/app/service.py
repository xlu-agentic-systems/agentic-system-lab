from __future__ import annotations

import uuid
from pathlib import Path

from project4_agentic_project_copilot.app.agents import CopilotOrchestrator, FileQaAgent, SqlAgent, ToolAgent
from project4_agentic_project_copilot.app.database import CopilotDatabase
from project4_agentic_project_copilot.app.document_store import DocumentStore
from project4_agentic_project_copilot.app.embeddings import EmbeddingClient
from project4_agentic_project_copilot.app.llm import LlmClient, OpenAILlmClient
from project4_agentic_project_copilot.app.models import (
    ChatRequest,
    ChatResponse,
    Citation,
    DecisionLog,
    PendingAction,
    SessionContext,
    SqlResult,
    ToolCall,
    ToolResult,
)
from project4_agentic_project_copilot.app.session_store import JsonSessionStore, append_turn
from project4_agentic_project_copilot.app.sql_safety import SqlSafetyError, validate_read_only_sql
from project4_agentic_project_copilot.app.tools import ProjectToolService, WRITE_TOOLS
from project4_agentic_project_copilot.app.trace_store import JsonlTraceStore


class ProjectCopilotService:
    def __init__(
        self,
        *,
        db: CopilotDatabase | None = None,
        llm_client: LlmClient | None = None,
        embedding_client: EmbeddingClient | None = None,
        session_store: JsonSessionStore | None = None,
        trace_store: JsonlTraceStore | None = None,
    ) -> None:
        self.db = db or CopilotDatabase()
        self.llm_client = llm_client or OpenAILlmClient()
        self.document_store = DocumentStore(self.db, embedding_client)
        self.session_store = session_store or JsonSessionStore()
        self.trace_store = trace_store or JsonlTraceStore()
        self.tools = ProjectToolService(self.db)
        self.orchestrator = CopilotOrchestrator(self.llm_client)
        self.sql_agent = SqlAgent(self.llm_client)
        self.tool_agent = ToolAgent(self.llm_client)
        self.file_qa_agent = FileQaAgent(self.llm_client)

    async def upload_file(self, *, filename: str, content_type: str, content: bytes, session_id: str | None = None):
        upload = await self.document_store.ingest_bytes(filename=filename, content_type=content_type, content=content)
        if session_id:
            context = await self.session_store.load(session_id)
            context.current_document_id = upload.document_id
            append_turn(context, role="assistant", content=f"Uploaded {filename} and selected it as the current document.")
            await self.session_store.save(context)
            upload.context = context
        return upload

    async def chat(self, request: ChatRequest) -> ChatResponse:
        context = await self.session_store.load(request.session_id)
        append_turn(context, role="user", content=request.message)
        if request.confirm_action_id:
            response = await self._confirm_action(request, context)
            await self._persist(request.message, response)
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
        return response

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
            return self._response(
                request,
                context,
                response=f"I can do that, but need confirmation first. Proposed action: {pending.preview}",
                route="api_tool",
                tool_call=tool_call,
                pending_action=pending,
                log=DecisionLog(route="api_tool", reasoning=decision.reasoning, selected_data_source="project_api", tool_name=tool_call.name),
            )
        result = self.tools.execute(tool_call)
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
            return self._response(
                request,
                context,
                response=result.error or "No pending action found.",
                route="api_tool",
                tool_result=result,
                log=DecisionLog(route="api_tool", reasoning="User attempted to confirm a missing pending action.", selected_data_source="project_api"),
            )
        result = self.tools.execute(tool_call)
        self._update_context_from_tool(context, tool_call, result)
        return self._response(
            request,
            context,
            response=_explain_tool_result(result),
            route="api_tool",
            tool_call=tool_call,
            tool_result=result,
            log=DecisionLog(route="api_tool", reasoning="User confirmed a pending state-changing API action.", selected_data_source="project_api", tool_name=tool_call.name),
        )

    def _answer_from_context(self, request: ChatRequest, context: SessionContext, reasoning: str) -> ChatResponse:
        response = (
            f"Current project: {context.current_project_id or 'none'}. "
            f"Current task: {context.current_task_id or 'none'}. "
            f"Current document: {context.current_document_id or 'none'}."
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
        await self.trace_store.append_response(user_message, response)

    def _update_context_from_tool(self, context: SessionContext, tool_call: ToolCall, result: ToolResult) -> None:
        if not result.ok or not isinstance(result.result, dict):
            return
        if "project_id" in result.result:
            context.current_project_id = int(result.result["project_id"])
        if "task_id" in result.result:
            context.current_task_id = int(result.result["task_id"])


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
