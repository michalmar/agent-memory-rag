"""Central runtime configuration read from environment variables."""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import quote


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _get_bool(name: str, default: bool = False) -> bool:
    value = _get(name, "true" if default else "false").lower()
    return value in {"1", "true", "yes", "on"}


def _get_int(
    name: str,
    default: int,
    *,
    minimum: int = 1,
    maximum: int | None = None,
) -> int:
    value = int(_get(name, str(default)) or str(default))
    if value < minimum or maximum is not None and value > maximum:
        expected = (
            f"{minimum}..{maximum}" if maximum is not None else f">= {minimum}"
        )
        raise ValueError(f"{name} must be {expected}")
    return value


def _get_float(
    name: str,
    default: float,
    *,
    minimum: float = 0.0,
    maximum: float | None = None,
) -> float:
    value = float(_get(name, str(default)) or str(default))
    if (
        not math.isfinite(value)
        or value <= minimum
        or maximum is not None
        and value > maximum
    ):
        expected = (
            f"> {minimum} and <= {maximum}"
            if maximum is not None
            else f"> {minimum}"
        )
        raise ValueError(f"{name} must be {expected}")
    return value


def _hosted_openai_endpoint(
    project_endpoint: str,
    agent_name: str,
    configured_endpoint: str,
) -> str:
    if configured_endpoint:
        return configured_endpoint.rstrip("/")
    if not project_endpoint or not agent_name:
        return ""
    encoded_name = quote(agent_name, safe="")
    return (
        f"{project_endpoint.rstrip('/')}/agents/{encoded_name}"
        "/endpoint/protocols/openai"
    )


