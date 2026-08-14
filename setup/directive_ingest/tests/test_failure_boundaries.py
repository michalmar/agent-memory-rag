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
