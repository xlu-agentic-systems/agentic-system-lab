from __future__ import annotations

import json
import logging
import urllib.parse

import pytest

from agentic_system_lab.observability import (
    LokiLogHandler,
    LokiLogQueryTool,
    LokiToolError,
    observability_tool_schema,
)


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


def test_loki_query_tool_builds_read_only_safe_query() -> None:
    requests = []

    def opener(request, timeout):
        requests.append((request, timeout))
        return FakeResponse({"status": "success", "data": {"resultType": "streams", "result": []}})

    tool = LokiLogQueryTool(
        base_url="http://loki:3100",
        allowed_projects={"project1"},
        opener=opener,
    )

    result = tool.query_agent_events(
        project="project1",
        session_id="session-123",
        agent="routing_agent",
        event="agent_decision",
        since_minutes=5,
        limit=25,
    )

    request, timeout = requests[0]
    parsed = urllib.parse.urlparse(request.full_url)
    params = urllib.parse.parse_qs(parsed.query)

    assert request.get_method() == "GET"
    assert parsed.path == "/loki/api/v1/query_range"
    assert params["query"] == [
        '{project="project1"} | json | session_id="session-123" | agent="routing_agent" | event="agent_decision"'
    ]
    assert params["since"] == ["5m"]
    assert params["limit"] == ["25"]
    assert timeout == 5.0
    assert result["response"]["status"] == "success"


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"project": "unknown"}, "project is not allowlisted"),
        ({"project": "project1", "session_id": "session-123", "event": 'x" |= "secret'}, "unsafe event"),
        ({"project": "project1", "limit": 0}, "limit must be between"),
        ({"project": "project1", "since_minutes": 1441}, "since_minutes must be between"),
    ],
)
def test_loki_query_tool_rejects_unsafe_or_unbounded_requests(kwargs, expected) -> None:
    tool = LokiLogQueryTool(base_url="http://loki:3100", allowed_projects={"project1"})

    with pytest.raises(LokiToolError, match=expected):
        tool.query_agent_events(**kwargs)


def test_observability_tool_schema_does_not_expose_raw_logql() -> None:
    schema = observability_tool_schema()

    assert schema["name"] == "query_agent_logs"
    assert "raw_query" not in schema["parameters"]["properties"]
    assert schema["parameters"]["properties"]["project"]["enum"] == [
        "project1",
        "project2",
        "project3",
        "project4",
    ]
    assert schema["parameters"]["required"] == ["project", "session_id"]


def test_loki_log_handler_pushes_redacted_structured_event() -> None:
    requests = []

    def opener(request, timeout):
        requests.append((request, timeout))
        return FakeResponse({"status": "success"})

    logger = logging.getLogger("tests.observability")
    logger.handlers = [
        LokiLogHandler(base_url="http://loki:3100", project="project1", background=False, opener=opener)
    ]
    logger.setLevel(logging.INFO)
    logger.propagate = False

    logger.info(
        "planner finished",
        extra={
            "agentic_event": {
                "event": "agent_decision",
                "agent": "planner_agent",
                "session_id": "session-123",
                "api_key": "should-not-leak",
            }
        },
    )

    request, timeout = requests[0]
    payload = json.loads(request.data.decode("utf-8"))
    line = json.loads(payload["streams"][0]["values"][0][1])

    assert request.get_method() == "POST"
    assert request.full_url == "http://loki:3100/loki/api/v1/push"
    assert timeout == 0.2
    assert payload["streams"][0]["stream"]["project"] == "project1"
    assert line["message"] == "planner finished"
    assert line["session_id"] == "session-123"
    assert line["agentic_event"]["agent"] == "planner_agent"
    assert line["agentic_event"]["session_id"] == "session-123"
    assert line["agentic_event"]["api_key"] == "[redacted]"


def test_loki_handler_only_forwards_structured_agent_events() -> None:
    requests = []

    def opener(request, timeout):
        requests.append((request, timeout))
        return FakeResponse({"status": "success"})

    logger = logging.getLogger("tests.unstructured")
    logger.handlers = [
        LokiLogHandler(base_url="http://loki:3100", project="project1", background=False, opener=opener)
    ]
    logger.setLevel(logging.INFO)
    logger.propagate = False

    logger.info("ordinary library log with possible raw payload")

    assert requests == []


def test_loki_log_handler_infers_project_from_logger_name() -> None:
    requests = []

    def opener(request, timeout):
        requests.append((request, timeout))
        return FakeResponse({"status": "success"})

    logger = logging.getLogger("project2_agent_orchestrator.app.service")
    logger.handlers = [
        LokiLogHandler(base_url="http://loki:3100", project="project4", background=False, opener=opener)
    ]
    logger.setLevel(logging.INFO)
    logger.propagate = False

    logger.info("orchestrator decision", extra={"agentic_event": {"event": "agent_decision"}})

    payload = json.loads(requests[0][0].data.decode("utf-8"))
    line = json.loads(payload["streams"][0]["values"][0][1])

    assert payload["streams"][0]["stream"]["project"] == "project2"
    assert line["project"] == "project2"


def test_loki_query_tool_requires_session_scope_by_default() -> None:
    tool = LokiLogQueryTool(base_url="http://loki:3100", allowed_projects={"project1"})

    with pytest.raises(LokiToolError, match="session_id is required"):
        tool.query_agent_events(project="project1")


def test_loki_query_tool_allows_explicit_developer_broad_query() -> None:
    requests = []

    def opener(request, timeout):
        requests.append((request, timeout))
        return FakeResponse({"status": "success", "data": {"resultType": "streams", "result": []}})

    tool = LokiLogQueryTool(
        base_url="http://loki:3100",
        allowed_projects={"project1"},
        require_session_scope=False,
        opener=opener,
    )

    result = tool.query_agent_events(project="project1", event="tool_execution")

    assert result["query"] == '{project="project1"} | json | event="tool_execution"'
    assert requests
