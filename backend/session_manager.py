"""In-memory SessionManager (Challenge 01, in-memory-only build).

Per the project decision, Azure Cache for Redis is NOT provisioned. The manager runs
its in-memory fallback path only: `REDIS_HOST` stays unset and every method uses the
local dicts. Sessions therefore do NOT survive a backend restart or a second replica —
the backend is pinned to a single replica in deployment.

The full solution serialises the framework `AgentSession`; here we keep an equivalent
lightweight session object holding the conversation transcript.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from fastapi import HTTPException

MAX_SESSIONS = 1000


@dataclass
class Session:
    session_id: str
    user_id: str | None = None
    title: str | None = None
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    message_count: int = 0
    # Full conversation history for the session (client-side history).
    messages: list[dict] = field(default_factory=list)

    def touch(self) -> None:
        self.last_activity = time.time()

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "title": self.title,
            "created_at": self.created_at,
            "last_activity": self.last_activity,
            "message_count": self.message_count,
        }


class SessionManager:
    """In-memory session store with LRU-style eviction by last_activity."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._redis = None  # always None in this build

    async def connect(self) -> None:
        # REDIS_HOST intentionally unset — stay in-memory.
        print("[session] REDIS_HOST not set — session manager running in-memory only")

    async def close(self) -> None:
        self._sessions.clear()

    def _evict_if_needed(self) -> None:
        if len(self._sessions) <= MAX_SESSIONS:
            return
        # Evict least-recently-active sessions.
        for sid, _ in sorted(self._sessions.items(), key=lambda kv: kv[1].last_activity)[
            : len(self._sessions) - MAX_SESSIONS
        ]:
            self._sessions.pop(sid, None)

    async def create_session(
        self,
        session_id: str | None = None,
        title: str | None = None,
        user_id: str | None = None,
    ) -> Session:
        sid = session_id or str(uuid.uuid4())
        session = Session(session_id=sid, user_id=user_id, title=title)
        self._sessions[sid] = session
        self._evict_if_needed()
        return session

    async def get_session(self, session_id: str, auto_create: bool = True) -> Session | None:
        session = self._sessions.get(session_id)
        if session is None and auto_create:
            session = await self.create_session(session_id=session_id)
        if session is not None:
            session.touch()
        return session

    async def delete_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    async def update_session(self, session_id: str, title: str) -> None:
        session = self._sessions.get(session_id)
        if session is not None:
            session.title = title
            session.touch()

    async def save_session_state(self, session: Session) -> None:
        # In-memory: the object is already live; just record activity.
        session.touch()

    async def increment_message_count(self, session_id: str, n: int = 2) -> None:
        session = self._sessions.get(session_id)
        if session is not None:
            session.message_count += n

    async def get_session_info(self, session_id: str) -> dict | None:
        session = self._sessions.get(session_id)
        return session.to_dict() if session else None

    async def list_sessions(self, user_id: str | None = None) -> list[dict]:
        sessions = self._sessions.values()
        if user_id is not None:
            sessions = [s for s in sessions if s.user_id == user_id]
        return [
            s.to_dict()
            for s in sorted(sessions, key=lambda s: s.last_activity, reverse=True)
        ]

    async def assert_session_owner(self, session_id: str, user_id: str) -> None:
        session = self._sessions.get(session_id)
        if session is not None and session.user_id not in (None, user_id):
            raise HTTPException(status_code=403, detail="Not the session owner")
