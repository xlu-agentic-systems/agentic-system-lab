from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from project4_agentic_project_copilot.app.eval_framework.assertions import (
    workflow_has_status,
    workflow_has_step_names,
    workflow_row,
)
from project4_agentic_project_copilot.app.eval_framework.models import GoalHarnessCheck, GoalHarnessRun
from project4_agentic_project_copilot.app.eval_framework.io import EVAL_CASES_PATH
from project4_agentic_project_copilot.app.eval_framework.models import EvaluationRunMetadata
from project4_agentic_project_copilot.app.eval_framework.runner import EvalRunner, run_evaluation
from project4_agentic_project_copilot.app.eval_framework.service_factory import create_evaluation_service
from project4_agentic_project_copilot.app.models import ChatRequest
from project4_agentic_project_copilot.app.service import ProjectCopilotService


ServiceFactory = Callable[[], ProjectCopilotService]


async def run_goal_harness(
    *,
    service_factory: ServiceFactory | None = None,
    regression_runner: EvalRunner | None = None,
    regression_cases_path: Path | str = EVAL_CASES_PATH,
    metadata: EvaluationRunMetadata | None = None,
) -> GoalHarnessRun:
    service = service_factory() if service_factory else create_evaluation_service(temp_prefix="project4-goal-harness-")
    checks: list[GoalHarnessCheck] = []

    upload = await service.upload_file(
        filename="personal_weekly_notes.md",
        content_type="text/markdown",
        content=(
            b"# Weekly Notes\n"
            b"Call Sam by Friday about the launch checklist. "
            b"Review the API contract before the rollout meeting."
        ),
        session_id="goal",
    )
    file_response = await service.chat(
        ChatRequest(session_id="goal", message="What does this file say about launch follow ups?")
    )
    checks.append(
        GoalHarnessCheck(
            name="rag_over_uploaded_documents",
            passed=file_response.route == "file_retrieval" and bool(file_response.citations),
            summary=f"route={file_response.route}, citations={len(file_response.citations)}",
        )
    )
    checks.append(
        GoalHarnessCheck(
            name="local_document_persistence",
            passed=bool(
                service.db.execute_read(
                    "SELECT 1 AS found FROM documents WHERE document_id = ?",
                    (upload.document_id,),
                )
            ),
            summary=f"document_id={upload.document_id}",
        )
    )

    note_proposal = await service.chat(
        ChatRequest(session_id="goal", message="Save this as a note: Call Sam by Friday about the launch checklist.")
    )
    note_rows_before = service.db.execute_read("SELECT note_id FROM personal_notes")
    note_workflow_type = _workflow_field(service, note_proposal.context.current_workflow_id, "workflow_type")
    checks.append(
        GoalHarnessCheck(
            name="human_review_gate_before_note_write",
            passed=(
                note_proposal.pending_action is not None
                and note_proposal.tool_call is not None
                and note_proposal.tool_call.name == "create_note"
                and note_rows_before == []
                and workflow_has_status(service, note_proposal.context.current_workflow_id, "awaiting_review")
                and note_workflow_type == "capture_personal_note"
            ),
            summary=f"pending={note_proposal.pending_action is not None}, notes_before={len(note_rows_before)}",
        )
    )
    note_confirmed = await service.chat(
        ChatRequest(
            session_id="goal",
            message="Confirm note",
            confirm_action_id=note_proposal.pending_action.action_id if note_proposal.pending_action else None,
        )
    )
    checks.append(
        GoalHarnessCheck(
            name="personal_note_tool_execution",
            passed=(
                note_confirmed.tool_result is not None
                and note_confirmed.tool_result.ok
                and note_confirmed.context.current_note_id is not None
                and workflow_has_status(service, note_confirmed.context.current_workflow_id, "completed")
                and workflow_has_step_names(
                    service,
                    note_confirmed.context.current_workflow_id,
                    ["proposed_tool_action", "confirmed_tool_execution"],
                )
            ),
            summary=f"note_id={note_confirmed.context.current_note_id}",
        )
    )

    tasks_before = service.db.execute_read("SELECT task_id FROM tasks")
    task_proposal = await service.chat(
        ChatRequest(session_id="goal", message="Turn this note into a follow-up task in project 1")
    )
    tasks_after_proposal = service.db.execute_read("SELECT task_id FROM tasks")
    task_workflow_type = _workflow_field(service, task_proposal.context.current_workflow_id, "workflow_type")
    task_source_note_id = _workflow_field(service, task_proposal.context.current_workflow_id, "source_note_id")
    checks.append(
        GoalHarnessCheck(
            name="persisted_workflow_state_for_reviewable_task",
            passed=(
                task_proposal.pending_action is not None
                and task_proposal.tool_call is not None
                and task_proposal.tool_call.name == "create_task"
                and len(tasks_after_proposal) == len(tasks_before)
                and workflow_has_status(service, task_proposal.context.current_workflow_id, "awaiting_review")
                and task_workflow_type == "document_or_note_to_task"
                and task_source_note_id == note_confirmed.context.current_note_id
            ),
            summary=(
                f"tool={task_proposal.tool_call.name if task_proposal.tool_call else None}, "
                f"tasks_before={len(tasks_before)}, tasks_after_proposal={len(tasks_after_proposal)}"
            ),
        )
    )
    task_confirmed = await service.chat(
        ChatRequest(
            session_id="goal",
            message="Confirm task",
            confirm_action_id=task_proposal.pending_action.action_id if task_proposal.pending_action else None,
        )
    )
    checks.append(
        GoalHarnessCheck(
            name="workflow_completion_after_human_review",
            passed=(
                task_confirmed.tool_result is not None
                and task_confirmed.tool_result.ok
                and task_confirmed.context.current_task_id is not None
                and workflow_has_status(service, task_confirmed.context.current_workflow_id, "completed")
                and workflow_has_step_names(
                    service,
                    task_confirmed.context.current_workflow_id,
                    ["proposed_tool_action", "confirmed_tool_execution"],
                )
            ),
            summary=(
                f"task_id={task_confirmed.context.current_task_id}, "
                f"workflow={task_confirmed.context.current_workflow_id}"
            ),
        )
    )

    missing_confirmation = await service.chat(
        ChatRequest(
            session_id="missing-confirmation",
            message="Confirm missing",
            confirm_action_id="missing-action-id",
        )
    )
    checks.append(
        GoalHarnessCheck(
            name="missing_confirmation_is_rejected",
            passed=(
                missing_confirmation.tool_result is not None
                and not missing_confirmation.tool_result.ok
                and missing_confirmation.context.current_workflow_id is None
            ),
            summary=missing_confirmation.response,
        )
    )

    eval_run = await run_evaluation(regression_cases_path, runner=regression_runner)
    checks.append(
        GoalHarnessCheck(
            name="regression_eval_suite",
            passed=eval_run.total > 0 and eval_run.passed == eval_run.total,
            summary=f"passed={eval_run.passed}/{eval_run.total}",
        )
    )
    return GoalHarnessRun(
        total=len(checks),
        passed=sum(1 for check in checks if check.passed),
        checks=checks,
        metadata=metadata or eval_run.metadata,
    )


def _workflow_field(service: ProjectCopilotService, workflow_id: str | None, field: str):
    row = workflow_row(service, workflow_id)
    return row.get(field) if row else None
