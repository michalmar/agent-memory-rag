from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from directive_ingestion.cli import _daily_run_approval_from_environment
from directive_ingestion.publication_commit_repository import (
    PublicationCommitRepository,
)
from directive_ingestion.reconcile import (
    DailyRunApproval,
    DirectiveIngestionRunner,
    SourceMetadata,
    ValidationSnapshot,
    _public_record_digest,
    _safe_environment,
    _source_inventory,
    _validation_payload,
)
from directive_ingestion.source import SourceDocument, SourceProvenance
from directive_ingestion.source_state_repository import SourceStateSnapshot


def _source() -> SourceDocument:
    content = b"%PDF-daily-approval"
    return SourceDocument(
        source_name="directive.pdf",
        source_hash=hashlib.sha256(content).hexdigest(),
        content=content,
        _provenance=SourceProvenance(kind="test", locator="directive.pdf"),
    )


def _config() -> SimpleNamespace:
    return SimpleNamespace(
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
        search_index="directive-chunks-v2",
    )


def _approval(config: SimpleNamespace, source: SourceDocument) -> DailyRunApproval:
    return DailyRunApproval(
        validation_digest="approved-validation",
        environment_digest=_public_record_digest(_safe_environment(config)),
        source_inventory_digest=_public_record_digest(
            _source_inventory([source])
        ),
    )


@pytest.mark.parametrize(
    "name",
    (
        "DIRECTIVE_APPROVED_VALIDATION_DIGEST",
        "DIRECTIVE_APPROVED_ENVIRONMENT_DIGEST",
        "DIRECTIVE_APPROVED_SOURCE_INVENTORY_DIGEST",
    ),
)
def test_cli_daily_approval_requires_each_nonempty_digest(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setenv("DIRECTIVE_APPROVED_VALIDATION_DIGEST", "validation")
    monkeypatch.setenv("DIRECTIVE_APPROVED_ENVIRONMENT_DIGEST", "environment")
    monkeypatch.setenv("DIRECTIVE_APPROVED_SOURCE_INVENTORY_DIGEST", "inventory")
    monkeypatch.setenv(name, " ")

    with pytest.raises(ValueError, match=name):
        _daily_run_approval_from_environment()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    ("approved_environment_digest", "approved_source_inventory_digest"),
)
async def test_daily_approval_mismatch_prevents_document_processing_and_writes(
    field: str,
) -> None:
    source = _source()
    config = _config()
    approval = _approval(config, source)
    runner = object.__new__(DirectiveIngestionRunner)
    runner.config = config
    runner.discover_sources = AsyncMock(return_value=[source])
    runner.extract_or_load_metadata = AsyncMock()
    runner.blobs = SimpleNamespace(put_immutable=AsyncMock())
    values = {
        "approved_validation_digest": approval.validation_digest,
        "approved_environment_digest": approval.environment_digest,
        "approved_source_inventory_digest": approval.source_inventory_digest,
    }
    values[field] = "mismatch"

    with pytest.raises(ValueError, match="does not match"):
        await runner.run_daily(**values)

    runner.extract_or_load_metadata.assert_not_awaited()
    runner.blobs.put_immutable.assert_not_awaited()


def test_exact_daily_approval_matches_canonical_environment_and_inventory() -> None:
    source = _source()
    config = _config()
    runner = object.__new__(DirectiveIngestionRunner)
    runner.config = config

    runner._validate_daily_approval(_approval(config, source), [source])


def test_validation_digest_is_independent_of_run_id() -> None:
    source = _source()
    config = SimpleNamespace(
        **_config().__dict__,
        processing_version="v2",
        processing_hash="a" * 64,
    )
    metadata = SourceMetadata(
        source,
        SimpleNamespace(
            directive_id="directive",
            directive_version_id="directive:v1",
        ),
        None,
        None,
    )
    mandates = SimpleNamespace(assignments=(), user_count=0)

    first = _validation_payload(
        config, "first-run", [source], [metadata], mandates, []
    )
    second = _validation_payload(
        config, "second-run", [source], [metadata], mandates, []
    )

    assert first["validation_digest"] == second["validation_digest"]
    assert first["environment_digest"] == second["environment_digest"]


