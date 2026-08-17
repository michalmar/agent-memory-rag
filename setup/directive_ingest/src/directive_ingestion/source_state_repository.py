"""Private, immutable source-state records used for ingestion idempotency."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from directive_contracts import (
    DirectiveMetadata,
    PublishedDirectiveVersion,
    source_fingerprint,
)

from .blob_repository import BlobArtifactRepository
from .extraction_cache import ExtractionCacheEvidence
from .integrity import IntegrityValidationError
from .source import (
    SourceDescriptor,
    SourceDocument,
    SourceIdentity,
    SourceReference,
)

MAX_VALIDATION_WARNINGS = 100
MAX_VALIDATION_WARNING_CODE_LENGTH = 128
SOURCE_STATE_SCHEMA = "3.0"


@dataclass(frozen=True)
class PublishedSourceState:
    """Trusted state only after the runner validates the referenced bundle."""

    source_filename: str
    source_hash: str
    source_fingerprint: str
    processing_hash: str
    directive_metadata: DirectiveMetadata
    artifact_generation_id: str
    publication_state: str
    published_bundle: PublishedDirectiveVersion | None = None
    repair_generation_salt: str | None = None
    pending_cleanup: tuple[PublishedDirectiveVersion, ...] = ()
    validation_warnings: tuple[tuple[str, str], ...] = ()
    validation_digest: str | None = None
    mandate_checksum: str | None = None
    source_etag: str | None = None
    source_version_id: str | None = None
    source_size: int = 0
    source_last_modified: str | None = None
    extraction_cache_blob: str = ""
    extractor_identity_hash: str = ""
    extraction_result_hash: str = ""

    @property
    def identity(self) -> SourceIdentity:
        return SourceIdentity(self.source_filename, self.source_hash)

    @property
    def extraction_evidence(self) -> ExtractionCacheEvidence:
        return ExtractionCacheEvidence(
            blob_name=self.extraction_cache_blob,
            extractor_identity_hash=self.extractor_identity_hash,
            result_hash=self.extraction_result_hash,
        )

    def matches_descriptor(self, descriptor: SourceDescriptor) -> bool:
        if (
            descriptor.source_name != self.source_filename
            or descriptor.size != self.source_size
        ):
            return False
        if (
            self.source_version_id is not None
            or descriptor.version_id is not None
        ):
            return (
                self.source_version_id is not None
                and descriptor.version_id is not None
                and self.source_version_id == descriptor.version_id
            )
        return (
            self.source_etag is not None
            and descriptor.etag is not None
            and self.source_etag == descriptor.etag
        )


@dataclass(frozen=True)
class SourceStateSnapshot:
    blob_name: str
    content: bytes
    etag: str


class SourceStateRepository:
    def __init__(self, blobs: BlobArtifactRepository) -> None:
        self._blobs = blobs

    @staticmethod
    def blob_name(
        source: SourceDocument | SourceIdentity,
        processing_hash: str,
    ) -> str:
        identity = _source_identity(source)
        return (
            f"source-state/{source_fingerprint(identity.source_name, identity.source_hash)}"
            f"/{processing_hash}.json"
        )

    async def load(
        self, source: SourceDocument, processing_hash: str
    ) -> PublishedSourceState | None:
        return await self.load_identity(source.identity, processing_hash)

    async def load_identity(
        self,
        identity: SourceIdentity,
        processing_hash: str,
        *,
        blob_name: str | None = None,
    ) -> PublishedSourceState | None:
        try:
            value = await self._blobs.get_json(
                blob_name or self.blob_name(identity, processing_hash)
            )
        except IntegrityValidationError:
            return None
        if value is None:
            return None
        try:
            if value["schema_version"] != SOURCE_STATE_SCHEMA:
                return None
            state = PublishedSourceState(
                source_filename=value["source_filename"],
                source_hash=value["source_hash"],
                source_fingerprint=value["source_fingerprint"],
                processing_hash=value["processing_hash"],
                directive_metadata=DirectiveMetadata.model_validate(
                    value["directive_metadata"]
                ),
                artifact_generation_id=value["artifact_generation_id"],
                publication_state=value["publication_state"],
                published_bundle=(
                    PublishedDirectiveVersion.model_validate(
                        value["published_bundle"]
                    )
                    if value.get("published_bundle") is not None
                    else None
                ),
                repair_generation_salt=value.get("repair_generation_salt"),
                pending_cleanup=tuple(
                    PublishedDirectiveVersion.model_validate(bundle)
                    for bundle in value.get("pending_cleanup", [])
                ),
                validation_warnings=_parse_validation_warnings(
                    value["validation_warnings"]
                ),
                validation_digest=value.get("validation_digest"),
                mandate_checksum=value.get("mandate_checksum"),
                source_etag=value["source_etag"],
                source_version_id=value["source_version_id"],
                source_size=value["source_size"],
                source_last_modified=value["source_last_modified"],
                extraction_cache_blob=value["extraction_cache_blob"],
                extractor_identity_hash=value["extractor_identity_hash"],
                extraction_result_hash=value["extraction_result_hash"],
            )
        except (KeyError, TypeError, ValueError):
            return None
        expected_fingerprint = source_fingerprint(
            identity.source_name, identity.source_hash
        )
        metadata = state.directive_metadata
        if (
            state.source_filename != identity.source_name
            or state.source_hash != identity.source_hash
            or state.source_fingerprint != expected_fingerprint
            or state.processing_hash != processing_hash
            or state.publication_state != "published"
            or (
                state.published_bundle is not None
                and not _bundle_matches_state(state.published_bundle, state)
            )
            or (
                state.repair_generation_salt is not None
                and not _is_checksum(state.repair_generation_salt)
            )
            or metadata.source_filename != identity.source_name
            or metadata.source_hash != identity.source_hash
            or metadata.processing_hash != processing_hash
            or (
                state.validation_digest is not None
                and (
                    not isinstance(state.validation_digest, str)
                    or not state.validation_digest.strip()
                )
            )
            or (
                state.mandate_checksum is not None
                and not _is_checksum(state.mandate_checksum)
            )
            or (state.validation_digest is None) != (state.mandate_checksum is None)
            or not isinstance(state.source_size, int)
            or isinstance(state.source_size, bool)
            or state.source_size < 1
            or (
                state.source_etag is not None
                and (
                    not isinstance(state.source_etag, str)
                    or not state.source_etag
                )
            )
            or (
                state.source_version_id is not None
                and (
                    not isinstance(state.source_version_id, str)
                    or not state.source_version_id
                )
            )
            or (
                state.source_last_modified is not None
                and not _is_datetime(state.source_last_modified)
            )
            or not state.extraction_cache_blob
            or not _is_checksum(state.extractor_identity_hash)
            or not _is_checksum(state.extraction_result_hash)
        ):
            return None
        return state

    async def record(
        self,
        source: SourceReference,
        metadata: DirectiveMetadata,
        artifact_generation_id: str,
        pending_cleanup: tuple[PublishedDirectiveVersion, ...] = (),
        *,
        extraction_evidence: ExtractionCacheEvidence,
        published_bundle: PublishedDirectiveVersion | None = None,
        repair_generation_salt: str | None = None,
        validation_warnings: tuple[tuple[str, str], ...] = (),
        validation_digest: str | None = None,
        mandate_checksum: str | None = None,
        expected_etag: str | None = None,
        require_absent: bool = False,
    ) -> str:
        if validation_digest is not None and not validation_digest.strip():
            raise ValueError("Validation digest must not be empty")
        if (validation_digest is None) != (mandate_checksum is None):
            raise ValueError(
                "Validation digest and mandate checksum must be recorded together"
            )
        if mandate_checksum is not None and not _is_checksum(mandate_checksum):
            raise ValueError("Mandate checksum must be a lowercase SHA-256 digest")
        if repair_generation_salt is not None and not _is_checksum(
            repair_generation_salt
        ):
            raise ValueError(
                "Repair generation salt must be a lowercase SHA-256 digest"
            )
        if published_bundle is not None and not _bundle_matches(
            published_bundle,
            source,
            metadata,
            artifact_generation_id,
        ):
            raise ValueError(
                "Published bundle does not match the source-state identity"
            )
        canonical_warnings = _canonical_validation_warnings(validation_warnings)
        descriptor = source.descriptor
        if (
            not extraction_evidence.blob_name
            or not _is_checksum(extraction_evidence.extractor_identity_hash)
            or not _is_checksum(extraction_evidence.result_hash)
        ):
            raise ValueError("Extraction cache evidence is invalid")
        payload: dict[str, Any] = {
            "schema_version": SOURCE_STATE_SCHEMA,
            "type": "source_state",
            "source_filename": source.source_name,
            "source_hash": source.source_hash,
            "source_fingerprint": source_fingerprint(
                source.source_name, source.source_hash
            ),
            "processing_hash": metadata.processing_hash,
            "directive_metadata": metadata.model_dump(mode="json"),
            "artifact_generation_id": artifact_generation_id,
            "publication_state": "published",
            "published_bundle": (
                published_bundle.model_dump(mode="json")
                if published_bundle is not None
                else None
            ),
            "pending_cleanup": [
                bundle.model_dump(mode="json") for bundle in pending_cleanup
            ],
            "validation_warnings": [
                {"code": code, "severity": severity}
                for code, severity in canonical_warnings
            ],
            "source_etag": descriptor.etag,
            "source_version_id": descriptor.version_id,
            "source_size": descriptor.size,
            "source_last_modified": (
                descriptor.last_modified.isoformat()
                if descriptor.last_modified is not None
                else None
            ),
            "extraction_cache_blob": extraction_evidence.blob_name,
            "extractor_identity_hash": (
                extraction_evidence.extractor_identity_hash
            ),
            "extraction_result_hash": extraction_evidence.result_hash,
        }
        if repair_generation_salt is not None:
            payload["repair_generation_salt"] = repair_generation_salt
        if validation_digest is not None:
            payload["validation_digest"] = validation_digest
            payload["mandate_checksum"] = mandate_checksum
        return await self._blobs.replace_json(
            self.blob_name(source, metadata.processing_hash),
            payload,
            expected_etag=expected_etag,
            require_absent=require_absent,
        )

    async def clear_pending(
        self,
        source: SourceReference,
        metadata: DirectiveMetadata,
        artifact_generation_id: str,
        published_bundle: PublishedDirectiveVersion | None = None,
        extraction_evidence: ExtractionCacheEvidence | None = None,
        validation_digest: str | None = None,
        mandate_checksum: str | None = None,
        repair_generation_salt: str | None = None,
        validation_warnings: tuple[tuple[str, str], ...] = (),
    ) -> None:
        if extraction_evidence is None:
            raise ValueError("Extraction cache evidence is required")
        await self.record(
            source,
            metadata,
            artifact_generation_id,
            extraction_evidence=extraction_evidence,
            published_bundle=published_bundle,
            repair_generation_salt=repair_generation_salt,
            validation_warnings=validation_warnings,
            validation_digest=validation_digest,
            mandate_checksum=mandate_checksum,
        )

    async def prune(self, expected_names: set[str]) -> None:
        """Remove every state record outside exact source+processing identities."""
        names = await self._blobs.list_names("source-state/")
        stale = names - expected_names
        if stale:
            await self._blobs.delete_names(stale)

    async def list_names(self) -> set[str]:
        return await self._blobs.list_names("source-state/")

    async def delete(
        self,
        source: SourceReference | SourceIdentity,
        processing_hash: str,
    ) -> None:
        await self._blobs.delete_names(
            {self.blob_name(source, processing_hash)}
        )

    async def snapshot(
        self,
        source: SourceReference | SourceIdentity,
        processing_hash: str,
    ) -> SourceStateSnapshot | None:
        name = self.blob_name(source, processing_hash)
        value = await self._blobs.read_bytes_with_etag(name)
        if value is None:
            return None
        content, etag = value
        return SourceStateSnapshot(name, content, etag)

    async def restore(
        self, snapshot: SourceStateSnapshot | None,
        source: SourceReference | SourceIdentity,
        processing_hash: str,
        candidate_etag: str,
    ) -> None:
        if snapshot is None:
            await self._blobs.delete_if_etag(
                self.blob_name(source, processing_hash), candidate_etag
            )
            return
        await self._blobs.restore_bytes(
            snapshot.blob_name, snapshot.content, candidate_etag
        )


def _is_checksum(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _is_datetime(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _source_identity(
    source: SourceReference | SourceIdentity,
) -> SourceIdentity:
    return source.identity if isinstance(source, SourceReference) else source


def _bundle_matches_state(
    bundle: PublishedDirectiveVersion, state: PublishedSourceState
) -> bool:
    return (
        bundle.source_filename == state.source_filename
        and bundle.source_hash == state.source_hash
        and bundle.processing_hash == state.processing_hash
        and bundle.artifact_generation_id == state.artifact_generation_id
        and _bundle_metadata(bundle) == state.directive_metadata
    )


def _bundle_matches(
    bundle: PublishedDirectiveVersion,
    source: SourceDocument,
    metadata: DirectiveMetadata,
    artifact_generation_id: str,
) -> bool:
    return (
        bundle.source_filename == source.source_name
        and bundle.source_hash == source.source_hash
        and bundle.processing_hash == metadata.processing_hash
        and bundle.artifact_generation_id == artifact_generation_id
        and _bundle_metadata(bundle) == metadata
    )


def _bundle_metadata(bundle: PublishedDirectiveVersion) -> DirectiveMetadata:
    return DirectiveMetadata.model_validate(
        {
            name: getattr(bundle, name)
            for name in DirectiveMetadata.model_fields
        }
    )


def _parse_validation_warnings(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list):
        raise ValueError("Source-state validation warnings must be a list")
    warnings: list[tuple[str, str]] = []
    for warning in value:
        if not isinstance(warning, dict):
            raise ValueError("Source-state validation warning must be an object")
        code = warning.get("code")
        severity = warning.get("severity")
        if not isinstance(code, str) or not isinstance(severity, str):
            raise ValueError("Source-state validation warning is invalid")
        warnings.append((code, severity))
    return _canonical_validation_warnings(tuple(warnings))


def _canonical_validation_warnings(
    warnings: tuple[tuple[str, str], ...],
) -> tuple[tuple[str, str], ...]:
    if len(warnings) > MAX_VALIDATION_WARNINGS:
        raise ValueError("Source-state validation warnings exceed the limit")
    canonical = tuple(sorted(set(warnings)))
    if canonical != warnings:
        raise ValueError("Source-state validation warnings are not canonical")
    if any(
        not code
        or len(code) > MAX_VALIDATION_WARNING_CODE_LENGTH
        or severity != "warning"
        for code, severity in warnings
    ):
        raise ValueError("Source-state validation warning is invalid")
    return canonical
