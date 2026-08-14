from __future__ import annotations

import hashlib
import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from azure.core.exceptions import ResourceNotFoundError
from azure.cosmos import exceptions
from directive_contracts import (
    DirectiveMetadata,
    calculate_artifact_generation_id,
    canonical_json_hash,
)

from directive_ingestion.chunking import TextChunk
from directive_ingestion.blob_repository import BlobArtifactRepository
from directive_ingestion.catalog_repository import (
    CatalogSlotSnapshot,
    DirectiveCatalogRepository,
    _validate_published_bundle,
)
from directive_ingestion.integrity import (
    CatalogResetRequiredError,
    IntegrityValidationError,
)
from directive_ingestion.reconcile import (
    DirectiveIngestionRunner,
    MAX_PUBLIC_DIRECTIVES,
    PublicationSnapshot,
    SourceArtifactSnapshot,
    SourceMetadata,
    _generation_scoped_chunks,
    _generation_canonical_hash,
    _public_record_digest,
    _validation_digest_projection,
    _build_artifact_locators,
    _validate_public_corpus_limit,
    format_result,
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


def _live_bundle(
    source: SourceDocument, metadata: DirectiveMetadata, markdown: str
) -> SimpleNamespace:
    canonical_hash = hashlib.sha256(
        f"{source.source_name}\0{markdown}".encode("utf-8")
    ).hexdigest()
    generation_id = calculate_artifact_generation_id(
        metadata.processing_hash,
        canonical_hash,
        canonical_json_hash({}),
    )
    return SimpleNamespace(
        **metadata.model_dump(mode="python"),
        artifact_generation_id=generation_id,
        summary={},
        artifacts=_build_artifact_locators(
            SimpleNamespace(metadata=metadata), generation_id
        ),
    )


def _published_state(
    source: SourceDocument, metadata: DirectiveMetadata, generation_id: str
) -> PublishedSourceState:
    return PublishedSourceState(
        source_filename=source.source_name,
        source_hash=source.source_hash,
        source_fingerprint="b" * 64,
        processing_hash=metadata.processing_hash,
        directive_metadata=metadata,
        artifact_generation_id=generation_id,
        publication_state="published",
    )


@pytest.mark.asyncio
async def test_state_snapshot_reads_bytes_and_etag_from_one_download_response() -> None:
    stream = SimpleNamespace(
        properties=SimpleNamespace(etag="snapshot-etag"),
        readall=AsyncMock(return_value=b"snapshot"),
    )
    blob = SimpleNamespace(
        download_blob=AsyncMock(return_value=stream),
        get_blob_properties=AsyncMock(
            side_effect=AssertionError("snapshot must not preflight properties")
        ),
    )
    repository = object.__new__(BlobArtifactRepository)
    repository._container = SimpleNamespace(get_blob_client=lambda _: blob)

    assert await repository.read_bytes_with_etag("source-state/test.json") == (
        b"snapshot",
        "snapshot-etag",
    )
    blob.download_blob.assert_awaited_once()
    blob.get_blob_properties.assert_not_awaited()


@pytest.mark.asyncio
async def test_state_rollback_uses_only_the_candidate_write_etag() -> None:
    blob = SimpleNamespace(upload_blob=AsyncMock())
    container = SimpleNamespace(
        get_blob_client=lambda _: blob,
        delete_blob=AsyncMock(),
    )
    repository = object.__new__(BlobArtifactRepository)
    repository._container = container

    await repository.restore_bytes(
        "source-state/test.json", b"previous", "candidate-etag"
    )
    await repository.delete_if_etag("source-state/test.json", "candidate-etag")

    assert blob.upload_blob.await_args.kwargs["etag"] == "candidate-etag"
    assert container.delete_blob.await_args.kwargs["etag"] == "candidate-etag"


@pytest.mark.asyncio
async def test_catalog_raw_slot_snapshot_restores_corrupt_descriptor_by_etag() -> None:
    raw = {
        "id": "version:directive:v1",
        "directive_id": "directive",
        "malformed": object(),
        "_etag": "before",
    }
    container = SimpleNamespace(
        read_item=AsyncMock(return_value=raw),
        replace_item=AsyncMock(return_value={"_etag": "candidate"}),
    )
    repository = object.__new__(DirectiveCatalogRepository)
    repository._container = container
    bundle = SimpleNamespace(
        id="version:directive:v1",
        directive_id="directive",
        directive_version_id="directive:v1",
        model_dump=lambda **_: {
            "id": "version:directive:v1",
            "directive_id": "directive",
            "type": "version",
        },
    )

    snapshot = await repository.snapshot_version("directive", "directive:v1")
    assert snapshot is not None
    assert snapshot.payload is not raw
    assert snapshot.payload["malformed"] is raw["malformed"]

    candidate_etag = await repository._replace_published_bundle(
        bundle, snapshot
    )
    await repository.restore_version(bundle, snapshot, candidate_etag)

    assert container.replace_item.await_args_list[0].kwargs["etag"] == "before"
    restored = container.replace_item.await_args_list[1].kwargs
    assert restored["etag"] == "candidate"
    assert restored["body"]["malformed"] is raw["malformed"]
    assert "_etag" not in restored["body"]


@pytest.mark.asyncio
async def test_catalog_slot_rollback_refuses_etag_conflict() -> None:
    repository = object.__new__(DirectiveCatalogRepository)
    repository._container = SimpleNamespace(
        replace_item=AsyncMock(
            side_effect=exceptions.CosmosAccessConditionFailedError(
                status_code=412, message="changed"
            )
        )
    )
    bundle = SimpleNamespace(
        id="version:directive:v1",
        directive_id="directive",
        directive_version_id="directive:v1",
    )
    snapshot = CatalogSlotSnapshot(
        "directive",
        "directive:v1",
        {"id": bundle.id, "directive_id": "directive", "_etag": "before"},
        "before",
    )

    with pytest.raises(RuntimeError, match="Concurrent catalog publication"):
        await repository.restore_version(bundle, snapshot, "candidate")


@pytest.mark.asyncio
async def test_source_artifact_repair_uses_snapshot_etag_not_immutable_write() -> None:
    source = _source()
    item = SimpleNamespace(
        source=source,
        bundle=SimpleNamespace(
            source_hash=source.source_hash,
            artifacts=SimpleNamespace(
                source_blob_name="directives/key/source.pdf",
                canonical_blob_name="directives/key/document.md",
            ),
        ),
        canonical=SimpleNamespace(markdown="# Directive\n"),
    )
    runner = object.__new__(DirectiveIngestionRunner)
    runner.blobs = SimpleNamespace(
        replace_bytes=AsyncMock(return_value="candidate"),
        put_immutable=AsyncMock(),
        validate_hash=AsyncMock(),
    )

    candidate = await runner._publish_artifacts(
        item,
        SourceArtifactSnapshot(
            item.bundle.artifacts.source_blob_name, b"corrupt", "before"
        ),
    )

    assert candidate == "candidate"
    runner.blobs.replace_bytes.assert_awaited_once_with(
        item.bundle.artifacts.source_blob_name,
        source.content,
        "application/pdf",
        expected_etag="before",
    )
    runner.blobs.put_immutable.assert_awaited_once_with(
        item.bundle.artifacts.canonical_blob_name,
        b"# Directive\n",
        "text/markdown; charset=utf-8",
    )


@pytest.mark.asyncio
async def test_source_artifact_rollback_propagates_etag_conflict() -> None:
    item = SimpleNamespace(
        bundle=SimpleNamespace(
            directive_id="directive",
            directive_version_id="directive:v1",
            artifact_generation_id="candidate",
            artifacts=SimpleNamespace(source_blob_name="source.pdf"),
        ),
        canonical=SimpleNamespace(
            metadata=SimpleNamespace(processing_hash="a" * 64)
        ),
        search_chunks=[],
    )
    runner = object.__new__(DirectiveIngestionRunner)
    runner.catalog = SimpleNamespace(
        restore_current=AsyncMock(),
        snapshot_version=AsyncMock(),
        restore_version=AsyncMock(),
    )
    runner.source_states = SimpleNamespace()
    runner.blobs = SimpleNamespace(
        restore_bytes=AsyncMock(side_effect=RuntimeError("ETag conflict"))
    )
    snapshot = PublicationSnapshot(
        item=item,
        previous_version=None,
        previous_catalog_slot=None,
        previous_current=None,
        previous_current_bundle=None,
        previous_source_state=None,
        previous_source_artifact=SourceArtifactSnapshot(
            "source.pdf", b"old", "before"
        ),
        candidate_source_artifact_etag="candidate",
    )

    with pytest.raises(RuntimeError, match="ETag conflict"):
        await runner._rollback_publication([snapshot], None)

    runner.blobs.restore_bytes.assert_awaited_once_with(
        "source.pdf",
        b"old",
        "candidate",
        content_type="application/pdf",
    )


@pytest.mark.asyncio
async def test_replace_json_does_not_create_when_expected_etag_blob_is_missing() -> (
    None
):
    blob = SimpleNamespace(
        get_blob_properties=AsyncMock(
            side_effect=ResourceNotFoundError("missing")
        ),
        upload_blob=AsyncMock(),
    )
    repository = object.__new__(BlobArtifactRepository)
    repository._container = SimpleNamespace(get_blob_client=lambda _: blob)

    with pytest.raises(
        RuntimeError, match="Concurrent source-state replacement prevented"
    ):
        await repository.replace_json(
            "source-state/test.json",
            {"publication_state": "published"},
            expected_etag="expected-etag",
        )

    blob.upload_blob.assert_not_awaited()


@pytest.mark.asyncio
async def test_blob_payload_hash_mismatch_is_an_integrity_failure() -> None:
    stream = SimpleNamespace(readall=AsyncMock(return_value=b"corrupt"))
    blob = SimpleNamespace(
        get_blob_properties=AsyncMock(
            return_value=SimpleNamespace(
                metadata={
                    "content_sha256": hashlib.sha256(b"expected").hexdigest()
                }
            )
        ),
        download_blob=AsyncMock(return_value=stream),
    )
    repository = object.__new__(BlobArtifactRepository)
    repository._container = SimpleNamespace(get_blob_client=lambda _: blob)

    with pytest.raises(
        IntegrityValidationError, match="payload hash does not match metadata"
    ):
        await repository.content_hash("directives/corrupt/source.pdf")


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
async def test_corrupt_blob_payload_reextracts_instead_of_trusting_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source()
    metadata = _metadata(source)
    bundle = _live_bundle(source, metadata, "# Directive\n")
    state = _published_state(
        source, metadata, bundle.artifact_generation_id
    )
    runner = object.__new__(DirectiveIngestionRunner)
    runner.config = SimpleNamespace(processing_hash=metadata.processing_hash)
    runner.source_states = SimpleNamespace(load=AsyncMock(return_value=state))
    runner.catalog = SimpleNamespace(
        get_published_version=AsyncMock(return_value=bundle),
        get_current=AsyncMock(
            return_value={
                "directive_version_id": metadata.directive_version_id,
                "source_hash": source.source_hash,
                "processing_hash": metadata.processing_hash,
                "artifact_generation_id": bundle.artifact_generation_id,
            }
        ),
    )
    runner.blobs = SimpleNamespace(
        exists=AsyncMock(return_value=True),
        validate_hash=AsyncMock(
            side_effect=IntegrityValidationError(
                "Artifact payload hash does not match metadata"
            )
        ),
        quarantine=AsyncMock(),
    )
    runner.content = SimpleNamespace(validate_bundle=AsyncMock())
    runner.search = SimpleNamespace(validate_current_generation=AsyncMock())
    runner.extractor = SimpleNamespace(extract=AsyncMock(return_value=object()))
    import directive_ingestion.metadata as metadata_module

    monkeypatch.setattr(
        metadata_module,
        "extract_metadata",
        lambda *_args: SimpleNamespace(metadata=metadata),
    )

    result = await runner.extract_or_load_metadata([source], "run")

    assert result[0].changed is True
    runner.extractor.extract.assert_awaited_once_with(source.content)


@pytest.mark.asyncio
async def test_search_outage_propagates_instead_of_triggering_repair() -> None:
    source = _source()
    metadata = _metadata(source)
    markdown = "# Directive\n"
    bundle = _live_bundle(source, metadata, markdown)
    state = _published_state(
        source, metadata, bundle.artifact_generation_id
    )
    runner = object.__new__(DirectiveIngestionRunner)
    runner.catalog = SimpleNamespace(
        get_published_version=AsyncMock(return_value=bundle),
        get_current=AsyncMock(
            return_value={
                "directive_version_id": metadata.directive_version_id,
                "source_hash": source.source_hash,
                "processing_hash": metadata.processing_hash,
                "artifact_generation_id": bundle.artifact_generation_id,
            }
        ),
    )
    runner.blobs = SimpleNamespace(
        exists=AsyncMock(return_value=True),
        validate_hash=AsyncMock(),
        read_text=AsyncMock(return_value=markdown),
    )
    runner.content = SimpleNamespace(validate_bundle=AsyncMock())
    runner.search = SimpleNamespace(
        validate_current_generation=AsyncMock(
            side_effect=RuntimeError("Search service unavailable")
        )
    )

    with pytest.raises(RuntimeError, match="Search service unavailable"):
        await runner._state_has_live_publication(source, state)


def test_catalog_schema_and_unsafe_locator_are_integrity_failures() -> None:
    source = _source()
    metadata = _metadata(source)
    bundle = _live_bundle(source, metadata, "# Directive\n")

    with pytest.raises(IntegrityValidationError, match="invalid artifact schema"):
        _validate_published_bundle({"type": "version"})

    bundle.artifacts = SimpleNamespace(
        source_blob_name="../source.pdf",
        canonical_blob_name="../document.md",
    )
    from directive_ingestion.reconcile import _validate_safe_artifact_paths

    with pytest.raises(IntegrityValidationError, match="locator is unsafe"):
        _validate_safe_artifact_paths(bundle)


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
        validate_published_chunk_ids=AsyncMock(
            side_effect=lambda *_: events.append("search")
        )
    )
    runner.source_states = SimpleNamespace(
        record=AsyncMock(side_effect=lambda *_, **__: events.append("state"))
    )

    await runner.record_source_states([item])

    assert events == ["catalog", "content", "search", "state"]


