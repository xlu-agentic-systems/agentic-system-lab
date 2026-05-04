import asyncio

from project4_agentic_project_copilot.app.evaluation import run_evaluation, run_goal_harness


def test_evaluation_harness_passes_sample_cases() -> None:
    result = asyncio.run(run_evaluation())

    assert result.total == 8
    assert result.passed == 8


def test_goal_harness_validates_productivity_workflow_claim() -> None:
    result = asyncio.run(run_goal_harness())

    assert result.total == 7
    assert result.passed == 7
    assert {check.name for check in result.checks} == {
        "rag_over_uploaded_documents",
        "local_document_persistence",
        "human_review_gate_before_note_write",
        "personal_note_tool_execution",
        "persisted_workflow_state_for_reviewable_task",
        "workflow_completion_after_human_review",
        "regression_eval_suite",
    }
