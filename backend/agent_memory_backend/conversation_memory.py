"""Owner-partitioned semantic conversation memory in Azure Cosmos DB."""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

from azure.core.exceptions import ServiceRequestError, ServiceResponseError
from azure.cosmos.exceptions import (
    CosmosAccessConditionFailedError,
    CosmosClientTimeoutError,
    CosmosHttpResponseError,
    CosmosResourceExistsError,
    CosmosResourceNotFoundError,
)

from .config import get_settings
from .cosmos_container import CosmosContainerLifecycle
from .telemetry import span

logger = logging.getLogger("memory")

EMBEDDING_DIMENSIONS = 3072
_MEMORY_WRITE_ATTEMPTS = 3
_MAX_QUERY_LIMIT = 50
_COSMOS_AVAILABILITY_ERRORS = (
    CosmosClientTimeoutError,
    CosmosHttpResponseError,
    ServiceRequestError,
    ServiceResponseError,
)


class MemoryStoreUnavailable(RuntimeError):
    """Known semantic-memory availability failure safe for degraded operation."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def public_memory(row: dict) -> dict:
    return {
        key: row.get(key)
        for key in (
            "id",
            "conversation_id",
            "summary",
            "source_title",
            "message_count",
            "created_at",
            "updated_at",
            "similarity",
        )
        if key in row
    }


def _validated_embedding(embedding: list[float]) -> list[float]:
    if len(embedding) != EMBEDDING_DIMENSIONS:
        raise ValueError(
            f"Embedding must contain exactly {EMBEDDING_DIMENSIONS} values"
        )
    normalized: list[float] = []
    for value in embedding:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Embedding values must be finite numbers")
        normalized_value = float(value)
        if not math.isfinite(normalized_value):
            raise ValueError("Embedding values must be finite numbers")
        normalized.append(normalized_value)
    return normalized


def _validated_page(limit: int, offset: int = 0) -> tuple[int, int]:
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise ValueError("Memory query limit must be an integer")
    if not isinstance(offset, int) or isinstance(offset, bool):
        raise ValueError("Memory query offset must be an integer")
    if not 1 <= limit <= _MAX_QUERY_LIMIT:
        raise ValueError(f"Memory query limit must be between 1 and {_MAX_QUERY_LIMIT}")
    if offset < 0:
        raise ValueError("Memory query offset cannot be negative")
    return limit, offset


def _unavailable(exc: Exception) -> MemoryStoreUnavailable:
    return MemoryStoreUnavailable("Cosmos semantic memory is unavailable")


class ConversationMemoryStore(CosmosContainerLifecycle):
    def __init__(self) -> None:
        super().__init__()

    async def initialize(self) -> None:
        settings = get_settings()
        if not settings.cosmos_configured:
            logger.warning("Cosmos not configured; semantic memory store disabled")
            return

        await self._initialize_container(
            settings,
            settings.cosmos_memory_container,
        )
        logger.info(
            "Semantic memory store initialized (%s)",
            settings.cosmos_memory_container,
        )

    def _require_container(self) -> Any:
        if self._container is None:
            raise MemoryStoreUnavailable(
                "Cosmos semantic memory is not initialized"
            )
        return self._container

    async def health_check(self) -> None:
        container = self._require_container()
        try:
            await container.read()
        except _COSMOS_AVAILABILITY_ERRORS as exc:
            raise _unavailable(exc) from exc

    async def _read_memory(
        self, conversation_id: str, user_id: str
    ) -> dict[str, Any] | None:
        container = self._require_container()
        try:
            document = await container.read_item(
                conversation_id, partition_key=user_id
            )
        except CosmosResourceNotFoundError:
            return None
        except _COSMOS_AVAILABILITY_ERRORS as exc:
            raise _unavailable(exc) from exc
        if document.get("user_id") != user_id:
            raise RuntimeError("Semantic memory isolation check failed")
        return document

    async def create_memory(
        self,
        conversation_id: str,
        user_id: str,
        summary: str,
        embedding: list[float],
        source_title: str | None = None,
        message_count: int = 0,
    ) -> dict:
        from azure.core import MatchConditions

        container = self._require_container()
        vector = _validated_embedding(embedding)
        for attempt in range(_MEMORY_WRITE_ATTEMPTS):
            existing = await self._read_memory(conversation_id, user_id)
            now = _now()
            document = {
                "id": conversation_id,
                "user_id": user_id,
                "conversation_id": conversation_id,
                "summary": summary,
                "source_title": source_title,
                "message_count": message_count,
                "embedding": vector,
                "created_at": (
                    existing.get("created_at", now) if existing is not None else now
                ),
                "updated_at": now,
            }
            try:
                with span(
                    "store.cosmos.memory.upsert",
                    {"db.system": "cosmosdb"},
                ):
                    if existing is None:
                        result = await container.create_item(document)
                    else:
                        result = await container.replace_item(
                            item=conversation_id,
                            body=document,
                            etag=existing.get("_etag"),
                            match_condition=MatchConditions.IfNotModified,
                        )
                if result.get("user_id") != user_id:
                    raise RuntimeError("Semantic memory isolation check failed")
                return result
            except (
                CosmosAccessConditionFailedError,
                CosmosResourceExistsError,
            ) as exc:
                if attempt == _MEMORY_WRITE_ATTEMPTS - 1:
                    raise MemoryStoreUnavailable(
                        "Cosmos semantic memory write contention did not resolve"
                    ) from exc
            except _COSMOS_AVAILABILITY_ERRORS as exc:
                raise _unavailable(exc) from exc
        raise RuntimeError("Semantic memory write retry limit exceeded")

    async def list_memories(
        self, user_id: str, limit: int = 50, offset: int = 0
    ) -> list[dict]:
        limit, offset = _validated_page(limit, offset)
        container = self._require_container()
        query = (
            "SELECT c.id, c.user_id, c.conversation_id, c.summary, "
            "c.source_title, c.message_count, c.created_at, c.updated_at "
            "FROM c WHERE c.user_id=@uid "
            "ORDER BY c.created_at DESC OFFSET @offset LIMIT @limit"
        )
        parameters = [
            {"name": "@uid", "value": user_id},
            {"name": "@offset", "value": offset},
            {"name": "@limit", "value": limit},
        ]
        rows: list[dict] = []
        try:
            async for item in container.query_items(
                query=query,
                parameters=parameters,
                partition_key=user_id,
            ):
                if item.get("user_id") != user_id:
                    raise RuntimeError("Semantic memory isolation check failed")
                rows.append(item)
        except _COSMOS_AVAILABILITY_ERRORS as exc:
            raise _unavailable(exc) from exc
        return rows

    async def search(
        self, user_id: str, query_embedding: list[float], limit: int = 3
    ) -> list[dict]:
        limit, _ = _validated_page(limit)
        vector = _validated_embedding(query_embedding)
        container = self._require_container()
        query = (
            "SELECT TOP @limit c.id, c.user_id, c.conversation_id, c.summary, "
            "c.source_title, c.message_count, c.created_at, c.updated_at, "
            "VectorDistance(c.embedding, @embedding) AS distance "
            "FROM c WHERE c.user_id=@uid "
            "ORDER BY VectorDistance(c.embedding, @embedding)"
        )
        parameters = [
            {"name": "@limit", "value": limit},
            {"name": "@uid", "value": user_id},
            {"name": "@embedding", "value": vector},
        ]
        rows: list[dict] = []
        try:
            with span(
                "store.cosmos.memory.vector_search",
                {"db.system": "cosmosdb"},
            ):
                async for item in container.query_items(
                    query=query,
                    parameters=parameters,
                    partition_key=user_id,
                ):
                    if item.get("user_id") != user_id:
                        raise RuntimeError("Semantic memory isolation check failed")
                    distance = float(item.pop("distance"))
                    item["similarity"] = 1.0 - distance
                    rows.append(item)
        except _COSMOS_AVAILABILITY_ERRORS as exc:
            raise _unavailable(exc) from exc
        return rows

    async def delete_memory(self, memory_id: str, user_id: str) -> bool:
        container = self._require_container()
        try:
            await container.delete_item(memory_id, partition_key=user_id)
            return True
        except CosmosResourceNotFoundError:
            return False
        except _COSMOS_AVAILABILITY_ERRORS as exc:
            raise _unavailable(exc) from exc

    async def delete_by_conversation(
        self, conversation_id: str, user_id: str
    ) -> None:
        container = self._require_container()
        try:
            await container.delete_item(conversation_id, partition_key=user_id)
        except CosmosResourceNotFoundError:
            return
        except _COSMOS_AVAILABILITY_ERRORS as exc:
            raise _unavailable(exc) from exc
