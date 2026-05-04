from __future__ import annotations

import asyncio
import json
from pathlib import Path

from pydantic import BaseModel

from project4_agentic_project_copilot.app.database import CopilotDatabase
from project4_agentic_project_copilot.app.embeddings import HashEmbeddingClient
from project4_agentic_project_copilot.app.llm import RuleBasedLlmClient
from project4_agentic_project_copilot.app.models import (
    ChatRequest,
    EvaluationCase,
    EvaluationResult,
    GoalHarnessCheck,
    GoalHarnessRun,
    ToolCall,
)
from project4_agentic_project_copilot.app.service import ProjectCopilotService
from project4_agentic_project_copilot.app.session_store import JsonSessionStore
from project4_agentic_project_copilot.app.trace_store import JsonlTraceStore


EVAL_CASES_PATH = Path(__file__).resolve().parent.parent / "evals" / "cases.jsonl"


class EvaluationRun(BaseModel):
    total: int
    passed: int
    results: list[EvaluationResult]


async def run_evaluation(cases_path: Path | str = EVAL_CASES_PATH) -> EvaluationRun:
    import tempfile

    root = Path(tempfile.mkdtemp(prefix="project4-eval-"))
    db = CopilotDatabase(root / "copilot.sqlite3")
    embedding_client = HashEmbeddingClient()
    service = ProjectCopilotService(
        db=db,
        llm_client=RuleBasedLlmClient(),
        embedding_client=embedding_client,
        session_store=JsonSessionStore(root / "sessions.json"),
        trace_store=JsonlTraceStore(root / "traces.jsonl"),
    )
    cases = load_cases(cases_path)
    results = []
    for case in cases:
        session_id = f"eval-{case.case_id}"
        if case.expected_tool_choice == "file_retrieval":
            await service.upload_file(
                filename="launch_brief.md",
                content_type="text/markdown",
                content=b"# Launch Brief\nThe Apollo Launch project requires a rollout checklist, API contract review, and clear owners.",
                session_id=session_id,
            )
        if case.setup_note_title and case.setup_note_body:
            note_result = service.tools.execute(
                ToolCall(
                    name="create_note",
                    args={"title": case.setup_note_title, "body": case.setup_note_body},
                    requires_confirmation=True,
                    reason="Evaluation setup fixture.",
                )
            )
            if note_result.ok and isinstance(note_result.result, dict):
                context = await service.session_store.load(session_id)
                context.current_note_id = int(note_result.result["note_id"])
                await service.session_store.save(context)
        response = await service.chat(ChatRequest(session_id=session_id, message=case.user_query))
        passed = _case_passed(case, response, service)
        if passed and case.confirm_action and response.pending_action:
            confirmed = await service.chat(
                ChatRequest(
                    session_id=session_id,
                    message="Confirm action",
                    confirm_action_id=response.pending_action.action_id,
                )
            )
            passed = confirmed.tool_result is not None and confirmed.tool_result.ok
            if passed and case.expected_workflow_status:
                passed = _workflow_has_status(service, confirmed.context.current_workflow_id, case.expected_workflow_status)
        results.append(
            EvaluationResult(
                case_id=case.case_id,
                passed=passed,
                route=response.route,
                expected_route=case.expected_tool_choice,
                summary=response.decision_log.reasoning,
            )
        )
    return EvaluationRun(total=len(results), passed=sum(1 for item in results if item.passed), results=results)


