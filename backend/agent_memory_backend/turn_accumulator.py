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
    _citation_positions: dict[tuple[Any, ...], int] = field(
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
            key = _citation_key(citation)
            payload = citation.to_dict()
            existing_index = self._citation_positions.get(key)
            if existing_index is not None:
                existing = self.assistant_citations[existing_index]
                for name, value in payload.items():
                    if (
                        name == "mandatory_status"
                        and existing.get(name) == "unknown"
                        and value in {"mandatory", "non_mandatory"}
                    ):
                        existing[name] = value
                        continue
                    if existing.get(name) is None and value is not None:
                        existing[name] = value
                continue
            self._citation_positions[key] = len(self.assistant_citations)
            self.assistant_citations.append(payload)


def _citation_key(citation: Citation) -> tuple[Any, ...]:
    if citation.directive_id:
        return (
            citation.ref_id,
            citation.source_name,
            citation.directive_version_id,
            citation.section_id,
            citation.page_from,
            citation.page_to,
        )
    return (citation.ref_id, citation.source_name)
