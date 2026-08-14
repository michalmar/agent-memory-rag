"""Private, immutable source-state records used for ingestion idempotency."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from directive_contracts import (
    DirectiveMetadata,
    PublishedDirectiveVersion,
    source_fingerprint,
)

from .blob_repository import BlobArtifactRepository
from .integrity import IntegrityValidationError
from .source import SourceDocument


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
    pending_cleanup: tuple[PublishedDirectiveVersion, ...] = ()


@dataclass(frozen=True)
class SourceStateSnapshot:
    blob_name: str
    content: bytes
    etag: str


class SourceStateRepository:
    def __init__(self, blobs: BlobArtifactRepository) -> None:
        self._blobs = blobs

    @staticmethod
    def blob_name(source: SourceDocument, processing_hash: str) -> str:
        return (
            f"source-state/{source_fingerprint(source.source_name, source.source_hash)}"
            f"/{processing_hash}.json"
        )

    async def load(
        self, source: SourceDocument, processing_hash: str
    ) -> PublishedSourceState | None:
        try:
            value = await self._blobs.get_json(
                self.blob_name(source, processing_hash)
            )
        except IntegrityValidationError:
            return None
        if value is None:
            return None
        try:
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
                pending_cleanup=tuple(
                    PublishedDirectiveVersion.model_validate(bundle)
                    for bundle in value.get("pending_cleanup", [])
                ),
            )
        except (KeyError, TypeError, ValueError):
            return None
        expected_fingerprint = source_fingerprint(
            source.source_name, source.source_hash
        )
        metadata = state.directive_metadata
        if (
            state.source_filename != source.source_name
            or state.source_hash != source.source_hash
            or state.source_fingerprint != expected_fingerprint
            or state.processing_hash != processing_hash
            or state.publication_state != "published"
            or metadata.source_filename != source.source_name
            or metadata.source_hash != source.source_hash
            or metadata.processing_hash != processing_hash
        ):
            return None
        return state

    async def record(
        self,
        source: SourceDocument,
        metadata: DirectiveMetadata,
        artifact_generation_id: str,
        pending_cleanup: tuple[PublishedDirectiveVersion, ...] = (),
    ) -> str:
        return await self._blobs.replace_json(
            self.blob_name(source, metadata.processing_hash),
            {
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
                "pending_cleanup": [
                    bundle.model_dump(mode="json") for bundle in pending_cleanup
                ],
            },
        )

    async def clear_pending(
        self,
        source: SourceDocument,
        metadata: DirectiveMetadata,
        artifact_generation_id: str,
    ) -> None:
        await self.record(source, metadata, artifact_generation_id)

    async def prune(self, expected_names: set[str]) -> None:
        """Remove every state record outside exact source+processing identities."""
        names = await self._blobs.list_names("source-state/")
        stale = names - expected_names
        if stale:
            await self._blobs.delete_names(stale)

    async def list_names(self) -> set[str]:
        return await self._blobs.list_names("source-state/")

    async def delete(
        self, source: SourceDocument, processing_hash: str
    ) -> None:
        await self._blobs.delete_names(
            {self.blob_name(source, processing_hash)}
        )

    async def snapshot(
        self, source: SourceDocument, processing_hash: str
    ) -> SourceStateSnapshot | None:
        name = self.blob_name(source, processing_hash)
        value = await self._blobs.read_bytes_with_etag(name)
        if value is None:
            return None
        content, etag = value
        return SourceStateSnapshot(name, content, etag)

    async def restore(
        self, snapshot: SourceStateSnapshot | None,
        source: SourceDocument,
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
