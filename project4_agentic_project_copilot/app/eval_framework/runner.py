from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from project4_agentic_project_copilot.app.eval_framework.assertions import (
    case_diagnostics,
    confirmation_diagnostics,
    workflow_row,
    workflow_status,
    workflow_steps,
    workflow_type,
)
from project4_agentic_project_copilot.app.eval_framework.coverage import build_coverage
from project4_agentic_project_copilot.app.eval_framework.fixtures import EvaluationHarness, create_harness, prepare_case
from project4_agentic_project_copilot.app.eval_framework.io import EVAL_CASES_PATH, load_cases
from project4_agentic_project_copilot.app.eval_framework.models import (
    CaseExecution,
    EvalMode,
    EvaluationCase,
    EvaluationResult,
    EvaluationRunMetadata,
    EvaluationRun,
)
from project4_agentic_project_copilot.app.models import ChatRequest
from project4_agentic_project_copilot.app.service import ProjectCopilotService


EvaluationServiceFactory = Callable[[], ProjectCopilotService]


class EvalRunner(Protocol):
    mode: EvalMode

    async def run_case(self, case: EvaluationCase) -> CaseExecution:
        ...


class WorkflowEvalRunner:
    def __init__(
        self,
        *,
        mode: EvalMode = "workflow",
        service_factory: EvaluationServiceFactory | None = None,
        metadata: EvaluationRunMetadata | None = None,
    ) -> None:
        self.mode = mode
        self.service_factory = service_factory
        self.metadata = metadata or EvaluationRunMetadata()

    async def run_case(self, case: EvaluationCase) -> CaseExecution:
        harness = self._create_harness()
        session_id = f"eval-{self.mode}-{case.case_id}"
        await prepare_case(harness, case, session_id)
        return await _execute_case(harness, case, session_id, self.mode)

    def _create_harness(self) -> EvaluationHarness:
        if self.service_factory is None:
            return create_harness(prefix=f"project4-{self.mode}-eval-")
        return EvaluationHarness(root=Path("."), service=self.service_factory())


async def run_evaluation(
    cases_path: Path | str = EVAL_CASES_PATH,
    *,
    runner: EvalRunner | None = None,
    service_factory: EvaluationServiceFactory | None = None,
    mode: EvalMode = "workflow",
) -> EvaluationRun:
    cases = load_cases(cases_path)
    active_runner = runner or WorkflowEvalRunner(mode=mode, service_factory=service_factory)
    executions = []
    for case in cases:
        try:
            executions.append(await active_runner.run_case(case))
        except Exception as exc:
            executions.append(_failed_execution(case, active_runner.mode, exc))
    return build_evaluation_run(
        cases,
        executions,
        mode=active_runner.mode,
        metadata=getattr(active_runner, "metadata", EvaluationRunMetadata()),
    )


async def run_executions(
    cases_path: Path | str = EVAL_CASES_PATH,
    *,
    runner: EvalRunner | None = None,
    service_factory: EvaluationServiceFactory | None = None,
    mode: EvalMode = "workflow",
) -> tuple[list[EvaluationCase], list[CaseExecution]]:
    cases = load_cases(cases_path)
    active_runner = runner or WorkflowEvalRunner(mode=mode, service_factory=service_factory)
    executions = []
    for case in cases:
        try:
            executions.append(await active_runner.run_case(case))
        except Exception as exc:
            executions.append(_failed_execution(case, active_runner.mode, exc))
    return cases, executions


def build_evaluation_run(
    cases: list[EvaluationCase],
    executions: list[CaseExecution],
    *,
    mode: EvalMode,
    metadata: EvaluationRunMetadata | None = None,
) -> EvaluationRun:
    results = [execution.result for execution in executions]
    return EvaluationRun(
        mode=mode,
        total=len(results),
        passed=sum(1 for item in results if item.passed),
        results=results,
        coverage=build_coverage(cases, results),
        metadata=metadata or EvaluationRunMetadata(),
    )


async def run_case(service: ProjectCopilotService, case: EvaluationCase) -> EvaluationResult:
    harness = EvaluationHarness(root=Path("."), service=service)
    session_id = f"eval-{case.case_id}"
    await prepare_case(harness, case, session_id)
    return (await _execute_case(harness, case, session_id, "workflow")).result


def _failed_execution(case: EvaluationCase, mode: EvalMode, exc: Exception) -> CaseExecution:
    summary = f"Eval case raised {type(exc).__name__}: {exc}"
    result = EvaluationResult(
        case_id=case.case_id,
        passed=False,
        route=case.expected_tool_choice,
        expected_route=case.expected_tool_choice,
        summary=summary,
    )
    return CaseExecution(
        case_id=case.case_id,
        mode=mode,
        passed=False,
        result=result,
        diagnostics=[summary],
    )


async def _execute_case(
    harness: EvaluationHarness,
    case: EvaluationCase,
    session_id: str,
    mode: EvalMode,
) -> CaseExecution:
    response = await harness.service.chat(ChatRequest(session_id=session_id, message=case.user_query))
    diagnostics = case_diagnostics(case, response, harness.service)
    confirmed_response = None
    if not diagnostics and case.confirm_action:
        if response.pending_action is None:
            diagnostics.append("expected pending action before confirmation")
        else:
            confirmed_response = await harness.service.chat(
                ChatRequest(
                    session_id=session_id,
                    message="Confirm action",
                    confirm_action_id=response.pending_action.action_id,
                )
            )
            diagnostics.extend(confirmation_diagnostics(case, confirmed_response, harness.service))

    final_response = confirmed_response or response
    workflow_id = final_response.context.current_workflow_id
    workflow = workflow_row(harness.service, workflow_id)
    passed = not diagnostics
    summary = response.decision_log.reasoning if passed else "; ".join(diagnostics)
    result = EvaluationResult(
        case_id=case.case_id,
        passed=passed,
        route=response.route,
        expected_route=case.expected_tool_choice,
        summary=summary,
    )
    return CaseExecution(
        case_id=case.case_id,
        mode=mode,
        passed=passed,
        result=result,
        response=response,
        confirmed_response=confirmed_response,
        diagnostics=diagnostics,
        workflow_status=workflow_status(harness.service, workflow_id),
        workflow_type=workflow_type(harness.service, workflow_id),
        workflow_source_document_id=workflow["source_document_id"] if workflow else None,
        workflow_source_note_id=workflow["source_note_id"] if workflow else None,
        workflow_steps=workflow_steps(harness.service, workflow_id),
    )
