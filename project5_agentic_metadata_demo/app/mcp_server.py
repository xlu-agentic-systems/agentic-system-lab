from __future__ import annotations

from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from project5_agentic_metadata_demo.app.database import init_db
from project5_agentic_metadata_demo.app.main import app
from project5_agentic_metadata_demo.app.seed import seed_database
from project5_agentic_metadata_demo.app.tools import MetadataTools


mcp = FastMCP(
    "project5-metadata",
    instructions=(
        "Expose Project 5 metadata capabilities as MCP tools. "
        "Every tool call must include caller identity fields so the metadata "
        "service can enforce deterministic policy."
    ),
)


def _tools(caller_user: str, caller_team: str, caller_role: str, confirm_dangerous_action: bool = False) -> MetadataTools:
    init_db()
    seed_database()
    headers = {
        "X-User": caller_user,
        "X-Team": caller_team,
        "X-Role": caller_role,
    }
    if confirm_dangerous_action:
        headers["X-Confirm-Dangerous-Action"] = "true"
    return MetadataTools(app, headers=headers)


@mcp.tool()
async def list_datasets(caller_user: str, caller_team: str, caller_role: Literal["viewer", "editor", "admin", "service"]) -> Any:
    """List metadata datasets visible to the caller."""

    return await _tools(caller_user, caller_team, caller_role).list_datasets()


@mcp.tool()
async def search_datasets(
    caller_user: str,
    caller_team: str,
    caller_role: Literal["viewer", "editor", "admin", "service"],
    owner_team: str | None = None,
    sensitivity_level: str | None = None,
    keyword: str | None = None,
) -> Any:
    """Search metadata datasets visible to the caller."""

    return await _tools(caller_user, caller_team, caller_role).search_datasets(
        owner_team=owner_team,
        sensitivity_level=sensitivity_level,
        keyword=keyword,
    )


@mcp.tool()
async def get_dataset(
    caller_user: str,
    caller_team: str,
    caller_role: Literal["viewer", "editor", "admin", "service"],
    dataset_id: int,
) -> Any:
    """Get one metadata dataset by ID if policy allows the caller to read it."""

    return await _tools(caller_user, caller_team, caller_role).get_dataset(dataset_id=dataset_id)


@mcp.tool()
async def get_schema(
    caller_user: str,
    caller_team: str,
    caller_role: Literal["viewer", "editor", "admin", "service"],
    dataset_id: int,
) -> Any:
    """Get schema columns for a dataset if policy allows the caller to read it."""

    return await _tools(caller_user, caller_team, caller_role).get_schema(dataset_id=dataset_id)


@mcp.tool()
async def get_lineage(
    caller_user: str,
    caller_team: str,
    caller_role: Literal["viewer", "editor", "admin", "service"],
    dataset_id: int,
) -> Any:
    """Get lineage for a dataset if policy allows the caller to read it."""

    return await _tools(caller_user, caller_team, caller_role).get_lineage(dataset_id=dataset_id)


@mcp.tool()
async def create_dataset(
    caller_user: str,
    caller_team: str,
    caller_role: Literal["viewer", "editor", "admin", "service"],
    name: str,
    description: str,
    owner_team: str,
    data_source: str,
    sensitivity_level: str,
) -> Any:
    """Create a metadata dataset if policy allows the caller to write it."""

    return await _tools(caller_user, caller_team, caller_role).create_dataset(
        name=name,
        description=description,
        owner_team=owner_team,
        data_source=data_source,
        sensitivity_level=sensitivity_level,
    )


@mcp.tool()
async def update_dataset(
    caller_user: str,
    caller_team: str,
    caller_role: Literal["viewer", "editor", "admin", "service"],
    dataset_id: int,
    name: str | None = None,
    description: str | None = None,
    owner_team: str | None = None,
    data_source: str | None = None,
    sensitivity_level: str | None = None,
) -> Any:
    """Update metadata dataset fields if policy allows the caller to write it."""

    updates = {
        "name": name,
        "description": description,
        "owner_team": owner_team,
        "data_source": data_source,
        "sensitivity_level": sensitivity_level,
    }
    return await _tools(caller_user, caller_team, caller_role).update_dataset(
        dataset_id=dataset_id,
        **{key: value for key, value in updates.items() if value is not None},
    )


@mcp.tool()
async def delete_dataset(
    caller_user: str,
    caller_team: str,
    caller_role: Literal["viewer", "editor", "admin", "service"],
    dataset_id: int,
    confirm_dangerous_action: bool = False,
) -> Any:
    """Delete a metadata dataset only if policy allows and explicit confirmation is provided."""

    return await _tools(
        caller_user,
        caller_team,
        caller_role,
        confirm_dangerous_action=confirm_dangerous_action,
    ).delete_dataset(dataset_id=dataset_id)


if __name__ == "__main__":
    mcp.run()

