"""Application-owned async boundary for remote agent runtimes."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from agent_contracts import NormalizedAgentEvent, RuntimeState, TurnContext


class AgentRuntime(Protocol):
    async def initialize(self) -> None: ...

    async def close(self) -> None: ...

    async def create_state(
        self,
        application_conversation_id: str,
        authenticated_user_id: str,
        seed_messages: list[dict[str, str]] | None = None,
    ) -> RuntimeState: ...

    async def stream_turn(
        self, message: str, context: TurnContext
    ) -> AsyncIterator[NormalizedAgentEvent]: ...

    async def delete_state(
        self, state: RuntimeState, authenticated_user_id: str
    ) -> None: ...

    async def health_check(self) -> None: ...
