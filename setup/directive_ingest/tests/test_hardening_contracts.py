from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, create_autospec

import httpx
import pytest
from azure.core.exceptions import ResourceNotFoundError
from directive_contracts import DirectiveMetadata, source_fingerprint

from directive_ingestion.config import RetryPolicyConfig
from directive_ingestion.blob_repository import BlobArtifactRepository
from directive_ingestion.document_intelligence import (
    ContentSpan,
    ExtractedDocument,
    ExtractedPage,
)
from directive_ingestion.extraction_cache import (
    ExtractionCacheEvidence,
    ExtractionCacheRepository,
    ExtractorIdentity,
)
from directive_ingestion.provider_retry import (
    ProviderRetryExhausted,
    RetryBudget,
    retry_provider_call,
)
from directive_ingestion.publication_gate import _gate_body, _parse_gate
from directive_ingestion.run_metrics import IngestionRunMetrics
from directive_ingestion.source import SourceDescriptor, SourceIdentity
from directive_ingestion.source_inventory import (
    SOURCE_INVENTORY_BLOB,
    SourceInventory,
    SourceInventoryEntry,
    SourceInventoryRepository,
    SourceInventorySnapshot,
)
from directive_ingestion.source_planner import DirectiveSourcePlanner
from directive_ingestion.source_state_repository import PublishedSourceState
from directive_ingestion.validation_evidence import (
    ValidationEvidence,
    ValidationEvidenceDocument,
    ValidationEvidenceRepository,
)


def test_publication_gate_uses_the_v3_control_record_contract() -> None:
    item = _gate_body(
        state="committed",
        revision="a" * 64,
        candidate_revision=None,
        run_id="run-1",
    )
    stored = {**item, "_etag": "etag-1"}

    assert item["id"] == "directive-publication-gate"
    assert item["directive_id"] == "_control"
    assert item["committed_revision"] == "a" * 64
    assert "revision" not in item
    assert _parse_gate(stored).revision == "a" * 64


def test_ingestion_metrics_record_activation_outcome_and_duration() -> None:
    metrics = IngestionRunMetrics(
        run_id="run-1",
        operation="run-daily",
        processing_hash="a" * 64,
    )
    metrics.begin_activation_gate()
    metrics.end_activation_gate("rollback")
    metrics.fail("runtimeerror")

    payload = metrics.to_payload()

    assert payload["publication_result"] == "rollback"
    assert isinstance(payload["activation_gate_duration_ms"], int)


class _MemoryArtifacts:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}
        self.metadata: dict[str, dict[str, str]] = {}
        self.etags: dict[str, str] = {}

    async def put_immutable(
        self,
        name: str,
        content: bytes,
        content_type: str,
    ) -> str:
        del content_type
        if name in self.values and self.values[name] != content:
            raise RuntimeError("collision")
        self.values[name] = content
        self.metadata[name] = {
            "content_sha256": hashlib.sha256(content).hexdigest()
        }
        self.etags[name] = '"etag-1"'
        return self.etags[name]

    async def read_bytes_with_metadata_and_etag(self, name: str):
        if name not in self.values:
            raise ResourceNotFoundError("missing")
        return self.values[name], self.metadata[name], self.etags[name]

    async def replace_json(
        self,
        name: str,
        payload: dict,
        *,
        expected_etag: str | None,
        require_absent: bool,
    ) -> str:
        assert require_absent == (expected_etag is None)
        content = json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
        self.values[name] = content
        self.metadata[name] = {
            "content_sha256": hashlib.sha256(content).hexdigest()
        }
        self.etags[name] = '"etag-2"'
        return self.etags[name]


def _descriptor(
    etag: str = '"source-etag"',
    version_id: str = "version-1",
) -> SourceDescriptor:
    return SourceDescriptor(
        source_name="directive.pdf",
        kind="azure_blob",
        locator="incoming/directive.pdf",
        etag=etag,
        version_id=version_id,
        size=123,
        last_modified=datetime(2026, 8, 1, tzinfo=UTC),
    )


def _identity() -> SourceIdentity:
    return SourceIdentity("directive.pdf", "a" * 64)


def _metadata() -> DirectiveMetadata:
    return DirectiveMetadata(
        directive_id="12345678",
        directive_version_id="12345678:v1",
        version_label="1",
        title="Directive",
        status="Current",
        is_current=True,
        is_valid=True,
        effective_from=date(2026, 1, 1),
        source_filename="directive.pdf",
        source_hash="a" * 64,
        processing_hash="b" * 64,
    )


