"""Environment-backed ingestion configuration."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


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
    mandate_container: str
    search_endpoint: str
    search_index: str
    search_knowledge_source: str
    search_knowledge_base: str
    search_api_version: str
    knowledge_api_version: str
    openai_endpoint: str
    openai_resource_uri: str
    openai_api_version: str
    embedding_deployment: str
    embedding_model: str
    embedding_dimensions: int
    summary_deployment: str
    summary_model: str
    knowledge_model_deployment: str
    knowledge_model_name: str
    source_directory: Path
    mandate_csv: Path
    processing_version: str
    chunk_token_limit: int
    chunk_overlap_tokens: int
    summary_batch_tokens: int
    summary_full_document_tokens: int

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
            mandate_container=os.getenv(
                "DIRECTIVE_MANDATE_CONTAINER", "user_mandates"
            ),
            search_endpoint=_required("AZURE_SEARCH_ENDPOINT").rstrip("/"),
            search_index=os.getenv(
                "DIRECTIVE_SEARCH_INDEX", "directive-chunks-v1"
            ),
            search_knowledge_source=os.getenv(
                "DIRECTIVE_SEARCH_KNOWLEDGE_SOURCE",
                "directive-chunks-ks-v1",
            ),
            search_knowledge_base=os.getenv(
                "DIRECTIVE_SEARCH_KNOWLEDGE_BASE", "directive-kb-v1"
            ),
            search_api_version=os.getenv(
                "AZURE_SEARCH_API_VERSION", "2024-07-01"
            ),
            knowledge_api_version=os.getenv(
                "AZURE_SEARCH_KNOWLEDGE_API_VERSION", "2026-04-01"
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
            knowledge_model_deployment=os.getenv(
                "DIRECTIVE_KNOWLEDGE_MODEL_DEPLOYMENT",
                "gpt-5-nano-directive-kb",
            ),
            knowledge_model_name=os.getenv(
                "DIRECTIVE_KNOWLEDGE_MODEL_NAME", "gpt-5-nano"
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
                "DIRECTIVE_PROCESSING_VERSION", "directive-v1"
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
        )
        if config.chunk_overlap_tokens >= config.chunk_token_limit:
            raise ValueError(
                "DIRECTIVE_CHUNK_OVERLAP_TOKENS must be lower than "
                "DIRECTIVE_CHUNK_TOKEN_LIMIT"
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
            "embedding_deployment": self.embedding_deployment,
            "embedding_model": self.embedding_model,
            "embedding_dimensions": self.embedding_dimensions,
            "summary_deployment": self.summary_deployment,
            "summary_model": self.summary_model,
            "knowledge_model_deployment": self.knowledge_model_deployment,
            "knowledge_model_name": self.knowledge_model_name,
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
