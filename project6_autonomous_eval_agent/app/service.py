from __future__ import annotations

import logging
import uuid
from pathlib import Path

from agentic_system_lab.observability import log_agent_event
from project3_adaptive_eval_system.app.llm import RuleBasedLlmClient
from project3_adaptive_eval_system.app.service import EvaluationService
from project3_adaptive_eval_system.app.store import (
    ConversationTraceStore,
    GeneratedTestStore,
    PromptPatchStore,
)
from project6_autonomous_eval_agent.app.agents import (
    AutonomousPlanner,
    LlmAutonomousPlanner,
    RuleBasedAutonomousPlanner,
)
from project6_autonomous_eval_agent.app.models import (
    AcceptanceGate,
    AutonomousAction,
    AutonomousGoal,
    AutonomousRunResult,
    RunStatus,
    StepObservation,
)
from project6_autonomous_eval_agent.app.tools import AutonomousEvalTools


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RUN_ROOT = PROJECT_ROOT / "autonomous_runs"
logger = logging.getLogger(__name__)


class AutonomousEvalService:
    def __init__(
        self,
        *,
        run_root: Path | str = DEFAULT_RUN_ROOT,
        planner: AutonomousPlanner | None = None,
    ) -> None:
        self.run_root = Path(run_root)
        self.planner = planner

    async def run(
        self,
        goal: AutonomousGoal | None = None,
        *,
        use_rule_based: bool = False,
    ) -> AutonomousRunResult:
        goal = goal or AutonomousGoal()
        run_id = f"auto-eval-{uuid.uuid4().hex[:12]}"
        run_dir = self.run_root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        prompt_patch_store = PromptPatchStore(run_dir / "prompt_patches.jsonl")
        service = EvaluationService(
            trace_store=ConversationTraceStore(),
            generated_test_store=GeneratedTestStore(run_dir / "generated_tests" / "regression_cases.jsonl"),
            prompt_patch_store=prompt_patch_store,
            report_path=run_dir / "evaluation_report.md",
            llm_client=RuleBasedLlmClient() if use_rule_based else None,
            prompt_candidate_dir=run_dir / "prompt_candidates",
        )
        tools = AutonomousEvalTools(
            service=service,
            prompt_patch_store=prompt_patch_store,
            run_dir=run_dir,
        )
        planner = self.planner or (RuleBasedAutonomousPlanner() if use_rule_based else LlmAutonomousPlanner())
        observations: list[StepObservation] = []
        status: RunStatus = "max_iterations_reached"

        log_agent_event(
            logger,
            event="autonomous_run_started",
            message="project6 autonomous evaluation run started",
            agent="autonomous_eval_agent",
            session_id=run_id,
            attributes={"max_iterations": goal.max_iterations, "use_rule_based": use_rule_based},
        )

        for iteration in range(1, goal.max_iterations + 1):
            gates = _assess_gates(goal, tools)
            action = await planner.plan(goal=goal, observations=observations, gates=gates)
            observation = await self._execute_action(
                action=action,
                iteration=iteration,
                goal=goal,
                tools=tools,
                gates=gates,
            )
            observations.append(observation)
            log_agent_event(
                logger,
                event="tool_execution",
                message="project6 autonomous tool step executed",
                agent="autonomous_eval_agent",
                session_id=run_id,
                tool_name=action.tool_name,
                status="ok" if observation.ok else "failed",
                attributes={"iteration": iteration, "summary": observation.summary},
            )
            if action.tool_name == "finish":
                status = "completed" if _all_gates_pass(gates) else "blocked"
                break
            if not observation.ok:
                status = "failed"
                break

        gates = _assess_gates(goal, tools)
        if status == "max_iterations_reached" and _all_gates_pass(gates):
            status = "completed"
        result = _result(
            run_id=run_id,
            status=status,
            goal=goal,
            observations=observations,
            gates=gates,
            tools=tools,
            report_path=run_dir / "autonomous_report.md",
        )
        _write_report(result)
        log_agent_event(
            logger,
            event="autonomous_run_finished",
            message="project6 autonomous evaluation run finished",
            agent="autonomous_eval_agent",
            session_id=run_id,
            status=result.status,
            attributes={"iterations": result.iterations},
        )
        return result

    async def _execute_action(
        self,
        *,
        action: AutonomousAction,
        iteration: int,
        goal: AutonomousGoal,
        tools: AutonomousEvalTools,
        gates: list[AcceptanceGate],
    ) -> StepObservation:
        try:
            if action.tool_name == "evaluate_traces":
                execution = await tools.evaluate_traces(trace_limit=action.trace_limit or goal.trace_limit)
            elif action.tool_name == "run_labeled_benchmark":
                execution = await tools.run_labeled_benchmark()
            elif action.tool_name == "generate_candidate_prompts":
                execution = await tools.generate_candidate_prompts()
            elif action.tool_name == "finish":
                execution = _finish_execution(gates)
            else:
                execution = None
            if execution is None:
                return StepObservation(
                    iteration=iteration,
                    tool_name=action.tool_name,
                    ok=False,
                    summary=f"unsupported action: {action.tool_name}",
                )
            return StepObservation(
                iteration=iteration,
                tool_name=action.tool_name,
                ok=execution.ok,
                summary=f"{action.reason} Result: {execution.summary}",
            )
        except Exception as exc:
            return StepObservation(
                iteration=iteration,
                tool_name=action.tool_name,
                ok=False,
                summary=f"{action.reason} Error: {exc}",
            )


