from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from project4_agentic_project_copilot.app.database import CopilotDatabase
from project4_agentic_project_copilot.app.models import ChatResponse, DecisionLog, TraceDetail, TraceSpan, TraceSummary


DEFAULT_TRACE_PATH = Path(__file__).resolve().parent.parent / "data" / "conversation_traces.jsonl"


class TraceNotFoundError(ValueError):
    pass


class CopilotTrace(BaseModel):
    session_id: str
    user_message: str
    response: str
    decision_log: DecisionLog
    citations_count: int = 0
    has_sql: bool = False
    has_tool_call: bool = False
    errors: list[str] = Field(default_factory=list)


class SqliteTraceStore:
    def __init__(self, db: CopilotDatabase) -> None:
        self.db = db

    async def append_response(self, user_message: str, response: ChatResponse) -> str:
        detail = build_trace_detail(user_message, response)
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO trace_turns(trace_id, session_id, user_message, final_response, route, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    detail.trace_id,
                    detail.session_id,
                    detail.user_message,
                    detail.final_response,
                    detail.route,
                    detail.status,
                    detail.created_at.isoformat(),
                ),
            )
            conn.executemany(
                """
                INSERT INTO trace_spans(
                  span_id,
                  trace_id,
                  parent_span_id,
                  ordinal,
                  actor_type,
                  actor_name,
                  event_type,
                  title,
                  status,
                  input_summary,
                  output_summary,
                  metadata_json,
                  created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        span.span_id,
                        span.trace_id,
                        span.parent_span_id,
                        span.ordinal,
                        span.actor_type,
                        span.actor_name,
                        span.event_type,
                        span.title,
                        span.status,
                        span.input_summary,
                        span.output_summary,
                        json.dumps(span.metadata, sort_keys=True),
                        span.created_at.isoformat(),
                    )
                    for span in detail.spans
                ],
            )
            conn.commit()
        return detail.trace_id

    async def list_traces(self, limit: int = 50) -> list[TraceSummary]:
        rows = self.db.execute_read(
            """
            SELECT
              t.trace_id,
              t.session_id,
              t.user_message,
              t.final_response,
              t.route,
              t.status,
              t.created_at,
              COUNT(s.span_id) AS span_count
            FROM trace_turns t
            LEFT JOIN trace_spans s ON s.trace_id = t.trace_id
            GROUP BY t.trace_id
            ORDER BY t.created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [_summary_from_row(row) for row in rows]

    async def get_trace(self, trace_id: str) -> TraceDetail:
        rows = self.db.execute_read(
            """
            SELECT
              t.trace_id,
              t.session_id,
              t.user_message,
              t.final_response,
              t.route,
              t.status,
              t.created_at,
              COUNT(s.span_id) AS span_count
            FROM trace_turns t
            LEFT JOIN trace_spans s ON s.trace_id = t.trace_id
            WHERE t.trace_id = ?
            GROUP BY t.trace_id
            """,
            (trace_id,),
        )
        if not rows:
            raise TraceNotFoundError(f"trace {trace_id} does not exist")
        span_rows = self.db.execute_read(
            """
            SELECT *
            FROM trace_spans
            WHERE trace_id = ?
            ORDER BY ordinal ASC
            """,
            (trace_id,),
        )
        summary = _summary_from_row(rows[0])
        return TraceDetail(**summary.model_dump(), spans=[_span_from_row(row) for row in span_rows])


class JsonlTraceStore:
    def __init__(self, path: Path | str = DEFAULT_TRACE_PATH) -> None:
        self.path = Path(path)

    async def append_response(self, user_message: str, response: ChatResponse) -> str:
        detail = build_trace_detail(user_message, response)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as file:
            file.write(detail.model_dump_json() + "\n")
        return detail.trace_id

    async def list_traces(self, limit: int = 50) -> list[TraceSummary]:
        details = self._read_details()
        details.sort(key=lambda item: item.created_at, reverse=True)
        return [
            TraceSummary(
                trace_id=item.trace_id,
                session_id=item.session_id,
                route=item.route,
                status=item.status,
                user_message=item.user_message,
                final_response=item.final_response,
                span_count=len(item.spans),
                created_at=item.created_at,
            )
            for item in details[:limit]
        ]

    async def get_trace(self, trace_id: str) -> TraceDetail:
        for detail in self._read_details():
            if detail.trace_id == trace_id:
                return detail
        raise TraceNotFoundError(f"trace {trace_id} does not exist")

    def _read_details(self) -> list[TraceDetail]:
        if not self.path.exists():
            return []
        details = []
        with self.path.open() as file:
            for line in file:
                if not line.strip():
                    continue
                raw = json.loads(line)
                if "spans" in raw and "trace_id" in raw:
                    details.append(TraceDetail.model_validate(raw))
                else:
                    details.append(_legacy_detail(raw))
        return details


def build_trace_detail(user_message: str, response: ChatResponse) -> TraceDetail:
    trace_id = response.trace_id or f"trace_{uuid.uuid4().hex[:16]}"
    created_at = response.decision_log.created_at
    spans: list[TraceSpan] = []

    def add_span(
        *,
        parent_span_id: str | None,
        actor_type: TraceSpan.model_fields["actor_type"].annotation,
        actor_name: str,
        event_type: str,
        title: str,
        status: str,
        input_summary: str | None = None,
        output_summary: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TraceSpan:
        ordinal = len(spans) + 1
        span = TraceSpan(
            span_id=f"{trace_id}_span_{ordinal:03d}",
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            ordinal=ordinal,
            actor_type=actor_type,
            actor_name=actor_name,
            event_type=event_type,
            title=title,
            status=status,
            input_summary=_shorten(input_summary),
            output_summary=_shorten(output_summary),
            metadata=metadata or {},
            created_at=created_at,
        )
        spans.append(span)
        return span

    root = add_span(
        parent_span_id=None,
        actor_type="user",
        actor_name="user",
        event_type="user_message",
        title="User turn",
        status="received",
        input_summary=user_message,
        output_summary=f"Session {response.session_id}",
    )
    orchestrator = add_span(
        parent_span_id=root.span_id,
        actor_type="orchestrator",
        actor_name="copilot_orchestrator",
        event_type="route_decision",
        title=f"Route: {response.route}",
        status=response.route,
        input_summary=user_message,
        output_summary=response.decision_log.reasoning,
        metadata=response.decision_log.model_dump(mode="json"),
    )

    if response.route == "file_retrieval":
        add_span(
            parent_span_id=orchestrator.span_id,
            actor_type="retriever",
            actor_name="document_store",
            event_type="retrieval",
            title="Retrieve document chunks",
            status="found" if response.citations else "empty",
            input_summary=response.context.current_document_filename or response.context.current_document_id,
            output_summary=f"{len(response.citations)} cited chunks",
            metadata={
                "citations": [
                    citation.model_dump(mode="json", exclude={"quote"}) | {"quote_preview": _shorten(citation.quote, 180)}
                    for citation in response.citations
                ]
            },
        )
        add_span(
            parent_span_id=orchestrator.span_id,
            actor_type="agent",
            actor_name="file_qa_agent",
            event_type="answer_synthesis",
            title="Answer from retrieved evidence",
            status="completed",
            input_summary=f"{len(response.citations)} citations",
            output_summary=response.response,
        )
    elif response.route == "sql_query":
        add_span(
            parent_span_id=orchestrator.span_id,
            actor_type="agent",
            actor_name="sql_agent",
            event_type="sql_generation",
            title="Generate read-only SQL",
            status="generated" if response.generated_sql else "none",
            input_summary=user_message,
            output_summary=response.generated_sql,
        )
        add_span(
            parent_span_id=orchestrator.span_id,
            actor_type="validator",
            actor_name="sql_safety_validator",
            event_type="sql_validation",
            title="Validate SQL safety",
            status="allowed" if response.sql_result else "blocked",
            input_summary=response.generated_sql,
            output_summary="Read-only SQL accepted." if response.sql_result else response.response,
        )
        if response.sql_result:
            add_span(
                parent_span_id=orchestrator.span_id,
                actor_type="sql",
                actor_name="sqlite",
                event_type="sql_execution",
                title="Execute SQLite read",
                status="completed",
                input_summary=response.sql_result.sql,
                output_summary=f"{len(response.sql_result.rows)} rows returned",
                metadata={
                    "columns": response.sql_result.columns,
                    "rows_preview": response.sql_result.rows[:5],
                    "explanation": response.sql_result.explanation,
                },
            )
    elif response.route == "api_tool":
        tool_status = "none"
        if response.pending_action:
            tool_status = "awaiting_review"
        elif response.tool_result:
            tool_status = "completed" if response.tool_result.ok else "failed"
        add_span(
            parent_span_id=orchestrator.span_id,
            actor_type="agent",
            actor_name="tool_agent",
            event_type="tool_proposal",
            title=f"Propose tool: {response.tool_call.name if response.tool_call else 'none'}",
            status=tool_status,
            input_summary=user_message,
            output_summary=response.tool_call.reason if response.tool_call else response.response,
            metadata={"tool_call": response.tool_call.model_dump(mode="json") if response.tool_call else None},
        )
        if response.pending_action:
            add_span(
                parent_span_id=orchestrator.span_id,
                actor_type="validator",
                actor_name="review_gate",
                event_type="human_approval_required",
                title="Block write pending confirmation",
                status="awaiting_review",
                input_summary=response.pending_action.tool_call.name,
                output_summary=response.pending_action.preview,
                metadata={"pending_action": response.pending_action.model_dump(mode="json")},
            )
        if response.tool_result:
            add_span(
                parent_span_id=orchestrator.span_id,
                actor_type="tool",
                actor_name=response.tool_result.name,
                event_type="tool_execution",
                title=f"Execute tool: {response.tool_result.name}",
                status="completed" if response.tool_result.ok else "failed",
                input_summary=response.tool_call.model_dump_json() if response.tool_call else None,
                output_summary=response.tool_result.error or _short_json(response.tool_result.result),
                metadata={"tool_result": response.tool_result.model_dump(mode="json")},
            )
    elif response.route == "context":
        add_span(
            parent_span_id=orchestrator.span_id,
            actor_type="backend",
            actor_name="session_store",
            event_type="context_lookup",
            title="Read session context",
            status="completed",
            output_summary=response.response,
            metadata={"context": response.context.model_dump(mode="json", exclude={"history"})},
        )
    else:
        add_span(
            parent_span_id=orchestrator.span_id,
            actor_type="response",
            actor_name="clarification_handler",
            event_type="clarification",
            title="Ask for clarification or report recoverable issue",
            status="completed",
            output_summary=response.response,
        )

    add_span(
        parent_span_id=orchestrator.span_id,
        actor_type="response",
        actor_name="final_response_builder",
        event_type="final_response",
        title="Produce final answer",
        status="completed",
        input_summary=f"route={response.route}",
        output_summary=response.response,
        metadata={
            "citation_count": len(response.citations),
            "has_sql": response.generated_sql is not None,
            "has_tool_call": response.tool_call is not None,
            "pending_action": response.pending_action.action_id if response.pending_action else None,
        },
    )

    status = "failed" if response.tool_result and response.tool_result.error else "completed"
    return TraceDetail(
        trace_id=trace_id,
        session_id=response.session_id,
        route=response.route,
        status=status,
        user_message=user_message,
        final_response=response.response,
        span_count=len(spans),
        created_at=created_at,
        spans=spans,
    )


def _summary_from_row(row: dict[str, Any]) -> TraceSummary:
    return TraceSummary(
        trace_id=row["trace_id"],
        session_id=row["session_id"],
        route=row["route"],
        status=row["status"],
        user_message=row["user_message"],
        final_response=row["final_response"],
        span_count=int(row["span_count"]),
        created_at=_parse_datetime(row["created_at"]),
    )


def _span_from_row(row: dict[str, Any]) -> TraceSpan:
    return TraceSpan(
        span_id=row["span_id"],
        trace_id=row["trace_id"],
        parent_span_id=row["parent_span_id"],
        ordinal=int(row["ordinal"]),
        actor_type=row["actor_type"],
        actor_name=row["actor_name"],
        event_type=row["event_type"],
        title=row["title"],
        status=row["status"],
        input_summary=row["input_summary"],
        output_summary=row["output_summary"],
        metadata=json.loads(row["metadata_json"] or "{}"),
        created_at=_parse_datetime(row["created_at"]),
    )


def _legacy_detail(raw: dict[str, Any]) -> TraceDetail:
    trace = CopilotTrace.model_validate(raw)
    created_at = trace.decision_log.created_at
    response = ChatResponse(
        session_id=trace.session_id,
        response=trace.response,
        route=trace.decision_log.route,
        citations=[],
        generated_sql=trace.decision_log.generated_sql,
        context={"session_id": trace.session_id},
        decision_log=trace.decision_log,
    )
    detail = build_trace_detail(trace.user_message, response)
    detail.created_at = created_at
    return detail


def _parse_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _short_json(value: Any) -> str:
    return _shorten(json.dumps(value, sort_keys=True, default=str), 500) or ""


def _shorten(value: str | None, limit: int = 500) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + "..."
