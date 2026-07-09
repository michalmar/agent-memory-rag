"""UserProfileMemoryStore — Cosmos CRUD, one profile doc per user (PRD §F4).

Implements RFC 7396 JSON Merge Patch for the live update_user_profile tool.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from config import get_settings

logger = logging.getLogger("profile")

PROFILE_SECTIONS = ("basic_info", "interests", "habits", "preferences", "status", "facts")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_merge_patch(target: dict, patch: dict) -> dict:
    """RFC 7396 JSON Merge Patch (PRD §B6). null removes a key."""
    result = dict(target)
    for k, v in patch.items():
        if v is None:
            result.pop(k, None)
        elif isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = json_merge_patch(result[k], v)
        else:
            result[k] = v
    return result


def _empty_profile(user_id: str) -> dict:
    now = _now()
    return {
        "id": user_id,
        "user_id": user_id,
        "version": 0,
        "basic_info": {},
        "interests": [],
        "habits": [],
        "preferences": {},
        "status": {},
        "facts": [],
        "source_conversations": [],
        "created_at": now,
        "updated_at": now,
    }


class UserProfileMemoryStore:
    def __init__(self) -> None:
        self._client = None
        self._container = None

    async def initialize(self) -> None:
        s = get_settings()
        if not s.cosmos_configured:
            logger.warning("Cosmos not configured; profile store disabled")
            return
        from azure.cosmos.aio import CosmosClient

        if s.cosmos_key:
            self._client = CosmosClient(s.cosmos_endpoint, credential=s.cosmos_key)
        else:
            from azure_clients import get_credential

            self._client = CosmosClient(s.cosmos_endpoint, credential=get_credential())
        db = self._client.get_database_client(s.cosmos_database)
        self._container = db.get_container_client(s.cosmos_profiles_container)
        logger.info("Profile store initialized (%s)", s.cosmos_profiles_container)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()

    @property
    def enabled(self) -> bool:
        return self._container is not None

    async def get_profile(self, user_id: str) -> dict | None:
        if not self.enabled:
            return None
        from azure.cosmos.exceptions import CosmosResourceNotFoundError

        try:
            return await self._container.read_item(user_id, partition_key=user_id)
        except CosmosResourceNotFoundError:
            return None

    async def upsert_profile(
        self, user_id: str, profile_sections: dict, source_conversation: dict | None = None
    ) -> dict | None:
        if not self.enabled:
            return None
        doc = await self.get_profile(user_id) or _empty_profile(user_id)
        for section in PROFILE_SECTIONS:
            if section in profile_sections:
                doc[section] = profile_sections[section]
        doc["version"] = int(doc.get("version", 0)) + 1
        doc["updated_at"] = _now()
        if source_conversation:
            doc.setdefault("source_conversations", []).append(source_conversation)
        return await self._container.upsert_item(doc)

    async def patch_profile(self, user_id: str, updates: dict) -> dict | None:
        """Partial merge (RFC 7396) of provided sections; used by the live tool."""
        if not self.enabled:
            return None
        doc = await self.get_profile(user_id) or _empty_profile(user_id)
        for section, value in updates.items():
            if section not in PROFILE_SECTIONS or value is None:
                if value is None:
                    doc[section] = [] if section in ("interests", "habits", "facts") else {}
                continue
            if isinstance(value, dict) and isinstance(doc.get(section), dict):
                doc[section] = json_merge_patch(doc[section], value)
            else:
                doc[section] = value
        doc["version"] = int(doc.get("version", 0)) + 1
        doc["updated_at"] = _now()
        return await self._container.upsert_item(doc)

    async def delete_profile(self, user_id: str) -> bool:
        if not self.enabled:
            return False
        from azure.cosmos.exceptions import CosmosResourceNotFoundError

        try:
            await self._container.delete_item(user_id, partition_key=user_id)
            return True
        except CosmosResourceNotFoundError:
            return False


def profile_to_prompt_context(profile: dict | None) -> dict:
    """Reduce a profile doc to the fields injected into the system prompt."""
    if not profile:
        return {}
    return {k: profile.get(k) for k in PROFILE_SECTIONS if profile.get(k)}
