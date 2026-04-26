from __future__ import annotations

from typing import Any

from project5_agentic_metadata_demo.app.schemas import AgentTaskError, AgentTaskRequest, AgentTaskResponse, ToolCallRecord
from project5_agentic_metadata_demo.app.tools import MetadataToolError, MetadataTools


async def run_structured_task(request: AgentTaskRequest, tools: MetadataTools) -> AgentTaskResponse:
    tool_calls: list[ToolCallRecord] = []
    raw_results: dict[str, Any] = {}
    errors: list[AgentTaskError] = []

    if request.confirm_dangerous_action:
        tools.headers["X-Confirm-Dangerous-Action"] = "true"

    if request.task == "find_datasets":
        filters = request.filters.model_dump(exclude_none=True) if request.filters else {}
        datasets = await _record_tool(tool_calls, raw_results, errors, tools, "search_datasets", filters)
        if errors:
            return _blocked_or_failed(tool_calls, raw_results, errors)
        return await _with_requested_details(request, tools, tool_calls, raw_results, errors, datasets)

    if request.task == "get_dataset":
        if request.dataset_id is None:
            return _validation_failure(tool_calls, raw_results, "get_dataset", "dataset_id is required")
        dataset = await _record_tool(
            tool_calls,
            raw_results,
            errors,
            tools,
            "get_dataset",
            {"dataset_id": request.dataset_id},
        )
        if errors:
            return _blocked_or_failed(tool_calls, raw_results, errors)
        return await _with_requested_details(request, tools, tool_calls, raw_results, errors, [dataset])

    if request.task == "create_dataset":
        if request.dataset is None:
            return _validation_failure(tool_calls, raw_results, "create_dataset", "dataset is required")
        dataset = await _record_tool(
            tool_calls,
            raw_results,
            errors,
            tools,
            "create_dataset",
            request.dataset.model_dump(),
        )
        if errors:
            return _blocked_or_failed(tool_calls, raw_results, errors)
        return AgentTaskResponse(
            status="completed",
            answer=f"Created dataset {dataset['name']}.",
            datasets=[dataset],
            tool_calls=tool_calls,
            raw_results=raw_results,
            errors=errors,
        )

    if request.task == "update_dataset":
        if request.dataset_id is None or request.updates is None:
            return _validation_failure(tool_calls, raw_results, "update_dataset", "dataset_id and updates are required")
        dataset = await _record_tool(
            tool_calls,
            raw_results,
            errors,
            tools,
            "update_dataset",
            {"dataset_id": request.dataset_id, **request.updates.model_dump(exclude_none=True)},
        )
        if errors:
            return _blocked_or_failed(tool_calls, raw_results, errors)
        return AgentTaskResponse(
            status="completed",
            answer=f"Updated dataset {dataset['name']}.",
            datasets=[dataset],
            tool_calls=tool_calls,
            raw_results=raw_results,
            errors=errors,
        )

    if request.task == "delete_dataset":
        if request.dataset_id is None:
            return _validation_failure(tool_calls, raw_results, "delete_dataset", "dataset_id is required")
        result = await _record_tool(
            tool_calls,
            raw_results,
            errors,
            tools,
            "delete_dataset",
            {"dataset_id": request.dataset_id},
        )
        if errors:
            return _blocked_or_failed(tool_calls, raw_results, errors)
        return AgentTaskResponse(
            status="completed",
            answer=f"Deleted dataset {result['dataset_id']}.",
            tool_calls=tool_calls,
            raw_results=raw_results,
            errors=errors,
        )

    return _validation_failure(tool_calls, raw_results, request.task, "unsupported task")


async def _with_requested_details(
    request: AgentTaskRequest,
    tools: MetadataTools,
    tool_calls: list[ToolCallRecord],
    raw_results: dict[str, Any],
    errors: list[AgentTaskError],
    datasets: list[dict[str, Any]],
) -> AgentTaskResponse:
    schemas_by_dataset_id: dict[str, Any] = {}
    lineage_by_dataset_id: dict[str, Any] = {}

    if "schema" in request.include:
        for dataset in datasets:
            schema = await _record_tool(tool_calls, raw_results, errors, tools, "get_schema", {"dataset_id": dataset["id"]})
            if isinstance(schema, list):
                schemas_by_dataset_id[str(dataset["id"])] = schema

    if "lineage" in request.include:
        for dataset in datasets:
            lineage = await _record_tool(
                tool_calls,
                raw_results,
                errors,
                tools,
                "get_lineage",
                {"dataset_id": dataset["id"]},
            )
            if isinstance(lineage, dict):
                lineage_by_dataset_id[str(dataset["id"])] = lineage

    if errors:
        return AgentTaskResponse(
            status="blocked" if any(error.status_code == 403 for error in errors) else "failed",
            answer="The task partially completed, but one or more requested detail calls failed.",
            datasets=datasets,
            schemas_by_dataset_id=schemas_by_dataset_id,
            lineage_by_dataset_id=lineage_by_dataset_id,
            tool_calls=tool_calls,
            raw_results=raw_results,
            errors=errors,
        )

    detail_parts = []
    if schemas_by_dataset_id:
        detail_parts.append("schemas")
    if lineage_by_dataset_id:
        detail_parts.append("lineage")
    suffix = f" with {' and '.join(detail_parts)}" if detail_parts else ""
    return AgentTaskResponse(
        status="completed",
        answer=f"Found {len(datasets)} dataset(s){suffix}.",
        datasets=datasets,
        schemas_by_dataset_id=schemas_by_dataset_id,
        lineage_by_dataset_id=lineage_by_dataset_id,
        tool_calls=tool_calls,
        raw_results=raw_results,
        errors=errors,
    )


async def _record_tool(
    tool_calls: list[ToolCallRecord],
    raw_results: dict[str, Any],
    errors: list[AgentTaskError],
    tools: MetadataTools,
    tool_name: str,
    arguments: dict[str, Any],
) -> Any:
    clean_arguments = {key: value for key, value in arguments.items() if value is not None}
    tool_calls.append(ToolCallRecord(tool=tool_name, arguments=clean_arguments))
    result_key = f"{len(tool_calls)}_{tool_name}"
    try:
        result = await tools.call(tool_name, clean_arguments)
    except MetadataToolError as exc:
        error = AgentTaskError(tool=tool_name, status_code=exc.status_code, message=exc.message)
        errors.append(error)
        result = {"error": exc.message, "status_code": exc.status_code}
    raw_results[result_key] = result
    return result


def _blocked_or_failed(
    tool_calls: list[ToolCallRecord],
    raw_results: dict[str, Any],
    errors: list[AgentTaskError],
) -> AgentTaskResponse:
    status = "blocked" if any(error.status_code == 403 for error in errors) else "failed"
    return AgentTaskResponse(
        status=status,
        answer=errors[0].message,
        tool_calls=tool_calls,
        raw_results=raw_results,
        errors=errors,
    )


def _validation_failure(
    tool_calls: list[ToolCallRecord],
    raw_results: dict[str, Any],
    tool: str,
    message: str,
) -> AgentTaskResponse:
    return AgentTaskResponse(
        status="failed",
        answer=message,
        tool_calls=tool_calls,
        raw_results=raw_results,
        errors=[AgentTaskError(tool=tool, status_code=400, message=message)],
    )

