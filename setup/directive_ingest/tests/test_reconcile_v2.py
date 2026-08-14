from __future__ import annotations

import hashlib
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from directive_contracts import DirectiveMetadata

from directive_ingestion.reconcile import (
    DirectiveIngestionRunner,
    SourceMetadata,
    _build_artifact_locators,
)
from directive_ingestion.source import SourceDocument, SourceProvenance
from directive_ingestion.source_state_repository import PublishedSourceState


def _source(name: str = "neutral.pdf") -> SourceDocument:
    content = f"%PDF-v2-{name}".encode()
    return SourceDocument(
        source_name=name,
        source_hash=hashlib.sha256(content).hexdigest(),
        content=content,
        _provenance=SourceProvenance(kind="test", locator=name),
    )


def _metadata(source: SourceDocument, identifier: str = "Č/12") -> DirectiveMetadata:
    return DirectiveMetadata(
        directive_id=identifier,
        directive_version_id=f"{identifier}:v1",
        version_label="1.0",
        title="Bezpečný název",
        status="Current",
        is_current=True,
        is_valid=True,
        effective_from=date(2026, 1, 1),
        source_filename=source.source_name,
        source_hash=source.source_hash,
        processing_hash="a" * 64,
    )


@pytest.mark.asyncio
async def test_trusted_state_skips_document_intelligence() -> None:
    source = _source()
    metadata = _metadata(source)
    state = PublishedSourceState(
        source_filename=source.source_name,
        source_hash=source.source_hash,
        source_fingerprint="b" * 64,
        processing_hash=metadata.processing_hash,
        directive_metadata=metadata,
        artifact_generation_id="c" * 64,
        publication_state="published",
    )
    runner = object.__new__(DirectiveIngestionRunner)
    runner.config = SimpleNamespace(processing_hash=metadata.processing_hash)
    runner.source_states = SimpleNamespace(load=AsyncMock(return_value=state))
    runner._state_has_live_publication = AsyncMock(return_value=True)
    runner.extractor = SimpleNamespace(extract=AsyncMock())
    runner.blobs = SimpleNamespace(quarantine=AsyncMock())

    result = await runner.extract_or_load_metadata([source], "run")

    assert result == [
        SourceMetadata(source, metadata, extraction=None, source_state=state)
    ]
    runner.extractor.extract.assert_not_awaited()


@pytest.mark.asyncio
async def test_inconsistent_state_reextracts_metadata() -> None:
    source = _source()
    metadata = _metadata(source)
    runner = object.__new__(DirectiveIngestionRunner)
    runner.config = SimpleNamespace(processing_hash=metadata.processing_hash)
    runner.source_states = SimpleNamespace(load=AsyncMock(return_value=None))
    runner._state_has_live_publication = AsyncMock()
    runner.extractor = SimpleNamespace(extract=AsyncMock(return_value=object()))
    runner.blobs = SimpleNamespace(quarantine=AsyncMock())

    import directive_ingestion.metadata as metadata_module

    original = metadata_module.extract_metadata
    metadata_module.extract_metadata = lambda *_args: SimpleNamespace(
        metadata=metadata
    )
    try:
        result = await runner.extract_or_load_metadata([source], "run")
    finally:
        metadata_module.extract_metadata = original

    assert result[0].changed is True
    runner.extractor.extract.assert_awaited_once_with(source.content)


@pytest.mark.asyncio
async def test_duplicate_ids_abort_before_model_preparation_and_quarantine() -> None:
    first = _source("first.pdf")
    second = _source("second.pdf")
    runner = object.__new__(DirectiveIngestionRunner)
    runner.blobs = SimpleNamespace(quarantine=AsyncMock())
    records = [
        SourceMetadata(first, _metadata(first), None, None),
        SourceMetadata(second, _metadata(second), None, None),
    ]

    with pytest.raises(RuntimeError, match="Source-set validation failed"):
        await runner._validate_and_quarantine(records, "run")

    assert runner.blobs.quarantine.await_count == 2


def test_artifact_paths_contain_only_internal_keys() -> None:
    source = _source()
    metadata = _metadata(source, "Č/12")
    directive = SimpleNamespace(metadata=metadata)

    locators = _build_artifact_locators(directive, "d" * 64)

    assert "Č/12" not in locators.source_blob_name
    assert locators.source_blob_name.endswith("/source.pdf")
    assert locators.canonical_blob_name.endswith(
        f"/generations/{'d' * 64}/document.md"
    )


@pytest.mark.asyncio
async def test_source_state_is_written_only_after_cross_store_validation() -> None:
    events: list[str] = []
    metadata = _metadata(_source())
    item = SimpleNamespace(
        bundle=SimpleNamespace(artifact_generation_id="c" * 64),
        source=_source(),
        canonical=SimpleNamespace(metadata=metadata),
        search_chunks=[object()],
    )
    runner = object.__new__(DirectiveIngestionRunner)
    runner.catalog = SimpleNamespace(
        validate_published=AsyncMock(side_effect=lambda *_: events.append("catalog"))
    )
    runner.content = SimpleNamespace(
        validate_bundle=AsyncMock(side_effect=lambda *_: events.append("content"))
    )
    runner.search = SimpleNamespace(
        validate_published=AsyncMock(side_effect=lambda *_: events.append("search"))
    )
    runner.source_states = SimpleNamespace(
        record=AsyncMock(side_effect=lambda *_: events.append("state"))
    )

    await runner.record_source_states([item])

    assert events == ["catalog", "content", "search", "state"]
