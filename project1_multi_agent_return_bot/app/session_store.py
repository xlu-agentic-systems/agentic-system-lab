from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from project1_multi_agent_return_bot.app.models import SessionState


DEFAULT_SESSION_PATH = Path(__file__).resolve().parent.parent / "data" / "sessions.json"
DEFAULT_SESSION_TTL = timedelta(hours=24)


class JsonSessionStore:
    """File-backed active session store.

    This is the prototype equivalent of a production Redis/session-cache layer.
    A session is active only when it exists, belongs to the requesting user, and
    has not passed its TTL.
    """

    def __init__(
        self,
        path: Path | str = DEFAULT_SESSION_PATH,
        *,
        ttl: timedelta = DEFAULT_SESSION_TTL,
        clock: Callable[[], datetime] = datetime.utcnow,
    ) -> None:
        self.path = Path(path)
        self.ttl = ttl
        self.clock = clock

    async def load(self, session_id: str, user_id: str) -> SessionState:
        records = self._read_records()
        key = self._record_key(session_id, user_id)
        payload = records.get(key)
        if payload is None:
            return self._new_session(session_id, user_id)
        state = SessionState.model_validate(payload)
        expired = self._is_expired(state)
        if expired:
            records.pop(key, None)
            self._write_records(records)
            return self._new_session(session_id, user_id)
        state.status = "active"
        state.updated_at = self.clock()
        return state

    async def save(self, state: SessionState) -> None:
        records = self._read_records()
        now = self.clock()
        state.status = "active"
        state.updated_at = now
        state.expires_at = now + self.ttl
        records[self._record_key(state.session_id, state.user_id)] = state.model_dump(mode="json")
        self._write_records(records)

    async def is_active(self, session_id: str, user_id: str) -> bool:
        records = self._read_records()
        payload = records.get(self._record_key(session_id, user_id))
        if payload is None:
            return False
        state = SessionState.model_validate(payload)
        return state.user_id == user_id and not self._is_expired(state)

    def _read_records(self) -> dict:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text())

    def _write_records(self, records: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(records, indent=2))

    def _new_session(self, session_id: str, user_id: str) -> SessionState:
        now = self.clock()
        return SessionState(
            session_id=session_id,
            user_id=user_id,
            created_at=now,
            updated_at=now,
            expires_at=now + self.ttl,
        )

    def _is_expired(self, state: SessionState) -> bool:
        return state.expires_at is not None and state.expires_at <= self.clock()

    @staticmethod
    def _record_key(session_id: str, user_id: str) -> str:
        return f"{user_id}:{session_id}"