@pytest.mark.asyncio
async def test_prepare_changed_documents_builds_first_generation() -> None:
    source = _source()
    metadata = _metadata(source)
    canonical = SimpleNamespace(
        metadata=metadata,
        markdown="# Directive\n",
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
    runner.summaries = SimpleNamespace(summarize=AsyncMock(return_value={}))
    runner.search = SimpleNamespace(build_chunks=AsyncMock(return_value=[]))
    runner.blobs = SimpleNamespace(quarantine=AsyncMock())
    runner.catalog = SimpleNamespace(get_published_version=AsyncMock(return_value=None))
    runner.source_states = SimpleNamespace(record=AsyncMock())
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


def test_generation_scoped_chunk_ids_do_not_reuse_live_ids() -> None:
    chunks = [
        TextChunk(
            id="logical-chunk",
            section_id="1",
            ordinal=0,
            content="text",
            content_kind="section",
            page_from=1,
            page_to=1,
        )
    ]

    first = _generation_scoped_chunks(chunks, "a" * 64)
    second = _generation_scoped_chunks(chunks, "b" * 64)

    assert first[0].id != chunks[0].id
    assert first[0].id != second[0].id


def test_public_digest_uses_compact_utf8_canonical_json() -> None:
    value = {"ž": ["Č", 1], "a": True}

    expected = hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()

    assert _public_record_digest(value) == expected


def test_public_digest_rejects_floats() -> None:
    with pytest.raises(ValueError, match="must not contain floats"):
        _public_record_digest({"unsupported": 1.25})


def test_public_result_is_limited_to_64_kib() -> None:
    with pytest.raises(ValueError, match="exceeds"):
        format_result({"safe": "x" * 65_536})


def test_public_source_limit_rejects_before_metadata_processing() -> None:
    with pytest.raises(ValueError, match="limit"):
        _validate_public_corpus_limit(
            [_source(f"{index}.pdf") for index in range(MAX_PUBLIC_DIRECTIVES + 1)]
        )


def test_worst_case_public_id_arrays_remain_below_64_kib() -> None:
    identifier = "\U0010ffff" * 128
    payload = {
        "record_schema": "directive.validate.v2",
        "success": True,
        "run_id": "a" * 36,
        "environment": {name: "x" for name in (
            "source_kind", "source_storage_account", "source_container",
            "source_prefix", "artifact_storage_account", "artifact_container",
            "cosmos_account", "cosmos_database", "catalog_container",
            "content_container", "mandate_container", "search_service",
            "search_index",
        )},
        "processing_version": "x",
        "processing_hash": "a" * 64,
        "search_index": "directive-chunks-v2",
        "source_count": MAX_PUBLIC_DIRECTIVES,
        "directive_count": MAX_PUBLIC_DIRECTIVES,
        "normalized_directive_ids": [identifier + str(index) for index in range(MAX_PUBLIC_DIRECTIVES)],
        "directive_version_ids": [
            identifier + f"{index}:v" for index in range(MAX_PUBLIC_DIRECTIVES)
        ],
        "mandate_count": 0,
        "mandate_user_count": 0,
        "warnings": [],
        "warning_count": 0,
        "failures": [],
        "source_inventory_digest": "a" * 64,
    }
    payload["validation_digest"] = _public_record_digest(payload)

    assert len(format_result(payload).encode("utf-8")) <= 65_536


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
        source_storage_account="source",
        source_container="directive-source",
        source_prefix="",
        artifact_storage_account="artifacts",
        blob_container="directive-artifacts",
        cosmos_account="cosmos",
        cosmos_database="directives",
        catalog_container="catalog",
        content_container="directive_content",
        mandate_container="user_mandates",
        search_service="search",
    )
    runner.discover_sources = AsyncMock(return_value=[source])
    runner.extract_or_load_metadata = AsyncMock(
        return_value=[SourceMetadata(source, metadata, None, None)]
    )
    runner._validate_and_quarantine = AsyncMock()
    import directive_ingestion.reconcile as reconcile_module

    original_mandates = reconcile_module.parse_mandates
    reconcile_module.parse_mandates = lambda *_args: SimpleNamespace(
        assignments=(), checksum="b" * 64, user_count=0
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
        "record_schema",
        "run_id",
        "validation_digest",
    }.issubset(value)
    assert value["record_schema"] == "directive.validate.v2"
    assert "mandate_identity_digest" not in value
    assert value["validation_digest"] == _public_record_digest(
        _validation_digest_projection(
            value,
            SimpleNamespace(assignments=(), checksum="b" * 64, user_count=0),
        )
    )


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
async def test_malformed_state_repairs_without_restaging_live_generation() -> None:
    source = _source()
    metadata = _metadata(source)
    bundle = SimpleNamespace(artifact_generation_id="c" * 64)
    runner = object.__new__(DirectiveIngestionRunner)
    runner.catalog = SimpleNamespace(
        get_published_version=AsyncMock(return_value=bundle)
    )
    runner._state_has_live_publication = AsyncMock(return_value=True)
    runner.source_states = SimpleNamespace(record=AsyncMock())
    runner.summaries = SimpleNamespace(summarize=AsyncMock())
    runner.search = SimpleNamespace(build_chunks=AsyncMock())
    runner.blobs = SimpleNamespace(quarantine=AsyncMock())

    prepared = await runner.prepare_changed_documents(
        [SourceMetadata(source, metadata, object(), None)], "run"
    )

    assert prepared == []
    runner.source_states.record.assert_awaited_once_with(
        source, metadata, bundle.artifact_generation_id
    )
    runner.summaries.summarize.assert_not_awaited()
    runner.search.build_chunks.assert_not_awaited()


