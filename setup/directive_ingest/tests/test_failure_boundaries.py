from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from azure.cosmos import exceptions
from directive_contracts import (
    DirectiveMetadata,
    DirectiveRelation,
    MandateAssignment,
)

from directive_ingestion.catalog_repository import (
    DirectiveCatalogRepository,
    LegacyCatalogArtifact,
)
from directive_ingestion.document_intelligence import (
    DocumentIntelligenceExtractor,
)
from directive_ingestion.mandate_projection import (
    MandateRepository,
    ParsedMandates,
)
from directive_ingestion.reconcile import (
    DirectiveIngestionRunner,
    _cleanup_confirmation_token,
    _select_current_relations,
    _validate_relation_graph,
    _validate_relation_depth,
)
from directive_ingestion.search_repository import (
    DirectiveSearchRepository,
)
from directive_ingestion.source import discover_pdfs

ROOT = Path(__file__).parents[3]
FIXTURES = ROOT / "setup" / "directives"


class _Credential:
    async def get_token(self, scope: str):
        assert scope
        return SimpleNamespace(token="test-token")


def _published_bundle_stub() -> SimpleNamespace:
    return SimpleNamespace(
        id="version:72403881:v2",
        directive_id="72403881",
        directive_version_id="72403881:v2",
        model_dump=lambda **_kwargs: {
            "id": "version:72403881:v2",
            "directive_id": "72403881",
        },
    )


@pytest.mark.asyncio
async def test_document_intelligence_uses_acquired_bearer_token() -> None:
    class RecordingExtractor(DocumentIntelligenceExtractor):
        async def _request_with_retry(self, method, url, **kwargs):
            assert method == "POST"
            assert kwargs["headers"]["Authorization"] == (
                "Bearer test-token"
            )
            return httpx.Response(
                200,
                json={
                    "analyzeResult": {
                        "content": "# Directive\n\n## 1. Body\nText",
                        "pages": [{}],
                        "paragraphs": [],
                        "tables": [],
                    }
                },
            )

    extractor = RecordingExtractor(
        "https://document.example.com",
        "2024-11-30",
        _Credential(),
    )
    try:
        result = await extractor.extract(b"%PDF-test")
    finally:
        await extractor.close()

    assert result.total_pages == 1


@pytest.mark.asyncio
async def test_search_uses_acquired_bearer_token() -> None:
    repository = object.__new__(DirectiveSearchRepository)
    repository._credential = _Credential()

    headers = await repository._headers()

    assert headers["Authorization"] == "Bearer test-token"


@pytest.mark.asyncio
async def test_catalog_publish_reports_create_conflict_as_concurrency() -> None:
    repository = object.__new__(DirectiveCatalogRepository)
    repository.get_version = AsyncMock(return_value=None)
    repository._container = SimpleNamespace(
        create_item=AsyncMock(
            side_effect=exceptions.CosmosResourceExistsError(
                status_code=409,
                message="conflict",
            )
        )
    )

    with pytest.raises(RuntimeError, match="Concurrent catalog publication"):
        await repository._replace_published_bundle(_published_bundle_stub())


@pytest.mark.asyncio
async def test_catalog_publish_preserves_non_concurrency_cosmos_error() -> None:
    failure = exceptions.CosmosHttpResponseError(
        status_code=500,
        message="service unavailable",
    )
    repository = object.__new__(DirectiveCatalogRepository)
    repository.get_version = AsyncMock(return_value=None)
    repository._container = SimpleNamespace(
        create_item=AsyncMock(side_effect=failure)
    )

    with pytest.raises(exceptions.CosmosHttpResponseError) as caught:
        await repository._replace_published_bundle(_published_bundle_stub())

    assert caught.value is failure


