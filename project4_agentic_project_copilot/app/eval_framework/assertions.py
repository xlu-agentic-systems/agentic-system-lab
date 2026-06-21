from __future__ import annotations

import json

from project4_agentic_project_copilot.app.eval_framework.models import EvaluationCase
from project4_agentic_project_copilot.app.models import ChatResponse
from project4_agentic_project_copilot.app.service import ProjectCopilotService


def case_passed(case: EvaluationCase, response: ChatResponse, service: ProjectCopilotService) -> bool:
    return not case_diagnostics(case, response, service)


def case_diagnostics(case: EvaluationCase, response: ChatResponse, service: ProjectCopilotService) -> list[str]:
    diagnostics: list[str] = []
    if response.decision_log.reasoning.startswith("Model/API call failed:"):
        diagnostics.append(response.decision_log.reasoning)
    if response.route != case.expected_tool_choice:
        diagnostics.append(f"expected route {case.expected_tool_choice}, got {response.route}")
    if (
        case.expected_data_source != "conversation"
        and response.decision_log.selected_data_source != case.expected_data_source
    ):
        diagnostics.append(
            "expected data source "
            f"{case.expected_data_source}, got {response.decision_log.selected_data_source}"
        )
    if case.expected_tool_name:
        actual_tool = response.tool_call.name if response.tool_call else None
        if actual_tool != case.expected_tool_name:
            diagnostics.append(f"expected tool {case.expected_tool_name}, got {actual_tool}")
    if _expects_confirmation(case) and response.pending_action is None:
        diagnostics.append("expected pending confirmation action")
    if case.expected_requires_confirmation is False and response.pending_action is not None:
        diagnostics.append("did not expect pending confirmation action")
    if case.expected_tool_choice == "sql_query":
        diagnostics.extend(_sql_diagnostics(case, response))
    if case.expected_sql_contains and (
        response.generated_sql is None or case.expected_sql_contains.lower() not in response.generated_sql.lower()
    ):
        diagnostics.append(f"expected SQL to contain {case.expected_sql_contains!r}, got {response.generated_sql!r}")
    if case.expected_sql_result_value is not None and not _sql_result_contains_value(
        response,
        case.expected_sql_result_value,
    ):
        diagnostics.append(f"expected SQL result to contain {case.expected_sql_result_value!r}")
    if case.expected_response_contains and case.expected_response_contains.lower() not in response.response.lower():
        diagnostics.append(f"expected response to contain {case.expected_response_contains!r}")
    if case.expected_tool_choice == "file_retrieval" and not response.citations:
        diagnostics.append("expected file citations")
    diagnostics.extend(_workflow_diagnostics(case, response, service, check_completed_status=not case.confirm_action))
    return diagnostics


def _sql_result_contains_value(response: ChatResponse, expected: int | float | str | bool) -> bool:
    if response.sql_result is None:
        return False
    if len(response.sql_result.rows) != 1:
        return False
    row = response.sql_result.rows[0]
    if len(row) != 1:
        return False
    value = next(iter(row.values()))
    return value == expected or str(value) == str(expected)


def confirmation_diagnostics(
    case: EvaluationCase,
    response: ChatResponse,
    service: ProjectCopilotService,
) -> list[str]:
    diagnostics: list[str] = []
    if response.tool_result is None:
        diagnostics.append("expected confirmed tool result")
    elif not response.tool_result.ok:
        diagnostics.append(f"confirmed tool failed: {response.tool_result.error}")
    diagnostics.extend(_workflow_diagnostics(case, response, service, check_completed_status=True))
    return diagnostics


def workflow_has_status(service: ProjectCopilotService, workflow_id: str | None, status: str) -> bool:
    row = workflow_row(service, workflow_id)
    return row is not None and row["status"] == status


def workflow_status(service: ProjectCopilotService, workflow_id: str | None) -> str | None:
    row = workflow_row(service, workflow_id)
    return str(row["status"]) if row else None


def workflow_type(service: ProjectCopilotService, workflow_id: str | None) -> str | None:
    row = workflow_row(service, workflow_id)
    return str(row["workflow_type"]) if row else None


def workflow_row(service: ProjectCopilotService, workflow_id: str | None) -> dict | None:
    if not workflow_id:
        return None
    rows = service.db.execute_read(
        """
        SELECT workflow_id, workflow_type, status, source_document_id, source_note_id
        FROM productivity_workflows
        WHERE workflow_id = ?
        """,
        (workflow_id,),
    )
    return rows[0] if rows else None


def workflow_step_names(service: ProjectCopilotService, workflow_id: str | None) -> list[str]:
    return [str(step["name"]) for step in workflow_steps(service, workflow_id)]


def workflow_steps(service: ProjectCopilotService, workflow_id: str | None) -> list[dict[str, object]]:
    if not workflow_id:
        return []
    rows = service.db.execute_read(
        "SELECT name, status, output_json FROM workflow_steps WHERE workflow_id = ? ORDER BY step_index",
        (workflow_id,),
    )
    return [
        {
            "name": str(row["name"]),
            "status": str(row["status"]),
            "output": _json_payload(row["output_json"]),
        }
        for row in rows
    ]


def workflow_has_step_names(
    service: ProjectCopilotService,
    workflow_id: str | None,
    expected_step_names: list[str],
) -> bool:
    return workflow_step_names(service, workflow_id) == expected_step_names


def _sql_diagnostics(case: EvaluationCase, response: ChatResponse) -> list[str]:
    if case.expected_sql_refused:
        diagnostics = []
        if "refused" not in response.response.lower():
            diagnostics.append("expected SQL refusal response")
        if response.sql_result is not None:
            diagnostics.append("refused SQL should not return rows")
        return diagnostics
    diagnostics = []
    if response.generated_sql is None or not response.generated_sql.lower().startswith("select"):
        diagnostics.append(f"expected read-only SELECT SQL, got {response.generated_sql!r}")
    if response.sql_result is None or len(response.sql_result.rows) < 1:
        diagnostics.append("expected SQL result rows")
    return diagnostics


def _workflow_diagnostics(
    case: EvaluationCase,
    response: ChatResponse,
    service: ProjectCopilotService,
    *,
    check_completed_status: bool,
) -> list[str]:
    diagnostics: list[str] = []
    workflow_id = response.context.current_workflow_id
    row = workflow_row(service, workflow_id)
    if case.expected_workflow_status and check_completed_status:
        if row is None or row["status"] != case.expected_workflow_status:
            actual_status = row["status"] if row else None
            diagnostics.append(f"expected workflow status {case.expected_workflow_status}, got {actual_status}")
    if case.expected_workflow_type:
        if row is None or row["workflow_type"] != case.expected_workflow_type:
            actual_type = row["workflow_type"] if row else None
            diagnostics.append(f"expected workflow type {case.expected_workflow_type}, got {actual_type}")
    if case.expected_workflow_source_note:
        current_note_id = response.context.current_note_id
        source_note_id = row["source_note_id"] if row else None
        if source_note_id != current_note_id:
            diagnostics.append(f"expected workflow source note {current_note_id}, got {source_note_id}")
    if case.expected_workflow_step_names and check_completed_status:
        step_names = workflow_step_names(service, workflow_id)
        if step_names != case.expected_workflow_step_names:
            diagnostics.append(f"expected workflow steps {case.expected_workflow_step_names}, got {step_names}")
    return diagnostics


def _expects_confirmation(case: EvaluationCase) -> bool:
    if case.expected_requires_confirmation is not None:
        return case.expected_requires_confirmation
    return case.confirm_action


def _json_payload(value: object) -> object:
    if value is None:
        return None
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return str(value)
