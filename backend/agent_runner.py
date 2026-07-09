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


_MAX_TOOL_ROUNDS = 5


class RealAgentRunner:
    """Real Azure OpenAI runner (stock openai SDK, Chat Completions + tool calling).

    agent-framework is not on public PyPI, so this uses AsyncAzureOpenAI directly and
    maps streaming deltas + tool calls to the same AG-UI event stream the mock emits.
    """

    def __init__(self, instructions: str, rag_mode: str) -> None:
        from config import get_settings

        s = get_settings()
        if not s.openai_configured:
            raise RuntimeError(
                "RealAgentRunner requires Azure OpenAI configuration "
                "(AZURE_OPENAI_ENDPOINT / deployment). Set LLM_MODE=mock for offline."
            )
        self._instructions = instructions
        self._rag_mode = rag_mode
        self._deployment = s.chat_deployment

    async def stream(
        self, user_message: str, session: Session, rag_mode: str
    ) -> AsyncIterator[object]:
        from azure_clients import get_openai_client
        from agent_tools import execute_tool, for_rag_mode

        client = get_openai_client()
        tools = for_rag_mode(rag_mode)
        message_id = str(uuid.uuid4())

        messages: list[dict] = [{"role": "system", "content": self._instructions}]
        for m in session.messages:
            if m.get("role") in ("user", "assistant") and m.get("content"):
                messages.append({"role": m["role"], "content": m["content"]})
        messages.append({"role": "user", "content": user_message})

        for _ in range(_MAX_TOOL_ROUNDS):
            stream = await client.chat.completions.create(
                model=self._deployment,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                stream=True,
            )
            text_buf = ""
            tool_acc: dict[int, dict] = {}
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    text_buf += delta.content
                    yield TextMessageContentEvent(
                        message_id=message_id, delta=delta.content
                    )
                for tc in delta.tool_calls or []:
                    slot = tool_acc.setdefault(
                        tc.index, {"id": "", "name": "", "arguments": ""}
                    )
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function and tc.function.name:
                        slot["name"] = tc.function.name
                    if tc.function and tc.function.arguments:
                        slot["arguments"] += tc.function.arguments

            if not tool_acc:
                return

            # Replay the assistant tool-call turn, then execute each tool.
            messages.append(
                {
                    "role": "assistant",
                    "content": text_buf or None,
                    "tool_calls": [
                        {
                            "id": slot["id"],
                            "type": "function",
                            "function": {
                                "name": slot["name"],
                                "arguments": slot["arguments"] or "{}",
                            },
                        }
                        for slot in tool_acc.values()
                    ],
                }
            )
            for slot in tool_acc.values():
                try:
                    args = json.loads(slot["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                yield ToolCallStartEvent(
                    tool_call_id=slot["id"], tool_call_name=slot["name"]
                )
                result = await execute_tool(slot["name"], args)
                result_json = json.dumps(result)
                yield ToolCallResultEvent(
                    message_id=message_id,
                    tool_call_id=slot["id"],
                    content=result_json,
                )
                yield ToolCallEndEvent(tool_call_id=slot["id"])
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": slot["id"],
                        "content": result_json,
                    }
                )


def build_runner(mode: str, instructions: str, rag_mode: str) -> AgentRunner:
    """Return the appropriate runner; fall back to mock if the real path is unavailable."""
    if mode == "real":
        try:
            return RealAgentRunner(instructions, rag_mode)
        except RuntimeError as exc:
            print(f"[runner] real runner unavailable, falling back to mock: {exc}")
    return MockAgentRunner()
