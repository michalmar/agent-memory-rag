"""MemoryAgent — summarise a conversation and embed the summary (PRD §F3).

Uses AzureOpenAIChatClient (stock openai SDK) — no agent-framework dependency.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from config import get_settings

logger = logging.getLogger("memory_agent")

_PROMPT_DIR = os.path.join(os.path.dirname(__file__), "prompts")


def load_prompt(name: str) -> str:
    with open(os.path.join(_PROMPT_DIR, name), "r", encoding="utf-8") as fh:
        return fh.read()


def format_transcript(messages: list[dict], title: str | None = None) -> str:
    """Join messages as '{role}: {content}' with tool-call/result lines (PRD §B6)."""
    lines: list[str] = []
    if title:
        lines.append(f"Conversation title: {title}")
    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "") or ""
        lines.append(f"{role}: {content}")
        if m.get("tool_call"):
            tc = m["tool_call"]
            lines.append(f"[tool call: {tc.get('name')}({tc.get('arguments')})]")
        if m.get("tool_result") is not None:
            lines.append(f"[tool result: {m['tool_result']}]")
    return "\n".join(lines)


@dataclass
class MemoryResult:
    summary: str
    embedding: list[float]


class MemoryAgent:
    def __init__(self) -> None:
        self._system = load_prompt("conversation_memory.j2")

    async def create_memory(self, messages: list[dict], title: str | None = None) -> MemoryResult:
        from azure_clients import get_openai_client, embed_text

        s = get_settings()
        client = get_openai_client()
        transcript = format_transcript(messages, title)
        resp = await client.chat.completions.create(
            model=s.chat_deployment,
            messages=[
                {"role": "system", "content": self._system},
                {"role": "user", "content": transcript},
            ],
            temperature=0.3,
        )
        summary = (resp.choices[0].message.content or "").strip()
        embedding = await embed_text(summary)
        return MemoryResult(summary=summary, embedding=embedding)
