"""Directive-only Hosted Agent continuation backed by a Foundry conversation."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterable
from contextlib import suppress
from contextvars import ContextVar
from typing import Any

from agent_framework import AgentSession
from agent_framework_foundry_hosting import ResponsesHostServer

from .gateway import (
    complete_agent_state_turn,
    fail_agent_state_turn,
    resolve_agent_state,
)

_active_session: ContextVar[AgentSession | None] = ContextVar(
    "active_agent_session",
    default=None,
)


class _SessionBoundAgent:
    def __init__(self, agent: Any) -> None:
        self._agent = agent

    def __getattr__(self, name: str) -> Any:
        return getattr(self._agent, name)

    async def __aenter__(self) -> _SessionBoundAgent:
        enter = getattr(self._agent, "__aenter__", None)
        if enter is not None:
            await enter()
        return self

    async def __aexit__(self, *args: Any) -> Any:
        exit_method = getattr(self._agent, "__aexit__", None)
        if exit_method is not None:
            return await exit_method(*args)
        return None

    def run(self, *args: Any, **kwargs: Any) -> Any:
        session = _active_session.get()
        if session is None:
            raise RuntimeError("Directive AgentSession was not resolved")
        kwargs["session"] = session
        return self._agent.run(*args, **kwargs)


class _HistoryContext:
    def __init__(self, context: Any, *, include_history: bool) -> None:
        self._context = context
        self._include_history = include_history

    def __getattr__(self, name: str) -> Any:
        return getattr(self._context, name)

    async def get_history(self) -> list[Any]:
        if not self._include_history:
            return []
        return await self._context.get_history()


class StatefulResponsesHostServer(ResponsesHostServer):
    """Bind each Hosted session to its backend-owned inner model conversation."""

    def __init__(self, agent: Any, **kwargs: Any) -> None:
        super().__init__(_SessionBoundAgent(agent), **kwargs)
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._session_locks_guard = asyncio.Lock()

    async def _lock_for(self, inner_id: str) -> asyncio.Lock:
        async with self._session_locks_guard:
            return self._session_locks.setdefault(inner_id, asyncio.Lock())

    async def _handle_inner_agent(
        self,
        request: Any,
        context: Any,
    ) -> AsyncIterable[Any]:
        outer_foundry_id = getattr(context, "conversation_id", None)
        if not isinstance(outer_foundry_id, str) or not outer_foundry_id:
            raise RuntimeError("Outer Hosted Agent conversation is missing")
        state = await resolve_agent_state(outer_foundry_id)
        expected_release = os.environ["DIRECTIVE_AGENT_RELEASE_ID"]
        if state.release_id != expected_release:
            raise RuntimeError("Directive Agent release binding mismatch")

        lock = await self._lock_for(state.inner_model_conversation_id)
        async with lock:
            token = _active_session.set(
                AgentSession(
                    service_session_id=state.inner_model_conversation_id,
                )
            )
            turn_closed = False
            try:
                proxy = _HistoryContext(
                    context,
                    include_history=state.bootstrap_required,
                )
                async for event in super()._handle_inner_agent(request, proxy):
                    event_type = getattr(event, "type", None)
                    if event_type == "response.completed":
                        await complete_agent_state_turn(
                            outer_foundry_id,
                            revision=state.revision,
                        )
                        turn_closed = True
                    elif event_type == "response.failed":
                        await fail_agent_state_turn(
                            outer_foundry_id,
                            revision=state.revision,
                        )
                        turn_closed = True
                    yield event
            finally:
                if not turn_closed:
                    cleanup = asyncio.create_task(
                        fail_agent_state_turn(
                            outer_foundry_id,
                            revision=state.revision,
                        )
                    )
                    with suppress(Exception, asyncio.CancelledError):
                        await asyncio.shield(cleanup)
                _active_session.reset(token)
