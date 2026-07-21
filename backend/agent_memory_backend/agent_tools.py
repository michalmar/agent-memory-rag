"""Validated application tools executed under explicit trusted user context."""
from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from agent_contracts import (
    Citation,
    ToolResultEnvelope,
    lookup_order_status,
    tool_definition,
)
from .azure_clients import embed_text
from .conversation_memory import ConversationMemoryStore, MemoryStoreUnavailable
from .telemetry import span
from .user_profile_memory import UserProfileMemoryStore, profile_to_prompt_context

logger = logging.getLogger("agent_tools")


class ToolExecutionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ToolExecutor:
    def __init__(
        self,
        memory_store: ConversationMemoryStore | None,
        profile_store: UserProfileMemoryStore | None,
    ) -> None:
        self._memory_store = memory_store
        self._profile_store = profile_store

    async def execute(
        self, name: str, arguments: dict[str, Any], *, user_id: str
    ) -> dict[str, Any]:
        if not user_id:
            raise ToolExecutionError(
                "MISSING_USER_CONTEXT", "Authenticated user context is missing"
            )
        try:
            definition = tool_definition(name)
            validated = definition.validate(arguments)
        except ValidationError as exc:
            raise ToolExecutionError(
                "INVALID_TOOL_ARGUMENTS", "Tool arguments are invalid"
            ) from exc
        except ValueError as exc:
            raise ToolExecutionError("UNKNOWN_TOOL", str(exc)) from exc

        with span("agent.tool", {"agent.tool.name": name}):
            if name == "get_user_context":
                return await self._get_user_context(user_id)
            if name == "get_order_status":
                return lookup_order_status(validated["order_id"])
            if name == "check_memory":
                return await self._check_memory(user_id, validated["query"])
            if name == "update_user_profile":
                return await self._update_user_profile(user_id, validated)
        raise ToolExecutionError("UNKNOWN_TOOL", f"unknown tool: {name}")

    async def execute_envelope(
        self, name: str, arguments: dict[str, Any], *, user_id: str
    ) -> ToolResultEnvelope:
        result = await self.execute(name, arguments, user_id=user_id)
        citations = tuple(
            Citation(
                ref_id=str(item["ref_id"]),
                source_name=str(item["source_name"]),
                search_idx=(
                    int(item["search_idx"])
                    if item.get("search_idx") is not None
                    else None
                ),
                url=str(item["url"]) if item.get("url") else None,
            )
            for item in result.get("citations", [])
            if item.get("ref_id") and item.get("source_name")
        )
        return ToolResultEnvelope(status="ok", data=result, citations=citations)

    async def _get_user_context(self, user_id: str) -> dict[str, Any]:
        store = self._profile_store
        if store is None or not store.enabled:
            return {"profile": {}}
        profile = await store.get_profile(user_id)
        return {"profile": profile_to_prompt_context(profile or {})}

    async def _check_memory(self, user_id: str, query: str) -> dict[str, Any]:
        store = self._memory_store
        if store is None or not store.enabled:
            return {"memories": [], "message": "No memories available."}
        embedding = await embed_text(query)
        try:
            rows = await store.search(user_id, embedding, limit=3)
        except MemoryStoreUnavailable:
            logger.warning("Semantic memory lookup unavailable; continuing without it")
            return {"memories": [], "message": "No memories available."}
        return {
            "memories": [
                {
                    "summary": row["summary"],
                    "similarity": round(float(row.get("similarity", 0)), 3),
                }
                for row in rows
            ]
        }

    async def _update_user_profile(
        self, user_id: str, updates: dict[str, Any]
    ) -> dict[str, Any]:
        if not updates:
            raise ToolExecutionError(
                "EMPTY_PROFILE_UPDATE", "No profile fields were supplied"
            )
        store = self._profile_store
        if store is None or not store.enabled:
            raise ToolExecutionError(
                "PROFILE_STORE_UNAVAILABLE", "Profile store is unavailable"
            )
        await store.patch_profile(user_id, updates)
        return {"message": f"Profile updated: {', '.join(updates)}"}
