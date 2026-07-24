"""UserProfileMemoryStore — Cosmos CRUD, one profile doc per user (PRD §F4).

Implements RFC 7396 JSON Merge Patch for the live update_user_profile tool.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable

from .config import get_settings
from .cosmos_container import CosmosContainerLifecycle

logger = logging.getLogger("profile")

PROFILE_SECTIONS = ("basic_info", "interests", "habits", "preferences", "status", "facts")
_PROFILE_WRITE_ATTEMPTS = 3


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


def public_profile(document: dict) -> dict:
    return {
        key: document.get(key)
        for key in (
            "version",
            *PROFILE_SECTIONS,
            "updated_at",
        )
    }


class UserProfileMemoryStore(CosmosContainerLifecycle):
    def __init__(self) -> None:
        super().__init__()

    async def initialize(self) -> None:
        s = get_settings()
        if not s.cosmos_configured:
            logger.warning("Cosmos not configured; profile store disabled")
            return
        await self._initialize_container(s, s.cosmos_profiles_container)
        logger.info("Profile store initialized (%s)", s.cosmos_profiles_container)

    async def health_check(self) -> None:
        container = self._require_initialized_container(
            "Cosmos profile container is not initialized"
        )
        await container.read()

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

        def update(doc: dict) -> None:
            for section in PROFILE_SECTIONS:
                if section in profile_sections:
                    doc[section] = profile_sections[section]
            if source_conversation:
                doc.setdefault("source_conversations", []).append(
                    source_conversation
                )

        return await self._write_profile(user_id, update)

    async def patch_profile(self, user_id: str, updates: dict) -> dict | None:
        """Partial merge (RFC 7396) of provided sections; used by the live tool."""
        if not self.enabled:
            return None

        def update(doc: dict) -> None:
            for section, value in updates.items():
                if section not in PROFILE_SECTIONS:
                    continue
                if value is None:
                    doc[section] = (
                        [] if section in ("interests", "habits", "facts") else {}
                    )
                elif isinstance(value, dict) and isinstance(doc.get(section), dict):
                    doc[section] = json_merge_patch(doc[section], value)
                else:
                    doc[section] = value

        return await self._write_profile(user_id, update)

    async def _write_profile(
        self, user_id: str, update: Callable[[dict], None]
    ) -> dict:
        from azure.core import MatchConditions
        from azure.cosmos.exceptions import (
            CosmosAccessConditionFailedError,
            CosmosResourceExistsError,
        )

        for attempt in range(_PROFILE_WRITE_ATTEMPTS):
            existing = await self.get_profile(user_id)
            doc = dict(existing) if existing else _empty_profile(user_id)
            update(doc)
            doc["version"] = int(doc.get("version", 0)) + 1
            doc["updated_at"] = _now()
            try:
                if existing is None:
                    return await self._container.create_item(doc)
                return await self._container.replace_item(
                    item=user_id,
                    body=doc,
                    etag=existing["_etag"],
                    match_condition=MatchConditions.IfNotModified,
                )
            except (
                CosmosAccessConditionFailedError,
                CosmosResourceExistsError,
            ):
                if attempt == _PROFILE_WRITE_ATTEMPTS - 1:
                    raise
        raise RuntimeError("Profile write retry limit exceeded")

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
    """Reduce a profile doc to the fields injected into the system prompt.

    Returns {} when the profile is empty so the template skips the block. When any
    section is populated, ALL section keys are included (empty defaults) so the
    StrictUndefined template can safely reference every section.
    """
    if not profile:
        return {}
    if not any(profile.get(k) for k in PROFILE_SECTIONS):
        return {}
    return {k: profile.get(k) for k in PROFILE_SECTIONS}
