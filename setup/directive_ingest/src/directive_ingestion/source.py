"""Directive PDF source descriptors and conditional source reads."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
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
class SourceDescriptor:
    source_name: str
    kind: str
    locator: str
    etag: str | None
    version_id: str | None
    size: int
    last_modified: datetime | None

    def __post_init__(self) -> None:
        validate_directive_source_basename(self.source_name)
        if self.kind not in {"local", "azure_blob", "test", "memory"}:
            raise ValueError("Directive source descriptor kind is invalid")
        if not self.locator:
            raise ValueError("Directive source descriptor locator is required")
        if not isinstance(self.size, int) or isinstance(self.size, bool) or self.size < 1:
            raise ValueError("Directive source descriptor size must be positive")
        if self.kind == "azure_blob" and not self.etag:
            raise ValueError("Azure Blob source descriptors require an ETag")
        if self.last_modified is not None and self.last_modified.tzinfo is None:
            object.__setattr__(
                self,
                "last_modified",
                self.last_modified.replace(tzinfo=UTC),
            )


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    source_name: str
    source_hash: str

    def __post_init__(self) -> None:
        validate_directive_source_basename(self.source_name)
        if (
            len(self.source_hash) != 64
            or any(character not in "0123456789abcdef" for character in self.source_hash)
        ):
            raise ValueError("Directive source hash must be lowercase SHA-256")


@dataclass(frozen=True, slots=True)
class SourceReference:
    descriptor: SourceDescriptor
    identity: SourceIdentity

    @property
    def source_name(self) -> str:
        return self.identity.source_name

    @property
    def source_hash(self) -> str:
        return self.identity.source_hash


@dataclass(frozen=True, slots=True)
class SourceDocument(SourceReference):
    content: bytes

    def reference(self) -> SourceReference:
        return SourceReference(
            descriptor=self.descriptor,
            identity=self.identity,
        )


class DirectiveSource(Protocol):
    async def list_descriptors(self) -> list[SourceDescriptor]: ...

    async def download(self, descriptor: SourceDescriptor) -> SourceDocument: ...

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

    async def list_descriptors(self) -> list[SourceDescriptor]:
        return _local_descriptors(
            self._source_directory,
            max_corpus_bytes=self._max_corpus_bytes,
        )

    async def download(self, descriptor: SourceDescriptor) -> SourceDocument:
        if descriptor.kind != "local":
            raise DirectiveSourceError("Local source received a non-local descriptor")
        path = Path(descriptor.locator)
        try:
            stat = path.stat()
            if (
                stat.st_size != descriptor.size
                or _utc_datetime(stat.st_mtime) != descriptor.last_modified
            ):
                raise DirectiveSourceError(
                    "Directive source changed while reading: "
                    f"{descriptor.source_name}"
                )
            content = path.read_bytes()
        except DirectiveSourceError:
            raise
        except OSError as exc:
            raise DirectiveSourceError(
                f"Directive source could not be read: {descriptor.source_name}"
            ) from exc
        return _build_document(descriptor, content)

    async def check_access(self) -> None:
        await self.list_descriptors()

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

    async def list_descriptors(self) -> list[SourceDescriptor]:
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
            raise DirectiveSourceError(f"No directive PDFs found under {location}")
        descriptors = [
            self._descriptor(blob)
            for blob in blobs
        ]
        try:
            _validate_descriptor_set(
                descriptors,
                "Azure Blob Storage",
                self._max_corpus_bytes,
            )
        except ValueError as exc:
            raise DirectiveSourceError(str(exc)) from exc
        return descriptors

    async def download(self, descriptor: SourceDescriptor) -> SourceDocument:
        if descriptor.kind != "azure_blob":
            raise DirectiveSourceError(
                "Azure Blob source received a non-blob descriptor"
            )
        if not descriptor.etag:
            raise DirectiveSourceError(
                f"Directive source is missing an ETag: {descriptor.source_name}"
            )
        blob = self._container.get_blob_client(
            descriptor.locator,
            version_id=descriptor.version_id,
        )
        try:
            stream = await blob.download_blob(
                etag=descriptor.etag,
                match_condition=MatchConditions.IfNotModified,
            )
            content = await stream.readall()
        except AzureError as exc:
            raise DirectiveSourceError(
                "Directive source changed or became unavailable while reading: "
                f"{descriptor.source_name}"
            ) from exc
        return _build_document(descriptor, content)

    async def check_access(self) -> None:
        try:
            await self._container.get_container_properties()
            descriptors = await self.list_descriptors()
            descriptor = descriptors[0]
            blob = self._container.get_blob_client(
                descriptor.locator,
                version_id=descriptor.version_id,
            )
            stream = await blob.download_blob(
                offset=0,
                length=1,
                etag=descriptor.etag,
                match_condition=MatchConditions.IfNotModified,
            )
            prefix = await stream.readall()
            if prefix != b"%":
                raise DirectiveSourceError(
                    "Directive source permission probe did not return PDF data"
                )
        except DirectiveSourceError:
            raise
        except AzureError as exc:
            raise DirectiveSourceError(
                "Directive source container is not accessible"
            ) from exc

    async def close(self) -> None:
        await self._service.close()

    def _descriptor(self, properties: Any) -> SourceDescriptor:
        blob_name = str(properties.name)
        source_name = _relative_source_name(blob_name, self._prefix)
        etag = getattr(properties, "etag", None)
        size = getattr(properties, "size", None)
        if not isinstance(etag, str) or not etag:
            raise DirectiveSourceError(
                f"Directive source is missing an ETag: {source_name}"
            )
        if not isinstance(size, int) or isinstance(size, bool) or size < 1:
            raise DirectiveSourceError(
                f"Directive source has an invalid size: {source_name}"
            )
        version_id = getattr(properties, "version_id", None)
        if version_id is not None and not isinstance(version_id, str):
            raise DirectiveSourceError(
                f"Directive source has an invalid version ID: {source_name}"
            )
        last_modified = getattr(properties, "last_modified", None)
        if last_modified is not None and not isinstance(last_modified, datetime):
            raise DirectiveSourceError(
                f"Directive source has an invalid modification time: {source_name}"
            )
        return SourceDescriptor(
            source_name=source_name,
            kind="azure_blob",
            locator=blob_name,
            etag=etag,
            version_id=version_id,
            size=size,
            last_modified=last_modified,
        )


def discover_pdfs(
    source_directory: Path,
    *,
    max_corpus_bytes: int = DEFAULT_MAX_CORPUS_BYTES,
) -> list[SourceDocument]:
    """Read local PDFs for tooling that intentionally needs complete bodies."""
    source = LocalDirectiveSource(source_directory, max_corpus_bytes)
    descriptors = _local_descriptors(
        source_directory,
        max_corpus_bytes=max_corpus_bytes,
    )
    documents = [
        _build_document(descriptor, Path(descriptor.locator).read_bytes())
        for descriptor in descriptors
    ]
    return validate_document_set(documents, str(source_directory))


def _local_descriptors(
    source_directory: Path,
    *,
    max_corpus_bytes: int,
) -> list[SourceDescriptor]:
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
    descriptors = []
    for path in paths:
        stat = path.stat()
        descriptors.append(
            SourceDescriptor(
                source_name=path.name,
                kind="local",
                locator=str(path),
                etag=None,
                version_id=None,
                size=stat.st_size,
                last_modified=_utc_datetime(stat.st_mtime),
            )
        )
    return _validate_descriptor_set(
        descriptors,
        str(source_directory),
        max_corpus_bytes,
    )


def _build_document(
    descriptor: SourceDescriptor,
    content: bytes,
) -> SourceDocument:
    if len(content) != descriptor.size:
        raise DirectiveSourceError(
            f"Directive source size changed while reading: {descriptor.source_name}"
        )
    if not content.startswith(b"%PDF"):
        raise ValueError(f"Directive source is not a PDF: {descriptor.source_name}")
    return SourceDocument(
        descriptor=descriptor,
        identity=SourceIdentity(
            source_name=descriptor.source_name,
            source_hash=hashlib.sha256(content).hexdigest(),
        ),
        content=content,
    )


def _validate_descriptor_set(
    descriptors: list[SourceDescriptor],
    location: str,
    max_corpus_bytes: int,
) -> list[SourceDescriptor]:
    if not descriptors:
        raise ValueError(f"No directive PDFs found under {location}")
    names = [item.source_name for item in descriptors]
    if len(set(names)) != len(names):
        raise ValueError("Duplicate directive source filenames found")
    total_bytes = sum(item.size for item in descriptors)
    if total_bytes > max_corpus_bytes:
        raise ValueError(
            f"Directive source corpus exceeds {max_corpus_bytes} bytes"
        )
    return descriptors


def validate_document_set(
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


def _utc_datetime(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp, tz=UTC)
