from __future__ import annotations

import io
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from azure.core.exceptions import (
    ResourceExistsError,
    ResourceNotFoundError,
)
from fastapi import HTTPException

from agent_memory_backend.auth import (
    User,
    can_manage_directive_sources,
    require_directive_source_manager,
)
from agent_memory_backend.directive_sources import (
    DirectiveSourceConflict,
    DirectiveSourceInvalid,
    DirectiveSourceNotFound,
    DirectiveSourceRepository,
    DirectiveSourceTooLarge,
)
from agent_memory_backend.server import _spool_source_upload


class _AsyncItems:
    def __init__(self, values):
        self._values = values

    def __aiter__(self):
        self._iterator = iter(self._values)
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _AsyncPages:
    def __init__(self, values, start: int, page_size: int):
        self._values = values
        self._start = start
        self._page_size = page_size
        self._returned = False
        self.continuation_token = None

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._returned or self._start >= len(self._values):
            raise StopAsyncIteration
        self._returned = True
        end = min(self._start + self._page_size, len(self._values))
        if end < len(self._values):
            self.continuation_token = str(end)
        return _AsyncItems(self._values[self._start : end])


class _PagedItems(_AsyncItems):
    def __init__(self, values, page_size: int):
        super().__init__(values)
        self._page_size = page_size

    def by_page(self, continuation_token=None):
        return _AsyncPages(
            self._values,
            int(continuation_token or "0"),
            self._page_size,
        )


class _Request:
    def __init__(self, chunks: list[bytes], content_length: str = ""):
        self._chunks = chunks
        self.headers = (
            {"content-length": content_length}
            if content_length
            else {}
        )

    def stream(self):
        return _AsyncItems(self._chunks)


class _Blob:
    def __init__(self):
        self.upload_blob = AsyncMock()
        self.get_blob_properties = AsyncMock(
            return_value=SimpleNamespace(
                last_modified=datetime(2026, 7, 25, tzinfo=UTC)
            )
        )


class _Container:
    def __init__(self, items=None):
        self.items = items or []
        self.blob = _Blob()
        self.delete_blob = AsyncMock()
        self.page_sizes = []

    def list_blobs(self, **kwargs):
        page_size = kwargs.get("results_per_page")
        if page_size is None:
            return _AsyncItems(self.items)
        self.page_sizes.append(page_size)
        return _PagedItems(self.items, page_size)

    def get_blob_client(self, _name):
        return self.blob


@pytest.mark.asyncio
async def test_source_list_is_metadata_only_and_paginated() -> None:
    repository = DirectiveSourceRepository()
    container = _Container(
        [
            SimpleNamespace(
                name="12345678-alpha-v1.pdf",
                size=100,
                last_modified=datetime(2026, 7, 24, tzinfo=UTC),
                etag="private",
                version_id="private",
            ),
            SimpleNamespace(
                name="12345678-zeta-v2.pdf",
                size=200,
                last_modified=datetime(2026, 7, 25, tzinfo=UTC),
                etag="private",
                version_id="private",
            ),
        ]
    )
    repository._container = container

    first = await repository.list_sources(cursor=None, limit=1)
    second = await repository.list_sources(
        cursor=first.next_cursor,
        limit=1,
    )

    assert first.items[0].filename == "12345678-alpha-v1.pdf"
    assert first.next_cursor == "1"
    assert second.items[0].filename == "12345678-zeta-v2.pdf"
    assert second.next_cursor is None
    assert container.page_sizes == [1, 1]
    assert set(first.items[0].model_dump()) == {
        "filename",
        "size_bytes",
        "last_modified",
    }


@pytest.mark.asyncio
async def test_source_list_rejects_unknown_cursor() -> None:
    repository = DirectiveSourceRepository()
    repository._container = _Container()

    with pytest.raises(DirectiveSourceInvalid, match="invalid or expired"):
        await repository.list_sources(cursor="123", limit=10)


@pytest.mark.asyncio
async def test_source_upload_is_create_only() -> None:
    repository = DirectiveSourceRepository()
    container = _Container()
    container.blob.upload_blob.side_effect = ResourceExistsError("exists")
    repository._container = container

    with pytest.raises(DirectiveSourceConflict):
        await repository.upload_source(
            "12345678-policy-v1.pdf",
            io.BytesIO(b"%PDF-test"),
            9,
        )

    kwargs = container.blob.upload_blob.await_args.kwargs
    assert kwargs["overwrite"] is False
    assert kwargs["length"] == 9


@pytest.mark.asyncio
async def test_source_delete_maps_missing_blob() -> None:
    repository = DirectiveSourceRepository()
    container = _Container()
    container.delete_blob.side_effect = ResourceNotFoundError("missing")
    repository._container = container

    with pytest.raises(DirectiveSourceNotFound):
        await repository.delete_source("12345678-policy-v1.pdf")


@pytest.mark.asyncio
async def test_raw_upload_is_spooled_and_signature_checked() -> None:
    destination = io.BytesIO()

    size = await _spool_source_upload(
        _Request([b"%P", b"DF-content"]),
        destination,
        100,
    )

    assert size == 12
    assert destination.read() == b"%PDF-content"


@pytest.mark.asyncio
async def test_raw_upload_enforces_hard_limit_before_commit() -> None:
    with pytest.raises(DirectiveSourceTooLarge):
        await _spool_source_upload(
            _Request([b"%PDF-content"], content_length="12"),
            io.BytesIO(),
            10,
        )


@pytest.mark.asyncio
async def test_manage_role_is_separate_from_general_auth() -> None:
    user = User(
        "tenant:user",
        "User",
        "user@example.com",
        "U",
        roles=frozenset(),
    )
    manager = User(
        "tenant:manager",
        "Manager",
        "manager@example.com",
        "M",
        roles=frozenset({"DirectiveSource.Manage"}),
    )
    settings = SimpleNamespace(
        auth_mode="entra",
        directive_source_manage_role="DirectiveSource.Manage",
    )
    with patch(
        "agent_memory_backend.auth.get_settings",
        return_value=settings,
    ):
        assert not can_manage_directive_sources(user)
        assert can_manage_directive_sources(manager)
        with pytest.raises(HTTPException) as rejected:
            await require_directive_source_manager(user)
        accepted = await require_directive_source_manager(manager)

    assert rejected.value.status_code == 403
    assert accepted is manager
