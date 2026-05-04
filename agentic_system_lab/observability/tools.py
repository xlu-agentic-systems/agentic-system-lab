from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


SAFE_VALUE = re.compile(r"^[A-Za-z0-9_.:/@-]{1,160}$")
DEFAULT_ALLOWED_PROJECTS = frozenset({"project1", "project2", "project3", "project4", "project6"})


class LokiToolError(ValueError):
    """Raised when a Loki query request is unsafe or cannot be executed."""


class LokiLogQueryTool:
    """Read-only agent tool for querying Loki logs by safe filters.

    The tool does not accept raw LogQL from the model. It builds a LogQL query
    from allowlisted labels and literal line filters so the agent can inspect
    observability data without receiving broad curl/Grafana privileges.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        allowed_projects: set[str] | frozenset[str] = DEFAULT_ALLOWED_PROJECTS,
        timeout_seconds: float = 5.0,
        require_session_scope: bool = True,
        opener: Any | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("AGENTIC_LAB_LOKI_URL") or os.getenv("LOKI_URL") or "http://localhost:3100").rstrip("/")
        self.allowed_projects = frozenset(allowed_projects)
        self.timeout_seconds = timeout_seconds
        self.require_session_scope = require_session_scope
        self.opener = opener or urllib.request.urlopen

    def query_agent_events(
        self,
        *,
        project: str,
        since_minutes: int = 15,
        limit: int = 50,
        session_id: str | None = None,
        agent: str | None = None,
        event: str | None = None,
        tool_name: str | None = None,
    ) -> dict[str, Any]:
        _validate_project(project, self.allowed_projects)
        _validate_range("since_minutes", since_minutes, minimum=1, maximum=1440)
        _validate_range("limit", limit, minimum=1, maximum=500)
        filters = {
            "session_id": session_id,
            "agent": agent,
            "event": event,
            "tool_name": tool_name,
        }
        if self.require_session_scope and not session_id:
            raise LokiToolError("session_id is required for agent log queries")
        for key, value in filters.items():
            if value is not None and not SAFE_VALUE.fullmatch(value):
                raise LokiToolError(f"unsafe {key}: {value!r}")

        query = _build_query(project=project, filters=filters)
        params = urllib.parse.urlencode(
            {
                "query": query,
                "since": f"{since_minutes}m",
                "limit": str(limit),
                "direction": "backward",
            }
        )
        request = urllib.request.Request(
            f"{self.base_url}/loki/api/v1/query_range?{params}",
            headers={"Accept": "application/json"},
            method="GET",
        )
        try:
            with self.opener(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise LokiToolError(f"failed to query Loki: {exc}") from exc
        return {
            "query": query,
            "since_minutes": since_minutes,
            "limit": limit,
            "response": json.loads(body),
        }


def observability_tool_schema() -> dict[str, Any]:
    """Function-tool schema for exposing safe Loki lookups to an LLM agent."""

    return {
        "name": "query_agent_logs",
        "description": (
            "Read recent structured logs for one agentic-system project from Loki. "
            "Use this to debug routing, tool calls, handoffs, validation blocks, and final responses."
        ),
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "project": {
                    "type": "string",
                    "enum": sorted(DEFAULT_ALLOWED_PROJECTS),
                    "description": "Project label to query.",
                },
                "since_minutes": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1440,
                    "default": 15,
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                    "default": 50,
                },
                "session_id": {"type": "string"},
                "agent": {"type": "string"},
                "event": {"type": "string"},
                "tool_name": {"type": "string"},
            },
            "required": ["project", "session_id"],
        },
    }


def _build_query(*, project: str, filters: dict[str, str | None]) -> str:
    query = f'{{project="{project}"}} | json'
    for key, value in filters.items():
        if value:
            query += f' | {key}="{_escape_logql_string(value)}"'
    return query


def _escape_logql_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _validate_project(project: str, allowed_projects: frozenset[str]) -> None:
    if project not in allowed_projects:
        raise LokiToolError(f"project is not allowlisted: {project!r}")


def _validate_range(name: str, value: int, *, minimum: int, maximum: int) -> None:
    if not isinstance(value, int) or not minimum <= value <= maximum:
        raise LokiToolError(f"{name} must be between {minimum} and {maximum}")
