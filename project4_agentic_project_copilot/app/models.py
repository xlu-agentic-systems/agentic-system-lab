from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


Route = Literal["context", "file_retrieval", "sql_query", "api_tool", "clarify"]
RetrievalScope = Literal["current", "selected", "all"]
ToolName = Literal[
    "create_task",
    "update_task_status",
    "assign_task",
    "add_comment",
    "search_tasks",
    "create_note",
    "search_notes",
]
TaskStatus = Literal["open", "in_progress", "blocked", "done"]


class ToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: int | None = None
    title: str | None = None
    description: str | None = None
    task_id: int | None = None
    status: TaskStatus | None = None
    user_id: int | None = None
    assignee_id: int | None = None
    changed_by: int | None = None
    body: str | None = None
    query: str | None = None
    note_id: int | None = None
    source_document_id: str | None = None
    source_filename: str | None = None
    workflow_id: str | None = None

    def clean(self) -> dict:
        return self.model_dump(exclude_none=True)


class Citation(BaseModel):
    document_id: str
    filename: str
    chunk_id: str
    chunk_index: int
    score: float
    quote: str


class RetrievedChunk(Citation):
    text: str


class DocumentReference(BaseModel):
    document_id: str
    filename: str


class SessionContext(BaseModel):
    session_id: str
    current_project_id: int | None = None
    current_task_id: int | None = None
    current_document_id: str | None = None
    current_document_filename: str | None = None
    selected_documents: list[DocumentReference] = Field(default_factory=list)
    retrieval_scope: RetrievalScope = "current"
    current_note_id: int | None = None
    current_workflow_id: str | None = None
    pending_actions: dict[str, "ToolCall"] = Field(default_factory=dict)
    history: list["ChatTurn"] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ChatRequest(BaseModel):
    session_id: str
    message: str
    confirm_action_id: str | None = None


class OrchestratorDecision(BaseModel):
    route: Route
    reasoning: str
    search_query: str | None = None
    tool_name: ToolName | None = None
    tool_args: ToolArgs = Field(default_factory=ToolArgs)
    clarification_question: str | None = None


class SqlPlan(BaseModel):
    sql: str
    explanation: str


class SqlResult(BaseModel):
    sql: str
    columns: list[str]
    rows: list[dict[str, Any]]
    explanation: str


class ToolCall(BaseModel):
    name: ToolName
    args: ToolArgs = Field(default_factory=ToolArgs)
    requires_confirmation: bool
    reason: str


class ToolResult(BaseModel):
    name: ToolName
    ok: bool
    result: dict[str, Any] | list[dict[str, Any]] | None = None
    error: str | None = None


class PendingAction(BaseModel):
    action_id: str
    tool_call: ToolCall
    preview: str


class DecisionLog(BaseModel):
    route: Route
    reasoning: str
    selected_data_source: str | None = None
    generated_sql: str | None = None
    tool_name: ToolName | None = None
    workflow_id: str | None = None
    retrieval_scope: RetrievalScope | None = None
    searched_documents: list[DocumentReference] = Field(default_factory=list)
    retrieved_documents: list[DocumentReference] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ResponseTiming(BaseModel):
    started_at: datetime
    completed_at: datetime
    elapsed_ms: int
    note: str


class ChatResponse(BaseModel):
    session_id: str
    response: str
    route: Route
    trace_id: str | None = None
    response_timing: ResponseTiming | None = None
    citations: list[Citation] = Field(default_factory=list)
    generated_sql: str | None = None
    sql_result: SqlResult | None = None
    tool_call: ToolCall | None = None
    tool_result: ToolResult | None = None
    pending_action: PendingAction | None = None
    context: SessionContext
    decision_log: DecisionLog


class UploadResponse(BaseModel):
    document_id: str
    filename: str
    chunk_count: int
    reindexed_chunk_count: int = 0
    context: SessionContext | None = None


class DocumentSummary(BaseModel):
    document_id: str
    filename: str
    content_type: str
    chunk_count: int
    created_at: str


class DocumentListResponse(BaseModel):
    documents: list[DocumentSummary]


class SelectDocumentResponse(BaseModel):
    document: DocumentSummary
    context: SessionContext


class AttachDocumentResponse(BaseModel):
    document: DocumentSummary
    context: SessionContext


class DetachDocumentResponse(BaseModel):
    document_id: str
    detached: bool
    context: SessionContext


class RetrievalScopeResponse(BaseModel):
    retrieval_scope: RetrievalScope
    context: SessionContext


class DeleteDocumentResponse(BaseModel):
    document_id: str
    deleted: bool
    reindexed_chunk_count: int
    context: SessionContext | None = None


class FileAnswer(BaseModel):
    answer: str
    cited_chunk_ids: list[str] = Field(default_factory=list)


class TraceSpan(BaseModel):
    span_id: str
    trace_id: str
    parent_span_id: str | None = None
    ordinal: int
    actor_type: Literal["user", "orchestrator", "agent", "tool", "validator", "retriever", "sql", "backend", "response"]
    actor_name: str
    event_type: str
    title: str
    status: str
    input_summary: str | None = None
    output_summary: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class TraceSummary(BaseModel):
    trace_id: str
    session_id: str
    route: Route
    status: str
    user_message: str
    final_response: str
    span_count: int
    created_at: datetime = Field(default_factory=datetime.utcnow)


class TraceDetail(TraceSummary):
    spans: list[TraceSpan] = Field(default_factory=list)


class TraceListResponse(BaseModel):
    traces: list[TraceSummary]


SessionContext.model_rebuild()