@pytest.mark.asyncio
async def test_corrupt_live_generation_gets_isolated_repair_generation() -> None:
    source = _source()
    metadata = _metadata(source)
    canonical = SimpleNamespace(
        metadata=metadata,
        markdown="# Directive\n",
        sections=(),
        findings=(),
        relations=(),
    )
    generation_id = calculate_artifact_generation_id(
        metadata.processing_hash,
        _generation_canonical_hash(canonical),
        canonical_json_hash({}),
    )
    runner = object.__new__(DirectiveIngestionRunner)
    runner.config = SimpleNamespace(
        processing_hash=metadata.processing_hash,
        chunk_token_limit=800,
        chunk_overlap_tokens=120,
    )
    runner.summaries = SimpleNamespace(summarize=AsyncMock(return_value={}))
    runner.search = SimpleNamespace(build_chunks=AsyncMock(return_value=[]))
    runner.blobs = SimpleNamespace(quarantine=AsyncMock())
    runner.catalog = SimpleNamespace(
        get_published_version=AsyncMock(
            return_value=SimpleNamespace(artifact_generation_id=generation_id)
        )
    )
    runner.source_states = SimpleNamespace(record=AsyncMock())
    runner._state_has_live_publication = AsyncMock(return_value=False)
    import directive_ingestion.reconcile as reconcile_module

    captured: list[object] = []
    original_parse = reconcile_module.parse_canonical
    original_chunks = reconcile_module.chunk_sections
    original_manifest = reconcile_module._build_manifest
    original_bundle = reconcile_module._build_published_bundle
    reconcile_module.parse_canonical = lambda *_args: canonical
    reconcile_module.chunk_sections = lambda *_args, **_kwargs: ([], ())
    reconcile_module._build_manifest = lambda _value, *_args: captured.append(
        _args[-1]
    ) or object()
    reconcile_module._build_published_bundle = lambda *_args: (object(), ())
    try:
        await runner.prepare_changed_documents(
            [SourceMetadata(source, metadata, object(), None)], "run"
        )
    finally:
        reconcile_module.parse_canonical = original_parse
        reconcile_module.chunk_sections = original_chunks
        reconcile_module._build_manifest = original_manifest
        reconcile_module._build_published_bundle = original_bundle
    assert captured[0] is not None
    assert canonical.markdown == "# Directive\n"


