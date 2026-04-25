from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


Route = Literal["context", "file_retrieval", "sql_query", "api_tool", "clarify"]
ToolName = Literal["create_task", "update_task_status", "assign_task", "add_comment", "search_tasks"]
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


class SessionContext(BaseModel):
    session_id: str
    current_project_id: int | None = None
    current_task_id: int | None = None
    current_document_id: str | None = None
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
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ChatResponse(BaseModel):
    session_id: str
    response: str
    route: Route
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


class FileAnswer(BaseModel):
    answer: str
    cited_chunk_ids: list[str] = Field(default_factory=list)


class EvaluationCase(BaseModel):
    case_id: str
    user_query: str
    expected_tool_choice: Route
    expected_data_source: str
    expected_behavior: str
    expected_tool_name: ToolName | None = None
    expected_sql_contains: str | None = None
    expected_response_contains: str | None = None
    confirm_action: bool = False


class EvaluationResult(BaseModel):
    case_id: str
    passed: bool
    route: Route
    expected_route: Route
    summary: str


SessionContext.model_rebuild()
