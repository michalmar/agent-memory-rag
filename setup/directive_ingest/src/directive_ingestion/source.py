"""Directive PDF source adapters and shared validation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from azure.core import MatchConditions
from azure.core.exceptions import AzureError
from azure.storage.blob.aio import BlobServiceClient
from directive_contracts import (
    normalize_directive_source_prefix,
    validate_directive_source_basename,
)

DEFAULT_MAX_CORPUS_BYTES = 512 * 1024 * 1024


class DirectiveSourceError(RuntimeError):
    """Directive source cannot be listed or read safely."""


@dataclass(frozen=True, slots=True)
class SourceProvenance:
    kind: str
    locator: str
    etag: str | None = None
    version_id: str | None = None
    size: int | None = None
    last_modified: datetime | None = None


@dataclass(frozen=True, slots=True)
class SourceDocument:
    source_name: str
    source_hash: str
    content: bytes
    _provenance: SourceProvenance = field(repr=False, compare=False)


class DirectiveSource(Protocol):
    async def discover(self) -> list[SourceDocument]: ...

    async def check_access(self) -> None: ...

    async def close(self) -> None: ...


class LocalDirectiveSource:
    def __init__(
        self,
        source_directory: Path,
        max_corpus_bytes: int = DEFAULT_MAX_CORPUS_BYTES,
    ) -> None:
        self._source_directory = source_directory
        self._max_corpus_bytes = max_corpus_bytes

    async def discover(self) -> list[SourceDocument]:
        return discover_pdfs(
            self._source_directory,
            max_corpus_bytes=self._max_corpus_bytes,
        )

    async def check_access(self) -> None:
        discover_pdfs(
            self._source_directory,
            max_corpus_bytes=self._max_corpus_bytes,
        )

    async def close(self) -> None:
        return None


class BlobDirectiveSource:
    def __init__(
        self,
        account_url: str,
        container_name: str,
        prefix: str | None,
        credential: Any,
        *,
        max_corpus_bytes: int = DEFAULT_MAX_CORPUS_BYTES,
    ) -> None:
        self._service = BlobServiceClient(
            account_url=account_url,
            credential=credential,
        )
        self._container = self._service.get_container_client(container_name)
        self._prefix = normalize_directive_source_prefix(prefix)
        self._max_corpus_bytes = max_corpus_bytes

    async def discover(self) -> list[SourceDocument]:
        candidates = await self._list_candidates()
        documents = [
            await self._download_document(blob)
            for blob in candidates
        ]
        return _validate_document_set(documents, "Azure Blob Storage")

    async def check_access(self) -> None:
        try:
            await self._container.get_container_properties()
            candidates = await self._list_candidates()
            await self._download_document(candidates[0])
        except DirectiveSourceError:
            raise
        except AzureError as exc:
            raise DirectiveSourceError(
                "Directive source container is not accessible"
            ) from exc

    async def close(self) -> None:
        await self._service.close()

    async def _list_candidates(self) -> list[Any]:
        try:
            blobs = [
                blob
                async for blob in self._container.list_blobs(
                    name_starts_with=self._prefix or None
                )
                if str(getattr(blob, "name", "")).casefold().endswith(".pdf")
            ]
        except AzureError as exc:
            raise DirectiveSourceError(
                "Directive source container could not be listed"
            ) from exc
        blobs.sort(key=lambda blob: str(blob.name))
        if not blobs:
            location = self._prefix or "container root"
            raise DirectiveSourceError(
                f"No directive PDFs found under {location}"
            )
        total_bytes = 0
        for blob in blobs:
            size = getattr(blob, "size", None)
            if (
                not isinstance(size, int)
                or isinstance(size, bool)
                or size < 1
            ):
                raise DirectiveSourceError(
                    f"Directive source has an invalid size: {blob.name}"
                )
            total_bytes += size
            if total_bytes > self._max_corpus_bytes:
                raise DirectiveSourceError(
                    "Directive source corpus exceeds "
                    f"{self._max_corpus_bytes} bytes"
                )
        return blobs

    async def _download_document(self, properties: Any) -> SourceDocument:
        blob_name = str(properties.name)
        source_name = _relative_source_name(blob_name, self._prefix)
        etag = getattr(properties, "etag", None)
        if not isinstance(etag, str) or not etag:
            raise DirectiveSourceError(
                f"Directive source is missing an ETag: {source_name}"
            )
        blob = self._container.get_blob_client(blob_name)
        try:
            stream = await blob.download_blob(
                etag=etag,
                match_condition=MatchConditions.IfNotModified,
            )
            content = await stream.readall()
        except AzureError as exc:
            raise DirectiveSourceError(
                "Directive source changed or became unavailable while reading: "
                f"{source_name}"
            ) from exc
        provenance = SourceProvenance(
            kind="azure_blob",
            locator=blob_name,
            etag=etag,
            version_id=getattr(properties, "version_id", None),
            size=getattr(properties, "size", None),
            last_modified=getattr(properties, "last_modified", None),
        )
        return _build_document(source_name, content, provenance)


def discover_pdfs(
    source_directory: Path,
    *,
    max_corpus_bytes: int = DEFAULT_MAX_CORPUS_BYTES,
) -> list[SourceDocument]:
    if not source_directory.is_dir():
        raise ValueError(
            f"Directive source directory does not exist: {source_directory}"
        )
    paths = sorted(
        (
            path
            for path in source_directory.iterdir()
            if path.is_file() and path.suffix.casefold() == ".pdf"
        ),
        key=lambda path: path.name,
    )
    total_bytes = sum(path.stat().st_size for path in paths)
    if total_bytes > max_corpus_bytes:
        raise ValueError(
            f"Directive source corpus exceeds {max_corpus_bytes} bytes"
        )
    documents = [
        _build_document(
            path.name,
            path.read_bytes(),
            SourceProvenance(
                kind="local",
                locator=str(path),
                size=path.stat().st_size,
                last_modified=datetime.fromtimestamp(path.stat().st_mtime),
            ),
        )
        for path in paths
    ]
    return _validate_document_set(documents, str(source_directory))


def _build_document(
    source_name: str,
    content: bytes,
    provenance: SourceProvenance,
) -> SourceDocument:
    source_name = validate_directive_source_basename(source_name)
    if not content.startswith(b"%PDF"):
        raise ValueError(f"Directive source is not a PDF: {source_name}")
    return SourceDocument(
        source_name=source_name,
        source_hash=hashlib.sha256(content).hexdigest(),
        content=content,
        _provenance=provenance,
    )


def _validate_document_set(
    documents: list[SourceDocument],
    location: str,
) -> list[SourceDocument]:
    if not documents:
        raise ValueError(f"No directive PDFs found under {location}")
    names = [item.source_name for item in documents]
    if len(set(names)) != len(names):
        raise ValueError("Duplicate directive source filenames found")
    hashes = [item.source_hash for item in documents]
    if len(set(hashes)) != len(hashes):
        raise ValueError("Duplicate directive source content hashes found")
    return documents


def _relative_source_name(blob_name: str, prefix: str) -> str:
    if prefix and not blob_name.startswith(prefix):
        raise DirectiveSourceError(
            "Directive source listing returned a blob outside its prefix"
        )
    source_name = blob_name[len(prefix) :] if prefix else blob_name
    if "/" in source_name or "\\" in source_name:
        raise ValueError(
            "Directive source PDFs must be direct children of the configured "
            f"prefix: {blob_name}"
        )
    return source_name
