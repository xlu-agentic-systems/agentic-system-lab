from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from project4_agentic_project_copilot.app.eval_framework.models import EvaluationCase
from project4_agentic_project_copilot.app.eval_framework.service_factory import create_evaluation_service_at
from project4_agentic_project_copilot.app.llm import RuleBasedLlmClient
from project4_agentic_project_copilot.app.models import ChatRequest, ToolCall
from project4_agentic_project_copilot.app.service import ProjectCopilotService


@dataclass
class EvaluationHarness:
    root: Path
    service: ProjectCopilotService


def create_harness(prefix: str = "project4-eval-") -> EvaluationHarness:
    root = Path(tempfile.mkdtemp(prefix=prefix))
    service = create_evaluation_service_at(root, llm_client=RuleBasedLlmClient())
    return EvaluationHarness(root=root, service=service)


async def prepare_case(harness: EvaluationHarness, case: EvaluationCase, session_id: str) -> None:
    if case.expected_tool_choice == "file_retrieval":
        await harness.service.upload_file(
            filename="launch_brief.md",
            content_type="text/markdown",
            content=(
                b"# Launch Brief\n"
                b"The Apollo Launch project requires a rollout checklist, API contract review, and clear owners."
            ),
            session_id=session_id,
        )
    if case.setup_note_title and case.setup_note_body:
        if case.setup_note_via_workflow:
            await _create_note_through_workflow(harness, case, session_id)
            return
        note_result = harness.service.tools.execute(
            ToolCall(
                name="create_note",
                args={"title": case.setup_note_title, "body": case.setup_note_body},
                requires_confirmation=True,
                reason="Evaluation setup fixture.",
            )
        )
        if note_result.ok and isinstance(note_result.result, dict):
            context = await harness.service.session_store.load(session_id)
            context.current_note_id = int(note_result.result["note_id"])
            await harness.service.session_store.save(context)


async def _create_note_through_workflow(
    harness: EvaluationHarness,
    case: EvaluationCase,
    session_id: str,
) -> None:
    proposal = await harness.service.chat(
        ChatRequest(
            session_id=session_id,
            message=f"Save this as a note: {case.setup_note_body}",
        )
    )
    if proposal.pending_action is None:
        raise RuntimeError(f"Eval fixture {case.case_id} could not create a pending note action.")
    confirmed = await harness.service.chat(
        ChatRequest(
            session_id=session_id,
            message="Confirm note",
            confirm_action_id=proposal.pending_action.action_id,
        )
    )
    if confirmed.tool_result is None or not confirmed.tool_result.ok:
        error = confirmed.tool_result.error if confirmed.tool_result else "missing tool result"
        raise RuntimeError(f"Eval fixture {case.case_id} could not confirm setup note: {error}")
