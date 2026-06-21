from __future__ import annotations

from project4_agentic_project_copilot.app.eval_framework.assertions import (
    case_diagnostics,
    case_passed,
    confirmation_diagnostics,
    workflow_has_status,
    workflow_has_step_names,
    workflow_row,
    workflow_status,
    workflow_step_names,
    workflow_steps,
    workflow_type,
)
from project4_agentic_project_copilot.app.eval_framework.comparison import run_mode_comparison
from project4_agentic_project_copilot.app.eval_framework.coverage import build_coverage
from project4_agentic_project_copilot.app.eval_framework.fixtures import EvaluationHarness, create_harness, prepare_case
from project4_agentic_project_copilot.app.eval_framework.goal_harness import run_goal_harness
from project4_agentic_project_copilot.app.eval_framework.io import EVAL_CASES_PATH, load_cases
from project4_agentic_project_copilot.app.eval_framework.models import (
    CaseExecution,
    CoverageBucket,
    EvalMode,
    EvaluationCase,
    EvaluationCoverage,
    EvaluationResult,
    EvaluationRun,
    EvaluationRunMetadata,
    GoalHarnessCheck,
    GoalHarnessRun,
    ModeComparisonCaseResult,
    ModeComparisonRun,
)
from project4_agentic_project_copilot.app.eval_framework.openai_eval import (
    OPENAI_SMOKE_CASES_PATH,
    OpenAIEvaluationServiceFactory,
    has_openai_api_key,
    require_openai_api_key,
    run_openai_evaluation,
    run_openai_goal_harness,
)
from project4_agentic_project_copilot.app.eval_framework.runner import (
    EvalRunner,
    WorkflowEvalRunner,
    build_evaluation_run,
    run_case,
    run_evaluation,
    run_executions,
)
from project4_agentic_project_copilot.app.eval_framework.service_factory import (
    create_evaluation_service,
    create_evaluation_service_at,
)


__all__ = [
    "CaseExecution",
    "CoverageBucket",
    "EVAL_CASES_PATH",
    "EvalMode",
    "EvalRunner",
    "EvaluationCase",
    "EvaluationCoverage",
    "EvaluationHarness",
    "EvaluationResult",
    "EvaluationRun",
    "EvaluationRunMetadata",
    "GoalHarnessCheck",
    "GoalHarnessRun",
    "ModeComparisonCaseResult",
    "ModeComparisonRun",
    "OPENAI_SMOKE_CASES_PATH",
    "OpenAIEvaluationServiceFactory",
    "WorkflowEvalRunner",
    "build_coverage",
    "build_evaluation_run",
    "case_diagnostics",
    "case_passed",
    "confirmation_diagnostics",
    "create_evaluation_service",
    "create_evaluation_service_at",
    "create_harness",
    "has_openai_api_key",
    "load_cases",
    "prepare_case",
    "require_openai_api_key",
    "run_case",
    "run_evaluation",
    "run_executions",
    "run_goal_harness",
    "run_mode_comparison",
    "run_openai_evaluation",
    "run_openai_goal_harness",
    "workflow_has_status",
    "workflow_has_step_names",
    "workflow_row",
    "workflow_status",
    "workflow_step_names",
    "workflow_steps",
    "workflow_type",
]
