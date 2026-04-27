from __future__ import annotations

from typing import Protocol

from project3_adaptive_eval_system.app.llm import LlmClient, OpenAILlmClient
from project6_autonomous_eval_agent.app.models import (
    AcceptanceGate,
    AutonomousAction,
    AutonomousGoal,
    StepObservation,
)


class AutonomousPlanner(Protocol):
    async def plan(
        self,
        *,
        goal: AutonomousGoal,
        observations: list[StepObservation],
        gates: list[AcceptanceGate],
    ) -> AutonomousAction:
        ...


class LlmAutonomousPlanner:
    def __init__(self, llm_client: LlmClient | None = None) -> None:
        self.llm_client = llm_client or OpenAILlmClient()

    async def plan(
        self,
        *,
        goal: AutonomousGoal,
        observations: list[StepObservation],
        gates: list[AcceptanceGate],
    ) -> AutonomousAction:
        return await self.llm_client.parse(
            task_name="project6_autonomous_eval_planner",
            system_prompt=(
                "You are an autonomous adaptive-evaluation agent. Choose exactly one next tool. "
                "Your objective is to evaluate traces, create regression artifacts, benchmark evaluator "
                "quality, generate non-production candidate prompt files when required, and finish only "
                "after every acceptance gate passes. You may not directly mutate production prompts. "
                "The host application will validate and execute the selected tool."
            ),
            user_payload={
                "goal": goal.model_dump(mode="json"),
                "observations": [item.model_dump(mode="json") for item in observations],
                "gates": [gate.model_dump(mode="json") for gate in gates],
                "available_tools": [
                    "evaluate_traces",
                    "run_labeled_benchmark",
                    "generate_candidate_prompts",
                    "finish",
                ],
            },
            response_model=AutonomousAction,
        )


class RuleBasedAutonomousPlanner:
    """Deterministic planner for tests and offline demos."""

    async def plan(
        self,
        *,
        goal: AutonomousGoal,
        observations: list[StepObservation],
        gates: list[AcceptanceGate],
    ) -> AutonomousAction:
        tool_names = [observation.tool_name for observation in observations if observation.ok]
        failed = {gate.name for gate in gates if not gate.passed}
        if "evaluations_created" in failed and "evaluate_traces" not in tool_names:
            return AutonomousAction(
                tool_name="evaluate_traces",
                reason="Evaluate traces and generate regression artifacts before benchmarking.",
                trace_limit=goal.trace_limit,
            )
        if "benchmark_quality" in failed and "run_labeled_benchmark" not in tool_names:
            return AutonomousAction(
                tool_name="run_labeled_benchmark",
                reason="Measure evaluator quality against labeled cases.",
            )
        if "candidate_prompts" in failed and "generate_candidate_prompts" not in tool_names:
            return AutonomousAction(
                tool_name="generate_candidate_prompts",
                reason="Render proposed patches into non-production candidate prompt files.",
            )
        return AutonomousAction(
            tool_name="finish",
            reason="All known acceptance gates are satisfied or no further allowed tool can improve them.",
        )
