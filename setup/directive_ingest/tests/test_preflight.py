from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from directive_contracts import (
    DirectiveSummary,
    calculate_artifact_generation_id,
    canonical_json_hash,
)

from directive_ingestion.reconcile import DirectiveIngestionRunner

_SETUP_DIR = Path(__file__).parents[2]
_MANDATE_FIXTURE = Path(__file__).parent / "fixtures" / "mand.csv"


@pytest.mark.asyncio
async def test_preflight_checks_every_data_plane_without_publication() -> None:
    runner = object.__new__(DirectiveIngestionRunner)
    runner.config = SimpleNamespace(
        embedding_deployment="text-embedding-3-large",
        embedding_dimensions=3,
        summary_deployment="gpt-5.6-sol",
    )
    runner.blobs = SimpleNamespace(check_access=AsyncMock())
    runner.source = SimpleNamespace(check_access=AsyncMock())
    runner.catalog = SimpleNamespace(check_access=AsyncMock())
    runner.content = SimpleNamespace(check_access=AsyncMock())
    runner.mandates = SimpleNamespace(check_access=AsyncMock())
    runner.search = SimpleNamespace(check_access=AsyncMock())
    runner.extractor = SimpleNamespace(check_access=AsyncMock())
    embeddings = AsyncMock(
        return_value=SimpleNamespace(
            data=[SimpleNamespace(embedding=[0.1, 0.2, 0.3])]
        )
    )
    responses = AsyncMock(
        return_value=SimpleNamespace(output_text="READY")
    )
    runner.clients = SimpleNamespace(
        openai=SimpleNamespace(
            embeddings=SimpleNamespace(create=embeddings),
            responses=SimpleNamespace(create=responses),
        )
    )

    result = await runner.preflight()

    assert result == {
        "acr_pull": "ok",
        "source": "ok",
        "blob": "ok",
        "cosmos_catalog": "ok",
        "cosmos_content": "ok",
        "cosmos_mandates": "ok",
        "search": "ok",
        "document_intelligence": "ok",
        "embeddings": "ok",
        "summary_model": "ok",
    }
    runner.blobs.check_access.assert_awaited_once_with()
    runner.source.check_access.assert_awaited_once_with()
    runner.catalog.check_access.assert_awaited_once_with()
    runner.content.check_access.assert_awaited_once_with()
    runner.mandates.check_access.assert_awaited_once_with()
    runner.search.check_access.assert_awaited_once_with()
    runner.extractor.check_access.assert_awaited_once_with()
    responses.assert_awaited_once()


@pytest.mark.asyncio
async def test_preflight_rejects_wrong_embedding_dimensions() -> None:
    runner = object.__new__(DirectiveIngestionRunner)
    runner.config = SimpleNamespace(
        embedding_deployment="text-embedding-3-large",
        embedding_dimensions=3,
    )
    check = AsyncMock()
    runner.blobs = SimpleNamespace(check_access=check)
    runner.source = SimpleNamespace(check_access=AsyncMock())
    runner.catalog = SimpleNamespace(check_access=AsyncMock())
    runner.content = SimpleNamespace(check_access=AsyncMock())
    runner.mandates = SimpleNamespace(check_access=AsyncMock())
    runner.search = SimpleNamespace(check_access=AsyncMock())
    runner.extractor = SimpleNamespace(check_access=AsyncMock())
    runner.clients = SimpleNamespace(
        openai=SimpleNamespace(
            embeddings=SimpleNamespace(
                create=AsyncMock(
                    return_value=SimpleNamespace(
                        data=[SimpleNamespace(embedding=[0.1, 0.2])]
                    )
                )
            )
        )
    )

    with pytest.raises(
        RuntimeError,
        match="Embedding preflight returned an unexpected vector shape",
    ):
        await runner.preflight()


