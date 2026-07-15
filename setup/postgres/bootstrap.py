"""Idempotent PostgreSQL schema and managed-identity principal bootstrap."""
from __future__ import annotations

import asyncio
import os
from typing import Any

import asyncpg
from azure.identity.aio import DefaultAzureCredential, ManagedIdentityCredential

POSTGRES_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"

SCHEMA_SQL = """
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


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _credential() -> DefaultAzureCredential | ManagedIdentityCredential:
    client_id = os.environ.get("AZURE_CLIENT_ID", "").strip()
    if client_id:
        return ManagedIdentityCredential(client_id=client_id)
    return DefaultAzureCredential()


async def _connect(
    credential: Any, *, database: str, user: str
) -> asyncpg.Connection:
    token = await credential.get_token(POSTGRES_SCOPE)
    return await asyncpg.connect(
        host=_required("POSTGRES_HOST"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        database=database,
        user=user,
        password=token.token,
        ssl="require",
    )


async def _create_application_principal(
    credential: Any, *, admin_user: str, app_user: str, app_object_id: str
) -> None:
    connection = await _connect(credential, database="postgres", user=admin_user)
    try:
        role_exists = await connection.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1)", app_user
        )
        if not role_exists:
            await connection.fetchval(
                "SELECT * FROM pg_catalog.pgaadauth_create_principal_with_oid("
                "$1, $2, 'service', false, false)",
                app_user,
                app_object_id,
            )
            print(f"[bootstrap] created application principal: {app_user}")
        else:
            print(f"[bootstrap] application principal already exists: {app_user}")
    finally:
        await connection.close()


async def _create_schema_and_grants(
    credential: Any, *, admin_user: str, app_user: str, database: str
) -> None:
    connection = await _connect(credential, database=database, user=admin_user)
    quoted_role = _quote_identifier(app_user)
    try:
        async with connection.transaction():
            await connection.execute(SCHEMA_SQL)
            await connection.execute(f"GRANT CONNECT ON DATABASE {_quote_identifier(database)} TO {quoted_role}")
            await connection.execute(f"GRANT USAGE ON SCHEMA public TO {quoted_role}")
            await connection.execute(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "
                f"conversation_memory TO {quoted_role}"
            )
            await connection.execute(
                f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {quoted_role}"
            )
            await connection.execute(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, "
                f"UPDATE, DELETE ON TABLES TO {quoted_role}"
            )
            await connection.execute(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT "
                f"ON SEQUENCES TO {quoted_role}"
            )
        print("[bootstrap] schema and least-privilege grants ready")
    finally:
        await connection.close()


async def main() -> None:
    admin_user = _required("POSTGRES_USER")
    app_user = _required("POSTGRES_APP_USER")
    app_object_id = _required("POSTGRES_APP_OBJECT_ID")
    database = os.environ.get("POSTGRES_DB", "memory").strip() or "memory"
    credential = _credential()
    try:
        await _create_application_principal(
            credential,
            admin_user=admin_user,
            app_user=app_user,
            app_object_id=app_object_id,
        )
        await _create_schema_and_grants(
            credential,
            admin_user=admin_user,
            app_user=app_user,
            database=database,
        )
    finally:
        await credential.close()


if __name__ == "__main__":
    asyncio.run(main())
