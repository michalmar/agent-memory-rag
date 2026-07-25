"""Managed-identity access to directive source PDFs."""

from __future__ import annotations

import logging
from collections import OrderedDict
from datetime import datetime
from typing import Any, BinaryIO

from azure.core.exceptions import (
    AzureError,
    ResourceExistsError,
    ResourceNotFoundError,
)
from azure.storage.blob import ContentSettings
from directive_contracts import (
    DIRECTIVE_SOURCE_FILENAME_PATTERN,
    normalize_directive_source_prefix,
    parse_directive_source_filename,
)
from pydantic import BaseModel, ConfigDict, Field

from .config import get_settings
from .directive_errors import DirectiveDataUnavailable

logger = logging.getLogger("directive_sources")
_CURSOR_CACHE_LIMIT = 256


class DirectiveSourceConflict(RuntimeError):
    """A create-only source upload targeted an existing blob."""


class DirectiveSourceNotFound(RuntimeError):
    """The requested directive source blob does not exist."""


class DirectiveSourceInvalid(ValueError):
    """The source filename, PDF content, or pagination cursor is invalid."""


class DirectiveSourceTooLarge(ValueError):
    """The uploaded source exceeds the configured hard limit."""


class DirectiveSourceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(pattern=DIRECTIVE_SOURCE_FILENAME_PATTERN)
    size_bytes: int = Field(ge=1)
    last_modified: datetime


class DirectiveSourcePage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[DirectiveSourceItem]
    next_cursor: str | None = None