def _finish_execution(gates: list[AcceptanceGate]):
    from project6_autonomous_eval_agent.app.tools import ToolExecution

    if _all_gates_pass(gates):
        return ToolExecution(ok=True, summary="all acceptance gates passed")
    failed = ", ".join(gate.name for gate in gates if not gate.passed)
    return ToolExecution(ok=False, summary=f"cannot finish; failed gates: {failed}")


def _assess_gates(goal: AutonomousGoal, tools: AutonomousEvalTools) -> list[AcceptanceGate]:
    evaluation = tools.state.evaluation_result
    benchmark = tools.state.benchmark_result
    generated_tests = len(evaluation.generated_tests) if evaluation else 0
    patches = len(evaluation.prompt_patches) if evaluation else 0
    candidates = len(tools.state.candidate_prompt_paths)
    gates = [
        AcceptanceGate(
            name="evaluations_created",
            passed=evaluation is not None and generated_tests > 0 and patches > 0,
            summary=(
                f"generated_tests={generated_tests}, prompt_patches={patches}"
                if evaluation is not None
                else "evaluation has not run"
            ),
        ),
        AcceptanceGate(
            name="benchmark_quality",
            passed=(
                benchmark is not None
                and benchmark.pass_fail_accuracy >= goal.min_pass_fail_accuracy
                and benchmark.issue_category_recall >= goal.min_issue_category_recall
            ),
            summary=(
                f"pass_fail_accuracy={benchmark.pass_fail_accuracy}, "
                f"issue_category_recall={benchmark.issue_category_recall}"
                if benchmark is not None
                else "labeled benchmark has not run"
            ),
        ),
        AcceptanceGate(
            name="candidate_prompts",
            passed=not goal.require_candidate_prompts or (patches > 0 and candidates >= patches),
            summary=f"candidate_prompts={candidates}, required={goal.require_candidate_prompts}",
        ),
        AcceptanceGate(
            name="production_prompt_guard",
            passed=True,
            summary="autonomous run writes review artifacts only and does not mutate production prompts",
        ),
    ]
    return gates


def _all_gates_pass(gates: list[AcceptanceGate]) -> bool:
    return all(gate.passed for gate in gates)


def _result(
    *,
    run_id: str,
    status: RunStatus,
    goal: AutonomousGoal,
    observations: list[StepObservation],
    gates: list[AcceptanceGate],
    tools: AutonomousEvalTools,
    report_path: Path,
) -> AutonomousRunResult:
    evaluation = tools.state.evaluation_result
    benchmark = tools.state.benchmark_result
    return AutonomousRunResult(
        run_id=run_id,
        status=status,
        goal=goal,
        iterations=len(observations),
        observations=observations,
        gates=gates,
        generated_test_count=len(evaluation.generated_tests) if evaluation else 0,
        prompt_patch_count=len(evaluation.prompt_patches) if evaluation else 0,
        candidate_prompt_count=len(tools.state.candidate_prompt_paths),
        pass_fail_accuracy=benchmark.pass_fail_accuracy if benchmark else None,
        issue_category_recall=benchmark.issue_category_recall if benchmark else None,
        report_path=str(report_path),
    )


def _write_report(result: AutonomousRunResult) -> None:
    path = Path(result.report_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Project 6 Autonomous Evaluation Run",
        "",
        f"Run ID: {result.run_id}",
        f"Status: {result.status}",
        f"Iterations: {result.iterations}",
        f"Generated tests: {result.generated_test_count}",
        f"Prompt patches: {result.prompt_patch_count}",
        f"Candidate prompts: {result.candidate_prompt_count}",
        f"Pass/fail accuracy: {result.pass_fail_accuracy}",
        f"Issue category recall: {result.issue_category_recall}",
        "",
        "## Acceptance Gates",
        "",
    ]
    for gate in result.gates:
        status = "passed" if gate.passed else "failed"
        lines.append(f"- {gate.name}: {status} - {gate.summary}")
    lines.extend(["", "## Agent Steps", ""])
    for observation in result.observations:
        ok = "ok" if observation.ok else "failed"
        lines.append(f"- {observation.iteration}. {observation.tool_name}: {ok} - {observation.summary}")
    path.write_text("\n".join(lines) + "\n")
