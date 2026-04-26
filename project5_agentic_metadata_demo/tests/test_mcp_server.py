from __future__ import annotations

import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import TextContent

from project5_agentic_metadata_demo.app.mcp_server import mcp


@pytest.mark.anyio
async def test_mcp_server_exposes_metadata_tools() -> None:
    tools = await mcp.list_tools()

    assert [tool.name for tool in tools] == [
        "list_datasets",
        "search_datasets",
        "get_dataset",
        "get_schema",
        "get_lineage",
        "create_dataset",
        "update_dataset",
        "delete_dataset",
    ]


@pytest.mark.anyio
async def test_mcp_search_datasets_calls_metadata_service() -> None:
    result = await mcp.call_tool(
        "search_datasets",
        {
            "caller_user": "service-a",
            "caller_team": "platform",
            "caller_role": "service",
            "owner_team": "finance",
            "keyword": "revenue",
        },
    )

    datasets = [_text_json(item) for item in result]
    assert [dataset["name"] for dataset in datasets] == ["revenue_transactions", "revenue_forecast"]


@pytest.mark.anyio
async def test_mcp_tools_still_enforce_metadata_policy() -> None:
    with pytest.raises(ToolError, match="not allowed to read"):
        await mcp.call_tool(
            "get_schema",
            {
                "caller_user": "ana",
                "caller_team": "analytics",
                "caller_role": "viewer",
                "dataset_id": 1,
            },
        )


def _text_json(item: TextContent) -> dict:
    assert item.type == "text"
    return json.loads(item.text)

