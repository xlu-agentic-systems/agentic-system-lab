from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from project1_multi_agent_return_bot.app.models import PlannerOutput, RoutingOutput, ToolExecutionResult


DEFAULT_TRACE_PATH = Path(__file__).resolve().parent.parent / "data" / "conversation_traces.jsonl"


class ReturnConversationTrace(BaseModel):
    """Durable per-turn trace for audit, debugging, and offline evaluation."""

    session_id: str
    user_id: str
    user_message: str
    response: str
    routing: RoutingOutput
    planner: PlannerOutput
    tool_results: list[ToolExecutionResult] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class JsonlTraceStore:
    """File-backed trace store.

    This is the prototype equivalent of a production durable store such as
    Postgres, DynamoDB, or a data lake table. It is intentionally separate from
    the active session store.
    """

    def __init__(self, path: Path | str = DEFAULT_TRACE_PATH) -> None:
        self.path = Path(path)

    async def append(self, trace: ReturnConversationTrace) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as file:
            file.write(trace.model_dump_json() + "\n")

    def load_all(self) -> list[ReturnConversationTrace]:
        if not self.path.exists():
            return []
        traces = []
        for line in self.path.read_text().splitlines():
            if not line.strip():
                continue
            traces.append(ReturnConversationTrace.model_validate_json(line))
        return traces

    def load_raw(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]