class DirectiveSourceRepository:
    def __init__(self) -> None:
        self._service: Any = None
        self._container: Any = None
        self._prefix = ""
        self._cursor_tokens: OrderedDict[str, str] = OrderedDict()
        self._cursor_sequence = 0

    @property
    def enabled(self) -> bool:
        return self._container is not None

    async def initialize(self) -> None:
        settings = get_settings()
        if not (
            settings.directive_blob_endpoint
            and settings.directive_source_container
        ):
            logger.warning("Directive source storage is not configured")
            return
        from azure.storage.blob.aio import BlobServiceClient

        from .azure_clients import get_credential

        self._service = BlobServiceClient(
            account_url=settings.directive_blob_endpoint,
            credential=get_credential(),
        )
        self._container = self._service.get_container_client(
            settings.directive_source_container
        )
        self._prefix = normalize_directive_source_prefix(
            settings.directive_source_prefix
        )

    async def close(self) -> None:
        if self._service is not None:
            await self._service.close()
            self._service = None
            self._container = None
            self._cursor_tokens.clear()

    async def health_check(self) -> None:
        container = self._require_container()
        try:
            await container.get_container_properties()
            async for _ in container.list_blobs(
                name_starts_with=self._prefix or None
            ):
                break
        except AzureError as exc:
            raise DirectiveDataUnavailable(
                "Directive source health check failed"
            ) from exc

    async def list_sources(
        self,
        *,
        cursor: str | None,
        limit: int,
    ) -> DirectiveSourcePage:
        continuation_token = self._resolve_cursor(cursor)
        if limit < 1 or limit > 100:
            raise DirectiveSourceInvalid("Source page limit must be 1..100")
        items: list[DirectiveSourceItem] = []
        try:
            pages = self._require_container().list_blobs(
                name_starts_with=self._prefix or None,
                results_per_page=limit,
            ).by_page(continuation_token=continuation_token)
            try:
                page = await anext(pages)
            except StopAsyncIteration:
                page = None
            if page is not None:
                async for blob in page:
                    source_name = _relative_source_name(
                        str(getattr(blob, "name", "")),
                        self._prefix,
                    )
                    if source_name is None:
                        continue
                    try:
                        parse_directive_source_filename(source_name)
                    except ValueError:
                        continue
                    size = getattr(blob, "size", None)
                    last_modified = getattr(blob, "last_modified", None)
                    if not isinstance(size, int) or size < 1:
                        raise DirectiveDataUnavailable(
                            "Directive source metadata is invalid"
                        )
                    if not isinstance(last_modified, datetime):
                        raise DirectiveDataUnavailable(
                            "Directive source metadata is invalid"
                        )
                    items.append(
                        DirectiveSourceItem(
                            filename=source_name,
                            size_bytes=size,
                            last_modified=last_modified,
                        )
                    )
        except DirectiveDataUnavailable:
            raise
        except AzureError as exc:
            raise DirectiveDataUnavailable(
                "Directive source list failed"
            ) from exc
        next_token = getattr(pages, "continuation_token", None)
        return DirectiveSourcePage(
            items=items,
            next_cursor=(
                self._remember_cursor(next_token)
                if isinstance(next_token, str) and next_token
                else None
            ),
        )

    async def upload_source(
        self,
        filename: str,
        content: BinaryIO,
        size_bytes: int,
    ) -> DirectiveSourceItem:
        try:
            identity = parse_directive_source_filename(filename)
        except ValueError as exc:
            raise DirectiveSourceInvalid(str(exc)) from exc
        if size_bytes < 1:
            raise DirectiveSourceInvalid("Directive source PDF is empty")
        blob = self._require_container().get_blob_client(
            f"{self._prefix}{identity.filename}"
        )
        try:
            await blob.upload_blob(
                content,
                length=size_bytes,
                overwrite=False,
                content_settings=ContentSettings(
                    content_type="application/pdf"
                ),
            )
            self._cursor_tokens.clear()
            properties = await blob.get_blob_properties()
        except ResourceExistsError as exc:
            raise DirectiveSourceConflict(
                "Directive source already exists"
            ) from exc
        except AzureError as exc:
            raise DirectiveDataUnavailable(
                "Directive source upload failed"
            ) from exc
        last_modified = getattr(properties, "last_modified", None)
        if not isinstance(last_modified, datetime):
            raise DirectiveDataUnavailable(
                "Directive source upload metadata is invalid"
            )
        return DirectiveSourceItem(
            filename=identity.filename,
            size_bytes=size_bytes,
            last_modified=last_modified,
        )

    async def delete_source(self, filename: str) -> None:
        try:
            identity = parse_directive_source_filename(filename)
        except ValueError as exc:
            raise DirectiveSourceInvalid(str(exc)) from exc
        try:
            await self._require_container().delete_blob(
                f"{self._prefix}{identity.filename}",
                delete_snapshots="include",
            )
            self._cursor_tokens.clear()
        except ResourceNotFoundError as exc:
            raise DirectiveSourceNotFound(
                "Directive source does not exist"
            ) from exc
        except AzureError as exc:
            raise DirectiveDataUnavailable(
                "Directive source deletion failed"
            ) from exc

    def _require_container(self) -> Any:
        if self._container is None:
            raise DirectiveDataUnavailable(
                "Directive source storage is unavailable"
            )
        return self._container

    def _resolve_cursor(self, cursor: str | None) -> str | None:
        if cursor is None:
            return None
        _parse_cursor(cursor)
        token = self._cursor_tokens.get(cursor)
        if token is None:
            raise DirectiveSourceInvalid(
                "Source page cursor is invalid or expired"
            )
        self._cursor_tokens.move_to_end(cursor)
        return token

    def _remember_cursor(self, continuation_token: str) -> str:
        self._cursor_sequence += 1
        cursor = str(self._cursor_sequence)
        self._cursor_tokens[cursor] = continuation_token
        while len(self._cursor_tokens) > _CURSOR_CACHE_LIMIT:
            self._cursor_tokens.popitem(last=False)
        return cursor


def _parse_cursor(value: str | None) -> int:
    if value is None:
        return 0
    if (
        len(value) > 12
        or not value.isascii()
        or not value.isdecimal()
    ):
        raise DirectiveSourceInvalid("Source page cursor is invalid")
    return int(value)


def _relative_source_name(blob_name: str, prefix: str) -> str | None:
    if not blob_name.casefold().endswith(".pdf"):
        return None
    if prefix and not blob_name.startswith(prefix):
        return None
    source_name = blob_name[len(prefix) :] if prefix else blob_name
    if not source_name or "/" in source_name or "\\" in source_name:
        return None
    return source_name
