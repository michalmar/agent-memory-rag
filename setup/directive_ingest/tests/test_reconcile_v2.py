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
from directive_ingestion.source_state_repository import SourceStateRepository


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


@pytest.mark.asyncio
async def test_prepare_changed_documents_builds_first_generation() -> None:
    source = _source()
    metadata = _metadata(source)
    canonical = SimpleNamespace(
        metadata=metadata,
        sections=(),
        findings=(),
        relations=(),
    )
    runner = object.__new__(DirectiveIngestionRunner)
    runner.config = SimpleNamespace(
        processing_hash=metadata.processing_hash,
        chunk_token_limit=800,
        chunk_overlap_tokens=120,
    )
    runner.summaries = SimpleNamespace(summarize=AsyncMock(return_value=object()))
    runner.search = SimpleNamespace(build_chunks=AsyncMock(return_value=[]))
    runner.blobs = SimpleNamespace(quarantine=AsyncMock())
    import directive_ingestion.reconcile as reconcile_module

    original_parse = reconcile_module.parse_canonical
    original_manifest = reconcile_module._build_manifest
    original_bundle = reconcile_module._build_published_bundle
    reconcile_module.parse_canonical = lambda *_args: canonical
    reconcile_module._build_manifest = lambda *_args: object()
    reconcile_module._build_published_bundle = lambda *_args: (object(), ())
    try:
        prepared = await runner.prepare_changed_documents(
            [SourceMetadata(source, metadata, object(), None)], "run"
        )
    finally:
        reconcile_module.parse_canonical = original_parse
        reconcile_module._build_manifest = original_manifest
        reconcile_module._build_published_bundle = original_bundle

    assert len(prepared) == 1
    assert prepared[0].source is source
    runner.summaries.summarize.assert_awaited_once_with(canonical)


@pytest.mark.asyncio
async def test_validate_output_has_finalize_guard_shape() -> None:
    source = _source()
    metadata = _metadata(source)
    runner = object.__new__(DirectiveIngestionRunner)
    runner.config = SimpleNamespace(
        processing_hash=metadata.processing_hash,
        processing_version="directive-v2-czech-layout",
        search_index="directive-chunks-v2",
        azure_tenant_id="a7b1484c-f66a-496a-b1cf-35631a50396c",
        mandate_csv=object(),
        source_kind="local",
        source_container="directive-source",
        blob_container="directive-artifacts",
        catalog_container="catalog",
        content_container="directive_content",
        mandate_container="user_mandates",
    )
    runner.discover_sources = AsyncMock(return_value=[source])
    runner.extract_or_load_metadata = AsyncMock(
        return_value=[SourceMetadata(source, metadata, None, None)]
    )
    runner._validate_and_quarantine = AsyncMock()
    import directive_ingestion.reconcile as reconcile_module

    original_mandates = reconcile_module.parse_mandates
    reconcile_module.parse_mandates = lambda *_args: SimpleNamespace(
        assignments=(), user_count=0
    )
    try:
        value = await runner.validate_inputs()
    finally:
        reconcile_module.parse_mandates = original_mandates

    assert {
        "normalized_directive_ids",
        "directive_version_ids",
        "warnings",
        "environment",
        "processing_version",
        "processing_hash",
        "search_index",
        "source_inventory_digest",
        "validation_execution_id",
        "validation_digest",
    }.issubset(value)


@pytest.mark.asyncio
async def test_malformed_source_state_reprocesses_and_is_replaced() -> None:
    source = _source()
    blobs = SimpleNamespace(
        get_json=AsyncMock(return_value={"type": "source_state"}),
        replace_json=AsyncMock(),
    )
    repository = SourceStateRepository(blobs)

    assert await repository.load(source, "a" * 64) is None

    await repository.record(source, _metadata(source), "c" * 64)
    blobs.replace_json.assert_awaited_once()


@pytest.mark.asyncio
async def test_exact_corpus_retires_all_removed_store_records() -> None:
    retained = _source()
    retired_bundle = SimpleNamespace(
        artifacts=SimpleNamespace(
            source_blob_name="directives/old/source.pdf",
            canonical_blob_name="directives/old/generations/old/document.md",
        )
    )
    runner = object.__new__(DirectiveIngestionRunner)
    runner.catalog = SimpleNamespace(
        remove_absent_versions=AsyncMock(return_value=[retired_bundle])
    )
    runner.search = SimpleNamespace(retire_generation=AsyncMock())
    runner.content = SimpleNamespace(delete_bundle=AsyncMock())
    runner.blobs = SimpleNamespace(delete_names=AsyncMock())
    runner.source_states = SimpleNamespace(prune=AsyncMock())

    await runner.reconcile_exact_corpus(
        [SourceMetadata(retained, _metadata(retained), None, None)]
    )

    runner.search.retire_generation.assert_awaited_once_with(retired_bundle)
    runner.content.delete_bundle.assert_awaited_once_with(retired_bundle)
    runner.blobs.delete_names.assert_awaited_once()
    runner.source_states.prune.assert_awaited_once_with(
        {(retained.source_name, retained.source_hash)}
    )
