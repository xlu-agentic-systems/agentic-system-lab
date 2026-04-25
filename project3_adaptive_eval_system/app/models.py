from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


TraceOutcome = Literal["success", "failure", "unknown"]
IssueCategory = Literal[
    "incorrect_policy_interpretation",
    "missing_clarification_question",
    "unsafe_tool_proposal",
    "hallucinated_policy",
    "poor_customer_communication",
    "unnecessary_escalation",
    "context_loss",
]
Severity = Literal["low", "medium", "high"]
PatchStatus = Literal["proposed", "approved", "rejected"]


class AgentOutputRecord(BaseModel):
    agent_name: str
    output: str


class ToolCallRecord(BaseModel):
    tool_name: str
    proposed: bool
    executed: bool
    safe: bool
    summary: str


class BackendValidationRecord(BaseModel):
    check_name: str
    passed: bool
    summary: str


class ConversationTrace(BaseModel):
    trace_id: str
    project: str
    session_id: str
    user_id: str
    user_message: str
    agent_outputs: list[AgentOutputRecord] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    backend_validations: list[BackendValidationRecord] = Field(default_factory=list)
    final_response: str
    outcome: TraceOutcome = "unknown"
    expected_behavior: str
    policy_basis: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class EvaluationScores(BaseModel):
    intent_understanding: int = Field(ge=1, le=5)
    policy_correctness: int = Field(ge=1, le=5)
    tool_safety: int = Field(ge=1, le=5)
    response_helpfulness: int = Field(ge=1, le=5)
    escalation_correctness: int = Field(ge=1, le=5)
    latency_awareness: int = Field(ge=1, le=5)
    context_preservation: int = Field(ge=1, le=5)


class DetectedIssue(BaseModel):
    category: IssueCategory
    severity: Severity
    description: str
    evidence: str
    recommendation: str


class EvaluationResult(BaseModel):
    trace_id: str
    passed: bool
    overall_score: int = Field(ge=1, le=5)
    scores: EvaluationScores
    detected_issues: list[DetectedIssue] = Field(default_factory=list)
    summary: str
    requires_regression: bool


class GeneratedTestCase(BaseModel):
    test_id: str
    trace_id: str
    test_name: str
    user_message: str
    expected_behavior: str
    assertions: list[str]
    source_issue: str


class PromptPatch(BaseModel):
    patch_id: str
    trace_id: str
    target_prompt: str
    proposed_instruction: str
    rationale: str
    status: PatchStatus = "proposed"


class EvaluationRunRequest(BaseModel):
    trace_limit: int | None = Field(default=None, ge=1)


class EvaluationRunResult(BaseModel):
    trace_count: int
    evaluations: list[EvaluationResult]
    generated_tests: list[GeneratedTestCase]
    prompt_patches: list[PromptPatch]
    report_path: str


class PromptPatchReviewRequest(BaseModel):
    patch_id: str
    approved: bool
