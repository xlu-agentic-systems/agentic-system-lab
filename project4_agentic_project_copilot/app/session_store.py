from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from project4_agentic_project_copilot.app.models import ChatTurn, SessionContext


DEFAULT_SESSION_PATH = Path(__file__).resolve().parent.parent / "data" / "sessions.json"


class JsonSessionStore:
    def __init__(self, path: Path | str = DEFAULT_SESSION_PATH) -> None:
        self.path = Path(path)

    async def load(self, session_id: str) -> SessionContext:
        records = self._read_records()
        payload = records.get(session_id)
        if payload is None:
            return SessionContext(session_id=session_id)
        return SessionContext.model_validate(payload)

    async def save(self, context: SessionContext) -> None:
        records = self._read_records()
        context.updated_at = datetime.utcnow()
        records[context.session_id] = context.model_dump(mode="json")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(records, indent=2))

    def _read_records(self) -> dict:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text())


def append_turn(context: SessionContext, *, role: str, content: str, limit: int = 12) -> None:
    context.history.append(ChatTurn(role=role, content=content))  # type: ignore[arg-type]
    if len(context.history) > limit:
        context.history = context.history[-limit:]
