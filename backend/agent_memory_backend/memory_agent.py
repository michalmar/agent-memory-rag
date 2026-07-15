"""Summarize a conversation and embed the summary."""
from __future__ import annotations

import os
from dataclasses import dataclass

from .config import get_settings

_PROMPT_DIR = os.path.join(os.path.dirname(__file__), "prompts")


def load_prompt(name: str) -> str:
    with open(os.path.join(_PROMPT_DIR, name), "r", encoding="utf-8") as fh:
        return fh.read()


def format_transcript(messages: list[dict], title: str | None = None) -> str:
    """Join persisted user and assistant messages into a plain transcript."""
    lines: list[str] = []
    if title:
        lines.append(f"Conversation title: {title}")
    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "") or ""
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


@dataclass
class MemoryResult:
    summary: str
    embedding: list[float]


class MemoryAgent:
    def __init__(self) -> None:
        self._system = load_prompt("conversation_memory.txt")

    async def create_memory(self, messages: list[dict], title: str | None = None) -> MemoryResult:
        from .azure_clients import get_openai_client, embed_text

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
