from __future__ import annotations

from pathlib import Path

from project4_agentic_project_copilot.app.eval_framework.io import EVAL_CASES_PATH
from project4_agentic_project_copilot.app.eval_framework.models import (
    CaseExecution,
    EvalMode,
    EvaluationRunMetadata,
    ModeComparisonCaseResult,
    ModeComparisonRun,
)
from project4_agentic_project_copilot.app.eval_framework.runner import (
    EvalRunner,
    WorkflowEvalRunner,
    build_evaluation_run,
    run_executions,
)
from project4_agentic_project_copilot.app.models import SqlResult, ToolCall, ToolResult


async def run_mode_comparison(
    cases_path: Path | str = EVAL_CASES_PATH,
    *,
    baseline_runner: EvalRunner | None = None,
    candidate_runner: EvalRunner | None = None,
) -> ModeComparisonRun:
    active_baseline_runner = baseline_runner or WorkflowEvalRunner(mode="workflow")
    active_candidate_runner = candidate_runner or WorkflowEvalRunner(mode="workflow_shadow")
    baseline_cases, baseline_executions = await run_executions(
        cases_path,
        runner=active_baseline_runner,
    )
    candidate_cases, candidate_executions = await run_executions(
        cases_path,
        runner=active_candidate_runner,
    )
    baseline_mode = baseline_executions[0].mode if baseline_executions else "workflow"
    candidate_mode = candidate_executions[0].mode if candidate_executions else "workflow_shadow"
    baseline = build_evaluation_run(
        baseline_cases,
        baseline_executions,
        mode=baseline_mode,
        metadata=getattr(active_baseline_runner, "metadata", EvaluationRunMetadata()),
    )
    candidate = build_evaluation_run(
        candidate_cases,
        candidate_executions,
        mode=candidate_mode,
        metadata=getattr(active_candidate_runner, "metadata", EvaluationRunMetadata()),
    )
    _validate_case_alignment(baseline_executions, candidate_executions)
    comparisons = [
        _compare_executions(baseline_item, candidate_item, baseline.mode, candidate.mode)
        for baseline_item, candidate_item in zip(baseline_executions, candidate_executions, strict=True)
    ]
    divergent = sum(1 for item in comparisons if item.status != "equivalent")
    return ModeComparisonRun(
        total=len(comparisons),
        equivalent=len(comparisons) - divergent,
        divergent=divergent,
        results=comparisons,
        baseline=baseline,
        candidate=candidate,
    )


def _validate_case_alignment(
    baseline_executions: list[CaseExecution],
    candidate_executions: list[CaseExecution],
) -> None:
    baseline_ids = [execution.case_id for execution in baseline_executions]
    candidate_ids = [execution.case_id for execution in candidate_executions]
    if baseline_ids != candidate_ids:
        raise ValueError(
            "Mode comparison requires matching case order. "
            f"baseline={baseline_ids}, candidate={candidate_ids}"
        )


def _compare_executions(
    baseline: CaseExecution,
    candidate: CaseExecution,
    baseline_mode: EvalMode,
    candidate_mode: EvalMode,
) -> ModeComparisonCaseResult:
    baseline_result = baseline.result
    candidate_result = candidate.result
    pass_matches = baseline_result.passed == candidate_result.passed
    route_matches = baseline_result.route == candidate_result.route
    artifact_matches = _artifact_contract(baseline) == _artifact_contract(candidate)
    workflow_matches = _workflow_contract(baseline) == _workflow_contract(candidate)
    same_contract = (
        pass_matches
        and route_matches
        and baseline_result.expected_route == candidate_result.expected_route
        and artifact_matches
        and workflow_matches
    )
    if same_contract:
        status = "equivalent"
    elif baseline_result.passed and not candidate_result.passed:
        status = "baseline_only_pass"
    elif candidate_result.passed and not baseline_result.passed:
        status = "candidate_only_pass"
    elif route_matches:
        status = "divergent_safe"
    else:
        status = "divergent_unsafe"

    return ModeComparisonCaseResult(
        case_id=baseline.case_id,
        baseline_mode=baseline_mode,
        candidate_mode=candidate_mode,
        status=status,
        baseline_passed=baseline_result.passed,
        candidate_passed=candidate_result.passed,
        baseline_route=baseline_result.route,
        candidate_route=candidate_result.route,
        pass_matches=pass_matches,
        route_matches=route_matches,
        artifact_matches=artifact_matches,
        workflow_matches=workflow_matches,
        summary=f"{baseline_mode}:{baseline_result.summary} | {candidate_mode}:{candidate_result.summary}",
    )


def _artifact_contract(execution: CaseExecution) -> dict:
    response = execution.response
    confirmed = execution.confirmed_response
    if response is None:
        return {"error": execution.diagnostics}
    return {
        "tool_call": _tool_call_contract(response.tool_call),
        "pending_tool_call": _tool_call_contract(
            response.pending_action.tool_call if response.pending_action else None
        ),
        "initial_tool_result": _tool_result_contract(response.tool_result),
        "confirmed_tool_result": _tool_result_contract(confirmed.tool_result if confirmed else None),
        "generated_sql": response.generated_sql,
        "sql_result": _sql_result_contract(response.sql_result),
        "citations": [(citation.filename, citation.chunk_index) for citation in response.citations],
    }


def _workflow_contract(execution: CaseExecution) -> dict:
    return {
        "workflow_status": execution.workflow_status,
        "workflow_type": execution.workflow_type,
        "workflow_source_document_id": execution.workflow_source_document_id,
        "workflow_source_note_id": execution.workflow_source_note_id,
        "workflow_steps": execution.workflow_steps,
    }


def _tool_call_contract(tool_call: ToolCall | None) -> dict | None:
    if tool_call is None:
        return None
    return {
        "name": tool_call.name,
        "args": tool_call.args.clean(),
        "requires_confirmation": tool_call.requires_confirmation,
    }


def _tool_result_contract(tool_result: ToolResult | None) -> dict | None:
    if tool_result is None:
        return None
    return {
        "name": tool_result.name,
        "ok": tool_result.ok,
        "result": tool_result.result,
        "error": tool_result.error,
    }


def _sql_result_contract(sql_result: SqlResult | None) -> dict | None:
    if sql_result is None:
        return None
    return {
        "sql": sql_result.sql,
        "columns": sql_result.columns,
        "rows": sql_result.rows,
    }
