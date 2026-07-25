"""Published directive document access without exposing storage coordinates."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date

from directive_contracts import PublishedDirectiveVersion
from pydantic import BaseModel, ConfigDict, Field

from .directive_artifacts import DirectiveArtifactRepository
from .directive_catalog import DirectiveCatalogRepository
from .directive_errors import DirectiveDataUnavailable


class DirectiveDocumentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    directive_id: str = Field(pattern=r"^\d{8}$")
    directive_version_id: str
    title: str
    version_label: str
    effective_from: date
    source_filename: str
    total_pages: int = Field(ge=1)
    markdown: str


@dataclass(frozen=True)
class DirectiveSourceStream:
    source_filename: str
    source_hash: str
    chunks: AsyncIterator[bytes]


class DirectiveDocumentService:
    def __init__(
        self,
        catalog: DirectiveCatalogRepository,
        artifacts: DirectiveArtifactRepository,
    ) -> None:
        self._catalog = catalog
        self._artifacts = artifacts

    async def get_document(
        self,
        directive_id: str,
        directive_version_id: str,
    ) -> DirectiveDocumentResponse | None:
        resolved = await self._resolve_published_version(
            directive_id,
            directive_version_id,
        )
        if resolved is None:
            return None
        public_version = self._catalog.public_version(resolved)
        markdown = await self._artifacts.read_text(
            resolved.artifacts.canonical_blob_name
        )
        return DirectiveDocumentResponse(
            directive_id=directive_id,
            directive_version_id=directive_version_id,
            title=public_version["title"],
            version_label=public_version["version_label"],
            effective_from=public_version["effective_from"],
            source_filename=_safe_source_filename(
                public_version["source_filename"],
                directive_id,
            ),
            total_pages=resolved.manifest.total_pages,
            markdown=markdown,
        )

    async def get_source(
        self,
        directive_id: str,
        directive_version_id: str,
    ) -> DirectiveSourceStream | None:
        resolved = await self._resolve_published_version(
            directive_id,
            directive_version_id,
        )
        if resolved is None:
            return None
        public_version = self._catalog.public_version(resolved)
        chunks = await self._artifacts.stream_bytes(
            resolved.artifacts.source_blob_name
        )
        return DirectiveSourceStream(
            source_filename=_safe_source_filename(
                public_version["source_filename"],
                directive_id,
            ),
            source_hash=resolved.source_hash,
            chunks=chunks,
        )

    async def _resolve_published_version(
        self,
        directive_id: str,
        directive_version_id: str,
    ) -> PublishedDirectiveVersion | None:
        bundle = await self._catalog.get_published_version(
            directive_id,
            directive_version_id,
        )
        if bundle is None:
            return None
        if (
            bundle.manifest.directive_id != directive_id
            or bundle.manifest.directive_version_id
            != directive_version_id
            or bundle.manifest.source_hash != bundle.source_hash
        ):
            raise DirectiveDataUnavailable(
                "Directive manifest identity mismatch"
            )
        return bundle


def _safe_source_filename(source_filename: str, directive_id: str) -> str:
    filename = source_filename.replace("\\", "/").rsplit("/", 1)[-1]
    filename = "".join(
        character
        for character in filename
        if character >= " " and character != "\x7f"
    ).strip()
    if not filename or not filename.lower().endswith(".pdf"):
        return f"{directive_id}.pdf"
    return filename
