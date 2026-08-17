"""Environment-backed ingestion configuration."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Required environment variable {name} is not set")
    return value


def _integer(name: str, default: int, minimum: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _number(name: str, default: float, minimum: float) -> float:
    raw = os.getenv(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


@dataclass(frozen=True, slots=True)
class RetryPolicyConfig:
    max_attempts: int
    base_delay_seconds: float
    max_delay_seconds: float
    jitter_ratio: float
    stage_retry_budget: int
    operation_timeout_seconds: float = 180.0


@dataclass(frozen=True, slots=True)
class ConcurrencyConfig:
    document_intelligence: int
    embeddings: int
    summaries: int
    search_indexing: int


@dataclass(frozen=True)
class IngestionConfig:
    azure_client_id: str
    azure_tenant_id: str
    document_intelligence_endpoint: str
    document_intelligence_api_version: str
    blob_account_url: str
    blob_container: str
    cosmos_endpoint: str
    cosmos_database: str
    catalog_container: str
    content_container: str
    mandate_container: str
    search_endpoint: str
    search_index: str
    search_api_version: str
    openai_endpoint: str
    openai_resource_uri: str
    openai_api_version: str
    embedding_deployment: str
    embedding_model: str
    embedding_dimensions: int
    summary_deployment: str
    summary_model: str
    source_kind: str
    source_container: str
    source_prefix: str
    source_max_corpus_bytes: int
    source_directory: Path
    mandate_csv: Path
    processing_version: str
    chunk_token_limit: int
    chunk_overlap_tokens: int
    summary_batch_tokens: int
    summary_full_document_tokens: int
    summary_max_input_tokens: int
    summary_max_output_tokens: int
    embedding_max_item_tokens: int
    embedding_max_items_per_request: int
    embedding_max_aggregate_tokens: int
    table_max_rows_per_part: int
    table_max_chars_per_part: int
    retry_policy: RetryPolicyConfig
    concurrency: ConcurrencyConfig

    @classmethod
    def from_environment(cls) -> "IngestionConfig":
        config = cls(
            azure_client_id=_required("AZURE_CLIENT_ID"),
            azure_tenant_id=_required("AZURE_TENANT_ID"),
            document_intelligence_endpoint=_required(
                "DOCUMENT_INTELLIGENCE_ENDPOINT"
            ).rstrip("/"),
            document_intelligence_api_version=os.getenv(
                "DOCUMENT_INTELLIGENCE_API_VERSION", "2024-11-30"
            ),
            blob_account_url=_required("DIRECTIVE_BLOB_ACCOUNT_URL").rstrip(
                "/"
            ),
            blob_container=os.getenv(
                "DIRECTIVE_BLOB_CONTAINER", "directive-artifacts"
            ),
            cosmos_endpoint=_required("COSMOS_ENDPOINT"),
            cosmos_database=os.getenv("DIRECTIVE_COSMOS_DATABASE", "directives"),
            catalog_container=os.getenv(
                "DIRECTIVE_CATALOG_CONTAINER", "catalog"
            ),
            content_container=os.getenv(
                "DIRECTIVE_CONTENT_CONTAINER", "directive_content"
            ),
            mandate_container=os.getenv(
                "DIRECTIVE_MANDATE_CONTAINER", "user_mandates"
            ),
            search_endpoint=_required("AZURE_SEARCH_ENDPOINT").rstrip("/"),
            search_index=os.getenv(
                "DIRECTIVE_SEARCH_INDEX", "directive-chunks-v3"
            ),
            search_api_version=os.getenv(
                "AZURE_SEARCH_API_VERSION", "2026-04-01"
            ),
            openai_endpoint=_required("AZURE_OPENAI_ENDPOINT").rstrip("/"),
            openai_resource_uri=os.getenv(
                "AZURE_OPENAI_RESOURCE_URI",
                _required("AZURE_OPENAI_ENDPOINT"),
            ).rstrip("/"),
            openai_api_version=os.getenv(
                "AZURE_OPENAI_API_VERSION", "2025-04-01-preview"
            ),
            embedding_deployment=os.getenv(
                "AZURE_OPENAI_EMBED_DEPLOYMENT", "text-embedding-3-large"
            ),
            embedding_model=os.getenv(
                "AZURE_OPENAI_EMBED_MODEL", "text-embedding-3-large"
            ),
            embedding_dimensions=_integer(
                "DIRECTIVE_EMBEDDING_DIMENSIONS", 3072, 1
            ),
            summary_deployment=os.getenv(
                "DIRECTIVE_SUMMARY_DEPLOYMENT", "gpt-5.6-sol"
            ),
            summary_model=os.getenv(
                "DIRECTIVE_SUMMARY_MODEL", "gpt-5.6-sol"
            ),
            source_kind=os.getenv(
                "DIRECTIVE_SOURCE_KIND", "local"
            ).strip().casefold(),
            source_container=os.getenv(
                "DIRECTIVE_SOURCE_CONTAINER", "directive-source"
            ).strip(),
            source_prefix=os.getenv(
                "DIRECTIVE_SOURCE_PREFIX", ""
            ).strip(),
            source_max_corpus_bytes=_integer(
                "DIRECTIVE_SOURCE_MAX_CORPUS_BYTES",
                512 * 1024 * 1024,
                1,
            ),
            source_directory=Path(
                os.getenv("DIRECTIVE_SOURCE_DIR", "/app/fixtures/pdf")
            ),
            mandate_csv=Path(
                os.getenv(
                    "DIRECTIVE_MANDATE_CSV",
                    "/app/fixtures/mandatory/mand.csv",
                )
            ),
            processing_version=os.getenv(
                "DIRECTIVE_PROCESSING_VERSION",
                "directive-v3-bounded-ingestion",
            ),
            chunk_token_limit=_integer(
                "DIRECTIVE_CHUNK_TOKEN_LIMIT", 800, 128
            ),
            chunk_overlap_tokens=_integer(
                "DIRECTIVE_CHUNK_OVERLAP_TOKENS", 120, 0
            ),
            summary_batch_tokens=_integer(
                "DIRECTIVE_SUMMARY_BATCH_TOKENS", 60000, 1000
            ),
            summary_full_document_tokens=_integer(
                "DIRECTIVE_SUMMARY_FULL_DOCUMENT_TOKENS", 180000, 1000
            ),
            summary_max_input_tokens=_integer(
                "DIRECTIVE_SUMMARY_MAX_INPUT_TOKENS",
                900000,
                1000,
            ),
            summary_max_output_tokens=_integer(
                "DIRECTIVE_SUMMARY_MAX_OUTPUT_TOKENS",
                16000,
                256,
            ),
            embedding_max_item_tokens=_integer(
                "DIRECTIVE_EMBEDDING_MAX_ITEM_TOKENS",
                8192,
                128,
            ),
            embedding_max_items_per_request=_integer(
                "DIRECTIVE_EMBEDDING_MAX_ITEMS_PER_REQUEST",
                256,
                1,
            ),
            embedding_max_aggregate_tokens=_integer(
                "DIRECTIVE_EMBEDDING_MAX_AGGREGATE_TOKENS",
                240000,
                128,
            ),
            table_max_rows_per_part=_integer(
                "DIRECTIVE_TABLE_MAX_ROWS_PER_PART",
                25,
                1,
            ),
            table_max_chars_per_part=_integer(
                "DIRECTIVE_TABLE_MAX_CHARS_PER_PART",
                12000,
                512,
            ),
            retry_policy=RetryPolicyConfig(
                max_attempts=_integer(
                    "DIRECTIVE_PROVIDER_MAX_ATTEMPTS",
                    5,
                    1,
                ),
                base_delay_seconds=_number(
                    "DIRECTIVE_PROVIDER_RETRY_BASE_SECONDS",
                    1.0,
                    0.0,
                ),
                max_delay_seconds=_number(
                    "DIRECTIVE_PROVIDER_RETRY_MAX_SECONDS",
                    30.0,
                    0.0,
                ),
                jitter_ratio=_number(
                    "DIRECTIVE_PROVIDER_RETRY_JITTER_RATIO",
                    0.2,
                    0.0,
                ),
                stage_retry_budget=_integer(
                    "DIRECTIVE_STAGE_RETRY_BUDGET",
                    12,
                    0,
                ),
                operation_timeout_seconds=_number(
                    "DIRECTIVE_PROVIDER_OPERATION_TIMEOUT_SECONDS",
                    180.0,
                    1.0,
                ),
            ),
            concurrency=ConcurrencyConfig(
                document_intelligence=_integer(
                    "DIRECTIVE_DOCUMENT_INTELLIGENCE_CONCURRENCY",
                    4,
                    1,
                ),
                embeddings=_integer(
                    "DIRECTIVE_EMBEDDING_CONCURRENCY",
                    2,
                    1,
                ),
                summaries=_integer(
                    "DIRECTIVE_SUMMARY_CONCURRENCY",
                    2,
                    1,
                ),
                search_indexing=_integer(
                    "DIRECTIVE_SEARCH_INDEXING_CONCURRENCY",
                    2,
                    1,
                ),
            ),
        )
        if config.chunk_overlap_tokens >= config.chunk_token_limit:
            raise ValueError(
                "DIRECTIVE_CHUNK_OVERLAP_TOKENS must be lower than "
                "DIRECTIVE_CHUNK_TOKEN_LIMIT"
            )
        if config.source_kind not in {"local", "azure_blob"}:
            raise ValueError(
                "DIRECTIVE_SOURCE_KIND must be local or azure_blob"
            )
        if config.source_kind == "azure_blob" and not config.source_container:
            raise ValueError(
                "DIRECTIVE_SOURCE_CONTAINER is required in azure_blob mode"
            )
        if (
            config.summary_batch_tokens > config.summary_max_input_tokens
            or config.summary_full_document_tokens
            > config.summary_max_input_tokens
        ):
            raise ValueError(
                "Summary thresholds must not exceed "
                "DIRECTIVE_SUMMARY_MAX_INPUT_TOKENS"
            )
        if config.embedding_max_item_tokens > 8192:
            raise ValueError(
                "DIRECTIVE_EMBEDDING_MAX_ITEM_TOKENS exceeds provider limit"
            )
        if config.embedding_max_items_per_request > 2048:
            raise ValueError(
                "DIRECTIVE_EMBEDDING_MAX_ITEMS_PER_REQUEST exceeds provider limit"
            )
        if config.embedding_max_aggregate_tokens > 300000:
            raise ValueError(
                "DIRECTIVE_EMBEDDING_MAX_AGGREGATE_TOKENS exceeds provider limit"
            )
        if (
            config.retry_policy.max_delay_seconds
            < config.retry_policy.base_delay_seconds
        ):
            raise ValueError(
                "Provider retry maximum delay must be at least the base delay"
            )
        if config.retry_policy.jitter_ratio > 1:
            raise ValueError(
                "DIRECTIVE_PROVIDER_RETRY_JITTER_RATIO must not exceed 1"
            )
        return config

    @property
    def processing_hash(self) -> str:
        processing_inputs = {
            "processing_version": self.processing_version,
            "document_intelligence_api_version": (
                self.document_intelligence_api_version
            ),
            "chunk_token_limit": self.chunk_token_limit,
            "chunk_overlap_tokens": self.chunk_overlap_tokens,
            "summary_batch_tokens": self.summary_batch_tokens,
            "summary_full_document_tokens": (
                self.summary_full_document_tokens
            ),
            "summary_max_input_tokens": self.summary_max_input_tokens,
            "summary_max_output_tokens": self.summary_max_output_tokens,
            "embedding_max_item_tokens": self.embedding_max_item_tokens,
            "embedding_max_items_per_request": (
                self.embedding_max_items_per_request
            ),
            "embedding_max_aggregate_tokens": (
                self.embedding_max_aggregate_tokens
            ),
            "table_max_rows_per_part": self.table_max_rows_per_part,
            "table_max_chars_per_part": self.table_max_chars_per_part,
            "retry_policy": asdict(self.retry_policy),
            "concurrency": asdict(self.concurrency),
            "embedding_deployment": self.embedding_deployment,
            "embedding_model": self.embedding_model,
            "embedding_dimensions": self.embedding_dimensions,
            "summary_deployment": self.summary_deployment,
            "summary_model": self.summary_model,
        }
        encoded = json.dumps(
            processing_inputs, sort_keys=True, separators=(",", ":")
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    def public_summary(self) -> dict[str, object]:
        values = asdict(self)
        values.pop("azure_client_id")
        values["source_directory"] = str(self.source_directory)
        values["mandate_csv"] = str(self.mandate_csv)
        values["processing_hash"] = self.processing_hash
        return values

    @property
    def source_storage_account(self) -> str:
        return _endpoint_name(
            os.getenv("DIRECTIVE_SOURCE_STORAGE_ACCOUNT", self.blob_account_url)
        )

    @property
    def artifact_storage_account(self) -> str:
        return _endpoint_name(
            os.getenv("DIRECTIVE_ARTIFACT_STORAGE_ACCOUNT", self.blob_account_url)
        )

    @property
    def cosmos_account(self) -> str:
        return _endpoint_name(
            os.getenv("DIRECTIVE_COSMOS_ACCOUNT", self.cosmos_endpoint)
        )

    @property
    def search_service(self) -> str:
        return _endpoint_name(
            os.getenv("DIRECTIVE_SEARCH_SERVICE", self.search_endpoint)
        )


def _endpoint_name(value: str) -> str:
    parsed = urlparse(value)
    host = parsed.hostname or value.strip()
    name = host.split(".", 1)[0]
    if not name:
        raise ValueError("Deployment environment identity must not be empty")
    return name
