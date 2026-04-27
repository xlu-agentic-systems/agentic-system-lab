from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


AutonomousToolName = Literal[
    "evaluate_traces",
    "run_labeled_benchmark",
    "generate_candidate_prompts",
    "finish",
]
RunStatus = Literal["completed", "blocked", "max_iterations_reached", "failed"]


class AutonomousGoal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: str = "Improve the adaptive evaluation harness until acceptance gates pass."
    trace_limit: int | None = Field(default=None, ge=1)
    max_iterations: int = Field(default=8, ge=1, le=20)
    min_pass_fail_accuracy: float = Field(default=0.8, ge=0, le=1)
    min_issue_category_recall: float = Field(default=0.8, ge=0, le=1)
    require_candidate_prompts: bool = True


class AutonomousAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: AutonomousToolName
    reason: str
    trace_limit: int | None = Field(default=None, ge=1)


class AcceptanceGate(BaseModel):
    name: str
    passed: bool
    summary: str


class StepObservation(BaseModel):
    iteration: int
    tool_name: AutonomousToolName
    ok: bool
    summary: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AutonomousRunRequest(BaseModel):
    goal: AutonomousGoal = Field(default_factory=AutonomousGoal)
    use_rule_based: bool = False


class AutonomousRunResult(BaseModel):
    run_id: str
    status: RunStatus
    goal: AutonomousGoal
    iterations: int
    observations: list[StepObservation]
    gates: list[AcceptanceGate]
    generated_test_count: int = 0
    prompt_patch_count: int = 0
    candidate_prompt_count: int = 0
    pass_fail_accuracy: float | None = None
    issue_category_recall: float | None = None
    report_path: str
