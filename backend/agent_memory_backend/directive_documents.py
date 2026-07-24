"""Published directive document access without exposing storage coordinates."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date
from typing import Any

from directive_contracts import DirectiveManifest
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
        public_version, manifest = resolved
        markdown = await self._artifacts.read_text(
            manifest.canonical_blob_name
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
            total_pages=manifest.total_pages,
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
        public_version, manifest = resolved
        chunks = await self._artifacts.stream_bytes(manifest.source_blob_name)
        return DirectiveSourceStream(
            source_filename=_safe_source_filename(
                public_version["source_filename"],
                directive_id,
            ),
            source_hash=manifest.source_hash,
            chunks=chunks,
        )

    async def _resolve_published_version(
        self,
        directive_id: str,
        directive_version_id: str,
    ) -> tuple[dict[str, Any], DirectiveManifest] | None:
        version = await self._catalog.get_version_record(
            directive_id,
            directive_version_id,
        )
        if version is None:
            return None

        manifest = await self._catalog.get_manifest(
            directive_id,
            directive_version_id,
        )
        if manifest is None:
            raise DirectiveDataUnavailable(
                "Directive manifest unavailable"
            )
        if (
            manifest.directive_id != directive_id
            or manifest.directive_version_id != directive_version_id
            or manifest.source_hash != version.get("source_hash")
        ):
            raise DirectiveDataUnavailable(
                "Directive manifest identity mismatch"
            )
        return self._catalog.public_version(version), manifest


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
