"""LLM runner abstraction.

Two strategies, selected at request time, that emit IDENTICAL AG-UI event streams:

* MockAgentRunner  — streams a canned assistant reply and, when the user references an
  order ID, simulates a `get_order_status` tool call. Requires NO Azure access; this is
  what powers the offline vertical slice.
* RealAgentRunner  — the real Microsoft Agent Framework path (Agent + AzureOpenAI
  Responses client). Scaffolded behind a guarded import so the offline slice never fails
  to start when the private `agent-framework` package is absent.

Both yield content/tool events; the server wraps them with RUN_STARTED / RUN_FINISHED.
"""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from typing import AsyncIterator, Protocol

from ag_ui.core.events import (
    TextMessageContentEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)

from agent_tools import get_order_status
from session_manager import Session

_ORDER_RE = re.compile(r"ORD-\d+", re.IGNORECASE)


class AgentRunner(Protocol):
    async def stream(
        self, user_message: str, session: Session, rag_mode: str
    ) -> AsyncIterator[object]:
        ...


async def _stream_text(message_id: str, text: str, delay: float = 0.01):
    """Yield a TEXT_MESSAGE_CONTENT event per whitespace-delimited token."""
    tokens = text.split(" ")
    for i, tok in enumerate(tokens):
        delta = tok if i == len(tokens) - 1 else tok + " "
        yield TextMessageContentEvent(message_id=message_id, delta=delta)
        if delay:
            await asyncio.sleep(delay)


class MockAgentRunner:
    """Offline runner — no Azure calls."""

    async def stream(
        self, user_message: str, session: Session, rag_mode: str
    ) -> AsyncIterator[object]:
        message_id = str(uuid.uuid4())
        match = _ORDER_RE.search(user_message or "")

        if match:
            order_id = match.group(0).upper()
            tool_call_id = str(uuid.uuid4())
            yield ToolCallStartEvent(
                tool_call_id=tool_call_id, tool_call_name="get_order_status"
            )
            result = get_order_status(order_id)
            yield ToolCallResultEvent(
                message_id=message_id,
                tool_call_id=tool_call_id,
                content=json.dumps(result),
            )
            yield ToolCallEndEvent(tool_call_id=tool_call_id)

            if result.get("status") == "not_found":
                reply = (
                    f"I couldn't find any order matching **{order_id}**. "
                    "Please double-check the order ID and try again."
                )
            else:
                reply = (
                    f"Here's the latest on **{order_id}**: the order is "
                    f"**{result['status']}**. Tracking number is "
                    f"{result['trackingNumber']}, with an estimated delivery of "
                    f"{result['eta']}. Let me know if there's anything else I can help with!"
                )
        else:
            reply = (
                "Thanks for reaching out! I'm running in **offline demo mode**, so I can't "
                "reach a live language model right now. Try asking about an order, e.g. "
                '"What is the status of order ORD-001?" to see a live tool call and a '
                "shipping-status card."
            )

        async for ev in _stream_text(message_id, reply):
            yield ev


class RealAgentRunner:
    """Real Azure/Agent-Framework runner (scaffold — not exercised in the slice)."""

    def __init__(self, instructions: str, rag_mode: str) -> None:
        try:
            from agent_framework import Agent  # noqa: F401
            from agent_framework.azure import AzureOpenAIResponsesClient  # noqa: F401
        except Exception as exc:  # pragma: no cover - depends on private package
            raise RuntimeError(
                "RealAgentRunner requires the 'agent-framework-ag-ui' package and Azure "
                "OpenAI configuration, which are unavailable in this environment. "
                "Set LLM_MODE=mock (default) for the offline slice."
            ) from exc
        self._instructions = instructions
        self._rag_mode = rag_mode
        # Full construction (client, tools, streaming loop) lands in a later phase.

    async def stream(
        self, user_message: str, session: Session, rag_mode: str
    ) -> AsyncIterator[object]:  # pragma: no cover - not reachable offline
        raise RuntimeError("RealAgentRunner.stream is not implemented in this build")
        yield  # make this an async generator


def build_runner(mode: str, instructions: str, rag_mode: str) -> AgentRunner:
    """Return the appropriate runner; fall back to mock if the real path is unavailable."""
    if mode == "real":
        try:
            return RealAgentRunner(instructions, rag_mode)
        except RuntimeError as exc:
            print(f"[runner] real runner unavailable, falling back to mock: {exc}")
    return MockAgentRunner()
