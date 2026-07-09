"""Central runtime configuration read from environment variables."""
from __future__ import annotations

import os
from functools import lru_cache


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


class Settings:
    # LLM / Foundry
    llm_mode = _get("LLM_MODE")  # "mock" | "real" | "" (auto)
    openai_endpoint = _get("AZURE_OPENAI_ENDPOINT")
    openai_api_version = _get("AZURE_OPENAI_API_VERSION", "2024-10-21")
    chat_deployment = _get("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-4o-mini")
    embed_deployment = _get("AZURE_OPENAI_EMBED_DEPLOYMENT", "text-embedding-3-large")
    openai_api_key = _get("AZURE_OPENAI_API_KEY")  # optional; else AAD

    # Identity
    azure_client_id = _get("AZURE_CLIENT_ID")  # user-assigned MI client id

    # Cosmos
    cosmos_endpoint = _get("COSMOS_ENDPOINT")
    cosmos_key = _get("COSMOS_KEY")
    cosmos_database = _get("COSMOS_DATABASE", "support")
    cosmos_history_container = _get("COSMOS_HISTORY_CONTAINER", "history")
    cosmos_profiles_container = _get("COSMOS_PROFILES_CONTAINER", "profiles")

    # Postgres
    pg_host = _get("POSTGRES_HOST")
    pg_db = _get("POSTGRES_DB", "memory")
    pg_user = _get("POSTGRES_USER", "pgadmin")
    pg_password = _get("POSTGRES_PASSWORD")
    pg_port = int(_get("POSTGRES_PORT", "5432") or "5432")
    pg_auth_mode = _get("PG_AUTH_MODE", "password")  # password | managed_identity

    # Search
    search_endpoint = _get("SEARCH_ENDPOINT")
    search_api_key = _get("SEARCH_API_KEY")  # optional; else AAD
    search_orders_index = _get("SEARCH_ORDERS_INDEX", "orders")
    search_policy_index = _get("SEARCH_POLICY_INDEX", "return-policy")
    search_kb = _get("SEARCH_KB", "customer-support-kb")

    # App
    rag_mode_default = _get("RAG_MODE_DEFAULT", "agentic")
    auth_mode = _get("AUTH_MODE", "mock")

    @property
    def cosmos_configured(self) -> bool:
        return bool(self.cosmos_endpoint)

    @property
    def postgres_configured(self) -> bool:
        return bool(self.pg_host)

    @property
    def search_configured(self) -> bool:
        return bool(self.search_endpoint)

    @property
    def openai_configured(self) -> bool:
        return bool(self.openai_endpoint)

    def resolve_llm_mode(self) -> str:
        if self.llm_mode in ("mock", "real"):
            return self.llm_mode
        return "real" if self.openai_endpoint else "mock"


@lru_cache
def get_settings() -> Settings:
    return Settings()
