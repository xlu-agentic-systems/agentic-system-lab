import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from project6_autonomous_eval_agent.app import main
from project6_autonomous_eval_agent.app.models import (
    AutonomousAction,
    AutonomousGoal,
)
from project6_autonomous_eval_agent.app.service import AutonomousEvalService


def run(coro):
    return asyncio.run(coro)


def test_rule_based_autonomous_run_completes(tmp_path: Path) -> None:
    service = AutonomousEvalService(run_root=tmp_path)

    result = run(service.run(use_rule_based=True))

    assert result.status == "completed"
    assert [observation.tool_name for observation in result.observations] == [
        "evaluate_traces",
        "run_labeled_benchmark",
        "generate_candidate_prompts",
        "finish",
    ]
    assert result.generated_test_count == 2
    assert result.prompt_patch_count == 2
    assert result.candidate_prompt_count == 2
    assert result.pass_fail_accuracy is not None and result.pass_fail_accuracy >= 0.8
    assert result.issue_category_recall is not None and result.issue_category_recall >= 0.8
    assert all(gate.passed for gate in result.gates)
    assert Path(result.report_path).exists()


def test_finish_before_gates_is_blocked(tmp_path: Path) -> None:
    service = AutonomousEvalService(run_root=tmp_path, planner=FinishImmediatelyPlanner())

    result = run(service.run(use_rule_based=True))

    assert result.status == "blocked"
    assert result.iterations == 1
    assert result.observations[0].tool_name == "finish"
    assert result.observations[0].ok is False
    assert any(not gate.passed for gate in result.gates)


def test_max_iteration_guard_stops_repeated_tool_choice(tmp_path: Path) -> None:
    service = AutonomousEvalService(run_root=tmp_path, planner=RepeatEvaluationPlanner())
    goal = AutonomousGoal(max_iterations=2)

    result = run(service.run(goal=goal, use_rule_based=True))

    assert result.status == "max_iterations_reached"
    assert result.iterations == 2
    assert [observation.tool_name for observation in result.observations] == [
        "evaluate_traces",
        "evaluate_traces",
    ]
    assert any(gate.name == "benchmark_quality" and not gate.passed for gate in result.gates)


def test_candidate_generation_does_not_mutate_production_prompt(tmp_path: Path) -> None:
    source_path = Path("prompts/project1_multi_agent.md")
    before = source_path.read_text()
    service = AutonomousEvalService(run_root=tmp_path)

    result = run(service.run(use_rule_based=True))

    assert result.status == "completed"
    assert source_path.read_text() == before
    candidate_files = list(tmp_path.glob("*/prompt_candidates/*.md"))
    assert len(candidate_files) == result.candidate_prompt_count
    assert all("Autonomous Candidate Patch" in path.read_text() for path in candidate_files)


def test_http_app_runs_autonomous_loop_with_local_service(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main, "service", AutonomousEvalService(run_root=tmp_path))
    client = TestClient(main.app)

    assert client.get("/health").json() == {"status": "ok"}
    response = client.post("/autonomous-runs", json={"use_rule_based": True})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["generated_test_count"] == 2
    assert body["candidate_prompt_count"] == 2


class FinishImmediatelyPlanner:
    async def plan(self, *, goal, observations, gates):
        return AutonomousAction(tool_name="finish", reason="Try to finish immediately.")


class RepeatEvaluationPlanner:
    async def plan(self, *, goal, observations, gates):
        return AutonomousAction(
            tool_name="evaluate_traces",
            reason="Keep evaluating and ignore benchmark gates.",
            trace_limit=goal.trace_limit,
        )
