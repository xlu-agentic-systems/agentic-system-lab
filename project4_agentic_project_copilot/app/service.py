from __future__ import annotations

import logging
from datetime import UTC, datetime
from time import perf_counter

from agentic_system_lab.observability import log_agent_event
from project4_agentic_project_copilot.app.agents import CopilotOrchestrator, FileQaAgent, SqlAgent, ToolAgent
from project4_agentic_project_copilot.app.database import CopilotDatabase
from project4_agentic_project_copilot.app.document_store import DocumentStore
from project4_agentic_project_copilot.app.embeddings import EmbeddingClient
from project4_agentic_project_copilot.app.llm import LlmClient, OpenAILlmClient
from project4_agentic_project_copilot.app.models import (
    AttachDocumentResponse,
    ChatRequest,
    ChatResponse,
    Citation,
    DeleteDocumentResponse,
    DecisionLog,
    DetachDocumentResponse,
    DocumentReference,
    DocumentListResponse,
    ResponseTiming,
    RetrievalScope,
    RetrievalScopeResponse,
    SelectDocumentResponse,
    SessionContext,
    SqlResult,
    TraceDetail,
    TraceListResponse,
)
from project4_agentic_project_copilot.app.response_utils import build_chat_response
from project4_agentic_project_copilot.app.retrieval_context import (
    RetrievalContextResolver,
    asks_about_files,
    attach_document,
    detach_document,
    detach_missing_documents,
    document_reference,
    references_from_chunks,
    should_answer_current_document,
)
from project4_agentic_project_copilot.app.session_store import JsonSessionStore, append_turn
from project4_agentic_project_copilot.app.sql_safety import SqlSafetyError, validate_read_only_sql
from project4_agentic_project_copilot.app.tools import ProjectToolService
from project4_agentic_project_copilot.app.trace_store import JsonlTraceStore, SqliteTraceStore
from project4_agentic_project_copilot.app.workflow_actions import WorkflowActionService


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
        self.retrieval_context = RetrievalContextResolver(self.document_store)
        self.workflow_actions = WorkflowActionService(
            db=self.db,
            tool_agent=self.tool_agent,
            tools=self.tools,
            event_logger=log_agent_event,
            logger=logger,
        )

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
            attach_document(context, DocumentReference(document_id=upload.document_id, filename=upload.filename))
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
        attach_document(context, document_reference(document))
        append_turn(context, role="assistant", content=f"Selected {document.filename} as the current document.")
        await self.session_store.save(context)
        return SelectDocumentResponse(document=document, context=context)

    async def attach_document(self, *, session_id: str, document_id: str) -> AttachDocumentResponse:
        document = self.document_store.get_document(document_id)
        if document is None:
            raise ValueError(f"document {document_id} does not exist")
        context = await self.session_store.load(session_id)
        attach_document(context, document_reference(document))
        if not context.current_document_id:
            context.current_document_id = document.document_id
            context.current_document_filename = document.filename
        append_turn(context, role="assistant", content=f"Attached {document.filename} to selected files.")
        await self.session_store.save(context)
        return AttachDocumentResponse(document=document, context=context)

    async def detach_document(self, *, session_id: str, document_id: str) -> DetachDocumentResponse:
        context = await self.session_store.load(session_id)
        detached = detach_document(context, document_id)
        if detached:
            append_turn(context, role="assistant", content="Detached the file from selected files.")
        await self.session_store.save(context)
        return DetachDocumentResponse(document_id=document_id, detached=detached, context=context)

    async def set_retrieval_scope(self, *, session_id: str, retrieval_scope: RetrievalScope) -> RetrievalScopeResponse:
        context = await self.session_store.load(session_id)
        context.retrieval_scope = retrieval_scope
        append_turn(context, role="assistant", content=f"Set file retrieval scope to {retrieval_scope}.")
        await self.session_store.save(context)
        return RetrievalScopeResponse(retrieval_scope=retrieval_scope, context=context)

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
            detach_document(context, document_id)
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
        started_at = datetime.now(UTC)
        started_monotonic = perf_counter()
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
            response = await self.workflow_actions.confirm_action(request, context)
            await self._persist(request.message, response, started_at, started_monotonic)
            self._log_response(request, response)
            return response

        response = self._preflight_response(request, context)
        if response is None:
            try:
                text = request.message.strip().lower()
                if should_answer_current_document(text, context):
                    response = await self._answer_from_files(
                        request,
                        context,
                        "The user asked about uploaded session documents.",
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
                        response = await self.workflow_actions.handle_tool(request, context, decision)
                    elif decision.route == "context":
                        response = self._answer_from_context(request, context, decision.reasoning)
                    else:
                        response = self._clarify(request, context, decision.reasoning, decision.clarification_question)
            except Exception as exc:
                response = self._model_error_response(request, context, exc)

        await self._persist(request.message, response, started_at, started_monotonic)
        self._log_response(request, response)
        return response

    def _attach_response_timing(self, response: ChatResponse, started_at: datetime, started_monotonic: float) -> None:
        completed_at = datetime.now(UTC)
        elapsed_ms = max(0, round((perf_counter() - started_monotonic) * 1000))
        response.response_timing = ResponseTiming(
            started_at=started_at,
            completed_at=completed_at,
            elapsed_ms=elapsed_ms,
            note=f"Processed in {_format_elapsed(elapsed_ms)}.",
        )

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
                "elapsed_ms": response.response_timing.elapsed_ms if response.response_timing else None,
            },
        )

    async def _answer_from_files(
        self,
        request: ChatRequest,
        context: SessionContext,
        reasoning: str,
        query: str,
    ) -> ChatResponse:
        target = self.retrieval_context.resolve(context, request.message)
        if target.reason:
            return self._clarify(
                request,
                context,
                target.reasoning or reasoning,
                target.reason,
            )
        if target.scope == "current" and target.document_ids and not self.document_store.has_documents(document_ids=target.document_ids):
            context.current_document_id = None
            context.current_document_filename = None
            detach_missing_documents(context, target.document_ids)
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
        chunks = await self.document_store.search(
            query,
            top_k=4 if target.scope == "current" else 8,
            document_id=target.document_ids[0] if target.scope == "current" and target.document_ids else None,
            document_ids=target.document_ids if target.scope == "selected" else None,
            diversify=target.scope in {"selected", "all"},
        )
        retrieved_documents = references_from_chunks(chunks)
        if not chunks:
            return self._response(
                request,
                context,
                response="I could not find relevant uploaded file content for that question.",
                route="file_retrieval",
                citations=[],
                log=DecisionLog(
                    route="file_retrieval",
                    reasoning=reasoning,
                    selected_data_source="uploaded_files",
                    retrieval_scope=target.scope,
                    searched_documents=target.documents,
                    retrieved_documents=[],
                ),
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
        if chunks and target.scope == "current":
            context.current_document_id = chunks[0].document_id
            context.current_document_filename = chunks[0].filename
        return self._response(
            request,
            context,
            response=answer.answer,
            route="file_retrieval",
            citations=citations,
            log=DecisionLog(
                route="file_retrieval",
                reasoning=reasoning,
                selected_data_source="uploaded_files",
                retrieval_scope=target.scope,
                searched_documents=target.documents,
                retrieved_documents=retrieved_documents,
            ),
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

    def _answer_from_context(self, request: ChatRequest, context: SessionContext, reasoning: str) -> ChatResponse:
        response = (
            f"Current project: {context.current_project_id or 'none'}. "
            f"Current task: {context.current_task_id or 'none'}. "
            f"Current document: {context.current_document_filename or context.current_document_id or 'none'}. "
            f"Selected documents: {', '.join(document.filename for document in context.selected_documents) or 'none'}. "
            f"Retrieval scope: {context.retrieval_scope}. "
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
        if _asks_about_session_context(text):
            return self._answer_from_context(
                request,
                context,
                "The user asked about current session context.",
            )
        if asks_about_files(text):
            target = self.retrieval_context.resolve(context, request.message)
            if target.reason:
                return self._clarify(request, context, target.reasoning or "The user asked about files without available file context.", target.reason)
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
        log = kwargs.pop("log")
        return build_chat_response(request, context, response=response_text, log=log, **kwargs)

    async def _persist(
        self,
        user_message: str,
        response: ChatResponse,
        started_at: datetime,
        started_monotonic: float,
    ) -> None:
        await self.session_store.save(response.context)
        self._attach_response_timing(response, started_at, started_monotonic)
        response.trace_id = await self.trace_store.append_response(user_message, response)


def _explain_rows(rows: list[dict]) -> str:
    if not rows:
        return "The read-only query returned no rows."
    if len(rows) == 1 and len(rows[0]) == 1:
        key, value = next(iter(rows[0].items()))
        return f"The query returned {key}: {value}."
    preview = "; ".join(", ".join(f"{key}={value}" for key, value in row.items()) for row in rows[:5])
    suffix = "" if len(rows) <= 5 else f" Showing 5 of {len(rows)} rows."
    return f"The query returned {len(rows)} rows: {preview}.{suffix}"


def _is_greeting(text: str) -> bool:
    return text in {"hi", "hello", "hey", "hi there", "hello there"}


def _asks_about_session_context(text: str) -> bool:
    if "session" in text or "context" in text:
        return any(term in text for term in ("project", "task", "document", "file", "note", "workflow"))
    return any(
        phrase in text
        for phrase in (
            "current project",
            "current task",
            "current note",
            "current workflow",
        )
    )


def _format_elapsed(elapsed_ms: int) -> str:
    if elapsed_ms < 1000:
        return f"{elapsed_ms} ms"
    return f"{elapsed_ms / 1000:.1f}s"