def _validation_evidence() -> ValidationEvidence:
    document = ValidationEvidenceDocument(
        descriptor=_descriptor(),
        identity=_identity(),
        metadata=_metadata(),
        source_state_blob="source-state/state.json",
        disposition="changed",
        extraction=ExtractionCacheEvidence(
            blob_name="extractions/cache.json.gz",
            extractor_identity_hash="c" * 64,
            result_hash="d" * 64,
        ),
    )

    return ValidationEvidence.create(
        processing_hash="b" * 64,
        mandate_checksum="e" * 64,
        documents=(document,),
    )


def test_validation_evidence_serializes_descriptor_timestamp() -> None:
    evidence = _validation_evidence()

    assert evidence.to_payload()["documents"][0]["descriptor"][
        "last_modified"
    ] == "2026-08-01T00:00:00+00:00"


@pytest.mark.asyncio
async def test_validation_evidence_uses_real_immutable_json_interface() -> None:
    artifacts = create_autospec(BlobArtifactRepository, instance=True)
    repository = ValidationEvidenceRepository(artifacts)
    evidence = _validation_evidence()

    await repository.store("f" * 64, evidence)

    artifacts.put_json.assert_awaited_once_with(
        f"validation-evidence/{'f' * 64}.json",
        evidence.to_payload(),
    )


def test_source_inventory_matches_only_immutable_descriptor_identity() -> None:
    entry = SourceInventoryEntry.create(
        _descriptor(),
        _identity(),
        "source-state/state.json",
    )
    inventory = SourceInventory.create("run-1", [entry])

    assert entry.matches(_descriptor())
    assert not entry.matches(_descriptor('"changed"', "version-2"))
    assert SourceInventory.create("run-1", [entry]).inventory_hash == (
        inventory.inventory_hash
    )


@pytest.mark.asyncio
async def test_absent_source_inventory_is_a_valid_empty_snapshot() -> None:
    artifacts = SimpleNamespace(
        read_bytes_with_metadata_and_etag=AsyncMock(return_value=None)
    )
    repository = SourceInventoryRepository(artifacts)

    snapshot = await repository.load_snapshot()

    assert snapshot == SourceInventorySnapshot(
        inventory=None,
        etag=None,
        valid=True,
    )


@pytest.mark.asyncio
async def test_source_inventory_corruption_forces_safe_invalid_snapshot() -> None:
    artifacts = _MemoryArtifacts()
    repository = SourceInventoryRepository(artifacts)
    inventory = SourceInventory.create(
        "run-1",
        [
            SourceInventoryEntry.create(
                _descriptor(),
                _identity(),
                "source-state/state.json",
            )
        ],
    )
    await repository.commit(inventory, expected_etag=None)
    artifacts.values[SOURCE_INVENTORY_BLOB] += b"corrupt"

    snapshot = await repository.load_snapshot()

    assert snapshot.inventory is None
    assert snapshot.etag == '"etag-2"'
    assert snapshot.valid is False


@pytest.mark.asyncio
async def test_absent_extraction_cache_is_a_cache_miss() -> None:
    artifacts = SimpleNamespace(
        read_bytes_with_metadata_and_etag=AsyncMock(return_value=None)
    )
    repository = ExtractionCacheRepository(artifacts)

    cached = await repository.load(
        _identity(),
        ExtractorIdentity("2024-11-30"),
    )

    assert cached is None


@pytest.mark.asyncio
async def test_extraction_cache_round_trip_is_strict_and_gzipped() -> None:
    artifacts = _MemoryArtifacts()
    repository = ExtractionCacheRepository(artifacts)
    extractor = ExtractorIdentity("2024-11-30")
    span = ContentSpan(offset=0, length=3, page_number=1)
    document = ExtractedDocument(
        markdown="abc",
        pages=(
            ExtractedPage(
                page_number=1,
                width=10,
                height=10,
                unit="pixel",
                spans=(span,),
            ),
        ),
        lines=(),
        paragraphs=(),
        tables=(),
        content_spans=(span,),
    )

    stored = await repository.store(_identity(), extractor, document)
    loaded = await repository.load(
        _identity(),
        extractor,
        expected_result_hash=stored.evidence.result_hash,
    )

    assert loaded == stored
    assert artifacts.values[stored.evidence.blob_name][:2] == b"\x1f\x8b"