@pytest.mark.asyncio
async def test_invalid_schema_is_not_treated_as_unchanged() -> None:
    source = SimpleNamespace(
        directive_id_hint="72403881",
        directive_version_id_hint="72403881:v2",
        source_hash="a" * 64,
    )
    repository = object.__new__(DirectiveCatalogRepository)
    repository.get_version = AsyncMock(
        return_value={
            "publication_state": "published",
            "artifact_schema_version": "2.0",
            "source_hash": source.source_hash,
            "processing_hash": "b" * 64,
        }
    )

    assert await repository.is_unchanged(source, "b" * 64) is False


@pytest.mark.asyncio
async def test_cleanup_dry_run_does_not_delete_artifacts() -> None:
    blob_names = ["legacy/summary.json"]
    artifacts = [
        LegacyCatalogArtifact(
            item_type="summary",
            directive_id="72403881",
            item_id="summary:72403881:v2:source",
        )
    ]
    runner = object.__new__(DirectiveIngestionRunner)
    runner.verify = AsyncMock()
    runner.blobs = SimpleNamespace(
        list_legacy_directive_artifacts=AsyncMock(
            return_value=blob_names
        ),
        delete_legacy_directive_artifacts=AsyncMock(),
    )
    runner.catalog = SimpleNamespace(
        list_legacy_artifacts=AsyncMock(return_value=artifacts),
        delete_legacy_artifacts=AsyncMock(),
    )

    result = await runner.cleanup_legacy_artifacts()

    assert result["mode"] == "dry_run"
    assert result["blob_names"] == blob_names
    assert result["catalog_items"] == [artifacts[0].as_dict()]
    runner.verify.assert_not_awaited()
    runner.blobs.delete_legacy_directive_artifacts.assert_not_awaited()
    runner.catalog.delete_legacy_artifacts.assert_not_awaited()


@pytest.mark.asyncio
async def test_cleanup_execute_is_bound_to_inventory_and_verifies() -> None:
    blob_names = ["legacy/manifest.json"]
    artifacts = [
        LegacyCatalogArtifact(
            item_type="manifest",
            directive_id="72403881",
            item_id="manifest:72403881:v2:source",
        )
    ]
    inventory = {
        "blob_names": blob_names,
        "catalog_items": [artifacts[0].as_dict()],
    }
    runner = object.__new__(DirectiveIngestionRunner)
    runner.verify = AsyncMock()
    runner.blobs = SimpleNamespace(
        list_legacy_directive_artifacts=AsyncMock(
            side_effect=[blob_names, []]
        ),
        delete_legacy_directive_artifacts=AsyncMock(),
    )
    runner.catalog = SimpleNamespace(
        list_legacy_artifacts=AsyncMock(
            side_effect=[artifacts, []]
        ),
        delete_legacy_artifacts=AsyncMock(),
    )

    result = await runner.cleanup_legacy_artifacts(
        _cleanup_confirmation_token(inventory)
    )

    assert result["mode"] == "execute"
    assert result["deleted_blob_count"] == 1
    assert result["deleted_catalog_item_count"] == 1
    assert runner.verify.await_count == 2
    runner.blobs.delete_legacy_directive_artifacts.assert_awaited_once_with(
        blob_names
    )
    runner.catalog.delete_legacy_artifacts.assert_awaited_once_with(
        artifacts
    )


@pytest.mark.asyncio
async def test_cleanup_execute_rejects_changed_inventory() -> None:
    runner = object.__new__(DirectiveIngestionRunner)
    runner.verify = AsyncMock()
    runner.blobs = SimpleNamespace(
        list_legacy_directive_artifacts=AsyncMock(return_value=[]),
        delete_legacy_directive_artifacts=AsyncMock(),
    )
    runner.catalog = SimpleNamespace(
        list_legacy_artifacts=AsyncMock(return_value=[]),
        delete_legacy_artifacts=AsyncMock(),
    )

    with pytest.raises(RuntimeError, match="inventory changed"):
        await runner.cleanup_legacy_artifacts("a" * 64)

    runner.verify.assert_awaited_once_with()
    runner.blobs.delete_legacy_directive_artifacts.assert_not_awaited()
    runner.catalog.delete_legacy_artifacts.assert_not_awaited()


