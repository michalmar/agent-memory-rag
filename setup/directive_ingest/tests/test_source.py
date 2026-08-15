from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from azure.core import MatchConditions
from azure.core.exceptions import ResourceModifiedError

from directive_ingestion.source import (
    BlobDirectiveSource,
    DirectiveSourceError,
    LocalDirectiveSource,
    discover_pdfs,
)


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


class _Download:
    def __init__(self, content: bytes):
        self._content = content

    async def readall(self) -> bytes:
        return self._content


class _Blob:
    def __init__(self, content: bytes, expected_etag: str):
        self.content = content
        self.expected_etag = expected_etag
        self.calls = []
        self.failure = None

    async def download_blob(self, **kwargs):
        self.calls.append(kwargs)
        if self.failure is not None:
            raise self.failure
        assert kwargs == {
            "etag": self.expected_etag,
            "match_condition": MatchConditions.IfNotModified,
        }
        return _Download(self.content)


class _Container:
    def __init__(self, properties, content_by_name):
        self.properties = properties
        self.blobs = {
            item.name: _Blob(content_by_name[item.name], item.etag)
            for item in properties
        }
        self.prefixes = []
        self.checked = False

    async def get_container_properties(self):
        self.checked = True

    def list_blobs(self, *, name_starts_with=None):
        self.prefixes.append(name_starts_with)
        return _AsyncItems(self.properties)

    def get_blob_client(self, name):
        return self.blobs[name]


def _properties(name: str, etag: str):
    return SimpleNamespace(
        name=name,
        etag=etag,
        version_id="version-1",
        size=12,
        last_modified=datetime(2026, 7, 25, tzinfo=UTC),
    )


def _blob_source(
    monkeypatch,
    container: _Container,
    prefix: str = "",
    max_corpus_bytes: int = 512 * 1024 * 1024,
):
    service = SimpleNamespace(
        get_container_client=lambda _name: container,
        close=lambda: None,
    )

    async def close():
        return None

    service.close = close
    monkeypatch.setattr(
        "directive_ingestion.source.BlobServiceClient",
        lambda **_kwargs: service,
    )
    return BlobDirectiveSource(
        "https://storage.example",
        "directive-source",
        prefix,
        object(),
        max_corpus_bytes=max_corpus_bytes,
    )


@pytest.mark.asyncio
async def test_local_source_retains_exact_safe_basename(tmp_path: Path) -> None:
    (tmp_path / "Pokyn interní 2026.PDF").write_bytes(b"%PDF-local")

    documents = await LocalDirectiveSource(tmp_path).discover()

    assert [item.source_name for item in documents] == ["Pokyn interní 2026.PDF"]
    assert len(documents[0].source_hash) == 64
    assert not hasattr(documents[0], "directive_id_hint")


@pytest.mark.asyncio
async def test_blob_source_lists_prefix_and_downloads_with_etag(
    monkeypatch,
) -> None:
    properties = [
        _properties("incoming/záznam zeta.pdf", '"etag-2"'),
        _properties("incoming/záznam alpha.pdf", '"etag-1"'),
    ]
    container = _Container(
        properties,
        {
            properties[0].name: b"%PDF-zeta",
            properties[1].name: b"%PDF-alpha",
        },
    )
    source = _blob_source(monkeypatch, container, "incoming")

    documents = await source.discover()

    assert [item.source_name for item in documents] == [
        "záznam alpha.pdf",
        "záznam zeta.pdf",
    ]
    assert container.prefixes == ["incoming/"]
    assert container.blobs[properties[0].name].calls[0][
        "match_condition"
    ] is MatchConditions.IfNotModified


@pytest.mark.asyncio
async def test_blob_source_rejects_empty_container(monkeypatch) -> None:
    source = _blob_source(monkeypatch, _Container([], {}))

    with pytest.raises(DirectiveSourceError, match="No directive PDFs"):
        await source.discover()


@pytest.mark.asyncio
async def test_blob_source_rejects_oversized_corpus_before_download(
    monkeypatch,
) -> None:
    properties = [_properties("policy.pdf", '"etag-1"')]
    properties[0].size = 101
    container = _Container(
        properties,
        {properties[0].name: b"%PDF-policy"},
    )
    source = _blob_source(
        monkeypatch,
        container,
        max_corpus_bytes=100,
    )

    with pytest.raises(DirectiveSourceError, match="corpus exceeds"):
        await source.discover()

    assert container.blobs[properties[0].name].calls == []


@pytest.mark.asyncio
async def test_blob_source_surfaces_etag_change(monkeypatch) -> None:
    properties = [_properties("policy.pdf", '"etag-1"')]
    container = _Container(
        properties,
        {properties[0].name: b"%PDF-policy"},
    )
    container.blobs[properties[0].name].failure = ResourceModifiedError(
        "changed"
    )
    source = _blob_source(monkeypatch, container)

    with pytest.raises(DirectiveSourceError, match="changed or became"):
        await source.discover()


@pytest.mark.asyncio
async def test_blob_source_rejects_duplicate_content_hash(monkeypatch) -> None:
    properties = [
        _properties("policy-a.pdf", '"etag-1"'),
        _properties("policy-b.pdf", '"etag-2"'),
    ]
    container = _Container(
        properties,
        {
            properties[0].name: b"%PDF-policy",
            properties[1].name: b"%PDF-policy",
        },
    )
    source = _blob_source(monkeypatch, container)

    with pytest.raises(ValueError, match="Duplicate directive source content"):
        await source.discover()


def test_discovery_rejects_duplicate_content_hashes(tmp_path: Path) -> None:
    (tmp_path / "a.pdf").write_bytes(b"%PDF-same")
    (tmp_path / "b.pdf").write_bytes(b"%PDF-same")

    with pytest.raises(ValueError, match="Duplicate directive source content"):
        discover_pdfs(tmp_path)
