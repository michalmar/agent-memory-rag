from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
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
