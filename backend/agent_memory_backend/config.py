"""Central runtime configuration read from environment variables."""
from __future__ import annotations

import os
from functools import lru_cache


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _get_bool(name: str, default: bool = False) -> bool:
    value = _get(name, "true" if default else "false").lower()
    return value in {"1", "true", "yes", "on"}


class Settings:
    # LLM / Foundry
    llm_mode = _get("LLM_MODE")  # "mock" | "real" | "" (auto)
    openai_endpoint = _get("AZURE_OPENAI_ENDPOINT")
    openai_api_version = _get("AZURE_OPENAI_API_VERSION", "2024-10-21")
    chat_deployment = _get("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-4o-mini")
    embed_deployment = _get("AZURE_OPENAI_EMBED_DEPLOYMENT", "text-embedding-3-large")
    openai_api_key = _get("AZURE_OPENAI_API_KEY")  # optional; else AAD
    foundry_project_endpoint = _get("FOUNDRY_PROJECT_ENDPOINT")
    foundry_prompt_agent_name = _get(
        "FOUNDRY_PROMPT_AGENT_NAME", "customer-support-prompt"
    )
    foundry_hosted_agent_name = _get(
        "FOUNDRY_HOSTED_AGENT_NAME", "customer-support-maf-hosted"
    )
    foundry_prompt_enabled = _get_bool("FOUNDRY_PROMPT_ENABLED")
    foundry_hosted_enabled = _get_bool("FOUNDRY_HOSTED_ENABLED")
    agent_release_id = _get("AGENT_RELEASE_ID", "dual-foundry-local")
    agent_request_timeout_seconds = float(
        _get("AGENT_REQUEST_TIMEOUT_SECONDS", "120") or "120"
    )

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
    pg_token_refresh_margin_seconds = int(
        _get("POSTGRES_TOKEN_REFRESH_MARGIN_SECONDS", "300") or "300"
    )

    # Search
    search_endpoint = _get("SEARCH_ENDPOINT")
    search_kb = _get("SEARCH_KB", "customer-support-kb")
    search_orders_knowledge_source = _get(
        "SEARCH_ORDERS_KNOWLEDGE_SOURCE", "orders-ks"
    )
    search_policy_knowledge_source = _get(
        "SEARCH_POLICY_KNOWLEDGE_SOURCE", "return-policy-ks"
    )
    search_knowledge_api_version = _get(
        "SEARCH_KNOWLEDGE_API_VERSION", "2026-05-01-preview"
    )
    search_retrieval_timeout_seconds = float(
        _get("SEARCH_RETRIEVAL_TIMEOUT_SECONDS", "30") or "30"
    )
    search_health_cache_seconds = float(
        _get("SEARCH_HEALTH_CACHE_SECONDS", "60") or "60"
    )

    # App
    app_environment = _get("APP_ENV", "local").lower()
    auth_mode = _get("AUTH_MODE", "mock")
    readiness_timeout_seconds = float(
        _get("READINESS_TIMEOUT_SECONDS", "5") or "5"
    )

    # Observability
    applicationinsights_connection_string = _get(
        "APPLICATIONINSIGHTS_CONNECTION_STRING"
    )
    otel_service_name = _get("OTEL_SERVICE_NAME", "agent-memory-backend")

    # Entra ID (auth_mode == "entra")
    entra_tenant_id = _get("ENTRA_TENANT_ID")
    entra_audience = _get("ENTRA_AUDIENCE")  # api client id or api://<id>
    entra_issuer = _get("ENTRA_ISSUER")  # optional override; else derived from tenant
    entra_jwks_uri = _get("ENTRA_JWKS_URI")  # optional override
    # space/comma-delimited lists; empty => not required
    entra_required_scopes = _get("ENTRA_REQUIRED_SCOPES")
    entra_required_roles = _get("ENTRA_REQUIRED_ROLES")
    agent_gateway_audience = _get("AGENT_GATEWAY_AUDIENCE")
    agent_gateway_required_role = _get(
        "AGENT_GATEWAY_REQUIRED_ROLE", "AgentTools.Invoke"
    )
    hosted_agent_principal_ids = tuple(
        value.strip()
        for value in _get("HOSTED_AGENT_PRINCIPAL_IDS").replace(",", " ").split()
        if value.strip()
    )
    @property
    def entra_configured(self) -> bool:
        return bool(self.entra_tenant_id and self.entra_audience)

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
    def foundry_configured(self) -> bool:
        return bool(self.foundry_project_endpoint)

    def resolve_llm_mode(self) -> str:
        if self.llm_mode in ("mock", "real"):
            return self.llm_mode
        return "real" if self.openai_endpoint else "mock"


@lru_cache
def get_settings() -> Settings:
    return Settings()
