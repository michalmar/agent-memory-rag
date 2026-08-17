from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from directive_contracts import DirectiveMetadata, build_directive_version_id

from directive_ingestion.cli import (
    _daily_run_approval_from_environment,
    _parser,
    _run,
)
from directive_ingestion.publication_commit_repository import (
    PublicationCommitRepository,
)
from directive_ingestion.reconcile import (
    DailyRunApproval,
    DirectiveIngestionRunner,
    ReconcileResult,
    SourceMetadata,
    ValidationSnapshot,
    _descriptor_inventory_digest,
    _public_record_digest,
    _safe_environment,
    _validation_payload,
)
from directive_ingestion.extraction_cache import ExtractionCacheEvidence
from directive_ingestion.mandate_projection import ParsedMandates
from directive_ingestion.source import (
    SourceDescriptor,
    SourceDocument,
    SourceIdentity,
)
from directive_ingestion.source_state_repository import SourceStateSnapshot
from directive_ingestion.source_inventory import (
    SourceInventory,
    SourceInventoryEntry,
    SourceInventorySnapshot,
)
from directive_ingestion.validation_evidence import (
    ValidationEvidence,
    ValidationEvidenceDocument,
)


def _source() -> SourceDocument:
    content = b"%PDF-daily-approval"
    return SourceDocument(
        descriptor=SourceDescriptor(
            source_name="directive.pdf",
            kind="test",
            locator="directive.pdf",
            etag='"etag"',
            version_id=None,
            size=len(content),
            last_modified=None,
        ),
        identity=SourceIdentity(
            "directive.pdf",
            hashlib.sha256(content).hexdigest(),
        ),
        content=content,
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
        processing_version="directive-v3-bounded-ingestion",
        processing_hash="a" * 64,
    )


def _approval(config: SimpleNamespace, source: SourceDocument) -> DailyRunApproval:
    document = _evidence_document(source)
    return DailyRunApproval(
        validation_digest="5" * 64,
        environment_digest=_public_record_digest(_safe_environment(config)),
        source_inventory_digest=_descriptor_inventory_digest((document,)),
        validation_evidence_digest="6" * 64,
    )


def _metadata(source: SourceDocument) -> DirectiveMetadata:
    return DirectiveMetadata(
        directive_id="directive",
        directive_version_id=build_directive_version_id(
            "directive",
            "1.0",
        ),
        version_label="1.0",
        title="Directive",
        status="Current",
        is_current=True,
        is_valid=True,
        effective_from=date(2026, 1, 1),
        source_filename=source.source_name,
        source_hash=source.source_hash,
        processing_hash="a" * 64,
    )


def _extraction_evidence() -> ExtractionCacheEvidence:
    return ExtractionCacheEvidence(
        blob_name="extractions/cache.json.gz",
        extractor_identity_hash="2" * 64,
        result_hash="3" * 64,
    )


def _evidence_document(
    source: SourceDocument,
    *,
    disposition: str = "unchanged",
) -> ValidationEvidenceDocument:
    return ValidationEvidenceDocument(
        descriptor=source.descriptor,
        identity=source.identity,
        metadata=_metadata(source),
        source_state_blob="source-state/directive.json",
        disposition=disposition,
        extraction=_extraction_evidence(),
    )


