"""ConversationMemoryStore — async CRUD + cosine search over Postgres/pgvector (§F3).

vector(3072) exceeds pgvector's 2000-dim ANN limit, so search uses an exact
full-scan cosine ordering (no HNSW index). Table + extension are auto-created.
"""
from __future__ import annotations

import logging
from typing import Any

from config import get_settings

logger = logging.getLogger("memory")

_CREATE_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS conversation_memory (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  summary TEXT NOT NULL,
  embedding vector(3072) NOT NULL,
  source_title TEXT,
  message_count INT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_conversation_memory UNIQUE (conversation_id, user_id)
);
"""


def _vec_literal(embedding: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"


class ConversationMemoryStore:
    def __init__(self) -> None:
        self._pool = None

    async def initialize(self) -> None:
        s = get_settings()
        if not s.postgres_configured:
            logger.warning("Postgres not configured; memory store disabled")
            return
        import asyncpg

        if s.pg_auth_mode == "managed_identity":
            from azure_clients import get_credential

            token = await get_credential().get_token(
                "https://ossrdbms-aad.database.windows.net/.default"
            )
            password = token.token
            ssl = "require"
        else:
            password = s.pg_password
            ssl = "prefer"

        self._pool = await asyncpg.create_pool(
            host=s.pg_host,
            port=s.pg_port,
            database=s.pg_db,
            user=s.pg_user,
            password=password,
            ssl=ssl,
            min_size=2,
            max_size=10,
        )
        async with self._pool.acquire() as conn:
            await conn.execute(_CREATE_SQL)
        logger.info("Memory store initialized (pgvector)")

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()

    @property
    def enabled(self) -> bool:
        return self._pool is not None

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
        vec = _vec_literal(embedding)
        row = await self._pool.fetchrow(
            """
            INSERT INTO conversation_memory
              (conversation_id, user_id, summary, embedding, source_title, message_count)
            VALUES ($1,$2,$3,$4::vector,$5,$6)
            ON CONFLICT (conversation_id, user_id) DO UPDATE SET
              summary=EXCLUDED.summary, embedding=EXCLUDED.embedding,
              source_title=EXCLUDED.source_title, message_count=EXCLUDED.message_count,
              updated_at=now()
            RETURNING id, conversation_id, user_id, summary, source_title,
                      message_count, created_at, updated_at
            """,
            conversation_id, user_id, summary, vec, source_title, message_count,
        )
        return dict(row) if row else None

    async def get_memory(self, memory_id: str, user_id: str) -> dict | None:
        if not self.enabled:
            return None
        row = await self._pool.fetchrow(
            "SELECT id, conversation_id, user_id, summary, source_title, message_count, "
            "created_at, updated_at FROM conversation_memory WHERE id=$1 AND user_id=$2",
            memory_id, user_id,
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
            user_id, offset, limit,
        )
        return [dict(r) for r in rows]

    async def search(
        self, user_id: str, query_embedding: list[float], limit: int = 3
    ) -> list[dict]:
        if not self.enabled:
            return []
        vec = _vec_literal(query_embedding)
        rows = await self._pool.fetch(
            """
            SELECT id, conversation_id, user_id, summary, source_title, message_count,
                   created_at, 1 - (embedding <=> $1::vector) AS similarity
            FROM conversation_memory
            WHERE user_id=$2
            ORDER BY embedding <=> $1::vector
            LIMIT $3
            """,
            vec, user_id, limit,
        )
        return [dict(r) for r in rows]

    async def delete_memory(self, memory_id: str, user_id: str) -> bool:
        if not self.enabled:
            return False
        result = await self._pool.execute(
            "DELETE FROM conversation_memory WHERE id=$1 AND user_id=$2",
            memory_id, user_id,
        )
        return result.endswith("1")

    async def delete_by_conversation(self, conversation_id: str, user_id: str) -> None:
        if not self.enabled:
            return
        await self._pool.execute(
            "DELETE FROM conversation_memory WHERE conversation_id=$1 AND user_id=$2",
            conversation_id, user_id,
        )