async def run_goal_harness() -> GoalHarnessRun:
    import tempfile

    root = Path(tempfile.mkdtemp(prefix="project4-goal-harness-"))
    service = ProjectCopilotService(
        db=CopilotDatabase(root / "copilot.sqlite3"),
        llm_client=RuleBasedLlmClient(),
        embedding_client=HashEmbeddingClient(),
        session_store=JsonSessionStore(root / "sessions.json"),
        trace_store=JsonlTraceStore(root / "traces.jsonl"),
    )
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
            passed=bool(service.db.execute_read("SELECT 1 AS found FROM documents WHERE document_id = ?", (upload.document_id,))),
            summary=f"document_id={upload.document_id}",
        )
    )

    note_proposal = await service.chat(
        ChatRequest(session_id="goal", message="Save this as a note: Call Sam by Friday about the launch checklist.")
    )
    note_rows_before = service.db.execute_read("SELECT note_id FROM personal_notes")
    checks.append(
        GoalHarnessCheck(
            name="human_review_gate_before_note_write",
            passed=(
                note_proposal.pending_action is not None
                and note_proposal.tool_call is not None
                and note_proposal.tool_call.name == "create_note"
                and note_rows_before == []
                and _workflow_has_status(service, note_proposal.context.current_workflow_id, "awaiting_review")
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
                and _workflow_has_status(service, note_confirmed.context.current_workflow_id, "completed")
            ),
            summary=f"note_id={note_confirmed.context.current_note_id}",
        )
    )

    tasks_before = service.db.execute_read("SELECT task_id FROM tasks")
    task_proposal = await service.chat(
        ChatRequest(session_id="goal", message="Turn this note into a follow-up task in project 1")
    )
    tasks_after_proposal = service.db.execute_read("SELECT task_id FROM tasks")
    checks.append(
        GoalHarnessCheck(
            name="persisted_workflow_state_for_reviewable_task",
            passed=(
                task_proposal.pending_action is not None
                and task_proposal.tool_call is not None
                and task_proposal.tool_call.name == "create_task"
                and len(tasks_after_proposal) == len(tasks_before)
                and _workflow_has_status(service, task_proposal.context.current_workflow_id, "awaiting_review")
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
                and _workflow_has_status(service, task_confirmed.context.current_workflow_id, "completed")
            ),
            summary=f"task_id={task_confirmed.context.current_task_id}, workflow={task_confirmed.context.current_workflow_id}",
        )
    )

    eval_run = await run_evaluation()
    checks.append(
        GoalHarnessCheck(
            name="regression_eval_suite",
            passed=eval_run.total > 0 and eval_run.passed == eval_run.total,
            summary=f"passed={eval_run.passed}/{eval_run.total}",
        )
    )
    return GoalHarnessRun(total=len(checks), passed=sum(1 for check in checks if check.passed), checks=checks)


def _case_passed(case: EvaluationCase, response, service: ProjectCopilotService) -> bool:
    passed = response.route == case.expected_tool_choice
    if case.expected_data_source != "conversation":
        passed = passed and response.decision_log.selected_data_source == case.expected_data_source
    if case.expected_tool_name:
        passed = passed and response.tool_call is not None and response.tool_call.name == case.expected_tool_name
    if case.expected_tool_choice == "api_tool" and "confirmation" in case.expected_behavior.lower():
        passed = passed and response.pending_action is not None
    if case.expected_tool_choice == "sql_query":
        if "refuse" in case.expected_behavior.lower() or "refusal" in case.expected_behavior.lower():
            passed = passed and "refused" in response.response.lower()
        else:
            passed = passed and response.generated_sql is not None and response.generated_sql.lower().startswith("select")
            passed = passed and response.sql_result is not None and len(response.sql_result.rows) >= 1
    if case.expected_sql_contains:
        passed = passed and response.generated_sql is not None and case.expected_sql_contains.lower() in response.generated_sql.lower()
    if case.expected_response_contains:
        passed = passed and case.expected_response_contains.lower() in response.response.lower()
    if case.expected_tool_choice == "file_retrieval":
        passed = passed and bool(response.citations)
    if case.expected_workflow_status and (not case.confirm_action or case.expected_workflow_status == "awaiting_review"):
        passed = passed and _workflow_has_status(service, response.context.current_workflow_id, case.expected_workflow_status)
    return passed


def _workflow_has_status(service: ProjectCopilotService, workflow_id: str | None, status: str) -> bool:
    if not workflow_id:
        return False
    rows = service.db.execute_read(
        "SELECT status FROM productivity_workflows WHERE workflow_id = ?",
        (workflow_id,),
    )
    return rows == [{"status": status}]


def load_cases(path: Path | str) -> list[EvaluationCase]:
    return [EvaluationCase.model_validate_json(line) for line in Path(path).read_text().splitlines() if line.strip()]


def main() -> None:
    run = asyncio.run(run_evaluation())
    print(json.dumps(run.model_dump(mode="json"), indent=2))


if __name__ == "__main__":
    main()