@pytest.mark.asyncio
async def test_unchanged_planning_performs_zero_source_downloads() -> None:
    descriptor = _descriptor()
    identity = _identity()
    evidence = ExtractionCacheEvidence(
        blob_name="extractions/cache.json.gz",
        extractor_identity_hash=ExtractorIdentity(
            "2024-11-30"
        ).identity_hash,
        result_hash="c" * 64,
    )
    state = PublishedSourceState(
        source_filename=identity.source_name,
        source_hash=identity.source_hash,
        source_fingerprint=source_fingerprint(
            identity.source_name,
            identity.source_hash,
        ),
        processing_hash="b" * 64,
        directive_metadata=_metadata(),
        artifact_generation_id="d" * 64,
        publication_state="published",
        source_etag=descriptor.etag,
        source_version_id=descriptor.version_id,
        source_size=descriptor.size,
        source_last_modified=descriptor.last_modified.isoformat(),
        extraction_cache_blob=evidence.blob_name,
        extractor_identity_hash=evidence.extractor_identity_hash,
        extraction_result_hash=evidence.result_hash,
    )
    entry = SourceInventoryEntry.create(
        descriptor,
        identity,
        "source-state/state.json",
    )
    source = SimpleNamespace(
        list_descriptors=AsyncMock(return_value=[descriptor]),
        download=AsyncMock(side_effect=AssertionError("must not download")),
    )
    states = SimpleNamespace(
        load_identity=AsyncMock(return_value=state),
        blob_name=AsyncMock(),
    )
    planner = DirectiveSourcePlanner(
        source=source,
        inventory=SimpleNamespace(
            load_snapshot=AsyncMock(
                return_value=SourceInventorySnapshot(
                    inventory=SourceInventory.create("run-1", [entry]),
                    etag='"etag"',
                    valid=True,
                )
            )
        ),
        states=states,
        cache=SimpleNamespace(),
        extractor=SimpleNamespace(),
        extractor_identity=ExtractorIdentity("2024-11-30"),
        processing_hash="b" * 64,
        extraction_concurrency=2,
        is_live=AsyncMock(return_value=True),
    )

    plan = await planner.validate()

    assert plan.documents[0].disposition == "unchanged"
    source.download.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_policy_retries_only_transient_statuses() -> None:
    policy = RetryPolicyConfig(3, 0, 0, 0, 2)
    transient = httpx.HTTPStatusError(
        "busy",
        request=httpx.Request("GET", "https://example.test"),
        response=httpx.Response(
            429,
            request=httpx.Request("GET", "https://example.test"),
        ),
    )
    operation = AsyncMock(side_effect=[transient, "ok"])

    assert await retry_provider_call(
        operation,
        policy=policy,
        budget=RetryBudget(2),
    ) == "ok"
    assert operation.await_count == 2

    forbidden = httpx.HTTPStatusError(
        "forbidden",
        request=httpx.Request("GET", "https://example.test"),
        response=httpx.Response(
            403,
            request=httpx.Request("GET", "https://example.test"),
        ),
    )
    operation = AsyncMock(side_effect=forbidden)
    with pytest.raises(httpx.HTTPStatusError):
        await retry_provider_call(
            operation,
            policy=policy,
            budget=RetryBudget(2),
        )
    assert operation.await_count == 1


@pytest.mark.asyncio
async def test_retry_metrics_count_attempts_retries_and_throttles_exactly() -> None:
    request = httpx.Request("GET", "https://example.test")
    throttled = httpx.HTTPStatusError(
        "busy",
        request=request,
        response=httpx.Response(
            429,
            headers={"Retry-After": "10"},
            request=request,
        ),
    )
    unavailable = httpx.HTTPStatusError(
        "unavailable",
        request=request,
        response=httpx.Response(503, request=request),
    )
    operation = AsyncMock(side_effect=[throttled, unavailable, throttled])
    sleep = AsyncMock()
    attempts: list[None] = []
    retries: list[int] = []
    throttles: list[int] = []
    budget = RetryBudget(2)

    with pytest.raises(ProviderRetryExhausted, match="exhausted retry attempts"):
        await retry_provider_call(
            operation,
            policy=RetryPolicyConfig(3, 1, 2, 0, 2),
            budget=budget,
            sleep=sleep,
            on_attempt=lambda: attempts.append(None),
            on_retry=lambda error: retries.append(error.response.status_code),
            on_throttle=lambda error: throttles.append(
                error.response.status_code
            ),
        )

    assert len(attempts) == 3
    assert retries == [429, 503]
    assert throttles == [429, 429]
    assert [call.args[0] for call in sleep.await_args_list] == [2, 2]
    assert budget.remaining == 0


@pytest.mark.asyncio
async def test_retry_deadline_cancels_a_stalled_provider_attempt() -> None:
    attempts: list[None] = []

    async def stalled() -> None:
        await asyncio.sleep(60)

    with pytest.raises(ProviderRetryExhausted, match="retry deadline"):
        await retry_provider_call(
            stalled,
            policy=RetryPolicyConfig(3, 0, 0, 0, 2, 0.01),
            budget=RetryBudget(2),
            on_attempt=lambda: attempts.append(None),
        )

    assert len(attempts) == 1
