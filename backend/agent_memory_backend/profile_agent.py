"""Extract durable user facts from a persisted conversation."""
from __future__ import annotations

import json
import logging

from .config import get_settings
from .memory_agent import format_transcript, load_prompt
from .user_profile_memory import PROFILE_SECTIONS

logger = logging.getLogger("profile_agent")

class ProfileAgent:
    def __init__(self) -> None:
        self._policy = load_prompt("profile_extraction.txt")

    async def extract(self, messages: list[dict], title: str | None = None) -> dict:
        from .azure_clients import get_openai_client

        s = get_settings()
        client = get_openai_client()
        transcript = format_transcript(messages, title)
        resp = await client.chat.completions.create(
            model=s.chat_deployment,
            messages=[
                {"role": "system", "content": self._policy},
                {"role": "user", "content": transcript},
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or "{}"
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("profile extraction returned non-JSON")
            return {}
        return {k: v for k, v in data.items() if k in PROFILE_SECTIONS}
