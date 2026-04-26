from __future__ import annotations

import re
from typing import Any

from project5_agentic_metadata_demo.app.schemas import AgentQueryResponse, ToolCallRecord
from project5_agentic_metadata_demo.app.tools import MetadataTools


OWNER_ALIASES = {
    "finance": "finance",
    "analytics": "analytics",
    "growth": "growth",
    "commerce": "commerce",
    "support": "customer_support",
    "customer support": "customer_support",
    "operations": "operations",
    "ops": "operations",
}

SENSITIVITY_ALIASES = {
    "sensitive": "high",
    "high": "high",
    "restricted": "restricted",
    "confidential": "confidential",
    "internal": "internal",
    "public": "public",
}


async def run_rule_based_agent(question: str, tools: MetadataTools) -> AgentQueryResponse:
    normalized = question.lower().strip()
    tool_calls: list[ToolCallRecord] = []
    raw_results: dict[str, Any] = {}

    if _asks_for_all_datasets(normalized):
        datasets = await _record_tool(tool_calls, raw_results, tools, "list_datasets", {})
        return AgentQueryResponse(
            answer=f"I found {len(datasets)} datasets: {', '.join(dataset['name'] for dataset in datasets)}.",
            tool_calls=tool_calls,
            raw_results=raw_results,
        )

    owner_team = _extract_owner(normalized)
    sensitivity_level = _extract_sensitivity(normalized)
    keyword = _extract_keyword(normalized)

    if "schema" in normalized:
        datasets = await _find_candidate_datasets(tool_calls, raw_results, tools, owner_team, sensitivity_level, keyword)
        for dataset in datasets:
            await _record_tool(tool_calls, raw_results, tools, "get_schema", {"dataset_id": dataset["id"]})
        return AgentQueryResponse(
            answer=_schema_answer(datasets, raw_results),
            tool_calls=tool_calls,
            raw_results=raw_results,
        )

    if "lineage" in normalized:
        datasets = await _find_candidate_datasets(tool_calls, raw_results, tools, owner_team, sensitivity_level, keyword)
        for dataset in datasets[:3]:
            await _record_tool(tool_calls, raw_results, tools, "get_lineage", {"dataset_id": dataset["id"]})
        return AgentQueryResponse(
            answer=_lineage_answer(datasets[:3], raw_results),
            tool_calls=tool_calls,
            raw_results=raw_results,
        )

    datasets = await _record_tool(
        tool_calls,
        raw_results,
        tools,
        "search_datasets",
        {"owner_team": owner_team, "sensitivity_level": sensitivity_level, "keyword": keyword},
    )
    filters = _describe_filters(owner_team, sensitivity_level, keyword)
    return AgentQueryResponse(
        answer=f"I found {len(datasets)} dataset(s){filters}: {', '.join(dataset['name'] for dataset in datasets) or 'none'}.",
        tool_calls=tool_calls,
        raw_results=raw_results,
    )


async def _find_candidate_datasets(
    tool_calls: list[ToolCallRecord],
    raw_results: dict[str, Any],
    tools: MetadataTools,
    owner_team: str | None,
    sensitivity_level: str | None,
    keyword: str | None,
) -> list[dict[str, Any]]:
    return await _record_tool(
        tool_calls,
        raw_results,
        tools,
        "search_datasets",
        {"owner_team": owner_team, "sensitivity_level": sensitivity_level, "keyword": keyword},
    )


async def _record_tool(
    tool_calls: list[ToolCallRecord],
    raw_results: dict[str, Any],
    tools: MetadataTools,
    tool_name: str,
    arguments: dict[str, Any],
) -> Any:
    clean_arguments = {key: value for key, value in arguments.items() if value is not None}
    tool_calls.append(ToolCallRecord(tool=tool_name, arguments=clean_arguments))
    result = await tools.call(tool_name, clean_arguments)
    raw_results[f"{len(tool_calls)}_{tool_name}"] = result
    return result


def _asks_for_all_datasets(question: str) -> bool:
    return any(phrase in question for phrase in ("show all datasets", "list all datasets", "all datasets"))


def _extract_owner(question: str) -> str | None:
    for phrase, owner in sorted(OWNER_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
        if re.search(rf"\b{re.escape(phrase)}\b", question):
            return owner
    return None


def _extract_sensitivity(question: str) -> str | None:
    for phrase, sensitivity in SENSITIVITY_ALIASES.items():
        if re.search(rf"\b{re.escape(phrase)}\b", question):
            return sensitivity
    return None


def _extract_keyword(question: str) -> str | None:
    if "user profile" in question:
        return "customer profile"
    known_keywords = [
        "revenue",
        "forecast",
        "customer profile",
        "customer",
        "profile",
        "product",
        "support",
        "ticket",
        "web",
        "event",
        "expense",
        "inventory",
    ]
    for keyword in known_keywords:
        if keyword in question:
            return keyword
    match = re.search(r"for ([a-z0-9_ -]+?) dataset", question)
    if match:
        return match.group(1).strip()
    return None


def _describe_filters(owner_team: str | None, sensitivity_level: str | None, keyword: str | None) -> str:
    parts = []
    if owner_team:
        parts.append(f"owned by {owner_team}")
    if sensitivity_level:
        parts.append(f"with {sensitivity_level} sensitivity")
    if keyword:
        parts.append(f"matching '{keyword}'")
    return f" {' and '.join(parts)}" if parts else ""


def _schema_answer(datasets: list[dict[str, Any]], raw_results: dict[str, Any]) -> str:
    if not datasets:
        return "I could not find a matching dataset to inspect its schema."
    names = [dataset["name"] for dataset in datasets]
    column_count = sum(
        len(value)
        for key, value in raw_results.items()
        if key.endswith("_get_schema") and isinstance(value, list)
    )
    return f"I found {len(datasets)} matching dataset(s), {', '.join(names)}, and retrieved {column_count} schema columns."


def _lineage_answer(datasets: list[dict[str, Any]], raw_results: dict[str, Any]) -> str:
    if not datasets:
        return "I could not find a matching dataset to inspect its lineage."
    summaries = []
    for key, value in raw_results.items():
        if not key.endswith("_get_lineage"):
            continue
        upstream = len(value.get("upstream", []))
        downstream = len(value.get("downstream", []))
        summaries.append(f"{value['dataset']['name']} has {upstream} upstream and {downstream} downstream relationship(s)")
    return "; ".join(summaries) + "."