@pytest.mark.asyncio
async def test_validation_digest_drift_stops_before_summary_or_writes() -> None:
    source = _source()
    config = SimpleNamespace(
        **_config().__dict__,
        mandate_csv=object(),
        azure_tenant_id="tenant",
        processing_hash="a" * 64,
    )
    approval = _approval(config, source)
    snapshot = ValidationSnapshot(
        [source],
        [],
        SimpleNamespace(assignments=(), user_count=0),
        {
            "validation_digest": "different-validation",
            "environment_digest": approval.environment_digest,
            "source_inventory_digest": approval.source_inventory_digest,
        },
    )
    runner = object.__new__(DirectiveIngestionRunner)
    runner.config = config
    runner.discover_sources = AsyncMock(return_value=[source])
    runner._metadata_validation_snapshot = AsyncMock(return_value=snapshot)
    runner.prepare_changed_documents = AsyncMock()
    runner.summaries = SimpleNamespace(summarize=AsyncMock())
    runner.blobs = SimpleNamespace(put_immutable=AsyncMock())

    with pytest.raises(ValueError, match="metadata snapshot"):
        await runner.run_daily(
            approved_validation_digest=approval.validation_digest,
            approved_environment_digest=approval.environment_digest,
            approved_source_inventory_digest=approval.source_inventory_digest,
        )

    runner.prepare_changed_documents.assert_not_awaited()
    runner.summaries.summarize.assert_not_awaited()
    runner.blobs.put_immutable.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "marker",
    (
        None,
        {
            "record_schema": "directive.approval.v2",
            "validation_digest": "approved-validation",
            "environment_digest": "wrong",
            "source_inventory_digest": "inventory",
            "processing_hash": "a" * 64,
        },
    ),
)
async def test_published_approval_requires_exact_named_marker(
    marker: dict[str, str] | None,
) -> None:
    runner = object.__new__(DirectiveIngestionRunner)
    runner.config = SimpleNamespace(processing_hash="a" * 64)
    runner.blobs = SimpleNamespace(get_json=AsyncMock(return_value=marker))

    with pytest.raises(RuntimeError, match="does not exactly match"):
        await runner._validate_published_approval(
            "approved-validation", "environment", "inventory"
        )

    runner.blobs.get_json.assert_awaited_once_with(
        "publication-approval/approved-validation.json"
    )


@pytest.mark.asyncio
async def test_daily_binding_persists_validation_digest_to_source_state() -> None:
    source = _source()
    metadata = SimpleNamespace()
    state = SimpleNamespace(
        validation_digest=None,
        directive_metadata=metadata,
        artifact_generation_id="generation",
        pending_cleanup=(),
    )
    runner = object.__new__(DirectiveIngestionRunner)
    runner.config = SimpleNamespace(processing_hash="a" * 64)
    runner.source_states = SimpleNamespace(
        load=AsyncMock(return_value=state),
        snapshot=AsyncMock(
            return_value=SourceStateSnapshot("source-state/test.json", b"{}", "etag")
        ),
        record=AsyncMock(),
    )
    runner._state_has_live_publication = AsyncMock(return_value=True)

    await runner._bind_source_state_validation_digest(
        [SimpleNamespace(source=source)], "approved-validation"
    )

    runner.source_states.record.assert_awaited_once_with(
        source,
        metadata,
        "generation",
        validation_digest="approved-validation",
        pending_cleanup=(),
        expected_etag="etag",
    )


@pytest.mark.asyncio
async def test_verify_rejects_source_state_with_different_approval_binding() -> None:
    source = _source()
    config = SimpleNamespace(
        **_config().__dict__,
        processing_hash="a" * 64,
    )
    environment_digest = _public_record_digest(_safe_environment(config))
    source_inventory_digest = _public_record_digest(_source_inventory([source]))
    runner = object.__new__(DirectiveIngestionRunner)
    runner.config = config
    runner.discover_sources = AsyncMock(return_value=[source])
    runner.blobs = SimpleNamespace(
        get_json=AsyncMock(
            return_value={
                "record_schema": "directive.approval.v2",
                "validation_digest": "approved-validation",
                "environment_digest": environment_digest,
                "source_inventory_digest": source_inventory_digest,
                "processing_hash": config.processing_hash,
            }
        )
    )
    runner.source_states = SimpleNamespace(
        load=AsyncMock(
            return_value=SimpleNamespace(validation_digest="other-validation")
        )
    )
    runner._state_has_live_publication = AsyncMock(return_value=True)

    with pytest.raises(RuntimeError, match="Source-state records"):
        await runner.verify(expected_validation_digest="approved-validation")

    runner.blobs.get_json.assert_awaited_once_with(
        "publication-approval/approved-validation.json"
    )


@pytest.mark.asyncio
async def test_publication_commit_binds_and_restores_validation_digest() -> None:
    blobs = SimpleNamespace(
        replace_json=AsyncMock(),
        get_json=AsyncMock(),
    )
    repository = PublicationCommitRepository(blobs)

    await repository.record(
        "run",
        [],
        {"source-state/directive.json"},
        validation_digest="approved-validation",
    )
    payload = blobs.replace_json.await_args.args[1]
    blobs.get_json.return_value = payload

    marker = await repository.load()

    assert payload["validation_digest"] == "approved-validation"
    assert marker is not None
    assert marker.validation_digest == "approved-validation"
