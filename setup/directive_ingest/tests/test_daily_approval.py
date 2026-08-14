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
    _public_record_digest,
    _safe_environment,
    _source_inventory,
)
from directive_ingestion.source import SourceDocument, SourceProvenance


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