@pytest.mark.asyncio
async def test_cosmos_publish_failure_retires_published_search_chunks() -> None:
    failure = exceptions.CosmosHttpResponseError(
        status_code=500,
        message="service unavailable",
    )
    item = SimpleNamespace(
        bundle=object(),
        canonical=SimpleNamespace(relations=()),
        content_items=(),
        search_chunks=[object()],
        findings=(),
    )
    runner = object.__new__(DirectiveIngestionRunner)
    runner._publish_artifacts = AsyncMock()
    runner.content = SimpleNamespace(
        create_or_compare=AsyncMock(),
        validate_bundle=AsyncMock(),
    )
    runner.search = SimpleNamespace(
        stage_chunks=AsyncMock(),
        publish_chunks=AsyncMock(),
        validate_published=AsyncMock(),
        retire_chunks=AsyncMock(),
    )
    runner.catalog = SimpleNamespace(
        stage_version=AsyncMock(),
        publish_version=AsyncMock(side_effect=failure),
        validate_published=AsyncMock(),
    )

    with pytest.raises(exceptions.CosmosHttpResponseError) as caught:
        await runner._publish_documents([item], [], "test-run")

    assert caught.value is failure
    runner.search.retire_chunks.assert_awaited_once_with(item.search_chunks)


@pytest.mark.asyncio
async def test_catalog_validation_transport_failure_keeps_search_published() -> None:
    failure = exceptions.CosmosHttpResponseError(
        status_code=503,
        message="service unavailable",
    )
    item = SimpleNamespace(
        bundle=object(),
        canonical=SimpleNamespace(relations=()),
        content_items=(),
        search_chunks=[object()],
        findings=(),
    )
    runner = object.__new__(DirectiveIngestionRunner)
    runner._publish_artifacts = AsyncMock()
    runner.content = SimpleNamespace(
        create_or_compare=AsyncMock(),
        validate_bundle=AsyncMock(),
    )
    runner.search = SimpleNamespace(
        stage_chunks=AsyncMock(),
        publish_chunks=AsyncMock(),
        validate_published=AsyncMock(),
        retire_chunks=AsyncMock(),
    )
    runner.catalog = SimpleNamespace(
        stage_version=AsyncMock(),
        publish_version=AsyncMock(),
        validate_published=AsyncMock(side_effect=failure),
    )

    with pytest.raises(exceptions.CosmosHttpResponseError) as caught:
        await runner._publish_documents([item], [], "test-run")

    assert caught.value is failure
    runner.search.retire_chunks.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_document_is_quarantined_before_publication() -> None:
    class Catalog:
        async def is_unchanged(self, source, processing_hash):
            return False

    class Extractor:
        async def extract(self, content):
            raise ValueError("invalid document-control table")

    class Blobs:
        def __init__(self):
            self.calls = []

        async def quarantine(self, run_id, filename, source, errors):
            self.calls.append((run_id, filename, source, errors))

    runner = object.__new__(DirectiveIngestionRunner)
    runner.config = SimpleNamespace(processing_hash="a" * 64)
    runner.catalog = Catalog()
    runner.extractor = Extractor()
    runner.blobs = Blobs()
    source = discover_pdfs(FIXTURES / "pdf")[0]

    with pytest.raises(RuntimeError, match="Preflight failed"):
        await runner._prepare([source], "test-run")

    assert len(runner.blobs.calls) == 1
    assert runner.blobs.calls[0][0:2] == ("test-run", source.source_name)


