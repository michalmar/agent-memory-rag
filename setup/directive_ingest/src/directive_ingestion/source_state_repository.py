"""Private, immutable source-state records used for ingestion idempotency."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from directive_contracts import DirectiveMetadata, source_fingerprint

from .blob_repository import BlobArtifactRepository
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
        except RuntimeError as exc:
            if str(exc).startswith("Invalid JSON artifact"):
                return None
            raise
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
    ) -> None:
        await self._blobs.replace_json(
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
            },
        )

    async def prune(
        self, expected: set[tuple[str, str]]
    ) -> None:
        """Remove state records whose filename/hash identity left the corpus."""
        names = await self._blobs.list_names("source-state/")
        stale: set[str] = set()
        for name in names:
            try:
                value = await self._blobs.get_json(name)
            except RuntimeError:
                stale.add(name)
                continue
            if value is None:
                continue
            filename = value.get("source_filename")
            source_hash = value.get("source_hash")
            if not isinstance(filename, str) or not isinstance(source_hash, str):
                stale.add(name)
                continue
            if (filename, source_hash) not in expected:
                stale.add(name)
        if stale:
            await self._blobs.delete_names(stale)
