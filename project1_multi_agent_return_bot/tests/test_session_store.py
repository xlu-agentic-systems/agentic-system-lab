import asyncio
from datetime import datetime, timedelta

from project1_multi_agent_return_bot.app.models import ChatMessage
from project1_multi_agent_return_bot.app.session_store import JsonSessionStore


def run(coro):
    return asyncio.run(coro)


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


def test_session_store_tracks_active_session_with_ttl(tmp_path) -> None:
    clock = FakeClock(datetime(2026, 4, 24, 12, 0, 0))
    store = JsonSessionStore(tmp_path / "sessions.json", ttl=timedelta(minutes=30), clock=clock)

    state = run(store.load("session-1", "user-1"))
    state.history.append(ChatMessage(role="user", content="hello"))
    run(store.save(state))

    assert run(store.is_active("session-1", "user-1")) is True

    loaded = run(store.load("session-1", "user-1"))

    assert loaded.user_id == "user-1"
    assert loaded.history[0].content == "hello"
    assert loaded.expires_at == datetime(2026, 4, 24, 12, 30, 0)


def test_session_store_expires_old_session_and_starts_fresh(tmp_path) -> None:
    clock = FakeClock(datetime(2026, 4, 24, 12, 0, 0))
    store = JsonSessionStore(tmp_path / "sessions.json", ttl=timedelta(minutes=5), clock=clock)

    state = run(store.load("session-1", "user-1"))
    state.history.append(ChatMessage(role="user", content="old message"))
    run(store.save(state))

    clock.advance(timedelta(minutes=6))

    assert run(store.is_active("session-1", "user-1")) is False

    fresh = run(store.load("session-1", "user-1"))

    assert fresh.user_id == "user-1"
    assert fresh.history == []
    assert fresh.expires_at == datetime(2026, 4, 24, 12, 11, 0)


def test_session_store_does_not_leak_session_across_users(tmp_path) -> None:
    clock = FakeClock(datetime(2026, 4, 24, 12, 0, 0))
    store = JsonSessionStore(tmp_path / "sessions.json", clock=clock)

    state = run(store.load("shared-session", "user-1"))
    state.history.append(ChatMessage(role="user", content="private"))
    run(store.save(state))

    other_user_state = run(store.load("shared-session", "user-2"))

    assert other_user_state.user_id == "user-2"
    assert other_user_state.history == []
    assert run(store.is_active("shared-session", "user-1")) is True
    assert run(store.is_active("shared-session", "user-2")) is False

    other_user_state.history.append(ChatMessage(role="user", content="other user"))
    run(store.save(other_user_state))

    original_user_state = run(store.load("shared-session", "user-1"))

    assert original_user_state.history[0].content == "private"
    assert run(store.is_active("shared-session", "user-2")) is True