@dataclass(frozen=True)
class Settings:
    llm_mode: str
    openai_endpoint: str
    openai_api_version: str
    chat_deployment: str
    embed_deployment: str
    openai_api_key: str
    foundry_project_endpoint: str
    foundry_prompt_agent_name: str
    foundry_hosted_agent_name: str
    foundry_hosted_agent_endpoint: str
    foundry_prompt_enabled: bool
    foundry_hosted_enabled: bool
    agent_release_id: str
    agent_request_timeout_seconds: float
    directive_foundry_agent_name: str
    directive_foundry_agent_endpoint: str
    directive_agent_enabled: bool
    directive_agent_visible: bool
    directive_agent_release_id: str
    directive_model_deployment: str
    directive_search_kb: str
    directive_search_knowledge_source: str
    directive_search_api_version: str
    directive_cosmos_database: str
    directive_catalog_container: str
    directive_mandates_container: str
    directive_blob_endpoint: str
    directive_blob_container: str
    directive_max_content_tokens: int
    directive_max_sections_per_call: int
    directive_max_search_results: int
    directive_max_related_depth: int
    directive_tool_timeout_seconds: float
    directive_progress_heartbeat_seconds: float
    azure_client_id: str
    cosmos_endpoint: str
    cosmos_key: str
    cosmos_database: str
    cosmos_history_container: str
    cosmos_profiles_container: str
    cosmos_memory_container: str
    search_endpoint: str
    search_kb: str
    search_orders_knowledge_source: str
    search_policy_knowledge_source: str
    search_knowledge_api_version: str
    search_retrieval_timeout_seconds: float
    search_health_cache_seconds: float
    app_environment: str
    auth_mode: str
    readiness_timeout_seconds: float
    applicationinsights_connection_string: str
    otel_service_name: str
    entra_tenant_id: str
    entra_audience: str
    entra_issuer: str
    entra_jwks_uri: str
    entra_required_scopes: str
    entra_required_roles: str
    agent_gateway_audience: str
    agent_gateway_required_role: str
    hosted_agent_principal_ids: tuple[str, ...]
    support_hosted_agent_principal_ids: tuple[str, ...]
    directive_hosted_agent_principal_ids: tuple[str, ...]

    @classmethod
    def from_environment(cls) -> Settings:
        project_endpoint = _get("FOUNDRY_PROJECT_ENDPOINT")
        hosted_agent_name = _get(
            "FOUNDRY_HOSTED_AGENT_NAME", "customer-support-maf-hosted"
        )
        directive_agent_name = _get(
            "DIRECTIVE_FOUNDRY_AGENT_NAME", "directive-rag-maf-hosted"
        )
        legacy_principals = _principal_ids("HOSTED_AGENT_PRINCIPAL_IDS")
        support_principals = _principal_ids(
            "SUPPORT_HOSTED_AGENT_PRINCIPAL_IDS"
        ) or legacy_principals
        directive_principals = _principal_ids(
            "DIRECTIVE_HOSTED_AGENT_PRINCIPAL_IDS"
        )
        return cls(
            llm_mode=_get("LLM_MODE"),
            openai_endpoint=_get("AZURE_OPENAI_ENDPOINT"),
            openai_api_version=_get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
            chat_deployment=_get(
                "AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-4o-mini"
            ),
            embed_deployment=_get(
                "AZURE_OPENAI_EMBED_DEPLOYMENT", "text-embedding-3-large"
            ),
            openai_api_key=_get("AZURE_OPENAI_API_KEY"),
            foundry_project_endpoint=project_endpoint,
            foundry_prompt_agent_name=_get(
                "FOUNDRY_PROMPT_AGENT_NAME", "customer-support-prompt"
            ),
            foundry_hosted_agent_name=hosted_agent_name,
            foundry_hosted_agent_endpoint=_hosted_openai_endpoint(
                project_endpoint,
                hosted_agent_name,
                _get("FOUNDRY_HOSTED_AGENT_ENDPOINT"),
            ),
            foundry_prompt_enabled=_get_bool("FOUNDRY_PROMPT_ENABLED"),
            foundry_hosted_enabled=_get_bool("FOUNDRY_HOSTED_ENABLED"),
            agent_release_id=_get("AGENT_RELEASE_ID", "dual-foundry-local"),
            agent_request_timeout_seconds=float(
                _get("AGENT_REQUEST_TIMEOUT_SECONDS", "120") or "120"
            ),
            directive_foundry_agent_name=directive_agent_name,
            directive_foundry_agent_endpoint=_hosted_openai_endpoint(
                project_endpoint,
                directive_agent_name,
                _get("DIRECTIVE_FOUNDRY_AGENT_ENDPOINT"),
            ),
            directive_agent_enabled=_get_bool("DIRECTIVE_AGENT_ENABLED"),
            directive_agent_visible=_get_bool("DIRECTIVE_AGENT_VISIBLE"),
            directive_agent_release_id=_get(
                "DIRECTIVE_AGENT_RELEASE_ID", "directive-rag-local"
            ),
            directive_model_deployment=_get(
                "DIRECTIVE_MODEL_DEPLOYMENT", "gpt-5.6-sol"
            ),
            directive_search_kb=_get(
                "DIRECTIVE_SEARCH_KB", "directive-kb-v1"
            ),
            directive_search_knowledge_source=_get(
                "DIRECTIVE_SEARCH_KNOWLEDGE_SOURCE", "directive-chunks-ks-v1"
            ),
            directive_search_api_version=_get(
                "DIRECTIVE_SEARCH_API_VERSION", "2026-04-01"
            ),
            directive_cosmos_database=_get(
                "DIRECTIVE_COSMOS_DATABASE", "directives"
            ),
            directive_catalog_container=_get(
                "DIRECTIVE_CATALOG_CONTAINER", "catalog"
            ),
            directive_mandates_container=_get(
                "DIRECTIVE_MANDATES_CONTAINER", "user_mandates"
            ),
            directive_blob_endpoint=_get("DIRECTIVE_BLOB_ENDPOINT"),
            directive_blob_container=_get(
                "DIRECTIVE_BLOB_CONTAINER", "directive-artifacts"
            ),
            directive_max_content_tokens=_get_int(
                "DIRECTIVE_MAX_CONTENT_TOKENS",
                750_000,
                maximum=900_000,
            ),
            directive_max_sections_per_call=_get_int(
                "DIRECTIVE_MAX_SECTIONS_PER_CALL",
                20,
                maximum=100,
            ),
            directive_max_search_results=_get_int(
                "DIRECTIVE_MAX_SEARCH_RESULTS",
                25,
                maximum=100,
            ),
            directive_max_related_depth=_get_int(
                "DIRECTIVE_MAX_RELATED_DEPTH", 2, maximum=2
            ),
            directive_tool_timeout_seconds=_get_float(
                "DIRECTIVE_TOOL_TIMEOUT_SECONDS",
                120,
                maximum=600,
            ),
            directive_progress_heartbeat_seconds=_get_float(
                "DIRECTIVE_PROGRESS_HEARTBEAT_SECONDS",
                10,
                maximum=60,
            ),
            azure_client_id=_get("AZURE_CLIENT_ID"),
            cosmos_endpoint=_get("COSMOS_ENDPOINT"),
            cosmos_key=_get("COSMOS_KEY"),
            cosmos_database=_get("COSMOS_DATABASE", "support"),
            cosmos_history_container=_get(
                "COSMOS_HISTORY_CONTAINER", "history"
            ),
            cosmos_profiles_container=_get(
                "COSMOS_PROFILES_CONTAINER", "profiles"
            ),
            cosmos_memory_container=_get(
                "COSMOS_MEMORY_CONTAINER", "memories"
            ),
            search_endpoint=_get("SEARCH_ENDPOINT"),
            search_kb=_get("SEARCH_KB", "customer-support-kb"),
            search_orders_knowledge_source=_get(
                "SEARCH_ORDERS_KNOWLEDGE_SOURCE", "orders-ks"
            ),
            search_policy_knowledge_source=_get(
                "SEARCH_POLICY_KNOWLEDGE_SOURCE", "return-policy-ks"
            ),
            search_knowledge_api_version=_get(
                "SEARCH_KNOWLEDGE_API_VERSION", "2026-05-01-preview"
            ),
            search_retrieval_timeout_seconds=float(
                _get("SEARCH_RETRIEVAL_TIMEOUT_SECONDS", "30") or "30"
            ),
            search_health_cache_seconds=float(
                _get("SEARCH_HEALTH_CACHE_SECONDS", "60") or "60"
            ),
            app_environment=_get("APP_ENV", "local").lower(),
            auth_mode=_get("AUTH_MODE", "mock"),
            readiness_timeout_seconds=float(
                _get("READINESS_TIMEOUT_SECONDS", "5") or "5"
            ),
            applicationinsights_connection_string=_get(
                "APPLICATIONINSIGHTS_CONNECTION_STRING"
            ),
            otel_service_name=_get(
                "OTEL_SERVICE_NAME", "agent-memory-backend"
            ),
            entra_tenant_id=_get("ENTRA_TENANT_ID"),
            entra_audience=_get("ENTRA_AUDIENCE"),
            entra_issuer=_get("ENTRA_ISSUER"),
            entra_jwks_uri=_get("ENTRA_JWKS_URI"),
            entra_required_scopes=_get("ENTRA_REQUIRED_SCOPES"),
            entra_required_roles=_get("ENTRA_REQUIRED_ROLES"),
            agent_gateway_audience=_get("AGENT_GATEWAY_AUDIENCE"),
            agent_gateway_required_role=_get(
                "AGENT_GATEWAY_REQUIRED_ROLE", "AgentTools.Invoke"
            ),
            hosted_agent_principal_ids=tuple(
                dict.fromkeys(
                    (
                        *legacy_principals,
                        *support_principals,
                        *directive_principals,
                    )
                )
            ),
            support_hosted_agent_principal_ids=support_principals,
            directive_hosted_agent_principal_ids=directive_principals,
        )

    @property
    def entra_configured(self) -> bool:
        return bool(self.entra_tenant_id and self.entra_audience)

    @property
    def cosmos_configured(self) -> bool:
        return bool(self.cosmos_endpoint)

    @property
    def search_configured(self) -> bool:
        return bool(self.search_endpoint)

    @property
    def foundry_configured(self) -> bool:
        return bool(self.foundry_project_endpoint)

    @property
    def directive_agent_configured(self) -> bool:
        return bool(
            self.foundry_project_endpoint
            and self.directive_foundry_agent_name
            and self.directive_foundry_agent_endpoint
        )

    @property
    def directive_data_configured(self) -> bool:
        return bool(
            self.cosmos_endpoint
            and self.directive_cosmos_database
            and self.directive_catalog_container
            and self.directive_mandates_container
            and self.search_endpoint
            and self.directive_search_kb
            and self.directive_search_knowledge_source
            and self.directive_blob_endpoint
            and self.directive_blob_container
        )

    def resolve_llm_mode(self) -> str:
        if self.llm_mode in ("mock", "real"):
            return self.llm_mode
        return "real" if self.openai_endpoint else "mock"


@lru_cache
def get_settings() -> Settings:
    return Settings.from_environment()


def _principal_ids(name: str) -> tuple[str, ...]:
    return tuple(
        value.strip()
        for value in _get(name).replace(",", " ").split()
        if value.strip()
    )