@pytest.mark.parametrize(
    "name",
    (
        "DIRECTIVE_APPROVED_VALIDATION_DIGEST",
        "DIRECTIVE_APPROVED_ENVIRONMENT_DIGEST",
        "DIRECTIVE_APPROVED_SOURCE_INVENTORY_DIGEST",
        "DIRECTIVE_APPROVED_VALIDATION_EVIDENCE_DIGEST",
    ),
)
def test_cli_daily_approval_requires_each_nonempty_digest(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setenv("DIRECTIVE_APPROVED_VALIDATION_DIGEST", "validation")
    monkeypatch.setenv("DIRECTIVE_APPROVED_ENVIRONMENT_DIGEST", "environment")
    monkeypatch.setenv("DIRECTIVE_APPROVED_SOURCE_INVENTORY_DIGEST", "inventory")
    monkeypatch.setenv(
        "DIRECTIVE_APPROVED_VALIDATION_EVIDENCE_DIGEST",
        "evidence",
    )
    monkeypatch.setenv(name, " ")

    with pytest.raises(ValueError, match=name):
        _daily_run_approval_from_environment()


@pytest.mark.parametrize(
    "command",
    (
        "reconcile-documents",
        "publish-mandates",
        "reset-publication-guards",
    ),
)
def test_cli_rejects_publishing_bypass_commands(
    command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        _parser().parse_args([command])

    assert exit_info.value.code == 2
    assert f"invalid choice: '{command}'" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_cli_bootstraps_publication_gate_with_execution_run_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = SimpleNamespace(processing_hash="a" * 64, source_kind="local")
    records: list[dict[str, object]] = []
    bootstrap_run_ids: list[str] = []

    class Runner:
        def __init__(self, _config) -> None:
            self.catalog = SimpleNamespace(
                record_run_metrics=AsyncMock(
                    side_effect=lambda payload: records.append(payload)
                )
            )

        def attach_metrics(self, _metrics) -> None:
            return None

        async def bootstrap_publication_gate(self, *, run_id: str):
            bootstrap_run_ids.append(run_id)
            return {
                "status": "ready",
                "state": "committed",
                "committed_revision": "b" * 64,
            }

        async def close(self) -> None:
            return None

    monkeypatch.setattr(
        "directive_ingestion.cli.IngestionConfig",
        SimpleNamespace(from_environment=lambda: config),
    )
    monkeypatch.setattr("directive_ingestion.cli.DirectiveIngestionRunner", Runner)

    await _run(SimpleNamespace(command="bootstrap-gate"))

    assert bootstrap_run_ids == [records[0]["run_id"]]
    assert records[0]["operation"] == "bootstrap-gate"
    assert records[0]["status"] == "succeeded"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mandate_changed", "expected_status"),
    ((False, "skipped"), (True, "succeeded")),
)
async def test_cli_records_all_skipped_and_mandate_only_attempts(
    monkeypatch: pytest.MonkeyPatch,
    mandate_changed: bool,
    expected_status: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = SimpleNamespace(processing_hash="a" * 64, source_kind="local")
    records: list[dict[str, object]] = []
    result = ReconcileResult(
        run_id="run-result",
        source_count=1,
        changed_count=0,
        skipped_count=1,
        chunk_count=0,
        mandate_snapshot_id="mandates",
        mandate_changed=mandate_changed,
        verification={
            "record_schema": "directive.verify.v2",
            "success": True,
        },
    )

    class Runner:
        def __init__(self, _config) -> None:
            self.catalog = SimpleNamespace(
                record_run_metrics=AsyncMock(
                    side_effect=lambda payload: records.append(payload)
                )
            )

        def attach_metrics(self, _metrics) -> None:
            return None

        async def run_daily(self, *_args, **_kwargs):
            return result

        async def close(self) -> None:
            return None

    monkeypatch.setattr(
        "directive_ingestion.cli.IngestionConfig",
        SimpleNamespace(from_environment=lambda: config),
    )
    monkeypatch.setattr("directive_ingestion.cli.DirectiveIngestionRunner", Runner)
    for name in (
        "DIRECTIVE_APPROVED_VALIDATION_DIGEST",
        "DIRECTIVE_APPROVED_ENVIRONMENT_DIGEST",
        "DIRECTIVE_APPROVED_SOURCE_INVENTORY_DIGEST",
        "DIRECTIVE_APPROVED_VALIDATION_EVIDENCE_DIGEST",
    ):
        monkeypatch.setenv(name, "digest")

    await _run(
        SimpleNamespace(command="run-daily", source=None, mandates=None)
    )

    assert len(records) == 1
    assert records[0]["run_id"] == "run-result"
    assert records[0]["status"] == expected_status
    assert records[0]["counters"] == {"skipped_count": 1}
    assert json.loads(capsys.readouterr().out) == {
        "record_schema": "directive.verify.v2",
        "success": True,
    }


@pytest.mark.asyncio
async def test_cli_records_failed_attempt_before_propagating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = SimpleNamespace(processing_hash="a" * 64, source_kind="local")
    records: list[dict[str, object]] = []

    class Runner:
        def __init__(self, _config) -> None:
            self.catalog = SimpleNamespace(
                record_run_metrics=AsyncMock(
                    side_effect=lambda payload: records.append(payload)
                )
            )

        def attach_metrics(self, _metrics) -> None:
            return None

        async def run_daily(self, *_args, **_kwargs):
            raise RuntimeError("publication failed")

        async def close(self) -> None:
            return None

    monkeypatch.setattr(
        "directive_ingestion.cli.IngestionConfig",
        SimpleNamespace(from_environment=lambda: config),
    )
    monkeypatch.setattr("directive_ingestion.cli.DirectiveIngestionRunner", Runner)
    for name in (
        "DIRECTIVE_APPROVED_VALIDATION_DIGEST",
        "DIRECTIVE_APPROVED_ENVIRONMENT_DIGEST",
        "DIRECTIVE_APPROVED_SOURCE_INVENTORY_DIGEST",
        "DIRECTIVE_APPROVED_VALIDATION_EVIDENCE_DIGEST",
    ):
        monkeypatch.setenv(name, "digest")

    with pytest.raises(RuntimeError, match="publication failed"):
        await _run(
            SimpleNamespace(command="run-daily", source=None, mandates=None)
        )

    assert len(records) == 1
    assert records[0]["status"] == "failed"
    assert records[0]["error_code"] == "runtimeerror"


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
    evidence = ValidationEvidence.create(
        processing_hash=config.processing_hash,
        mandate_checksum="b" * 64,
        documents=(_evidence_document(source),),
    )
    runner.validation_evidence = SimpleNamespace(
        load=AsyncMock(return_value=evidence)
    )
    runner.extract_or_load_metadata = AsyncMock()
    runner.blobs = SimpleNamespace(put_immutable=AsyncMock())
    values = {
        "approved_validation_digest": approval.validation_digest,
        "approved_environment_digest": approval.environment_digest,
        "approved_source_inventory_digest": approval.source_inventory_digest,
        "approved_validation_evidence_digest": (
            approval.validation_evidence_digest
        ),
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

    runner._validate_daily_approval(
        _approval(config, source),
        (_evidence_document(source),),
    )


def test_validation_digest_is_independent_of_run_id() -> None:
    source = _source()
    config = _config()
    document = _evidence_document(source)
    mandates = SimpleNamespace(assignments=(), checksum="b" * 64, user_count=0)

    first = _validation_payload(
        config,
        "first-run",
        (document,),
        mandates,
        [],
        "6" * 64,
    )
    second = _validation_payload(
        config,
        "second-run",
        (document,),
        mandates,
        [],
        "6" * 64,
    )

    assert first["validation_digest"] == second["validation_digest"]
    assert first["environment_digest"] == second["environment_digest"]


def test_validation_digest_binds_canonical_mandate_identity_and_tenant() -> None:
    source = _source()
    config = _config()
    document = _evidence_document(source)
    first = ParsedMandates(
        assignments=(
            SimpleNamespace(
                user_id="tenant-a:11111111-1111-1111-1111-111111111111",
                directive_id="directive",
            ),
        ),
        checksum="a" * 64,
        user_count=1,
    )
    drifted = ParsedMandates(
        assignments=(
            SimpleNamespace(
                user_id="tenant-b:11111111-1111-1111-1111-111111111111",
                directive_id="directive",
            ),
        ),
        checksum="b" * 64,
        user_count=1,
    )

    approved = _validation_payload(
        config,
        "run",
        (document,),
        first,
        [],
        "6" * 64,
    )
    candidate = _validation_payload(
        config,
        "run",
        (document,),
        drifted,
        [],
        "6" * 64,
    )

    assert approved["mandate_count"] == candidate["mandate_count"] == 1
    assert approved["mandate_user_count"] == candidate["mandate_user_count"] == 1
    assert approved["validation_digest"] != candidate["validation_digest"]


@pytest.mark.asyncio
async def test_tenant_drifted_mandates_stop_before_summary_or_writes() -> None:
    source = _source()
    config = _config()
    config.mandate_csv = object()
    config.azure_tenant_id = "tenant-b"
    document = _evidence_document(source)
    approved_mandates = ParsedMandates(
        assignments=(
            SimpleNamespace(
                user_id="tenant-a:11111111-1111-1111-1111-111111111111",
                directive_id="directive",
            ),
        ),
        checksum="a" * 64,
        user_count=1,
    )
    drifted_mandates = ParsedMandates(
        assignments=(
            SimpleNamespace(
                user_id="tenant-b:11111111-1111-1111-1111-111111111111",
                directive_id="directive",
            ),
        ),
        checksum="b" * 64,
        user_count=1,
    )
    evidence = ValidationEvidence.create(
        processing_hash=config.processing_hash,
        mandate_checksum=approved_mandates.checksum,
        documents=(document,),
    )
    approved_payload = _validation_payload(
        config,
        "approved",
        evidence.documents,
        approved_mandates,
        [],
        evidence.evidence_hash,
    )
    runner = object.__new__(DirectiveIngestionRunner)
    runner.config = config
    runner.validation_evidence = SimpleNamespace(
        load=AsyncMock(return_value=evidence)
    )
    runner.source_planner = SimpleNamespace(
        revalidate_descriptors=AsyncMock()
    )
    runner.prepare_changed_documents = AsyncMock()
    runner.summaries = SimpleNamespace(summarize=AsyncMock())
    runner.blobs = SimpleNamespace(put_immutable=AsyncMock())

    import directive_ingestion.reconcile as reconcile_module

    original_mandates = reconcile_module.parse_mandates
    reconcile_module.parse_mandates = lambda *_args: drifted_mandates
    try:
        with pytest.raises(ValueError, match="mandate input"):
            await runner.run_daily(
                approved_validation_digest=approved_payload[
                    "validation_digest"
                ],
                approved_environment_digest=approved_payload[
                    "environment_digest"
                ],
                approved_source_inventory_digest=approved_payload[
                    "source_inventory_digest"
                ],
                approved_validation_evidence_digest=evidence.evidence_hash,
            )
    finally:
        reconcile_module.parse_mandates = original_mandates

    runner.prepare_changed_documents.assert_not_awaited()
    runner.summaries.summarize.assert_not_awaited()
    runner.blobs.put_immutable.assert_not_awaited()


@pytest.mark.asyncio
async def test_validation_evidence_drift_stops_before_summary_or_writes() -> None:
    source = _source()
    config = _config()
    approval = _approval(config, source)
    runner = object.__new__(DirectiveIngestionRunner)
    runner.config = config
    runner.validation_evidence = SimpleNamespace(
        load=AsyncMock(side_effect=ValueError("Evidence hash mismatch"))
    )
    runner.prepare_changed_documents = AsyncMock()
    runner.summaries = SimpleNamespace(summarize=AsyncMock())
    runner.blobs = SimpleNamespace(put_immutable=AsyncMock())

    with pytest.raises(ValueError, match="Evidence hash mismatch"):
        await runner.run_daily(
            approved_validation_digest=approval.validation_digest,
            approved_environment_digest=approval.environment_digest,
            approved_source_inventory_digest=approval.source_inventory_digest,
            approved_validation_evidence_digest=(
                approval.validation_evidence_digest
            ),
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
            "record_schema": "directive.approval.v3",
            "validation_digest": "approved-validation",
            "environment_digest": "wrong",
            "source_inventory_digest": "inventory",
            "processing_hash": "a" * 64,
            "mandate_checksum": "b" * 64,
            "validation_evidence_digest": "evidence",
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
            "approved-validation",
            "environment",
            "inventory",
            "b" * 64,
            "evidence",
        )

    runner.blobs.get_json.assert_awaited_once_with(
        "publication-approval/approved-validation.json"
    )


@pytest.mark.asyncio
async def test_published_approval_binds_the_mandate_checksum() -> None:
    checksum = "b" * 64
    runner = object.__new__(DirectiveIngestionRunner)
    runner.config = SimpleNamespace(processing_hash="a" * 64)
    runner.blobs = SimpleNamespace(
        get_json=AsyncMock(
            return_value={
                "record_schema": "directive.approval.v3",
                "validation_digest": "approved-validation",
                "environment_digest": "environment",
                "source_inventory_digest": "inventory",
                "processing_hash": "a" * 64,
                "mandate_checksum": checksum,
                "validation_evidence_digest": "evidence",
            }
        )
    )

    await runner._validate_published_approval(
        "approved-validation",
        "environment",
        "inventory",
        checksum,
        "evidence",
    )


@pytest.mark.asyncio
async def test_daily_binding_persists_validation_digest_to_source_state() -> None:
    source = _source()
    metadata = SimpleNamespace()
    state = SimpleNamespace(
        validation_digest=None,
        mandate_checksum=None,
        directive_metadata=metadata,
        artifact_generation_id="generation",
        pending_cleanup=(),
        validation_warnings=(),
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
        [
            SimpleNamespace(
                source=source,
                extraction_evidence=_extraction_evidence(),
            )
        ],
        "approved-validation",
        "b" * 64,
    )

    runner.source_states.record.assert_awaited_once_with(
        source,
        metadata,
        "generation",
        validation_digest="approved-validation",
        mandate_checksum="b" * 64,
        extraction_evidence=_extraction_evidence(),
        pending_cleanup=(),
        validation_warnings=(),
        expected_etag="etag",
    )


@pytest.mark.asyncio
async def test_daily_final_verification_uses_pinned_validation_evidence() -> None:
    source = _source()
    config = _config()
    config.mandate_csv = object()
    config.azure_tenant_id = "tenant"
    document = _evidence_document(source)
    mandates = SimpleNamespace(assignments=(), checksum="b" * 64, user_count=0)
    evidence = ValidationEvidence.create(
        processing_hash=config.processing_hash,
        mandate_checksum=mandates.checksum,
        documents=(document,),
    )
    approval_payload = _validation_payload(
        config,
        "approved",
        evidence.documents,
        mandates,
        [],
        evidence.evidence_hash,
    )
    inventory = SourceInventory.create(
        "approved",
        [
            SourceInventoryEntry.create(
                source.descriptor,
                source.identity,
                document.source_state_blob,
            )
        ],
    )
    inventory_snapshot = SourceInventorySnapshot(
        inventory=inventory,
        etag='"inventory-etag"',
        valid=True,
    )
    runner = object.__new__(DirectiveIngestionRunner)
    runner.config = config
    runner.validation_evidence = SimpleNamespace(
        load=AsyncMock(return_value=evidence)
    )
    runner.source_planner = SimpleNamespace(
        revalidate_descriptors=AsyncMock()
    )
    runner.source_inventory = SimpleNamespace(
        load_snapshot=AsyncMock(return_value=inventory_snapshot),
        commit=AsyncMock(),
    )
    runner._validate_published_approval = AsyncMock()
    runner._bind_source_state_validation_digest = AsyncMock()
    runner._prepare_approved_documents = AsyncMock(
        return_value=(
            [
                SourceMetadata(
                    source.reference(),
                    document.metadata,
                    None,
                    SimpleNamespace(),
                    extraction_evidence=document.extraction,
                )
            ],
            [],
        )
    )
    runner._validate_relations = AsyncMock()
    runner._reconcile_after_publication = AsyncMock()
    runner.mandates = SimpleNamespace(
        is_current=AsyncMock(return_value=True),
        validate_exact=AsyncMock(
            return_value={
                "snapshot_id": "mandates-" + "b" * 64,
                "checksum": "b" * 64,
                "assignment_count": 0,
                "user_count": 0,
            }
        ),
    )
    runner.search = SimpleNamespace(ensure_resources=AsyncMock())
    runner.commits = SimpleNamespace(
        load=AsyncMock(return_value=None),
        clear=AsyncMock(),
        acquire_publication_lock=AsyncMock(
            return_value=SimpleNamespace(run_id="run", etag="lock-etag")
        ),
        release_publication_lock=AsyncMock(),
    )
    runner.catalog = SimpleNamespace(record_run=AsyncMock())
    runner.verify = AsyncMock(return_value={"success": True})

    import directive_ingestion.reconcile as reconcile_module

    original_mandates = reconcile_module.parse_mandates
    reconcile_module.parse_mandates = lambda *_args: mandates
    try:
        await runner.run_daily(
            mandate_csv=Path("approved-mandates.csv"),
            approved_validation_digest=approval_payload["validation_digest"],
            approved_environment_digest=approval_payload["environment_digest"],
            approved_source_inventory_digest=approval_payload[
                "source_inventory_digest"
            ],
            approved_validation_evidence_digest=evidence.evidence_hash,
        )
    finally:
        reconcile_module.parse_mandates = original_mandates

    runner.source_planner.revalidate_descriptors.assert_awaited_once_with(
        evidence.documents
    )
    runner.verify.assert_awaited_once_with(
        expected_validation_digest=approval_payload["validation_digest"],
        expected_documents=evidence.documents,
        expected_mandates=mandates,
        expected_validation_evidence_digest=evidence.evidence_hash,
    )


@pytest.mark.asyncio
async def test_verify_rejects_source_state_with_different_approval_binding() -> None:
    source = _source()
    config = _config()
    document = _evidence_document(source)
    runner = object.__new__(DirectiveIngestionRunner)
    runner.config = config
    runner.source_states = SimpleNamespace(
        load_identity=AsyncMock(
            return_value=SimpleNamespace(
                validation_digest="other-validation",
                mandate_checksum="b" * 64,
                matches_descriptor=lambda _descriptor: True,
            )
        )
    )
    runner._state_has_live_publication = AsyncMock(return_value=True)

    with pytest.raises(RuntimeError, match="Source-state records"):
        await runner.verify(
            expected_validation_digest="approved-validation",
            expected_documents=(document,),
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
        mandate_checksum="b" * 64,
    )
    payload = blobs.replace_json.await_args.args[1]
    blobs.get_json.return_value = payload

    marker = await repository.load()

    assert payload["validation_digest"] == "approved-validation"
    assert payload["mandate_checksum"] == "b" * 64
    assert marker is not None
    assert marker.validation_digest == "approved-validation"
    assert marker.mandate_checksum == "b" * 64
