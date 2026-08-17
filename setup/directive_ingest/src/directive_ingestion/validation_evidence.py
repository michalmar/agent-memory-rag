"""Private approval evidence binding descriptors, identities, metadata, and cache."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from directive_contracts import DirectiveMetadata

from .blob_repository import BlobArtifactRepository
from .extraction_cache import ExtractionCacheEvidence
from .source import SourceDescriptor, SourceIdentity

VALIDATION_EVIDENCE_SCHEMA = "1.0"


@dataclass(frozen=True, slots=True)
class ValidationEvidenceDocument:
    descriptor: SourceDescriptor
    identity: SourceIdentity
    metadata: DirectiveMetadata
    source_state_blob: str
    disposition: Literal["changed", "unchanged"]
    extraction: ExtractionCacheEvidence
    validation_warnings: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if (
            self.descriptor.source_name != self.identity.source_name
            or self.metadata.source_filename != self.identity.source_name
            or self.metadata.source_hash != self.identity.source_hash
        ):
            raise ValueError("Validation evidence source identities disagree")
        if not self.source_state_blob:
            raise ValueError("Validation evidence source-state blob is required")
        if (
            len(self.validation_warnings) > 100
            or tuple(sorted(set(self.validation_warnings)))
            != self.validation_warnings
            or any(
                not code or len(code) > 128 or severity != "warning"
                for code, severity in self.validation_warnings
            )
        ):
            raise ValueError("Validation evidence warnings are invalid")

    def to_payload(self) -> dict[str, Any]:
        return {
            "descriptor": _descriptor_payload(self.descriptor),
            "identity": {
                "source_name": self.identity.source_name,
                "source_hash": self.identity.source_hash,
            },
            "metadata": self.metadata.model_dump(mode="json"),
            "source_state_blob": self.source_state_blob,
            "disposition": self.disposition,
            "extraction": {
                "blob_name": self.extraction.blob_name,
                "extractor_identity_hash": (
                    self.extraction.extractor_identity_hash
                ),
                "result_hash": self.extraction.result_hash,
            },
            "validation_warnings": [
                {"code": code, "severity": severity}
                for code, severity in self.validation_warnings
            ],
        }


@dataclass(frozen=True, slots=True)
class ValidationEvidence:
    processing_hash: str
    mandate_checksum: str
    documents: tuple[ValidationEvidenceDocument, ...]
    evidence_hash: str
    schema_version: str = VALIDATION_EVIDENCE_SCHEMA

    @classmethod
    def create(
        cls,
        *,
        processing_hash: str,
        mandate_checksum: str,
        documents: list[ValidationEvidenceDocument]
        | tuple[ValidationEvidenceDocument, ...],
    ) -> "ValidationEvidence":
        ordered = tuple(
            sorted(documents, key=lambda document: document.identity.source_name)
        )
        if not _is_hash(processing_hash) or not _is_hash(mandate_checksum):
            raise ValueError("Validation evidence checksums are invalid")
        if not ordered:
            raise ValueError("Validation evidence must contain documents")
        if len({item.identity.source_name for item in ordered}) != len(ordered):
            raise ValueError("Validation evidence contains duplicate names")
        projection = {
            "schema_version": VALIDATION_EVIDENCE_SCHEMA,
            "processing_hash": processing_hash,
            "mandate_checksum": mandate_checksum,
            "documents": [document.to_payload() for document in ordered],
        }
        return cls(
            processing_hash=processing_hash,
            mandate_checksum=mandate_checksum,
            documents=ordered,
            evidence_hash=_payload_hash(projection),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "processing_hash": self.processing_hash,
            "mandate_checksum": self.mandate_checksum,
            "documents": [document.to_payload() for document in self.documents],
            "evidence_hash": self.evidence_hash,
        }


class ValidationEvidenceRepository:
    def __init__(self, artifacts: BlobArtifactRepository) -> None:
        self._artifacts = artifacts

    @staticmethod
    def blob_name(validation_digest: str) -> str:
        if not _is_hash(validation_digest):
            raise ValueError("Validation digest must be lowercase SHA-256")
        return f"validation-evidence/{validation_digest}.json"

    async def store(
        self,
        validation_digest: str,
        evidence: ValidationEvidence,
    ) -> None:
        await self._artifacts.put_json(
            self.blob_name(validation_digest),
            evidence.to_payload(),
        )

    async def load(
        self,
        validation_digest: str,
        *,
        expected_evidence_hash: str,
    ) -> ValidationEvidence:
        payload = await self._artifacts.get_json(
            self.blob_name(validation_digest)
        )
        if payload is None:
            raise RuntimeError("Approved validation evidence is missing")
        evidence = _parse_evidence(payload)
        if evidence.evidence_hash != expected_evidence_hash:
            raise RuntimeError("Approved validation evidence hash mismatch")
        return evidence


def _parse_evidence(payload: Any) -> ValidationEvidence:
    _require_keys(
        payload,
        {
            "schema_version",
            "processing_hash",
            "mandate_checksum",
            "documents",
            "evidence_hash",
        },
        "validation evidence",
    )
    if payload["schema_version"] != VALIDATION_EVIDENCE_SCHEMA:
        raise RuntimeError("Unsupported validation evidence schema")
    raw_documents = payload["documents"]
    if not isinstance(raw_documents, list) or not raw_documents:
        raise RuntimeError("Validation evidence documents are invalid")
    documents = tuple(_parse_document(value) for value in raw_documents)
    processing_hash = _hash(payload["processing_hash"], "processing hash")
    mandate_checksum = _hash(payload["mandate_checksum"], "mandate checksum")
    projection = {
        "schema_version": VALIDATION_EVIDENCE_SCHEMA,
        "processing_hash": processing_hash,
        "mandate_checksum": mandate_checksum,
        "documents": [document.to_payload() for document in documents],
    }
    evidence_hash = _hash(payload["evidence_hash"], "evidence hash")
    if evidence_hash != _payload_hash(projection):
        raise RuntimeError("Validation evidence integrity check failed")
    return ValidationEvidence(
        processing_hash=processing_hash,
        mandate_checksum=mandate_checksum,
        documents=documents,
        evidence_hash=evidence_hash,
    )


def _parse_document(payload: Any) -> ValidationEvidenceDocument:
    _require_keys(
        payload,
        {
            "descriptor",
            "identity",
            "metadata",
            "source_state_blob",
            "disposition",
            "extraction",
            "validation_warnings",
        },
        "validation evidence document",
    )
    descriptor = _parse_descriptor(payload["descriptor"])
    _require_keys(
        payload["identity"],
        {"source_name", "source_hash"},
        "validation evidence identity",
    )
    identity = SourceIdentity(
        source_name=_text(payload["identity"]["source_name"], "source name"),
        source_hash=_hash(payload["identity"]["source_hash"], "source hash"),
    )
    try:
        metadata = DirectiveMetadata.model_validate(payload["metadata"])
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Validation evidence metadata is invalid") from exc
    disposition = payload["disposition"]
    if disposition not in {"changed", "unchanged"}:
        raise RuntimeError("Validation evidence disposition is invalid")
    _require_keys(
        payload["extraction"],
        {"blob_name", "extractor_identity_hash", "result_hash"},
        "validation evidence extraction",
    )
    extraction = ExtractionCacheEvidence(
        blob_name=_text(payload["extraction"]["blob_name"], "cache blob"),
        extractor_identity_hash=_hash(
            payload["extraction"]["extractor_identity_hash"],
            "extractor identity hash",
        ),
        result_hash=_hash(
            payload["extraction"]["result_hash"],
            "extraction result hash",
        ),
    )
    raw_warnings = payload["validation_warnings"]
    if not isinstance(raw_warnings, list) or len(raw_warnings) > 100:
        raise RuntimeError("Validation evidence warnings are invalid")
    warnings: list[tuple[str, str]] = []
    for warning in raw_warnings:
        _require_keys(
            warning,
            {"code", "severity"},
            "validation evidence warning",
        )
        code = _text(warning["code"], "validation warning code")
        severity = warning["severity"]
        if len(code) > 128 or severity != "warning":
            raise RuntimeError("Validation evidence warning is invalid")
        warnings.append((code, severity))
    canonical_warnings = tuple(sorted(set(warnings)))
    if tuple(warnings) != canonical_warnings:
        raise RuntimeError("Validation evidence warnings are not canonical")
    return ValidationEvidenceDocument(
        descriptor=descriptor,
        identity=identity,
        metadata=metadata,
        source_state_blob=_text(
            payload["source_state_blob"],
            "source-state blob",
        ),
        disposition=disposition,
        extraction=extraction,
        validation_warnings=canonical_warnings,
    )


def _descriptor_payload(descriptor: SourceDescriptor) -> dict[str, Any]:
    return {
        "source_name": descriptor.source_name,
        "kind": descriptor.kind,
        "locator": descriptor.locator,
        "etag": descriptor.etag,
        "version_id": descriptor.version_id,
        "size": descriptor.size,
        "last_modified": (
            descriptor.last_modified.astimezone(UTC).isoformat()
            if descriptor.last_modified is not None
            else None
        ),
    }


def _parse_descriptor(payload: Any) -> SourceDescriptor:
    _require_keys(
        payload,
        {
            "source_name",
            "kind",
            "locator",
            "etag",
            "version_id",
            "size",
            "last_modified",
        },
        "validation evidence descriptor",
    )
    last_modified = payload["last_modified"]
    if last_modified is not None:
        try:
            last_modified = datetime.fromisoformat(last_modified)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                "Validation evidence descriptor time is invalid"
            ) from exc
        if last_modified.tzinfo is None:
            raise RuntimeError(
                "Validation evidence descriptor time requires a timezone"
            )
    return SourceDescriptor(
        source_name=_text(payload["source_name"], "descriptor source name"),
        kind=_text(payload["kind"], "descriptor source kind"),
        locator=_text(payload["locator"], "descriptor locator"),
        etag=_optional_text(payload["etag"], "descriptor ETag"),
        version_id=_optional_text(
            payload["version_id"],
            "descriptor version ID",
        ),
        size=payload["size"],
        last_modified=last_modified,
    )


def _payload_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _require_keys(payload: Any, keys: set[str], name: str) -> None:
    if not isinstance(payload, dict) or set(payload) != keys:
        raise RuntimeError(f"{name} has an invalid schema")


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"{name} must be non-empty text")
    return value


def _optional_text(value: Any, name: str) -> str | None:
    if value is None:
        return None
    return _text(value, name)


def _hash(value: Any, name: str) -> str:
    if not _is_hash(value):
        raise RuntimeError(f"{name} must be lowercase SHA-256")
    return value


def _is_hash(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
