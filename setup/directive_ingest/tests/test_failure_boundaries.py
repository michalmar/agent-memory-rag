from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from azure.cosmos import exceptions

from directive_ingestion.reconcile import DirectiveIngestionRunner


@pytest.mark.asyncio
async def test_catalog_publication_failure_retires_staged_search_chunks() -> None:
    item = SimpleNamespace(
        bundle=object(),
        canonical=SimpleNamespace(
            relations=(), metadata=SimpleNamespace(is_current=True)
        ),
        content_items=(),
        search_chunks=[object()],
        findings=(),
    )
    runner = object.__new__(DirectiveIngestionRunner)
    runner._publish_artifacts = AsyncMock()
    runner.content = SimpleNamespace(
        create_or_compare=AsyncMock(), validate_bundle=AsyncMock()
    )
    runner.search = SimpleNamespace(
        stage_chunks=AsyncMock(),
        publish_chunks=AsyncMock(),
        validate_published=AsyncMock(),
        retire_chunks=AsyncMock(),
    )
    runner.catalog = SimpleNamespace(
        stage_version=AsyncMock(),
        publish_version=AsyncMock(
            side_effect=exceptions.CosmosHttpResponseError(
                status_code=500, message="failed"
            )
        ),
        validate_published=AsyncMock(),
    )

    with pytest.raises(exceptions.CosmosHttpResponseError):
        await runner._publish_documents([item], [], "run")

    runner.search.retire_chunks.assert_awaited_once_with(item.search_chunks)


@pytest.mark.asyncio
async def test_activation_failure_retires_every_new_generation() -> None:
    first = SimpleNamespace(
        bundle=SimpleNamespace(run_id="run"),
        canonical=SimpleNamespace(relations=(), metadata=SimpleNamespace()),
        search_chunks=["first"],
    )
    second = SimpleNamespace(
        bundle=SimpleNamespace(run_id="run"),
        canonical=SimpleNamespace(relations=(), metadata=SimpleNamespace()),
        search_chunks=["second"],
    )
    runner = object.__new__(DirectiveIngestionRunner)
    runner.search = SimpleNamespace(
        publish_chunks=AsyncMock(),
        validate_published=AsyncMock(),
        reconcile_current=AsyncMock(side_effect=RuntimeError("activation failed")),
        reconcile_generation=AsyncMock(),
        retire_chunks=AsyncMock(),
    )
    runner.catalog = SimpleNamespace(
        publish_version=AsyncMock(),
        validate_published=AsyncMock(),
        activate_current=AsyncMock(),
    )

    with pytest.raises(RuntimeError, match="activation failed"):
        await runner.publish_documents([first, second])

    assert runner.search.retire_chunks.await_args_list[0].args == (["first"],)
    assert runner.search.retire_chunks.await_args_list[1].args == (["second"],)