@pytest.mark.asyncio
async def test_corrupt_catalog_slot_repair_does_not_salt_from_bad_descriptor() -> None:
    source = _source()
    metadata = _metadata(source)
    canonical = SimpleNamespace(
        metadata=metadata,
        markdown="# Directive\n",
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
    runner.summaries = SimpleNamespace(summarize=AsyncMock(return_value={}))
    runner.search = SimpleNamespace(build_chunks=AsyncMock(return_value=[]))
    runner.blobs = SimpleNamespace(quarantine=AsyncMock())
    runner.catalog = SimpleNamespace(
        get_published_version=AsyncMock(
            side_effect=IntegrityValidationError("invalid descriptor")
        ),
        snapshot_version=AsyncMock(
            return_value=CatalogSlotSnapshot(
                metadata.directive_id,
                metadata.directive_version_id,
                {"malformed": object(), "_etag": "before"},
                "before",
            )
        ),
    )
    runner.source_states = SimpleNamespace(record=AsyncMock())
    import directive_ingestion.reconcile as reconcile_module

    captured: list[object] = []
    original_parse = reconcile_module.parse_canonical
    original_chunks = reconcile_module.chunk_sections
    original_manifest = reconcile_module._build_manifest
    original_bundle = reconcile_module._build_published_bundle
    reconcile_module.parse_canonical = lambda *_args: canonical
    reconcile_module.chunk_sections = lambda *_args, **_kwargs: ([], ())
    reconcile_module._build_manifest = lambda _value, *_args: captured.append(
        _args[-1]
    ) or object()
    reconcile_module._build_published_bundle = lambda *_args: (object(), ())
    try:
        await runner.prepare_changed_documents(
            [SourceMetadata(source, metadata, object(), None)], "run"
        )
    finally:
        reconcile_module.parse_canonical = original_parse
        reconcile_module.chunk_sections = original_chunks
        reconcile_module._build_manifest = original_manifest
        reconcile_module._build_published_bundle = original_bundle

    assert captured == [None]
    runner.catalog.snapshot_version.assert_awaited_once_with(
        metadata.directive_id, metadata.directive_version_id
    )


@pytest.mark.asyncio
async def test_unrestorable_corrupt_current_catalog_aborts_before_writes() -> None:
    metadata = _metadata(_source())
    item = SimpleNamespace(
        bundle=SimpleNamespace(
            directive_id=metadata.directive_id,
            directive_version_id=metadata.directive_version_id,
            source_hash=metadata.source_hash,
            processing_hash=metadata.processing_hash,
            artifact_generation_id="candidate",
            artifacts=SimpleNamespace(source_blob_name="source.pdf"),
        ),
        source=_source(),
        canonical=SimpleNamespace(
            metadata=SimpleNamespace(processing_hash=metadata.processing_hash)
        ),
    )
    runner = object.__new__(DirectiveIngestionRunner)
    runner.catalog = SimpleNamespace(
        snapshot_version=AsyncMock(
            return_value=CatalogSlotSnapshot(
                metadata.directive_id,
                metadata.directive_version_id,
                {"malformed": True, "_etag": "before"},
                "before",
            )
        ),
        get_published_version=AsyncMock(
            side_effect=IntegrityValidationError("invalid descriptor")
        ),
        get_current=AsyncMock(
            return_value={"directive_version_id": "other:v1"}
        ),
    )
    runner.stage_documents = AsyncMock()

    with pytest.raises(CatalogResetRequiredError, match="operator reset"):
        await runner._publish_transaction([item])

    runner.stage_documents.assert_not_awaited()


@pytest.mark.asyncio
async def test_exact_corpus_retires_all_removed_store_records() -> None:
    retained = _source()
    retired_bundle = SimpleNamespace(
        directive_id="d-1",
        directive_version_id="d-1:v1",
        artifact_generation_id="old",
        artifacts=SimpleNamespace(
            source_blob_name="directives/old/source.pdf",
            canonical_blob_name="directives/old/generations/old/document.md",
        )
    )
    runner = object.__new__(DirectiveIngestionRunner)
    runner.catalog = SimpleNamespace(
        list_published_versions=AsyncMock(return_value=[retired_bundle]),
        delete_versions=AsyncMock(),
    )
    runner.search = SimpleNamespace(delete_generation=AsyncMock())
    runner.content = SimpleNamespace(delete_bundle=AsyncMock())
    runner.blobs = SimpleNamespace(delete_names=AsyncMock())
    runner.source_states = SimpleNamespace(
        prune=AsyncMock(),
        list_names=AsyncMock(return_value={"source-state/expected.json"}),
        load=AsyncMock(return_value=None),
        clear_pending=AsyncMock(),
        blob_name=lambda *_: "source-state/expected.json",
    )
    runner.config = SimpleNamespace(processing_hash="a" * 64)
    runner.commits = SimpleNamespace(
        load=AsyncMock(return_value=None),
        record=AsyncMock(
            side_effect=lambda run_id, stale_bundles, expected_state_names: SimpleNamespace(
                stale_bundles=tuple(stale_bundles),
                expected_state_names=frozenset(expected_state_names),
            )
        ),
        clear=AsyncMock(),
    )

    await runner.reconcile_exact_corpus(
        [SourceMetadata(retained, _metadata(retained), None, None)]
    )

    runner.search.delete_generation.assert_awaited_once_with(retired_bundle)
    runner.content.delete_bundle.assert_awaited_once_with(retired_bundle)
    runner.blobs.delete_names.assert_awaited_once()
    runner.source_states.prune.assert_awaited_once_with(
        {"source-state/expected.json"}
    )
    runner.catalog.delete_versions.assert_awaited_once_with([retired_bundle])


@pytest.mark.asyncio
async def test_run_daily_unchanged_corpus_performs_no_publication_writes() -> None:
    source = _source()
    metadata = _metadata(source)
    state = SimpleNamespace()
    parsed = SimpleNamespace(
        assignments=(), checksum="c" * 64, user_count=0
    )
    runner = object.__new__(DirectiveIngestionRunner)
    runner.config = SimpleNamespace(
        mandate_csv=object(),
        azure_tenant_id="tenant",
        processing_hash=metadata.processing_hash,
        processing_version="directive-v2-czech-layout",
        search_index="directive-chunks-v2",
        source_kind="local",
        source_storage_account="source",
        source_container="directive-source",
        source_prefix="",
        artifact_storage_account="artifacts",
        blob_container="directive-artifacts",
        cosmos_account="cosmos",
        cosmos_database="directives",
        catalog_container="catalog",
        content_container="directive-content",
        mandate_container="mandates",
        search_service="search",
    )
    runner.discover_sources = AsyncMock(return_value=[source])
    runner.extract_or_load_metadata = AsyncMock(
        return_value=[SourceMetadata(source, metadata, None, state)]
    )
    runner._validate_and_quarantine = AsyncMock()
    runner.prepare_changed_documents = AsyncMock(return_value=[])
    runner._validate_relations = AsyncMock()
    runner.mandates = SimpleNamespace(
        is_current=AsyncMock(return_value=True),
        publish=AsyncMock(),
        validate_exact=AsyncMock(
            return_value={
                "snapshot_id": "mandates-" + "c" * 64 + "-repaired",
                "checksum": "c" * 64,
                "assignment_count": 0,
                "user_count": 0,
            }
        ),
    )
    runner.search = SimpleNamespace(ensure_resources=AsyncMock())
    runner._publish_transaction = AsyncMock()
    runner.reconcile_exact_corpus = AsyncMock()
    runner.verify = AsyncMock()
    runner.catalog = SimpleNamespace(record_run=AsyncMock())
    runner.commits = SimpleNamespace(load=AsyncMock(return_value=None), clear=AsyncMock())
    import directive_ingestion.reconcile as reconcile_module

    original_mandates = reconcile_module.parse_mandates
    reconcile_module.parse_mandates = lambda *_args: parsed
    try:
        result = await runner.run_daily()
    finally:
        reconcile_module.parse_mandates = original_mandates

    assert result.changed_count == 0
    assert result.mandate_snapshot_id == "mandates-" + "c" * 64 + "-repaired"
    runner.search.ensure_resources.assert_not_awaited()
    runner._publish_transaction.assert_not_awaited()
    runner.mandates.publish.assert_not_awaited()
    runner.catalog.record_run.assert_not_awaited()
    runner.commits.clear.assert_not_awaited()


@pytest.mark.asyncio
async def test_mandate_only_activation_is_reported_as_a_change() -> None:
    source = _source()
    metadata = _metadata(source)
    parsed = SimpleNamespace(
        assignments=(), checksum="c" * 64, user_count=0
    )
    snapshot = SimpleNamespace(
        snapshot_id="mandates-" + "c" * 64,
        checksum=parsed.checksum,
        assignment_count=0,
        user_count=0,
        complete=True,
    )
    runner = object.__new__(DirectiveIngestionRunner)
    runner.config = SimpleNamespace(
        mandate_csv=object(),
        azure_tenant_id="tenant",
        processing_hash=metadata.processing_hash,
        processing_version="directive-v2-czech-layout",
        search_index="directive-chunks-v2",
        source_kind="local",
        source_storage_account="source",
        source_container="directive-source",
        source_prefix="",
        artifact_storage_account="artifacts",
        blob_container="directive-artifacts",
        cosmos_account="cosmos",
        cosmos_database="directives",
        catalog_container="catalog",
        content_container="directive-content",
        mandate_container="mandates",
        search_service="search",
    )
    runner.discover_sources = AsyncMock(return_value=[source])
    runner.extract_or_load_metadata = AsyncMock(
        return_value=[SourceMetadata(source, metadata, None, object())]
    )
    runner._validate_and_quarantine = AsyncMock()
    runner.prepare_changed_documents = AsyncMock(return_value=[])
    runner._validate_relations = AsyncMock()
    runner.mandates = SimpleNamespace(
        is_current=AsyncMock(return_value=False),
        cleanup=AsyncMock(return_value=False),
    )
    runner.search = SimpleNamespace(ensure_resources=AsyncMock())
    runner._publish_transaction = AsyncMock(
        return_value=([], SimpleNamespace(snapshot=snapshot, changed=True))
    )
    runner._reconcile_after_publication = AsyncMock()
    runner.verify = AsyncMock()
    runner.catalog = SimpleNamespace(record_run=AsyncMock())
    runner.commits = SimpleNamespace(load=AsyncMock(return_value=None), clear=AsyncMock())
    import directive_ingestion.reconcile as reconcile_module

    original_mandates = reconcile_module.parse_mandates
    reconcile_module.parse_mandates = lambda *_args: parsed
    try:
        result = await runner.run_daily()
    finally:
        reconcile_module.parse_mandates = original_mandates

    assert result.changed_count == 0
    assert result.mandate_changed is True
    assert result.mandate_snapshot_id == snapshot.snapshot_id
    runner.mandates.cleanup.assert_awaited_once_with(snapshot.snapshot_id)
    runner.catalog.record_run.assert_awaited_once()
