from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from directive_ingestion.reconcile import DirectiveIngestionRunner


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
    runner.clients = SimpleNamespace(
        openai=SimpleNamespace(
            embeddings=SimpleNamespace(
                create=AsyncMock(
                    return_value=SimpleNamespace(
                        data=[SimpleNamespace(embedding=[0.1, 0.2, 0.3])]
                    )
                )
            ),
            responses=SimpleNamespace(
                create=AsyncMock(return_value=SimpleNamespace(output_text="READY"))
            ),
        )
    )

    result = await runner.preflight()

    assert result["search"] == "ok"
    assert result["document_intelligence"] == "ok"
    runner.search.check_access.assert_awaited_once_with()
