"""Translate runtime-neutral agent events to the public AG-UI stream."""

from __future__ import annotations

import json
from collections.abc import Iterable

from ag_ui.core.events import (
    CustomEvent,
    TextMessageContentEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)

from agent_contracts import (
    CitationsEvent,
    NormalizedAgentEvent,
    RuntimeCompletedEvent,
    TextDeltaEvent,
    ToolEndedEvent,
    ToolResultEvent,
    ToolStartedEvent,
    UsageEvent,
)


def to_agui_events(event: NormalizedAgentEvent) -> Iterable[object]:
    if isinstance(event, TextDeltaEvent):
        return (
            TextMessageContentEvent(
                message_id=event.message_id, delta=event.delta
            ),
        )
    if isinstance(event, ToolStartedEvent):
        return (
            ToolCallStartEvent(
                tool_call_id=event.call_id, tool_call_name=event.tool_name
            ),
        )
    if isinstance(event, ToolResultEvent):
        return (
            ToolCallResultEvent(
                message_id=event.message_id,
                tool_call_id=event.call_id,
                content=json.dumps(event.result.to_dict()),
            ),
        )
    if isinstance(event, ToolEndedEvent):
        return (ToolCallEndEvent(tool_call_id=event.call_id),)
    if isinstance(event, CitationsEvent):
        return (
            CustomEvent(
                name="agent_citations",
                value=[
                    {
                        "ref_id": citation.ref_id,
                        "source_name": citation.source_name,
                        "search_idx": citation.search_idx,
                        "url": citation.url,
                    }
                    for citation in event.citations
                ],
            ),
        )
    if isinstance(event, UsageEvent):
        return (
            CustomEvent(
                name="agent_usage",
                value={
                    "input_tokens": event.input_tokens,
                    "output_tokens": event.output_tokens,
                    "cached_tokens": event.cached_tokens,
                },
            ),
        )
    if isinstance(event, RuntimeCompletedEvent):
        return ()
    raise TypeError(f"Unsupported normalized event: {type(event).__name__}")
