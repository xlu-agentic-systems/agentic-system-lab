from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from agentic_system_lab.observability import LokiLogQueryTool, LokiToolError
from project1_multi_agent_return_bot.app.trace_store import (
    DEFAULT_TRACE_PATH as DEFAULT_PROJECT1_TRACE_PATH,
    JsonlTraceStore as Project1TraceStore,
    ReturnConversationTrace,
)
from project3_adaptive_eval_system.app.models import (
    AgentOutputRecord,
    BackendValidationRecord,
    ConversationTrace,
    Project1TraceImportResult,
    ToolCallRecord,
)


PROJECT1_NAME = "project1_multi_agent_return_bot"


class Project1FeedbackAdapter:
    """Converts Project 1 production traces into Project 3 evaluation traces."""

    def __init__(
        self,
        *,
        trace_path: Path | str = DEFAULT_PROJECT1_TRACE_PATH,
        log_query_tool: LokiLogQueryTool | None = None,
    ) -> None:
        self.trace_path = Path(trace_path)
        self.log_query_tool = log_query_tool

    def load_project1_traces(self, limit: int | None = None) -> list[ReturnConversationTrace]:
        traces = Project1TraceStore(self.trace_path).load_all()
        return traces[:limit] if limit is not None else traces

    def convert_traces(
        self,
        *,
        limit: int | None = None,
        include_loki_context: bool = False,
        loki_since_minutes: int = 60,
    ) -> tuple[list[ConversationTrace], Project1TraceImportResult]:
        project1_traces = self.load_project1_traces(limit)
        converted = [
            self.convert_trace(
                trace,
                include_loki_context=include_loki_context,
                loki_since_minutes=loki_since_minutes,
            )
            for trace in project1_traces
        ]
        return converted, Project1TraceImportResult(
            source_path=str(self.trace_path),
            imported_count=len(converted),
            trace_ids=[trace.trace_id for trace in converted],
            included_loki_context=include_loki_context,
        )

    def convert_trace(
        self,
        trace: ReturnConversationTrace,
        *,
        include_loki_context: bool = False,
        loki_since_minutes: int = 60,
    ) -> ConversationTrace:
        backend_validations = _backend_validations(trace)
        if include_loki_context:
            backend_validations.append(
                self._loki_context_record(trace.session_id, loki_since_minutes)
            )
        return ConversationTrace(
            trace_id=_trace_id(trace),
            project=PROJECT1_NAME,
            session_id=trace.session_id,
            user_id=trace.user_id,
            user_message=trace.user_message,
            agent_outputs=[
                AgentOutputRecord(agent_name="routing_agent", output=trace.routing.model_dump_json()),
                AgentOutputRecord(agent_name="planner_agent", output=trace.planner.model_dump_json()),
                AgentOutputRecord(agent_name="qa_agent", output=trace.response),
            ],
            tool_calls=[_tool_call_record(result) for result in trace.tool_results],
            backend_validations=backend_validations,
            final_response=trace.response,
            outcome=_infer_outcome(trace),
            expected_behavior=_expected_behavior(trace),
            policy_basis=_policy_basis(trace),
            created_at=trace.created_at,
        )

    def _loki_context_record(self, session_id: str, since_minutes: int) -> BackendValidationRecord:
        log_query_tool = self.log_query_tool or LokiLogQueryTool()
        try:
            result = log_query_tool.query_agent_events(
                project="project1",
                session_id=session_id,
                since_minutes=since_minutes,
                limit=100,
            )
        except LokiToolError as exc:
            return BackendValidationRecord(
                check_name="loki_context",
                passed=False,
                summary=f"Loki context unavailable: {exc}",
            )
        return BackendValidationRecord(
            check_name="loki_context",
            passed=True,
            summary=_summarize_loki_response(result),
        )


def _trace_id(trace: ReturnConversationTrace) -> str:
    payload = "|".join([trace.session_id, trace.user_id, trace.created_at.isoformat(), trace.user_message])
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    return f"project1-{trace.session_id}-{digest}"


def _tool_call_record(result) -> ToolCallRecord:
    safe = result.ok if result.proposal.safety == "unsafe_write" else True
    status = "executed" if result.executed else "blocked"
    return ToolCallRecord(
        tool_name=result.proposal.name,
        proposed=True,
        executed=result.executed,
        safe=safe,
        summary=(
            f"{result.proposal.name} {status}; backend_ok={result.ok}; "
            f"safety={result.proposal.safety}; error={result.error or 'none'}"
        ),
    )


def _backend_validations(trace: ReturnConversationTrace) -> list[BackendValidationRecord]:
    return [
        BackendValidationRecord(
            check_name=f"tool_validation:{result.proposal.name}",
            passed=result.ok,
            summary=(
                f"{result.proposal.name} {'executed' if result.executed else 'blocked'} "
                f"after backend validation; error={result.error or 'none'}"
            ),
        )
        for result in trace.tool_results
    ]


def _infer_outcome(trace: ReturnConversationTrace) -> str:
    if any(not result.ok for result in trace.tool_results):
        return "failure"
    if trace.planner.status in {"approved", "rejected", "needs_clarification", "escalated"}:
        return "success"
    return "unknown"


def _expected_behavior(trace: ReturnConversationTrace) -> str:
    if trace.planner.status == "approved":
        return (
            "Project 1 should approve a return only when backend facts show order ownership, "
            "policy eligibility, refundable item status, and exact refund amount validation."
        )
    if trace.planner.status == "rejected":
        return (
            "Project 1 should reject the request using policy and backend facts, and should not "
            "execute unsafe write tools for ineligible items."
        )
    if trace.planner.status == "needs_clarification":
        return (
            "Project 1 should ask a clarification question before planning tools when required "
            "return fields are missing."
        )
    if trace.planner.status == "escalated":
        return "Project 1 should escalate unsupported or ambiguous return requests safely."
    return "Project 1 should preserve backend validation and avoid unsupported side effects."


def _policy_basis(trace: ReturnConversationTrace) -> str | None:
    policy_payloads: list[str] = []
    for result in trace.tool_results:
        if result.proposal.name in {"get_return_policy", "check_refund_eligibility"} and result.result is not None:
            policy_payloads.append(json.dumps(result.result, default=str))
    if policy_payloads:
        return " ".join(policy_payloads)
    return trace.planner.explanation or None


def _summarize_loki_response(result: dict[str, Any]) -> str:
    counts: dict[str, int] = {}
    streams = result.get("response", {}).get("data", {}).get("result", [])
    for stream in streams:
        event = stream.get("stream", {}).get("event") or "unknown"
        counts[event] = counts.get(event, 0) + len(stream.get("values", []))
    if not counts:
        return "Loki returned no structured events for this Project 1 session."
    parts = [f"{event}={count}" for event, count in sorted(counts.items())]
    return f"Loki structured event counts: {', '.join(parts)}."
