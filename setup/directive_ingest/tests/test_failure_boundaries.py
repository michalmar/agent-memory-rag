from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from azure.cosmos import exceptions

from directive_ingestion.reconcile import DirectiveIngestionRunner


@pytest.mark.asyncio
async def test_catalog_publication_failure_retires_staged_search_chunks() -> None:
    item = SimpleNamespace(
        bundle=SimpleNamespace(
            directive_id="d-1",
            directive_version_id="d-1:v1",
            artifacts=SimpleNamespace(
                canonical_blob_name="directives/key/v1/generation/document.md",
                source_blob_name="directives/key/v1/source.pdf",
            ),
        ),
        canonical=SimpleNamespace(
            relations=(),
            metadata=SimpleNamespace(
                is_current=True, processing_hash="a" * 64
            ),
        ),
        source=SimpleNamespace(),
        content_items=(),
        search_chunks=[object()],
        findings=(),
    )
    runner = object.__new__(DirectiveIngestionRunner)
    runner._publish_artifacts = AsyncMock()
    runner.content = SimpleNamespace(
        create_or_compare=AsyncMock(),
        validate_bundle=AsyncMock(),
        delete_bundle=AsyncMock(),
    )
    runner.search = SimpleNamespace(
        stage_chunks=AsyncMock(),
        publish_chunks=AsyncMock(),
        validate_published_chunk_ids=AsyncMock(),
        retire_chunks=AsyncMock(),
        delete_chunks=AsyncMock(),
        restore_current_generation=AsyncMock(),
    )
    runner.catalog = SimpleNamespace(
        stage_version=AsyncMock(),
        publish_version=AsyncMock(
            side_effect=exceptions.CosmosHttpResponseError(
                status_code=500, message="failed"
            )
        ),
        validate_published=AsyncMock(),
        get_published_version=AsyncMock(return_value=None),
        get_current=AsyncMock(return_value=None),
        restore_version=AsyncMock(),
        restore_current=AsyncMock(),
    )
    runner.blobs = SimpleNamespace(delete_names=AsyncMock())
    runner.source_states = SimpleNamespace(
        record=AsyncMock(), delete=AsyncMock()
    )

    with pytest.raises(exceptions.CosmosHttpResponseError):
        await runner._publish_documents([item], [], "run")

    runner.search.delete_chunks.assert_awaited_once_with(item.search_chunks)
    runner.catalog.restore_version.assert_awaited_once_with(item.bundle, None)
    runner.catalog.restore_current.assert_awaited_once_with("d-1", None)
    runner.blobs.delete_names.assert_awaited_once_with(
        {
            "directives/key/v1/generation/document.md",
            "directives/key/v1/source.pdf",
        }
    )


@pytest.mark.asyncio
@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["stage", "publish", "state", "activate"])
async def test_transaction_failure_restores_prior_catalog_and_current(
    phase: str,
) -> None:
    item = SimpleNamespace(
        bundle=SimpleNamespace(
            directive_id="d-1",
            directive_version_id="d-1:v1",
            artifact_generation_id="new",
            artifacts=SimpleNamespace(
                canonical_blob_name="new.md", source_blob_name="new.pdf"
            ),
        ),
        search_chunks=[],
        source=SimpleNamespace(),
        canonical=SimpleNamespace(
            metadata=SimpleNamespace(processing_hash="a" * 64)
        ),
    )
    previous_bundle = SimpleNamespace(
        artifact_generation_id="old",
        artifacts=SimpleNamespace(source_blob_name="old.pdf"),
    )
    previous_current = {"id": "current", "directive_id": "d-1"}
    runner = object.__new__(DirectiveIngestionRunner)
    runner.catalog = SimpleNamespace(
        get_published_version=AsyncMock(return_value=previous_bundle),
        get_current=AsyncMock(return_value=previous_current),
        restore_version=AsyncMock(),
        restore_current=AsyncMock(),
    )
    runner.search = SimpleNamespace(
        delete_chunks=AsyncMock(), restore_current_generation=AsyncMock()
    )
    runner.content = SimpleNamespace(delete_bundle=AsyncMock())
    runner.blobs = SimpleNamespace(delete_names=AsyncMock())
    runner.source_states = SimpleNamespace(delete=AsyncMock())
    runner.stage_documents = AsyncMock()
    runner.publish_documents = AsyncMock()
    runner.record_source_states = AsyncMock()
    runner.activate_documents = AsyncMock()
    {
        "stage": runner.stage_documents,
        "publish": runner.publish_documents,
        "state": runner.record_source_states,
        "activate": runner.activate_documents,
    }[phase].side_effect = RuntimeError(f"{phase} failed")

    with pytest.raises(RuntimeError, match=f"{phase} failed"):
        await runner._publish_transaction([item])

    runner.catalog.restore_current.assert_awaited_once_with(
        "d-1", previous_current
    )
    runner.catalog.restore_version.assert_awaited_once_with(
        item.bundle, previous_bundle
    )
    runner.search.delete_chunks.assert_awaited_once_with([])


@pytest.mark.asyncio
async def test_activation_rollback_restores_prior_current_version_search() -> None:
    item = SimpleNamespace(
        bundle=SimpleNamespace(
            directive_id="d-1",
            directive_version_id="d-1:v2",
            artifact_generation_id="new",
            artifacts=SimpleNamespace(
                canonical_blob_name="new.md", source_blob_name="new.pdf"
            ),
        ),
        search_chunks=[],
        source=SimpleNamespace(),
        canonical=SimpleNamespace(
            metadata=SimpleNamespace(processing_hash="a" * 64)
        ),
    )
    old_current_bundle = SimpleNamespace(
        artifact_generation_id="old",
        artifacts=SimpleNamespace(source_blob_name="old.pdf"),
    )
    runner = object.__new__(DirectiveIngestionRunner)
    runner.catalog = SimpleNamespace(
        get_published_version=AsyncMock(
            side_effect=[None, old_current_bundle]
        ),
        get_current=AsyncMock(
            return_value={"directive_version_id": "d-1:v1"}
        ),
        restore_version=AsyncMock(),
        restore_current=AsyncMock(),
    )
    runner.search = SimpleNamespace(
        delete_chunks=AsyncMock(), restore_current_generation=AsyncMock()
    )
    runner.content = SimpleNamespace(delete_bundle=AsyncMock())
    runner.blobs = SimpleNamespace(delete_names=AsyncMock())
    runner.source_states = SimpleNamespace(delete=AsyncMock())
    runner.stage_documents = AsyncMock()
    runner.publish_documents = AsyncMock()
    runner.record_source_states = AsyncMock()
    runner.activate_documents = AsyncMock(
        side_effect=RuntimeError("activation failed")
    )

    with pytest.raises(RuntimeError, match="activation failed"):
        await runner._publish_transaction([item])

    runner.search.restore_current_generation.assert_awaited_once_with(
        old_current_bundle
    )