@pytest.mark.asyncio
async def test_verify_cross_checks_published_and_retained_surfaces() -> None:
    runner = object.__new__(DirectiveIngestionRunner)
    runner.config = SimpleNamespace(
        mandate_csv=_MANDATE_FIXTURE,
        azure_tenant_id="a7b1484c-f66a-496a-b1cf-35631a50396c",
    )
    from directive_ingestion.source import discover_pdfs

    source_documents = discover_pdfs(_SETUP_DIR / "directives" / "pdf")
    active_sources = [
        source
        for source in source_documents
        if source.directive_id_hint != "95315332"
    ]
    runner.source = SimpleNamespace(
        discover=AsyncMock(return_value=active_sources)
    )
    source_hashes = {
        (
            source.directive_id_hint,
            source.directive_version_id_hint,
        ): source.source_hash
        for source in source_documents
    }
    directive_ids = {"30336958", "36269153", "72403881", "95315332"}
    versions = [
        ("30336958", "30336958:v1"),
        ("36269153", "36269153:v1"),
        ("36269153", "36269153:v2"),
        ("72403881", "72403881:v1"),
        ("72403881", "72403881:v2"),
        ("95315332", "95315332:v1"),
        ("95315332", "95315332:v2"),
    ]
    canonical_hash = "c" * 64
    bundles = []
    artifact_hashes = {}
    for index, (directive_id, version_id) in enumerate(versions):
        source_hash = source_hashes[(directive_id, version_id)]
        processing_hash = f"{index + 10:064x}"
        summary = DirectiveSummary(
            directive_id=directive_id,
            directive_version_id=version_id,
            source_hash=source_hash,
            summary=f"summary {index}",
            covered_section_ids=[f"section-{index}"],
            total_section_count=1,
            input_token_count=10,
            strategy="full_document",
            model_deployment="test",
        )
        generation_id = calculate_artifact_generation_id(
            processing_hash,
            canonical_hash,
            canonical_json_hash(summary),
        )
        artifacts = SimpleNamespace(
            source_blob_name=f"directives/{index}/source.pdf",
            canonical_blob_name=f"directives/{index}/document.md",
        )
        bundles.append(
            SimpleNamespace(
                directive_id=directive_id,
                directive_version_id=version_id,
                source_hash=source_hash,
                processing_hash=processing_hash,
                artifact_generation_id=generation_id,
                summary=summary,
                artifacts=artifacts,
                manifest=SimpleNamespace(
                    sections=[
                        SimpleNamespace(chunk_ids=[f"chunk-{index}"])
                    ]
                ),
            )
        )
        artifact_hashes[artifacts.source_blob_name] = source_hash
        artifact_hashes[artifacts.canonical_blob_name] = canonical_hash
    artifact_names = {
        name
        for bundle in bundles
        for name in (
            bundle.artifacts.source_blob_name,
            bundle.artifacts.canonical_blob_name,
        )
    }
    current = {}
    for directive_id in directive_ids:
        bundle = next(
            item for item in bundles if item.directive_id == directive_id
        )
        current[directive_id] = (
            bundle.directive_version_id,
            bundle.source_hash,
            bundle.processing_hash,
            bundle.artifact_generation_id,
        )
    runner.catalog = SimpleNamespace(
        list_published_directive_ids=AsyncMock(return_value=directive_ids),
        list_published_versions=AsyncMock(return_value=bundles),
        list_current_pointers=AsyncMock(return_value=current),
        list_published_relations=AsyncMock(
            return_value=[
                (SimpleNamespace(relation_id="relation-1"), "source", "process")
            ]
        ),
    )
    runner.blobs = SimpleNamespace(
        list_names=AsyncMock(return_value=artifact_names),
        content_hash=AsyncMock(
            side_effect=lambda blob_name: artifact_hashes[blob_name]
        ),
    )
    runner.content = SimpleNamespace(
        validate_bundle=AsyncMock(
            return_value={
                "content_sections": 1,
                "content_parts": 1,
                "split_sections": 0,
            }
        )
    )
    runner.search = SimpleNamespace(
        verification_summary=AsyncMock(
            return_value={
                "published_chunks": 7,
                "published_directives": 4,
                "published_versions": 7,
                "current_chunks": 4,
                "current_directives": 4,
                "current_versions": 4,
                "vector_dimensions": 3072,
                "search_index": "directive-chunks-v1",
                "vector_profile": "directive-vector-profile",
                "vectorizer": "directive-openai-vectorizer",
                "semantic_configuration": "semantic_config",
                "direct_hybrid_query": "ok",
            }
        )
    )
    runner.mandates = SimpleNamespace(
        verification_summary=AsyncMock(
            return_value={
                "snapshot_id": "mandates-checksum",
                "assignment_count": 5,
                "user_count": 2,
            }
        )
    )

    result = await runner.verify()

    assert result["source_versions"] == 5
    assert result["directive_ids"] == 4
    assert result["current_versions"] == 4
    assert result["accepted_relations"] == 1
    assert result["required_artifacts"] == 14
    assert result["content_sections"] == 7
    assert result["content_parts"] == 7
    assert result["published_chunks"] == 7
    assert result["mandate_assignment_count"] == 5
