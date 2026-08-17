from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import httpx
import pytest
import directive_ingestion.search_repository as search_repository
from directive_contracts import DirectiveMetadata
from directive_ingestion.canonical import CanonicalDirective, ParsedSection
from directive_ingestion.chunking import TextChunk
from directive_ingestion.config import ConcurrencyConfig, RetryPolicyConfig
from directive_ingestion.integrity import IntegrityValidationError
from directive_ingestion.provider_retry import RetryBudget
from directive_ingestion.run_metrics import IngestionRunMetrics
from directive_ingestion.search_repository import DirectiveSearchRepository


def _repository() -> DirectiveSearchRepository:
    repository = object.__new__(DirectiveSearchRepository)
    repository._config = SimpleNamespace(
        search_index="directive-chunks-v2",
        openai_resource_uri="https://example.openai.azure.com",
        embedding_deployment="text-embedding-3-large",
        embedding_model="text-embedding-3-large",
        embedding_dimensions=3072,
        summary_deployment="gpt-5.6-sol",
        summary_model="gpt-5.6-sol",
    )
    return repository


def _embedding_directive() -> CanonicalDirective:
    content = "Embedding source"
    section = ParsedSection(
        section_id="section",
        ordinal=1,
        number="1",
        title="Section",
        path=("Section",),
        page_from=1,
        page_to=1,
        content=content,
        token_count=2,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
    )
    metadata = DirectiveMetadata(
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
    return CanonicalDirective(
        metadata=metadata,
        markdown=content,
        control={},
        sections=(section,),
        relations=(),
        findings=(),
        total_pages=1,
        total_tokens=2,
        metadata_candidate=SimpleNamespace(),
    )


def _embedding_chunks() -> list[TextChunk]:
    return [
        TextChunk(
            id=f"chunk-{index}",
            section_id="section",
            ordinal=index,
            content=content,
            content_kind="prose",
            page_from=1,
            page_to=1,
        )
        for index, content in enumerate(("slow", "medium", "fast"), 1)
    ]


def _embedding_repository(endpoint: object) -> DirectiveSearchRepository:
    repository = object.__new__(DirectiveSearchRepository)
    repository._config = SimpleNamespace(
        embedding_max_item_tokens=8192,
        embedding_max_items_per_request=1,
        embedding_max_aggregate_tokens=240000,
        embedding_deployment="embedding",
        embedding_dimensions=2,
        concurrency=ConcurrencyConfig(4, 2, 2, 2),
    )
    repository._openai = SimpleNamespace(embeddings=endpoint)
    repository._retry_policy = RetryPolicyConfig(1, 0, 0, 0, 0)
    repository._retry_budget = RetryBudget(0)
    repository._metrics = IngestionRunMetrics(
        run_id="run",
        operation="run-daily",
        processing_hash="b" * 64,
    )
    return repository


@pytest.mark.asyncio
async def test_embedding_concurrency_preserves_input_order() -> None:
    class Endpoint:
        def __init__(self) -> None:
            self.active = 0
            self.max_active = 0
            self.completion_order: list[str] = []

        async def create(self, **kwargs):
            content = kwargs["input"][0]
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                await asyncio.sleep(
                    {"slow": 0.03, "medium": 0.01, "fast": 0}[content]
                )
                self.completion_order.append(content)
                value = {"slow": 1.0, "medium": 2.0, "fast": 3.0}[content]
                return SimpleNamespace(
                    data=[SimpleNamespace(embedding=[value, value])]
                )
            finally:
                self.active -= 1

    endpoint = Endpoint()
    repository = _embedding_repository(endpoint)

    records = await repository.build_chunks(
        _embedding_directive(),
        _embedding_chunks(),
    )

    assert endpoint.max_active == 2
    assert endpoint.completion_order != ["slow", "medium", "fast"]
    assert [record.id for record in records] == [
        "chunk-1",
        "chunk-2",
        "chunk-3",
    ]
    assert [record.content_vector[0] for record in records] == [1.0, 2.0, 3.0]
    assert repository._metrics.counters == {
        "embedding_input_tokens": 3,
        "embedding_items": 3,
        "embedding_requests": 3,
    }


def test_all_semantic_fields_are_searchable_and_retrievable() -> None:
    definition = _repository()._index_definition()
    fields = {field["name"]: field for field in definition["fields"]}
    prioritized = definition["semantic"]["configurations"][0][
        "prioritizedFields"
    ]
    names = [
        prioritized["titleField"]["fieldName"],
        *[
            field["fieldName"]
            for field in prioritized["prioritizedContentFields"]
        ],
        *[
            field["fieldName"]
            for field in prioritized["prioritizedKeywordsFields"]
        ],
    ]

    assert all(fields[name].get("searchable") is True for name in names)
    assert all(fields[name].get("retrievable", True) is True for name in names)


def test_processing_hash_is_filterable_for_generation_cleanup() -> None:
    fields = {
        field["name"]: field for field in _repository()._index_fields()
    }

    assert fields["processing_hash"]["filterable"] is True
    assert fields["id"] == {
        "name": "id",
        "type": "Edm.String",
        "key": True,
        "filterable": True,
        "sortable": True,
        "retrievable": True,
    }
    assert fields["is_valid"] == {
        "name": "is_valid",
        "type": "Edm.Boolean",
        "filterable": True,
        "retrievable": True,
    }
    assert fields["title"]["analyzer"] == "cs.microsoft"
    assert fields["content"]["analyzer"] == "cs.microsoft"


def test_index_supports_direct_hybrid_semantic_queries() -> None:
    repository = _repository()
    definition = repository._index_definition()

    repository._validate_existing_index(definition)
    vector_search = definition["vectorSearch"]
    profile = vector_search["profiles"][0]
    vectorizer = vector_search["vectorizers"][0]
    vector = next(
        field for field in definition["fields"] if field["name"] == "content_vector"
    )

    assert vector["vectorSearchProfile"] == "directive-vector-profile"
    assert profile["vectorizer"] == "directive-openai-vectorizer"
    assert vectorizer["azureOpenAIParameters"]["deploymentId"] == (
        "text-embedding-3-large"
    )
    assert definition["semantic"]["configurations"][0]["name"] == (
        "semantic_config"
    )


@pytest.mark.asyncio
async def test_verification_exercises_direct_hybrid_query() -> None:
    repository = _repository()
    repository._config.search_api_version = "2026-04-01"
    repository._request = AsyncMock(
        side_effect=[
            repository._index_definition(),
            {
                "@odata.count": 7,
                "@search.facets": {
                    "directive_id": [{"value": "Č/12", "count": 7}],
                    "directive_version_id": [
                        {"value": "Č/12:v1", "count": 7}
                    ],
                },
            },
            {
                "@odata.count": 4,
                "@search.facets": {
                    "directive_id": [{"value": "Č/12", "count": 4}],
                    "directive_version_id": [
                        {"value": "Č/12:v1", "count": 4}
                    ],
                },
            },
            {"value": [{"id": "chunk-1"}]},
        ]
    )

    summary = await repository.verification_summary()

    direct_call = repository._request.await_args_list[-1]
    assert direct_call.args == (
        "POST",
        "/indexes/directive-chunks-v2/docs/search",
    )
    assert direct_call.kwargs["api_version"] == "2026-04-01"
    assert direct_call.kwargs["payload"] == {
        "search": "directive verification",
        "filter": (
            "publication_state eq 'published' and is_current eq true "
            "and is_valid eq true"
        ),
        "vectorFilterMode": "preFilter",
        "vectorQueries": [
            {
                "kind": "text",
                "text": "directive verification",
                "fields": "content_vector",
                "k": 50,
            }
        ],
        "queryType": "semantic",
        "semanticConfiguration": "semantic_config",
        "select": "id",
        "top": 1,
    }
    assert summary["direct_hybrid_query"] == "ok"


@pytest.mark.asyncio
async def test_published_chunk_visibility_waits_for_delayed_exact_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository()
    directive = SimpleNamespace(
        metadata=SimpleNamespace(
            directive_version_id="Č/12:v1",
            source_hash="a" * 64,
            processing_hash="b" * 64,
        )
    )
    repository._find_keys = AsyncMock(
        side_effect=[[], ["chunk-1", "chunk-2"], ["chunk-1", "chunk-2"]]
    )
    sleep = AsyncMock()
    monkeypatch.setattr(search_repository.asyncio, "sleep", sleep)

    await repository.validate_published_chunk_ids(
        directive, ["chunk-1", "chunk-2"]
    )

    assert repository._find_keys.await_count == 3
    assert sleep.await_args_list == [call(1.0), call(2.0)]
    filter_expression = repository._find_keys.await_args.args[0]
    assert "id eq 'chunk-1'" in filter_expression
    assert "id eq 'chunk-2'" in filter_expression


@pytest.mark.asyncio
async def test_published_chunk_visibility_allows_slow_live_convergence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository()
    directive = SimpleNamespace(
        metadata=SimpleNamespace(
            directive_version_id="MP/23/0141:v1",
            source_hash="a" * 64,
            processing_hash="b" * 64,
        )
    )
    repository._find_keys = AsyncMock(
        side_effect=[[], ["chunk-1"], ["chunk-1"]]
    )
    monotonic = iter((0.0, 120.0, 121.0))
    monkeypatch.setattr(search_repository, "monotonic", lambda: next(monotonic))
    sleep = AsyncMock()
    monkeypatch.setattr(search_repository.asyncio, "sleep", sleep)

    await repository.validate_published_chunk_ids(directive, ["chunk-1"])

    assert repository._find_keys.await_count == 3
    assert sleep.await_args_list == [call(1.0), call(2.0)]


@pytest.mark.asyncio
async def test_published_visibility_retries_after_empty_search_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository()
    repository._config.search_api_version = "2026-04-01"
    directive = SimpleNamespace(
        metadata=SimpleNamespace(
            directive_version_id="MP/23/0141:v1",
            source_hash="a" * 64,
            processing_hash="b" * 64,
        )
    )
    repository._request = AsyncMock(
        side_effect=[
            {"value": []},
            {"value": [{"id": "chunk-1"}]},
            {"value": [{"id": "chunk-1"}]},
        ]
    )
    sleep = AsyncMock()
    monkeypatch.setattr(search_repository.asyncio, "sleep", sleep)

    await repository.validate_published_chunk_ids(directive, ["chunk-1"])

    assert repository._request.await_count == 3
    assert sleep.await_args_list == [call(1.0), call(2.0)]


@pytest.mark.asyncio
async def test_current_visibility_timeout_fails_on_exact_id_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository()
    bundle = SimpleNamespace(
        directive_id="Č/12",
        directive_version_id="Č/12:v1",
        manifest=SimpleNamespace(
            sections=[SimpleNamespace(chunk_ids=["expected-chunk"])]
        ),
    )
    repository._find_keys = AsyncMock(return_value=["stale-chunk"])
    monkeypatch.setattr(search_repository, "_VISIBILITY_TIMEOUT_SECONDS", 0.5)
    monotonic = iter((0.0, 1.0))
    monkeypatch.setattr(search_repository, "monotonic", lambda: next(monotonic))

    with pytest.raises(IntegrityValidationError, match="expected exact chunk IDs"):
        await repository.validate_current_generation(bundle)

    repository._find_keys.assert_awaited_once()


@pytest.mark.asyncio
async def test_whole_corpus_visibility_waits_for_delayed_deletion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository()
    bundle = SimpleNamespace(
        manifest=SimpleNamespace(
            sections=[SimpleNamespace(chunk_ids=["expected-chunk"])]
        )
    )
    repository._find_keys = AsyncMock(
        side_effect=[
            ["expected-chunk", "stale-chunk"],
            ["expected-chunk"],
            ["expected-chunk"],
        ]
    )
    sleep = AsyncMock()
    monkeypatch.setattr(search_repository.asyncio, "sleep", sleep)

    await repository.validate_exact_published([bundle])

    assert repository._find_keys.await_count == 3
    assert repository._find_keys.await_args.args == ("",)
    assert sleep.await_args_list == [call(1.0), call(2.0)]


@pytest.mark.asyncio
async def test_deletion_waits_until_exact_ids_are_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository()
    repository._upload_actions = AsyncMock()
    repository._find_keys = AsyncMock(
        side_effect=[["stale-chunk"], [], []]
    )
    sleep = AsyncMock()
    monkeypatch.setattr(search_repository.asyncio, "sleep", sleep)

    await repository.delete_chunk_ids(["stale-chunk"])

    repository._upload_actions.assert_awaited_once_with(
        [{"id": "stale-chunk", "@search.action": "delete"}]
    )
    assert repository._find_keys.await_count == 3
    assert "id eq 'stale-chunk'" in repository._find_keys.await_args.args[0]
    assert sleep.await_args_list == [call(1.0), call(2.0)]


@pytest.mark.asyncio
async def test_current_reconciliation_waits_before_clearing_stale_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository()
    repository._upload_actions = AsyncMock()
    repository._find_keys = AsyncMock(
        side_effect=[
            [],
            ["expected-chunk", "stale-chunk"],
            ["expected-chunk", "stale-chunk"],
            ["expected-chunk"],
            ["expected-chunk"],
        ]
    )
    sleep = AsyncMock()
    monkeypatch.setattr(search_repository.asyncio, "sleep", sleep)
    bundle = SimpleNamespace(
        directive_id="MP/23/0141",
        directive_version_id="MP/23/0141:v1",
        is_current=True,
        manifest=SimpleNamespace(
            sections=[SimpleNamespace(chunk_ids=["expected-chunk"])]
        ),
    )

    await repository.reconcile_current(bundle)

    assert repository._find_keys.await_count == 5
    assert sleep.await_args_list == [
        call(1.0),
        call(2.0),
        call(1.0),
    ]
    assert repository._upload_actions.await_args_list == [
        call(
            [
                {
                    "id": "expected-chunk",
                    "@search.action": "merge",
                    "is_current": True,
                }
            ]
        ),
        call(
            [
                {
                    "id": "stale-chunk",
                    "@search.action": "merge",
                    "is_current": False,
                }
            ]
        ),
    ]


@pytest.mark.asyncio
async def test_deletion_times_out_on_persistent_stale_chunk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository()
    repository._upload_actions = AsyncMock()
    repository._find_keys = AsyncMock(return_value=["stale-chunk"])
    monkeypatch.setattr(search_repository, "_VISIBILITY_TIMEOUT_SECONDS", 0.5)
    monotonic = iter((0.0, 1.0))
    monkeypatch.setattr(search_repository, "monotonic", lambda: next(monotonic))

    with pytest.raises(IntegrityValidationError, match="deleted Search chunks"):
        await repository.delete_chunk_ids(["stale-chunk"])

    repository._find_keys.assert_awaited_once()


@pytest.mark.asyncio
async def test_exact_visibility_propagates_transport_errors() -> None:
    repository = _repository()
    directive = SimpleNamespace(
        metadata=SimpleNamespace(
            directive_version_id="Č/12:v1",
            source_hash="a" * 64,
            processing_hash="b" * 64,
        )
    )
    repository._find_keys = AsyncMock(
        side_effect=httpx.TransportError("Search unavailable")
    )

    with pytest.raises(httpx.TransportError, match="Search unavailable"):
        await repository.validate_published_chunk_ids(directive, ["chunk-1"])


@pytest.mark.asyncio
async def test_key_enumeration_seeks_all_results_beyond_one_page() -> None:
    repository = _repository()
    repository._config.search_api_version = "2026-04-01"
    all_keys = [f"chunk-{index:04d}" for index in range(1501)]
    requests: list[dict[str, object]] = []

    async def request(*_args, **kwargs):
        payload = kwargs["payload"]
        requests.append(payload)
        filter_expression = payload["filter"]
        match = re.search(r"id gt '([^']+)'", filter_expression)
        start = all_keys.index(match.group(1)) + 1 if match is not None else 0
        return {
            "value": [
                {"id": key}
                for key in all_keys[start : start + payload["top"]]
            ]
        }

    repository._request = request

    keys = await repository._find_keys("publication_state eq 'published'")

    assert keys == all_keys
    assert len(keys) == len(set(keys)) == 1501
    assert all(request["orderby"] == "id asc" for request in requests)
    assert all("skip" not in request for request in requests)
    assert requests[1]["filter"] == (
        "(publication_state eq 'published') and id gt 'chunk-0999'"
    )


@pytest.mark.asyncio
async def test_key_enumeration_rejects_nonascending_pages() -> None:
    repository = _repository()
    repository._config.search_api_version = "2026-04-01"
    repository._request = AsyncMock(
        return_value={"value": [{"id": "chunk-2"}, {"id": "chunk-1"}]}
    )

    with pytest.raises(RuntimeError, match="ascending order"):
        await repository._find_keys("")
