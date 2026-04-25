from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any


REDACTED = "[redacted]"
SENSITIVE_KEY_PARTS = ("api_key", "authorization", "bearer", "password", "secret", "token")


class JsonLogFormatter(logging.Formatter):
    """Formats Python log records as structured JSON for local logs and Loki."""

    def __init__(self, *, project: str) -> None:
        super().__init__()
        self.project = project

    def format(self, record: logging.LogRecord) -> str:
        payload = _record_payload(record, project=_project_from_logger(record.name) or self.project)
        return json.dumps(payload, default=str, separators=(",", ":"))


class LokiLogHandler(logging.Handler):
    """Best-effort Loki push handler.

    This handler is intentionally non-critical: if Loki is unavailable, app
    requests should continue and logs should still be written locally.
    """

    def __init__(
        self,
        *,
        base_url: str,
        project: str,
        timeout_seconds: float = 0.2,
        background: bool = True,
        max_queue_size: int = 1000,
        opener: Any | None = None,
    ) -> None:
        super().__init__()
        self.base_url = base_url.rstrip("/")
        self.project = project
        self.timeout_seconds = timeout_seconds
        self.background = background
        self.opener = opener or urllib.request.urlopen
        self._queue: queue.Queue[urllib.request.Request | None] | None = None
        if self.background:
            self._queue = queue.Queue(maxsize=max_queue_size)
            self._worker = threading.Thread(target=self._worker_loop, daemon=True)
            self._worker.start()

    def emit(self, record: logging.LogRecord) -> None:
        if not getattr(record, "agentic_event", None):
            return
        try:
            project = _project_from_logger(record.name) or self.project
            payload = _record_payload(record, project=project)
            body = {
                "streams": [
                    {
                        "stream": {
                            "project": project,
                            "level": record.levelname.lower(),
                            "logger": _safe_label(record.name),
                        },
                        "values": [
                            [
                                str(time.time_ns()),
                                json.dumps(payload, default=str, separators=(",", ":")),
                            ]
                        ],
                    }
                ]
            }
            request = urllib.request.Request(
                f"{self.base_url}/loki/api/v1/push",
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            if self.background:
                if self._queue is not None:
                    self._queue.put_nowait(request)
            else:
                self._post(request)
        except (OSError, queue.Full, urllib.error.URLError, ValueError):
            return

    def close(self) -> None:
        try:
            if self._queue is not None:
                self._queue.put_nowait(None)
        except queue.Full:
            pass
        super().close()

    def _post(self, request: urllib.request.Request) -> None:
        try:
            response = self.opener(request, timeout=self.timeout_seconds)
            if hasattr(response, "__enter__"):
                with response as opened:
                    if hasattr(opened, "read"):
                        opened.read()
                return
            try:
                if hasattr(response, "read"):
                    response.read()
            finally:
                if hasattr(response, "close"):
                    response.close()
        except (OSError, urllib.error.URLError, ValueError):
            return

    def _worker_loop(self) -> None:
        if self._queue is None:
            return
        while True:
            request = self._queue.get()
            try:
                if request is None:
                    return
                self._post(request)
            finally:
                self._queue.task_done()


def configure_observability_logging(
    *,
    project: str,
    level: int = logging.INFO,
    loki_url: str | None = None,
) -> None:
    """Configure JSON console logging and optional Loki forwarding.

    Loki forwarding is enabled by setting `AGENTIC_LAB_LOKI_URL` or `LOKI_URL`,
    or by passing `loki_url` explicitly. The handler never raises back into the
    application request path.
    """

    formatter = JsonLogFormatter(project=project)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [console_handler]
    root.setLevel(level)

    selected_loki_url = loki_url or os.getenv("AGENTIC_LAB_LOKI_URL") or os.getenv("LOKI_URL")
    if selected_loki_url:
        root.addHandler(LokiLogHandler(base_url=selected_loki_url, project=project))


def log_agent_event(
    logger: logging.Logger,
    *,
    event: str,
    message: str | None = None,
    agent: str | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
    tool_name: str | None = None,
    route: str | None = None,
    status: str | None = None,
    attributes: Mapping[str, Any] | None = None,
    level: int = logging.INFO,
) -> None:
    """Emit a structured agentic-system event.

    Keep payloads compact and avoid putting raw prompts, API keys, or full user
    documents into observability events.
    """

    payload = {
        "event": event,
        "agent": agent,
        "session_id": session_id,
        "user_id": user_id,
        "tool_name": tool_name,
        "route": route,
        "status": status,
        "attributes": _redact(attributes or {}),
    }
    logger.log(level, message or event, extra={"agentic_event": _drop_none(payload)})


def _record_payload(record: logging.LogRecord, *, project: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "timestamp_ns": time.time_ns(),
        "project": project,
        "level": record.levelname,
        "logger": record.name,
        "message": record.getMessage(),
    }
    agentic_event = getattr(record, "agentic_event", None)
    if agentic_event:
        redacted_event = _redact(agentic_event)
        payload["agentic_event"] = redacted_event
        if isinstance(redacted_event, Mapping):
            for key in ("event", "agent", "session_id", "user_id", "tool_name", "route", "status"):
                if redacted_event.get(key) is not None:
                    payload[key] = redacted_event[key]
    if record.exc_info:
        payload["exception"] = _format_exception(record)
    return payload


def _format_exception(record: logging.LogRecord) -> str:
    formatter = logging.Formatter()
    return formatter.formatException(record.exc_info)


def _redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        redacted = {}
        for key, item in value.items():
            key_str = str(key)
            if any(part in key_str.lower() for part in SENSITIVE_KEY_PARTS):
                redacted[key_str] = REDACTED
            else:
                redacted[key_str] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact(item) for item in value)
    return value


def _drop_none(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item is not None}


def _safe_label(value: str) -> str:
    return "".join(char if char.isalnum() or char in "_.-" else "_" for char in value)[:120]


def _project_from_logger(logger_name: str) -> str | None:
    package_projects = {
        "project1_multi_agent_return_bot": "project1",
        "project2_agent_orchestrator": "project2",
        "project3_adaptive_eval_system": "project3",
        "project4_agentic_project_copilot": "project4",
    }
    for package, project in package_projects.items():
        if logger_name == package or logger_name.startswith(f"{package}."):
            return project
    return None
