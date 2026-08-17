"""Immutable, strictly validated Document Intelligence extraction cache."""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from azure.core.exceptions import ResourceNotFoundError
from directive_contracts import source_fingerprint

from .blob_repository import BlobArtifactRepository
from .document_intelligence import (
    ExtractedDocument,
    extracted_document_from_payload,
    extracted_document_to_payload,
)
from .source import SourceIdentity

EXTRACTION_CACHE_SCHEMA = "1.0"


@dataclass(frozen=True, slots=True)
class ExtractorIdentity:
    document_intelligence_api_version: str
    model_id: str = "prebuilt-layout"
    output_content_format: str = "markdown"
    string_index_type: str = "unicodeCodePoint"
    parser_schema_version: str = "1.0"
    cache_schema_version: str = EXTRACTION_CACHE_SCHEMA

    @property
    def identity_hash(self) -> str:
        return _payload_hash(self.to_payload())

    def to_payload(self) -> dict[str, str]:
        values = {
            "document_intelligence_api_version": (
                self.document_intelligence_api_version
            ),
            "model_id": self.model_id,
            "output_content_format": self.output_content_format,
            "string_index_type": self.string_index_type,
            "parser_schema_version": self.parser_schema_version,
            "cache_schema_version": self.cache_schema_version,
        }
        if any(not value for value in values.values()):
            raise ValueError("Extractor identity fields must be non-empty")
        return values


@dataclass(frozen=True, slots=True)
class ExtractionCacheEvidence:
    blob_name: str
    extractor_identity_hash: str
    result_hash: str


@dataclass(frozen=True, slots=True)
class CachedExtraction:
    document: ExtractedDocument
    evidence: ExtractionCacheEvidence


class ExtractionCacheRepository:
    def __init__(self, artifacts: BlobArtifactRepository) -> None:
        self._artifacts = artifacts

    @staticmethod
    def blob_name(
        identity: SourceIdentity,
        extractor: ExtractorIdentity,
    ) -> str:
        fingerprint = source_fingerprint(
            identity.source_name,
            identity.source_hash,
        )
        return (
            f"extractions/{fingerprint}/{extractor.identity_hash}"
            f"/{identity.source_hash}.json.gz"
        )

    async def load(
        self,
        identity: SourceIdentity,
        extractor: ExtractorIdentity,
        *,
        expected_result_hash: str | None = None,
    ) -> CachedExtraction | None:
        blob_name = self.blob_name(identity, extractor)
        try:
            stored = await self._artifacts.read_bytes_with_metadata_and_etag(
                blob_name
            )
        except ResourceNotFoundError:
            return None
        if stored is None:
            return None
        content, metadata, _ = stored
        expected_content_hash = metadata.get("content_sha256")
        if expected_content_hash != hashlib.sha256(content).hexdigest():
            raise RuntimeError("Extraction cache content hash mismatch")
        try:
            raw = gzip.decompress(content)
            payload = json.loads(raw)
        except (gzip.BadGzipFile, EOFError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Extraction cache payload is invalid") from exc
        return _parse_cached_extraction(
            payload,
            blob_name=blob_name,
            expected_identity=identity,
            expected_extractor=extractor,
            expected_result_hash=expected_result_hash,
        )

    async def store(
        self,
        identity: SourceIdentity,
        extractor: ExtractorIdentity,
        document: ExtractedDocument,
    ) -> CachedExtraction:
        result = extracted_document_to_payload(document)
        result_hash = _payload_hash(result)
        payload = {
            "schema_version": EXTRACTION_CACHE_SCHEMA,
            "source": {
                "source_name": identity.source_name,
                "source_hash": identity.source_hash,
            },
            "extractor": extractor.to_payload(),
            "service_model_version": None,
            "result_hash": result_hash,
            "created_at": datetime.now(UTC).isoformat(),
            "result": result,
        }
        raw = json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode()
        content = gzip.compress(raw, compresslevel=9, mtime=0)
        blob_name = self.blob_name(identity, extractor)
        try:
            await self._artifacts.put_immutable(
                blob_name,
                content,
                content_type="application/gzip",
            )
        except RuntimeError:
            existing = await self.load(
                identity,
                extractor,
                expected_result_hash=result_hash,
            )
            if existing is None:
                raise
            return existing
        return CachedExtraction(
            document=document,
            evidence=ExtractionCacheEvidence(
                blob_name=blob_name,
                extractor_identity_hash=extractor.identity_hash,
                result_hash=result_hash,
            ),
        )


def _parse_cached_extraction(
    payload: Any,
    *,
    blob_name: str,
    expected_identity: SourceIdentity,
    expected_extractor: ExtractorIdentity,
    expected_result_hash: str | None,
) -> CachedExtraction:
    _require_keys(
        payload,
        {
            "schema_version",
            "source",
            "extractor",
            "service_model_version",
            "result_hash",
            "created_at",
            "result",
        },
        "extraction cache",
    )
    if payload["schema_version"] != EXTRACTION_CACHE_SCHEMA:
        raise RuntimeError("Unsupported extraction cache schema")
    _require_keys(
        payload["source"],
        {"source_name", "source_hash"},
        "extraction cache source",
    )
    if payload["source"] != {
        "source_name": expected_identity.source_name,
        "source_hash": expected_identity.source_hash,
    }:
        raise RuntimeError("Extraction cache source identity mismatch")
    if payload["extractor"] != expected_extractor.to_payload():
        raise RuntimeError("Extraction cache extractor identity mismatch")
    if payload["service_model_version"] is not None and (
        not isinstance(payload["service_model_version"], str)
        or not payload["service_model_version"]
    ):
        raise RuntimeError("Extraction cache service model version is invalid")
    created_at = payload["created_at"]
    if not isinstance(created_at, str):
        raise RuntimeError("Extraction cache creation time is invalid")
    try:
        timestamp = datetime.fromisoformat(created_at)
    except ValueError as exc:
        raise RuntimeError("Extraction cache creation time is invalid") from exc
    if timestamp.tzinfo is None:
        raise RuntimeError("Extraction cache creation time requires a timezone")
    result_hash = _payload_hash(payload["result"])
    if payload["result_hash"] != result_hash:
        raise RuntimeError("Extraction cache result hash mismatch")
    if expected_result_hash is not None and result_hash != expected_result_hash:
        raise RuntimeError("Extraction cache approval evidence mismatch")
    document = extracted_document_from_payload(payload["result"])
    return CachedExtraction(
        document=document,
        evidence=ExtractionCacheEvidence(
            blob_name=blob_name,
            extractor_identity_hash=expected_extractor.identity_hash,
            result_hash=result_hash,
        ),
    )


def _payload_hash(payload: Any) -> str:
    content = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(content).hexdigest()


def _require_keys(payload: Any, keys: set[str], name: str) -> None:
    if not isinstance(payload, dict) or set(payload) != keys:
        raise RuntimeError(f"{name} has an invalid schema")
