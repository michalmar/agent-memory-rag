"""Published directive document access without exposing storage coordinates."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date

from directive_contracts import (
    PublishedDirectiveVersion,
    normalize_directive_id,
    validate_directive_source_basename,
    validate_directive_version_id,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .directive_artifacts import DirectiveArtifactRepository
from .directive_catalog import DirectiveCatalogRepository
from .directive_errors import DirectiveDataUnavailable


class DirectiveDocumentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    directive_id: str = Field(min_length=1, max_length=128)
    directive_version_id: str = Field(min_length=1, max_length=200)
    title: str
    version_label: str
    effective_from: date
    source_filename: str
    total_pages: int = Field(ge=1)
    markdown: str

    @field_validator("directive_id", mode="before")
    @classmethod
    def normalize_id(cls, value: str) -> str:
        return normalize_directive_id(value)

    @field_validator("source_filename")
    @classmethod
    def validate_source_filename(cls, value: str) -> str:
        return validate_directive_source_basename(value)

    @model_validator(mode="after")
    def validate_identity(self) -> DirectiveDocumentResponse:
        self.directive_version_id = validate_directive_version_id(
            self.directive_version_id, self.directive_id
        )
        return self


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
        directive_id, directive_version_id = _normalize_identity(
            directive_id, directive_version_id
        )
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
            source_filename=_safe_source_filename(public_version["source_filename"]),
            total_pages=resolved.manifest.total_pages,
            markdown=markdown,
        )

    async def get_source(
        self,
        directive_id: str,
        directive_version_id: str,
    ) -> DirectiveSourceStream | None:
        directive_id, directive_version_id = _normalize_identity(
            directive_id, directive_version_id
        )
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
            source_filename=_safe_source_filename(public_version["source_filename"]),
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


def _normalize_identity(
    directive_id: str, directive_version_id: str
) -> tuple[str, str]:
    try:
        normalized_id = normalize_directive_id(directive_id)
        return normalized_id, validate_directive_version_id(
            directive_version_id, normalized_id
        )
    except (TypeError, ValueError) as exc:
        raise DirectiveDataUnavailable("Directive identity is invalid") from exc


def _safe_source_filename(source_filename: str) -> str:
    try:
        return validate_directive_source_basename(source_filename)
    except (TypeError, ValueError) as exc:
        raise DirectiveDataUnavailable("Directive source filename is invalid") from exc
