from __future__ import annotations

import asyncio
import json
from pathlib import Path

from pydantic import BaseModel

from project4_agentic_project_copilot.app.database import CopilotDatabase
from project4_agentic_project_copilot.app.embeddings import HashEmbeddingClient
from project4_agentic_project_copilot.app.llm import RuleBasedLlmClient
from project4_agentic_project_copilot.app.models import ChatRequest, EvaluationCase, EvaluationResult
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
        response = await service.chat(ChatRequest(session_id=session_id, message=case.user_query))
        passed = _case_passed(case, response)
        if passed and case.confirm_action and response.pending_action:
            confirmed = await service.chat(
                ChatRequest(
                    session_id=session_id,
                    message="Confirm action",
                    confirm_action_id=response.pending_action.action_id,
                )
            )
            passed = confirmed.tool_result is not None and confirmed.tool_result.ok
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


def _case_passed(case: EvaluationCase, response) -> bool:
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
    return passed


def load_cases(path: Path | str) -> list[EvaluationCase]:
    return [EvaluationCase.model_validate_json(line) for line in Path(path).read_text().splitlines() if line.strip()]


def main() -> None:
    run = asyncio.run(run_evaluation())
    print(json.dumps(run.model_dump(mode="json"), indent=2))


if __name__ == "__main__":
    main()
