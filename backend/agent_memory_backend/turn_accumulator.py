"""Accumulate one normalized agent turn into durable public message records."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from agent_contracts import (
    Citation,
    CitationsEvent,
    NormalizedAgentEvent,
    TextDeltaEvent,
    ToolResultEvent,
    ToolStartedEvent,
    UsageEvent,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TurnAccumulator:
    user_message: str
    user_created_at: str = field(default_factory=_now)
    assistant_text: str = ""
    assistant_usage: dict[str, int] | None = None
    assistant_tools: list[str] = field(default_factory=list)
    assistant_citations: list[dict[str, Any]] = field(default_factory=list)
    _citation_positions: dict[tuple[str, str], int] = field(
        default_factory=dict, init=False
    )

    def consume(self, event: NormalizedAgentEvent) -> None:
        if isinstance(event, TextDeltaEvent):
            self.assistant_text += event.delta
        elif isinstance(event, ToolStartedEvent):
            if event.tool_name not in self.assistant_tools:
                self.assistant_tools.append(event.tool_name)
        elif isinstance(event, ToolResultEvent):
            self._add_citations(event.result.citations)
        elif isinstance(event, CitationsEvent):
            self._add_citations(event.citations)
        elif isinstance(event, UsageEvent):
            self.assistant_usage = {
                "input_tokens": event.input_tokens,
                "output_tokens": event.output_tokens,
                "cached_tokens": event.cached_tokens,
            }

    def message_records(
        self, assistant_created_at: str | None = None
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        user_record: dict[str, Any] = {
            "role": "user",
            "content": self.user_message,
            "created_at": self.user_created_at,
        }
        assistant_record: dict[str, Any] = {
            "role": "assistant",
            "content": self.assistant_text,
            "created_at": assistant_created_at or _now(),
        }
        if self.assistant_usage:
            assistant_record["usage"] = self.assistant_usage
        if self.assistant_tools:
            assistant_record["tools"] = self.assistant_tools
        if self.assistant_citations:
            assistant_record["citations"] = self.assistant_citations
        return user_record, assistant_record

    def _add_citations(self, citations: tuple[Citation, ...]) -> None:
        for citation in citations:
            key = (citation.ref_id, citation.source_name)
            existing_index = self._citation_positions.get(key)
            if existing_index is not None:
                existing = self.assistant_citations[existing_index]
                if not existing.get("url") and citation.url:
                    existing["url"] = citation.url
                if (
                    existing.get("search_idx") is None
                    and citation.search_idx is not None
                ):
                    existing["search_idx"] = citation.search_idx
                continue
            self._citation_positions[key] = len(self.assistant_citations)
            self.assistant_citations.append(
                {
                    "ref_id": citation.ref_id,
                    "source_name": citation.source_name,
                    "search_idx": citation.search_idx,
                    "url": citation.url,
                }
            )
