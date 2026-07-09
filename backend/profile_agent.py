"""ProfileAgent — batch extraction of durable user facts from a transcript (§F4).

Returns the six profile sections as a JSON object suitable for upsert_profile.
"""
from __future__ import annotations

import json
import logging

from config import get_settings
from memory_agent import format_transcript, load_prompt
from user_profile_memory import PROFILE_SECTIONS

logger = logging.getLogger("profile_agent")

_EXTRACT_INSTRUCTIONS = (
    "Extract durable, explicitly-stated facts about the user from the conversation. "
    "Respond ONLY with a JSON object containing any of these keys: "
    "basic_info (object), preferences (object), status (object), "
    "interests (array), habits (array), facts (array). "
    "Objects hold key/value pairs; arrays hold short strings. Include a key only if "
    "the user clearly stated the information. Never infer or guess. Return {} if nothing."
)


class ProfileAgent:
    def __init__(self) -> None:
        # profile_update.j2 documents the extraction policy (verbatim rules).
        self._policy = load_prompt("profile_update.j2")

    async def extract(self, messages: list[dict], title: str | None = None) -> dict:
        from azure_clients import get_openai_client

        s = get_settings()
        client = get_openai_client()
        transcript = format_transcript(messages, title)
        resp = await client.chat.completions.create(
            model=s.chat_deployment,
            messages=[
                {"role": "system", "content": self._policy + "\n\n" + _EXTRACT_INSTRUCTIONS},
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
