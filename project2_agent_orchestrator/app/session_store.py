from __future__ import annotations

import json
from pathlib import Path

from project2_agent_orchestrator.app.models import SessionState


DEFAULT_SESSION_PATH = Path(__file__).resolve().parent.parent / "data" / "sessions.json"


class JsonSessionStore:
    def __init__(self, path: Path | str = DEFAULT_SESSION_PATH) -> None:
        self.path = Path(path)

    async def load(self, session_id: str, user_id: str) -> SessionState:
        records = self._read_records()
        payload = records.get(session_id)
        if payload is None:
            return SessionState(session_id=session_id, user_id=user_id)
        state = SessionState.model_validate(payload)
        if state.user_id != user_id:
            return SessionState(session_id=session_id, user_id=user_id)
        return state

    async def save(self, state: SessionState) -> None:
        records = self._read_records()
        records[state.session_id] = state.model_dump(mode="json")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(records, indent=2))

    def _read_records(self) -> dict:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text())
