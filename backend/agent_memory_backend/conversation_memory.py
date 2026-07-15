"""Async PostgreSQL/pgvector conversation memory with passwordless Azure auth."""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from typing import Any

from .config import get_settings
from .telemetry import span

logger = logging.getLogger("memory")


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
_POSTGRES_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"


def _vec_literal(embedding: list[float]) -> str:
    return "[" + ",".join(repr(float(value)) for value in embedding) + "]"


class AzurePostgresTokenCache:
    """Keep a nonblocking access token available for asyncpg's password callback."""

    def __init__(
        self,
        credential: Any,
        *,
        refresh_margin_seconds: int = 300,
        retry_seconds: int = 30,
    ) -> None:
        self._credential = credential
        self._refresh_margin_seconds = refresh_margin_seconds
        self._retry_seconds = retry_seconds
        self._token = ""
        self._expires_on = 0
        self._refresh_task: asyncio.Task[None] | None = None
        self._closed = asyncio.Event()

    async def start(self) -> None:
        await self._refresh()
        self._refresh_task = asyncio.create_task(
            self._refresh_loop(), name="postgres-token-refresh"
        )

    def password(self) -> str:
        """Return the cached token without network I/O or event-loop blocking."""
        if not self._token or self._expires_on <= int(time.time()):
            raise RuntimeError("PostgreSQL managed identity token is unavailable or expired")
        return self._token

    async def close(self) -> None:
        self._closed.set()
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._refresh_task
            self._refresh_task = None

    async def _refresh(self) -> None:
        access_token = await self._credential.get_token(_POSTGRES_SCOPE)
        self._token = access_token.token
        self._expires_on = access_token.expires_on

    async def _refresh_loop(self) -> None:
        while not self._closed.is_set():
            refresh_at = self._expires_on - self._refresh_margin_seconds
            delay = max(1, refresh_at - int(time.time()))
            try:
                await asyncio.wait_for(self._closed.wait(), timeout=delay)
                return
            except TimeoutError:
                pass

            try:
                await self._refresh()
                logger.info("PostgreSQL managed identity token refreshed")
            except Exception:
                logger.exception("PostgreSQL token refresh failed; retrying")
                try:
                    await asyncio.wait_for(
                        self._closed.wait(), timeout=self._retry_seconds
                    )
                    return
                except TimeoutError:
                    pass


class ConversationMemoryStore:
    def __init__(self) -> None:
        self._pool: Any = None
        self._token_cache: AzurePostgresTokenCache | None = None

    async def initialize(self) -> None:
        settings = get_settings()
        if not settings.postgres_configured:
            logger.warning("Postgres not configured; memory store disabled")
            return

        import asyncpg

        password: str | Any
        ssl: str
        if settings.pg_auth_mode == "managed_identity":
            from .azure_clients import get_credential

            self._token_cache = AzurePostgresTokenCache(
                get_credential(),
                refresh_margin_seconds=settings.pg_token_refresh_margin_seconds,
            )
            await self._token_cache.start()
            password = self._token_cache.password
            ssl = "require"
        elif settings.pg_auth_mode == "password":
            if not settings.pg_password:
                raise RuntimeError("POSTGRES_PASSWORD is required for password auth")
            password = settings.pg_password
            ssl = "prefer"
        else:
            raise RuntimeError(f"Unsupported PG_AUTH_MODE: {settings.pg_auth_mode}")

        try:
            self._pool = await asyncpg.create_pool(
                host=settings.pg_host,
                port=settings.pg_port,
                database=settings.pg_db,
                user=settings.pg_user,
                password=password,
                ssl=ssl,
                min_size=2,
                max_size=10,
            )
            async with self._pool.acquire() as connection:
                table_name = await connection.fetchval(
                    "SELECT to_regclass('public.conversation_memory')"
                )
            if table_name is None:
                raise RuntimeError(
                    "conversation_memory schema is missing; run the bootstrap job"
                )
        except Exception:
            await self.close()
            raise
        logger.info("Memory store initialized (pgvector)")

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
        if self._token_cache is not None:
            await self._token_cache.close()
            self._token_cache = None

    @property
    def enabled(self) -> bool:
        return self._pool is not None

    async def health_check(self) -> None:
        if self._pool is None:
            raise RuntimeError("PostgreSQL pool is not initialized")
        value = await self._pool.fetchval("SELECT 1")
        if value != 1:
            raise RuntimeError("PostgreSQL readiness query returned an invalid result")

    async def create_memory(
        self,
        conversation_id: str,
        user_id: str,
        summary: str,
        embedding: list[float],
        source_title: str | None = None,
        message_count: int = 0,
    ) -> dict | None:
        if not self.enabled:
            return None
        vector = _vec_literal(embedding)
        with span("store.postgres.upsert", {"db.system": "postgresql"}):
            row = await self._pool.fetchrow(
                """
                INSERT INTO conversation_memory
                  (conversation_id, user_id, summary, embedding, source_title, message_count)
                VALUES ($1,$2,$3,$4::vector,$5,$6)
                ON CONFLICT (conversation_id, user_id) DO UPDATE SET
                  summary=EXCLUDED.summary, embedding=EXCLUDED.embedding,
                  source_title=EXCLUDED.source_title,
                  message_count=EXCLUDED.message_count, updated_at=now()
                RETURNING id, conversation_id, user_id, summary, source_title,
                          message_count, created_at, updated_at
                """,
                conversation_id,
                user_id,
                summary,
                vector,
                source_title,
                message_count,
            )
        return dict(row) if row else None

    async def list_memories(
        self, user_id: str, limit: int = 50, offset: int = 0
    ) -> list[dict]:
        if not self.enabled:
            return []
        rows = await self._pool.fetch(
            "SELECT id, conversation_id, user_id, summary, source_title, message_count, "
            "created_at, updated_at FROM conversation_memory WHERE user_id=$1 "
            "ORDER BY created_at DESC OFFSET $2 LIMIT $3",
            user_id,
            offset,
            limit,
        )
        return [dict(row) for row in rows]

    async def search(
        self, user_id: str, query_embedding: list[float], limit: int = 3
    ) -> list[dict]:
        if not self.enabled:
            return []
        vector = _vec_literal(query_embedding)
        with span("store.postgres.vector_search", {"db.system": "postgresql"}):
            rows = await self._pool.fetch(
                """
                SELECT id, conversation_id, user_id, summary, source_title, message_count,
                       created_at, 1 - (embedding <=> $1::vector) AS similarity
                FROM conversation_memory
                WHERE user_id=$2
                ORDER BY embedding <=> $1::vector
                LIMIT $3
                """,
                vector,
                user_id,
                limit,
            )
        return [dict(row) for row in rows]

    async def delete_memory(self, memory_id: str, user_id: str) -> bool:
        if not self.enabled:
            return False
        result = await self._pool.execute(
            "DELETE FROM conversation_memory WHERE id=$1 AND user_id=$2",
            memory_id,
            user_id,
        )
        return result.endswith("1")

    async def delete_by_conversation(
        self, conversation_id: str, user_id: str
    ) -> None:
        if not self.enabled:
            return
        await self._pool.execute(
            "DELETE FROM conversation_memory WHERE conversation_id=$1 AND user_id=$2",
            conversation_id,
            user_id,
        )
