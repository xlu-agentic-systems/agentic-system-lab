import asyncio
import json

import pytest

from project4_agentic_project_copilot.app.eval_framework import (
    EVAL_CASES_PATH,
    WorkflowEvalRunner,
)
from project4_agentic_project_copilot.app.evaluation import (
    load_cases,
    run_evaluation,
    run_goal_harness,
    run_mode_comparison,
)


def test_evaluation_harness_passes_sample_cases() -> None:
    result = asyncio.run(run_evaluation())

    assert result.total == 10
    assert result.passed == 10
    assert set(result.coverage.routes) == {
        "api_tool",
        "clarify",
        "context",
        "file_retrieval",
        "sql_query",
    }
    assert result.coverage.tools["search_tasks"].total == 1
    assert result.coverage.tools["search_notes"].total == 1
    assert result.coverage.tools["create_task"].total == 2
    assert result.coverage.confirmation.total == 3
    assert result.coverage.sql_refusal.total == 1
    assert result.coverage.workflow_types["document_or_note_to_task"].total == 1


def test_goal_harness_validates_productivity_workflow_claim() -> None:
    result = asyncio.run(run_goal_harness())

    assert result.total == 8
    assert result.passed == 8
    assert {check.name for check in result.checks} == {
        "rag_over_uploaded_documents",
        "local_document_persistence",
        "human_review_gate_before_note_write",
        "personal_note_tool_execution",
        "persisted_workflow_state_for_reviewable_task",
        "workflow_completion_after_human_review",
        "missing_confirmation_is_rejected",
        "regression_eval_suite",
    }


def test_mode_comparison_matches_workflow_shadow() -> None:
    comparison = asyncio.run(run_mode_comparison())

    assert comparison.total == 10
    assert comparison.equivalent == 10
    assert comparison.divergent == 0
    assert comparison.baseline.passed == 10
    assert comparison.candidate.passed == 10
    assert all(item.artifact_matches and item.workflow_matches for item in comparison.results)


def test_mode_comparison_detects_candidate_divergence() -> None:
    comparison = asyncio.run(run_mode_comparison(candidate_runner=DivergentCandidateRunner()))
    by_case = {item.case_id: item for item in comparison.results}

    assert comparison.divergent == 5
    assert by_case["context-current-state"].status == "divergent_unsafe"
    assert by_case["tool-search-tasks"].status == "baseline_only_pass"
    assert by_case["tool-search-notes"].status == "divergent_safe"
    assert by_case["tool-search-notes"].artifact_matches is False
    assert by_case["tool-create-task-confirmed"].status == "divergent_safe"
    assert by_case["tool-create-task-confirmed"].workflow_matches is False
    assert by_case["tool-create-note-confirmed"].status == "divergent_safe"
    assert by_case["tool-create-note-confirmed"].artifact_matches is False


def test_mode_comparison_rejects_misaligned_candidate_cases() -> None:
    with pytest.raises(ValueError, match="matching case order"):
        asyncio.run(run_mode_comparison(candidate_runner=MislabelingCandidateRunner()))


def test_evaluation_rejects_duplicate_case_ids(tmp_path) -> None:
    first_case = EVAL_CASES_PATH.read_text().splitlines()[0]
    cases_path = tmp_path / "duplicate_cases.jsonl"
    cases_path.write_text(f"{first_case}\n{first_case}\n")

    with pytest.raises(ValueError, match="Duplicate eval case_id"):
        load_cases(cases_path)


def test_evaluation_cases_are_order_independent(tmp_path) -> None:
    cases_path = tmp_path / "reversed_cases.jsonl"
    cases_path.write_text("\n".join(reversed(EVAL_CASES_PATH.read_text().splitlines())) + "\n")

    result = asyncio.run(run_evaluation(cases_path))

    assert result.total == 10
    assert result.passed == 10


def test_evaluation_reports_failure_diagnostics(tmp_path) -> None:
    bad_case = json.loads(EVAL_CASES_PATH.read_text().splitlines()[0])
    bad_case["case_id"] = "bad-route-expectation"
    bad_case["expected_tool_choice"] = "context"
    bad_case["expected_data_source"] = "session_context"
    cases_path = tmp_path / "bad_case.jsonl"
    cases_path.write_text(json.dumps(bad_case) + "\n")

    result = asyncio.run(run_evaluation(cases_path))

    assert result.total == 1
    assert result.passed == 0
    assert "expected route context" in result.results[0].summary


class DivergentCandidateRunner:
    mode = "autonomous"

    def __init__(self) -> None:
        self.delegate = WorkflowEvalRunner(mode=self.mode)

    async def run_case(self, case):
        execution = await self.delegate.run_case(case)
        if case.case_id == "context-current-state":
            result = execution.result.model_copy(
                update={"route": "clarify", "summary": "candidate routed differently"}
            )
            return execution.model_copy(update={"result": result})
        if case.case_id == "tool-search-tasks":
            result = execution.result.model_copy(
                update={"passed": False, "summary": "candidate failed task search"}
            )
            return execution.model_copy(
                update={
                    "passed": False,
                    "result": result,
                    "diagnostics": ["candidate failed task search"],
                }
            )
        if case.case_id == "tool-search-notes" and execution.response.tool_result:
            response = execution.response.model_copy(
                update={
                    "tool_result": execution.response.tool_result.model_copy(
                        update={"result": []}
                    )
                }
            )
            return execution.model_copy(update={"response": response})
        if case.case_id == "tool-create-task-confirmed":
            return execution.model_copy(
                update={
                    "workflow_steps": [
                        {"name": "proposed_tool_action", "status": "awaiting_review"},
                    ]
                }
            )
        if case.case_id == "tool-create-note-confirmed" and execution.confirmed_response:
            tool_result = execution.confirmed_response.tool_result
            if tool_result is not None:
                confirmed_response = execution.confirmed_response.model_copy(
                    update={
                        "tool_result": tool_result.model_copy(
                            update={
                                "result": {
                                    "note_id": 999,
                                    "title": "Wrong note",
                                    "source_document_id": None,
                                }
                            }
                        )
                    }
                )
                return execution.model_copy(update={"confirmed_response": confirmed_response})
        return execution


class MislabelingCandidateRunner:
    mode = "autonomous"

    def __init__(self) -> None:
        self.delegate = WorkflowEvalRunner(mode=self.mode)

    async def run_case(self, case):
        execution = await self.delegate.run_case(case)
        if case.case_id == "file-launch-brief":
            return execution.model_copy(update={"case_id": "wrong-case-id"})
        return execution
