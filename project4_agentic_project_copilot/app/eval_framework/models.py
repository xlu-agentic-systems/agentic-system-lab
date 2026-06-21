from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from project4_agentic_project_copilot.app.models import ChatResponse, Route, ToolName


EvalMode = str
ComparisonStatus = Literal[
    "equivalent",
    "baseline_only_pass",
    "candidate_only_pass",
    "divergent_safe",
    "divergent_unsafe",
]


class EvaluationCase(BaseModel):
    case_id: str
    user_query: str
    expected_tool_choice: Route
    expected_data_source: str
    expected_behavior: str
    expected_tool_name: ToolName | None = None
    expected_sql_contains: str | None = None
    expected_sql_result_value: int | float | str | bool | None = None
    expected_response_contains: str | None = None
    expected_requires_confirmation: bool | None = None
    expected_sql_refused: bool = False
    confirm_action: bool = False
    setup_note_title: str | None = None
    setup_note_body: str | None = None
    setup_note_via_workflow: bool = False
    expected_workflow_status: Literal["awaiting_review", "completed", "failed"] | None = None
    expected_workflow_type: str | None = None
    expected_workflow_step_names: list[str] = Field(default_factory=list)
    expected_workflow_source_note: bool = False


class EvaluationResult(BaseModel):
    case_id: str
    passed: bool
    route: Route
    expected_route: Route
    summary: str


class GoalHarnessCheck(BaseModel):
    name: str
    passed: bool
    summary: str


class EvaluationRunMetadata(BaseModel):
    live: bool = False
    llm_provider: str = "rule_based"
    llm_model: str | None = None
    embedding_provider: str = "hash"
    embedding_model: str | None = None
    call_count_by_task: dict[str, int] = Field(default_factory=dict)


class GoalHarnessRun(BaseModel):
    total: int
    passed: int
    checks: list[GoalHarnessCheck]
    metadata: EvaluationRunMetadata = Field(default_factory=EvaluationRunMetadata)


class CoverageBucket(BaseModel):
    total: int = 0
    passed: int = 0
    case_ids: list[str] = Field(default_factory=list)


class EvaluationCoverage(BaseModel):
    routes: dict[str, CoverageBucket] = Field(default_factory=dict)
    data_sources: dict[str, CoverageBucket] = Field(default_factory=dict)
    tools: dict[str, CoverageBucket] = Field(default_factory=dict)
    workflow_statuses: dict[str, CoverageBucket] = Field(default_factory=dict)
    workflow_types: dict[str, CoverageBucket] = Field(default_factory=dict)
    confirmation: CoverageBucket = Field(default_factory=CoverageBucket)
    sql_refusal: CoverageBucket = Field(default_factory=CoverageBucket)


class EvaluationRun(BaseModel):
    mode: EvalMode = "workflow"
    total: int
    passed: int
    results: list[EvaluationResult]
    coverage: EvaluationCoverage = Field(default_factory=EvaluationCoverage)
    metadata: EvaluationRunMetadata = Field(default_factory=EvaluationRunMetadata)


class CaseExecution(BaseModel):
    case_id: str
    mode: EvalMode
    passed: bool
    result: EvaluationResult
    response: ChatResponse | None = None
    confirmed_response: ChatResponse | None = None
    diagnostics: list[str] = Field(default_factory=list)
    workflow_status: str | None = None
    workflow_type: str | None = None
    workflow_source_document_id: str | None = None
    workflow_source_note_id: int | None = None
    workflow_steps: list[dict[str, object]] = Field(default_factory=list)


class ModeComparisonCaseResult(BaseModel):
    case_id: str
    baseline_mode: EvalMode
    candidate_mode: EvalMode
    status: ComparisonStatus
    baseline_passed: bool
    candidate_passed: bool
    baseline_route: Route
    candidate_route: Route
    pass_matches: bool
    route_matches: bool
    artifact_matches: bool
    workflow_matches: bool
    summary: str


class ModeComparisonRun(BaseModel):
    total: int
    equivalent: int
    divergent: int
    results: list[ModeComparisonCaseResult]
    baseline: EvaluationRun
    candidate: EvaluationRun
