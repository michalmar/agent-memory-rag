"""Local-only runtime used when Azure is intentionally not configured."""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import AsyncIterator

from agent_contracts import (
    AgentType,
    FOUNDRY_PROMPT_VERSION,
    NormalizedAgentEvent,
    PROMPT_VERSION,
    RuntimeCompletedEvent,
    RuntimeDescriptor,
    RuntimeState,
    TextDeltaEvent,
    ToolEndedEvent,
    ToolResultEvent,
    ToolStartedEvent,
    TurnContext,
)
from .agent_tools import ToolExecutor
from .config import get_settings

_ORDER_RE = re.compile(r"ORD-\d+", re.IGNORECASE)


class MockAgentRuntime:
    def __init__(self, agent_type: AgentType, tool_executor: ToolExecutor) -> None:
        self._agent_type = agent_type
        self._tool_executor = tool_executor

    async def initialize(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def create_state(
        self,
        application_conversation_id: str,
        authenticated_user_id: str,
        seed_messages: list[dict[str, str]] | None = None,
    ) -> RuntimeState:
        return RuntimeState(
            descriptor=RuntimeDescriptor(
                agent_type=self._agent_type,
                physical_agent_name=f"local-mock-{self._agent_type.value}",
                release_id=get_settings().agent_release_id,
                prompt_version=(
                    FOUNDRY_PROMPT_VERSION
                    if self._agent_type == AgentType.FOUNDRY_PROMPT
                    else PROMPT_VERSION
                ),
                observed_agent_version="local",
            )
        )

    async def stream_turn(
        self, message: str, context: TurnContext
    ) -> AsyncIterator[NormalizedAgentEvent]:
        message_id = str(uuid.uuid4())
        match = _ORDER_RE.search(message)
        if match and self._agent_type == AgentType.AGENT_FRAMEWORK:
            order_id = match.group(0).upper()
            call_id = str(uuid.uuid4())
            yield ToolStartedEvent(call_id=call_id, tool_name="get_order_status")
            result = await self._tool_executor.execute_envelope(
                "get_order_status",
                {"order_id": order_id},
                user_id=context.authenticated_user_id,
            )
            yield ToolResultEvent(
                call_id=call_id,
                message_id=f"{call_id}-result",
                result=result,
            )
            yield ToolEndedEvent(call_id=call_id)
            order = result.data
            if order.get("status") == "not_found":
                reply = f"I couldn't find an order matching **{order_id}**."
            else:
                reply = (
                    f"Order **{order_id}** is **{order['status']}**. "
                    f"Tracking: {order['trackingNumber']}; ETA: {order['eta']}."
                )
        else:
            reply = (
                f"This is the local **{self._agent_type.value}** runtime. "
                "Configure the private Foundry project to use the deployed agent."
            )
        for index, token in enumerate(reply.split(" ")):
            delta = token if index == len(reply.split(" ")) - 1 else f"{token} "
            yield TextDeltaEvent(message_id=message_id, delta=delta)
            await asyncio.sleep(0)
        yield RuntimeCompletedEvent(response_id=f"mock-{uuid.uuid4()}")

    async def delete_state(
        self, state: RuntimeState, authenticated_user_id: str
    ) -> None:
        return None

    async def health_check(self) -> None:
        return None
