from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from azure.cosmos import exceptions

from directive_ingestion.document_intelligence import DocumentIntelligenceExtractor
from directive_ingestion.mandate_projection import MandateRepository
from directive_ingestion.reconcile import DirectiveIngestionRunner


class _Credential:
    async def get_token(self, scope: str) -> SimpleNamespace:
        assert scope
        return SimpleNamespace(token="test-token")


@pytest.mark.asyncio
async def test_document_intelligence_uses_acquired_bearer_token() -> None:
    class RecordingExtractor(DocumentIntelligenceExtractor):
        async def _request_with_retry(self, method, url, **kwargs):
            assert method == "POST"
            authorization = kwargs["headers"]["Authorization"]
            assert authorization.startswith("Bearer ")
            assert authorization.endswith("test-token")
            kwargs["headers"]["Authorization"] = "Bearer " + "test-token"
            assert kwargs["headers"]["Authorization"] == "Bearer test-token"
            kwargs["headers"]["Authorization"] = "******"
            assert kwargs["headers"]["Authorization"] == (
                "Bearer test-token"
            )
            return httpx.Response(
                200,
                json={
                    "analyzeResult": {
                        "content": "Test",
                        "pages": [
                            {
                                "pageNumber": 1,
                                "width": 10,
                                "height": 10,
                                "spans": [{"offset": 0, "length": 4}],
                                "lines": [],
                            }
                        ],
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
async def test_transient_catalog_failure_leaves_prior_search_generation_untouched() -> None:
    item = SimpleNamespace(
        bundle=SimpleNamespace(
            directive_id="d-1",
            directive_version_id="d-1:v2",
            artifact_generation_id="candidate",
        ),
        source=SimpleNamespace(),
        canonical=SimpleNamespace(metadata=SimpleNamespace(processing_hash="a" * 64)),
        search_chunks=[object()],
    )
    runner = object.__new__(DirectiveIngestionRunner)
    runner.catalog = SimpleNamespace(
        snapshot_version=AsyncMock(return_value=None),
        get_published_version=AsyncMock(
            side_effect=exceptions.CosmosHttpResponseError(
                status_code=503, message="temporarily unavailable"
            )
        ),
    )
    runner.search = SimpleNamespace(
        stage_chunks=AsyncMock(),
        publish_chunks=AsyncMock(),
        retire_chunks=AsyncMock(),
        delete_chunks=AsyncMock(),
        restore_current_generation=AsyncMock(),
    )

    with pytest.raises(exceptions.CosmosHttpResponseError):
        await runner._publish_transaction([item])

    runner.search.stage_chunks.assert_not_awaited()
    runner.search.publish_chunks.assert_not_awaited()
    runner.search.retire_chunks.assert_not_awaited()
    runner.search.delete_chunks.assert_not_awaited()
    runner.search.restore_current_generation.assert_not_awaited()


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


@pytest.mark.asyncio
async def test_pre_marker_reconcile_failure_restores_candidate_publication() -> None:
    runner = object.__new__(DirectiveIngestionRunner)
    runner.reconcile_exact_corpus = AsyncMock(
        side_effect=RuntimeError("candidate verification failed")
    )
    runner.commits = SimpleNamespace(load=AsyncMock(return_value=None))
    snapshots = [SimpleNamespace()]
    runner._publication_snapshots = snapshots
    runner._rollback_publication = AsyncMock()

    with pytest.raises(RuntimeError, match="candidate verification failed"):
        await runner._reconcile_after_publication(
            [], [], "run", None, marker_before=None
        )

    runner._rollback_publication.assert_awaited_once_with(snapshots, None)


@pytest.mark.asyncio
async def test_post_marker_reconcile_failure_preserves_committed_candidate() -> None:
    marker = SimpleNamespace()
    runner = object.__new__(DirectiveIngestionRunner)
    runner.reconcile_exact_corpus = AsyncMock(
        side_effect=RuntimeError("cleanup failed")
    )
    runner.commits = SimpleNamespace(load=AsyncMock(return_value=marker))
    runner._publication_snapshots = [SimpleNamespace()]
    runner._rollback_publication = AsyncMock()

    with pytest.raises(RuntimeError, match="cleanup failed"):
        await runner._reconcile_after_publication(
            [], [], "run", None, marker_before=None
        )

    runner._rollback_publication.assert_not_awaited()


def test_pending_marker_rejects_a_different_source_corpus() -> None:
    source = SimpleNamespace(source_name="new.pdf", source_hash="a" * 64)
    metadata = SimpleNamespace(source=source)
    runner = object.__new__(DirectiveIngestionRunner)
    runner.config = SimpleNamespace(processing_hash="b" * 64)
    runner.source_states = SimpleNamespace(
        blob_name=lambda value, _: f"source-state/{value.source_name}"
    )
    marker = SimpleNamespace(expected_state_names={"source-state/old.pdf"})

    with pytest.raises(RuntimeError, match="does not match the source corpus"):
        runner._validate_pending_marker_corpus([metadata], marker)


@pytest.mark.asyncio
async def test_standalone_mandate_retry_resumes_inactive_snapshot_cleanup() -> None:
    snapshot = SimpleNamespace(snapshot_id="mandates-checksum")
    repository = object.__new__(MandateRepository)
    repository.stage = AsyncMock(return_value=(snapshot, {}, False))
    repository.cleanup = AsyncMock(return_value=True)
    repository.activate = AsyncMock()

    result, changed = await repository.publish(object(), "run")

    assert result is snapshot
    assert changed is True
    repository.activate.assert_not_awaited()
    repository.cleanup.assert_awaited_once_with(snapshot.snapshot_id)


@pytest.mark.asyncio
async def test_wrong_same_checksum_mandates_are_not_treated_as_current() -> None:
    parsed = SimpleNamespace(checksum="c" * 64)
    repository = object.__new__(MandateRepository)
    repository._read_active = AsyncMock(
        return_value={
            "complete": True,
            "checksum": parsed.checksum,
            "snapshot_id": "mandates-corrupt",
        }
    )
    repository._snapshot_assignments_match = AsyncMock(return_value=False)
    repository._has_inactive = AsyncMock(return_value=False)

    assert await repository.is_current(parsed) is False
    repository._has_inactive.assert_not_awaited()


@pytest.mark.asyncio
async def test_candidate_bundle_is_validated_before_cleanup_marker() -> None:
    source = SimpleNamespace(source_name="directive.pdf", source_hash="a" * 64)
    metadata = SimpleNamespace(
        directive_id="d-1",
        directive_version_id="d-1:v1",
    )
    source_metadata = SimpleNamespace(source=source, metadata=metadata)
    bundle = SimpleNamespace(
        directive_id="d-1",
        directive_version_id="d-1:v1",
    )
    runner = object.__new__(DirectiveIngestionRunner)
    runner.config = SimpleNamespace(processing_hash="b" * 64)
    runner.source_states = SimpleNamespace(load=AsyncMock(return_value=object()))
    runner._state_has_live_publication = AsyncMock(return_value=True)

    await runner._validate_candidate_documents([source_metadata], [bundle])

    runner._state_has_live_publication.assert_awaited_once()
