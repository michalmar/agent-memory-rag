"""ConversationHistoryStore — async CRUD over a Cosmos NoSQL container (PRD §F2).

Auth: COSMOS_KEY if present, else DefaultAzureCredential (AAD data-plane).
Partition key /user_id; document id = session_id. All failures during turn
persistence are logged and swallowed so the chat stream is never broken.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from config import get_settings

logger = logging.getLogger("history")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConversationHistoryStore:
    def __init__(self) -> None:
        self._client = None
        self._container = None

    async def initialize(self) -> None:
        s = get_settings()
        if not s.cosmos_configured:
            logger.warning("Cosmos not configured; history store disabled")
            return
        from azure.cosmos.aio import CosmosClient

        if s.cosmos_key:
            self._client = CosmosClient(s.cosmos_endpoint, credential=s.cosmos_key)
        else:
            from azure_clients import get_credential

            self._client = CosmosClient(s.cosmos_endpoint, credential=get_credential())
        db = self._client.get_database_client(s.cosmos_database)
        self._container = db.get_container_client(s.cosmos_history_container)
        logger.info("History store initialized (%s)", s.cosmos_history_container)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()

    @property
    def enabled(self) -> bool:
        return self._container is not None

    async def get_conversation(self, session_id: str, user_id: str) -> dict | None:
        if not self.enabled:
            return None
        from azure.cosmos.exceptions import CosmosResourceNotFoundError

        try:
            return await self._container.read_item(session_id, partition_key=user_id)
        except CosmosResourceNotFoundError:
            return None

    async def save_conversation(
        self,
        session_id: str,
        user_id: str,
        messages: list[dict],
        title: str | None = None,
        metadata: dict | None = None,
    ) -> dict | None:
        if not self.enabled:
            return None
        existing = await self.get_conversation(session_id, user_id)
        now = _now()
        doc = {
            "id": session_id,
            "user_id": user_id,
            "title": title or (existing or {}).get("title"),
            "created_at": (existing or {}).get("created_at", now),
            "updated_at": now,
            "message_count": len(messages),
            "messages": messages,
            "metadata": {**((existing or {}).get("metadata") or {}), **(metadata or {})},
        }
        return await self._container.upsert_item(doc)

    async def _persist_turn(
        self,
        session_id: str,
        user_id: str,
        user_message: str,
        assistant_message: str,
        title: str | None = None,
        rag_mode: str | None = None,
    ) -> None:
        """Append a user+assistant turn to the stored conversation. Never raises."""
        if not self.enabled:
            return
        try:
            existing = await self.get_conversation(session_id, user_id)
            messages = list((existing or {}).get("messages") or [])
            messages.append({"role": "user", "content": user_message})
            messages.append({"role": "assistant", "content": assistant_message})
            meta = {"agent_name": "CustomerSupportAgent", "api": "responses"}
            if rag_mode:
                meta["rag_mode"] = rag_mode
            await self.save_conversation(
                session_id, user_id, messages, title=title, metadata=meta
            )
        except Exception:  # noqa: BLE001
            logger.exception("history persist failed (session=%s)", session_id)

    async def list_conversations(
        self, user_id: str, limit: int = 50, offset: int = 0
    ) -> list[dict]:
        if not self.enabled:
            return []
        query = (
            "SELECT c.id, c.user_id, c.title, c.created_at, c.updated_at, "
            "c.message_count FROM c WHERE c.user_id=@uid "
            "ORDER BY c.updated_at DESC OFFSET @off LIMIT @lim"
        )
        params = [
            {"name": "@uid", "value": user_id},
            {"name": "@off", "value": offset},
            {"name": "@lim", "value": limit},
        ]
        items: list[dict] = []
        async for it in self._container.query_items(query=query, parameters=params):
            items.append(it)
        return items

    async def update_title(self, session_id: str, user_id: str, title: str) -> dict | None:
        doc = await self.get_conversation(session_id, user_id)
        if not doc:
            return None
        doc["title"] = title
        doc["updated_at"] = _now()
        return await self._container.upsert_item(doc)

    async def delete_conversation(self, session_id: str, user_id: str) -> bool:
        if not self.enabled:
            return False
        from azure.cosmos.exceptions import CosmosResourceNotFoundError

        try:
            await self._container.delete_item(session_id, partition_key=user_id)
        except CosmosResourceNotFoundError:
            return False
        # Emulator workaround: verify with a point-read (PRD §F2).
        return await self.get_conversation(session_id, user_id) is None
