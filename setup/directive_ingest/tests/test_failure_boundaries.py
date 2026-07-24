from __future__ import annotations

from pathlib import Path
from datetime import date
from types import SimpleNamespace

import httpx
import pytest
from directive_contracts import (
    DirectiveMetadata,
    DirectiveRelation,
    MandateAssignment,
)

from directive_ingestion.catalog_repository import DirectiveCatalogRepository
from directive_ingestion.document_intelligence import (
    DocumentIntelligenceExtractor,
)
from directive_ingestion.mandate_projection import (
    MandateRepository,
    ParsedMandates,
)
from directive_ingestion.reconcile import (
    DirectiveIngestionRunner,
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
    assert runner.blobs.calls[0][0:2] == ("test-run", source.path.name)


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
        async def get_version(self, directive_id, directive_version_id):
            assert directive_id == metadata.directive_id
            assert directive_version_id == metadata.directive_version_id
            return {
                "publication_state": "published",
                "manifest_blob_name": "manifest.json",
                "summary_blob_name": "summary.json",
            }

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
