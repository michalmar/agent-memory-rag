from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from directive_ingestion.reconcile import DirectiveIngestionRunner

_SETUP_DIR = Path(__file__).parents[2]


@pytest.mark.asyncio
async def test_preflight_checks_every_data_plane_without_publication() -> None:
    runner = object.__new__(DirectiveIngestionRunner)
    runner.config = SimpleNamespace(
        embedding_deployment="text-embedding-3-large",
        embedding_dimensions=3,
        summary_deployment="gpt-5.6-sol",
        knowledge_model_deployment="gpt-5-nano-directive-kb",
    )
    runner.blobs = SimpleNamespace(check_access=AsyncMock())
    runner.catalog = SimpleNamespace(check_access=AsyncMock())
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
        "blob": "ok",
        "cosmos_catalog": "ok",
        "cosmos_mandates": "ok",
        "search": "ok",
        "document_intelligence": "ok",
        "embeddings": "ok",
        "summary_model": "ok",
        "knowledge_planner_model": "ok",
    }
    runner.blobs.check_access.assert_awaited_once_with()
    runner.catalog.check_access.assert_awaited_once_with()
    runner.mandates.check_access.assert_awaited_once_with()
    runner.search.check_access.assert_awaited_once_with()
    runner.extractor.check_access.assert_awaited_once_with()
    assert responses.await_count == 2


@pytest.mark.asyncio
async def test_preflight_rejects_wrong_embedding_dimensions() -> None:
    runner = object.__new__(DirectiveIngestionRunner)
    runner.config = SimpleNamespace(
        embedding_deployment="text-embedding-3-large",
        embedding_dimensions=3,
    )
    check = AsyncMock()
    runner.blobs = SimpleNamespace(check_access=check)
    runner.catalog = SimpleNamespace(check_access=AsyncMock())
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
async def test_verify_cross_checks_all_published_surfaces() -> None:
    runner = object.__new__(DirectiveIngestionRunner)
    runner.config = SimpleNamespace(
        source_directory=_SETUP_DIR / "directives" / "pdf",
        mandate_csv=_SETUP_DIR / "directives" / "mandatory" / "mand.csv",
        azure_tenant_id="a7b1484c-f66a-496a-b1cf-35631a50396c",
    )
    directive_ids = {"30336958", "36269153", "72403881", "95315332"}
    manifests = [
        SimpleNamespace(
            source_blob_name=f"directives/{index}/source.pdf",
            canonical_blob_name=f"directives/{index}/canonical.md",
            summary_blob_name=f"directives/{index}/summary.json",
            manifest_blob_name=f"directives/{index}/manifest.json",
            sections=[
                SimpleNamespace(
                    blob_name=f"directives/{index}/section.md",
                    chunk_ids=[f"chunk-{index}"],
                )
            ],
        )
        for index in range(7)
    ]
    artifact_names = {
        name
        for manifest in manifests
        for name in (
            manifest.source_blob_name,
            manifest.canonical_blob_name,
            manifest.summary_blob_name,
            manifest.manifest_blob_name,
            manifest.sections[0].blob_name,
        )
    }
    runner.catalog = SimpleNamespace(
        list_published_directive_ids=AsyncMock(return_value=directive_ids),
        list_published_manifests=AsyncMock(return_value=manifests),
        list_current_pointers=AsyncMock(
            return_value={directive_id: ("v", "s", "p") for directive_id in directive_ids}
        ),
        list_published_relations=AsyncMock(
            return_value=[
                (SimpleNamespace(relation_id="relation-1"), "source", "process")
            ]
        ),
    )
    runner.blobs = SimpleNamespace(
        list_names=AsyncMock(return_value=artifact_names)
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
                "knowledge_source": "directive-chunks-ks-v1",
                "knowledge_base": "directive-kb-v1",
                "planner_deployment": "gpt-5-nano-directive-kb",
                "planner_model": "gpt-5-nano",
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

    assert result["source_versions"] == 7
    assert result["directive_ids"] == 4
    assert result["current_versions"] == 4
    assert result["accepted_relations"] == 1
    assert result["required_artifacts"] == 35
    assert result["published_chunks"] == 7
    assert result["mandate_assignment_count"] == 5
