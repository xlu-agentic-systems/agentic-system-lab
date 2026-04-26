from __future__ import annotations

import json
import os
from typing import Any

from openai import AsyncOpenAI

from project5_agentic_metadata_demo.app.schemas import AgentQueryResponse, ToolCallRecord
from project5_agentic_metadata_demo.app.tools import MetadataToolError, MetadataTools


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "list_datasets",
            "description": "List all datasets from the metadata microservice.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_datasets",
            "description": "Search datasets by owner team, sensitivity level, or keyword.",
            "parameters": {
                "type": "object",
                "properties": {
                    "owner_team": {"type": "string"},
                    "sensitivity_level": {"type": "string"},
                    "keyword": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_dataset",
            "description": "Get one dataset by numeric ID.",
            "parameters": {
                "type": "object",
                "properties": {"dataset_id": {"type": "integer"}},
                "required": ["dataset_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_schema",
            "description": "Get the schema columns for one dataset by numeric ID.",
            "parameters": {
                "type": "object",
                "properties": {"dataset_id": {"type": "integer"}},
                "required": ["dataset_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_lineage",
            "description": "Get upstream and downstream lineage for one dataset by numeric ID.",
            "parameters": {
                "type": "object",
                "properties": {"dataset_id": {"type": "integer"}},
                "required": ["dataset_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_dataset",
            "description": "Create a dataset metadata record. The metadata service policy may deny this operation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "owner_team": {"type": "string"},
                    "data_source": {"type": "string"},
                    "sensitivity_level": {"type": "string"},
                },
                "required": ["name", "description", "owner_team", "data_source", "sensitivity_level"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_dataset",
            "description": "Update dataset metadata by ID. The metadata service policy may deny this operation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "integer"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "owner_team": {"type": "string"},
                    "data_source": {"type": "string"},
                    "sensitivity_level": {"type": "string"},
                },
                "required": ["dataset_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_dataset",
            "description": "Delete a dataset metadata record by ID. This is dangerous and the metadata service policy may deny it.",
            "parameters": {
                "type": "object",
                "properties": {"dataset_id": {"type": "integer"}},
                "required": ["dataset_id"],
                "additionalProperties": False,
            },
        },
    },
]


SYSTEM_PROMPT = """You answer metadata questions by calling the provided metadata tools.
The tools call the metadata microservice; do not invent data. If the user asks
for schemas or lineage and you need a dataset ID, search first, then call the
schema or lineage tool for the relevant dataset IDs. Dangerous writes may be
blocked by backend policy; report policy denials plainly. Keep the final answer
brief and cite dataset names from tool results."""


async def run_openai_agent(question: str, tools: MetadataTools) -> AgentQueryResponse:
    client = AsyncOpenAI()
    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    tool_calls: list[ToolCallRecord] = []
    raw_results: dict[str, Any] = {}

    for _ in range(6):
        completion = await client.chat.completions.create(
            model=model,
            messages=messages,
            tools=TOOL_DEFINITIONS,
            tool_choice="auto",
        )
        message = completion.choices[0].message
        messages.append(message.model_dump(exclude_none=True))
        if not message.tool_calls:
            return AgentQueryResponse(
                answer=message.content or "I could not produce an answer from the available metadata.",
                tool_calls=tool_calls,
                raw_results=raw_results,
            )

        for call in message.tool_calls:
            arguments = json.loads(call.function.arguments or "{}")
            clean_arguments = {key: value for key, value in arguments.items() if value is not None}
            try:
                result = await tools.call(call.function.name, clean_arguments)
            except MetadataToolError as exc:
                result = {"error": exc.message, "status_code": exc.status_code}
            tool_calls.append(ToolCallRecord(tool=call.function.name, arguments=clean_arguments))
            raw_results[f"{len(tool_calls)}_{call.function.name}"] = result
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result),
                }
            )

    return AgentQueryResponse(
        answer="I reached the tool-call limit before producing a final answer.",
        tool_calls=tool_calls,
        raw_results=raw_results,
    )