def test_relation_graph_rejects_cycles_and_third_layer() -> None:
    with pytest.raises(ValueError, match="cycle"):
        _validate_relation_depth({"a": {"b"}, "b": {"a"}})

    with pytest.raises(ValueError, match="two-layer"):
        _validate_relation_depth({"a": {"b"}, "b": {"c"}})

    with pytest.raises(ValueError, match="disconnected cycle"):
        _validate_relation_depth(
            {"root": {"child"}, "x": {"y"}, "y": {"x"}}
        )


def test_relation_graph_combines_changed_and_unchanged_sources() -> None:
    changed = DirectiveRelation(
        relation_id="changed",
        source_directive_id="11111111",
        source_version_id="11111111:v1",
        target_directive_id="22222222",
        relation_type="sub_directive",
        status="accepted",
        evidence="changed",
    )
    unchanged = DirectiveRelation(
        relation_id="unchanged",
        source_directive_id="22222222",
        source_version_id="22222222:v1",
        target_directive_id="33333333",
        relation_type="sub_directive",
        status="accepted",
        evidence="unchanged",
    )
    relations = _select_current_relations(
        {"11111111": [changed]},
        [(unchanged, "a" * 64, "b" * 64)],
        {
            "22222222": (
                "22222222:v1",
                "a" * 64,
                "b" * 64,
            )
        },
    )

    with pytest.raises(ValueError, match="two-layer"):
        _validate_relation_graph(relations)


@pytest.mark.asyncio
async def test_mandate_pointer_is_not_switched_on_count_mismatch() -> None:
    class Container:
        def __init__(self):
            self.items = []

        async def upsert_item(self, item):
            self.items.append(item)

        async def query_items(self, **kwargs):
            del kwargs
            yield 0

    class Repository(MandateRepository):
        async def _read_active(self):
            return None

    repository = object.__new__(Repository)
    repository._container = Container()
    parsed = ParsedMandates(
        assignments=(
            MandateAssignment(
                user_id=(
                    "a7b1484c-f66a-496a-b1cf-35631a50396c:"
                    "9254fe2a-17e2-4326-b724-095edc1d96a8"
                ),
                directive_id="72403881",
            ),
        ),
        checksum="b" * 64,
        user_count=1,
    )

    with pytest.raises(RuntimeError, match="validation failed"):
        await repository.publish(parsed, "test-run")

    assert not any(
        item["id"] == "active-snapshot"
        for item in repository._container.items
    )


@pytest.mark.asyncio
async def test_published_version_repairs_missing_current_pointer() -> None:
    metadata = DirectiveMetadata(
        directive_id="72403881",
        directive_version_id="72403881:v2",
        version_label="2.0",
        title="Company Car Policy",
        status="Current",
        is_current=True,
        effective_from=date(2026, 4, 1),
        source_filename="72403881-company-car-policy-v2.pdf",
        source_hash="c" * 64,
        processing_hash="d" * 64,
    )

    class Container:
        def __init__(self):
            self.items = []

        async def upsert_item(self, item):
            self.items.append(item)

    class Repository(DirectiveCatalogRepository):
        async def get_published_version(
            self, directive_id, directive_version_id
        ):
            assert directive_id == metadata.directive_id
            assert directive_version_id == metadata.directive_version_id
            return SimpleNamespace(
                directive_id=metadata.directive_id,
                directive_version_id=metadata.directive_version_id,
                version_label=metadata.version_label,
                source_hash=metadata.source_hash,
                processing_hash=metadata.processing_hash,
                artifact_generation_id="e" * 64,
                effective_from=metadata.effective_from,
            )

        async def get_current(self, directive_id):
            assert directive_id == metadata.directive_id
            return None

    repository = object.__new__(Repository)
    repository._container = Container()

    changed = await repository.activate_current(metadata, "retry-run")

    assert changed is True
    assert repository._container.items[0]["id"] == "current"
    assert (
        repository._container.items[0]["directive_version_id"]
        == metadata.directive_version_id
    )
    assert (
        repository._container.items[0]["artifact_generation_id"]
        == "e" * 64
    )
