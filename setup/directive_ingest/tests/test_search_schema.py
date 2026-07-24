from __future__ import annotations

from types import SimpleNamespace

from directive_ingestion.search_repository import DirectiveSearchRepository


def _repository() -> DirectiveSearchRepository:
    repository = object.__new__(DirectiveSearchRepository)
    repository._config = SimpleNamespace(
        search_index="directive-chunks-v1",
        openai_resource_uri="https://example.openai.azure.com",
        embedding_deployment="text-embedding-3-large",
        embedding_model="text-embedding-3-large",
        embedding_dimensions=3072,
        search_knowledge_source="directive-chunks-ks-v1",
        search_knowledge_base="directive-kb-v1",
        summary_deployment="gpt-5.6-sol",
        summary_model="gpt-5.6-sol",
        knowledge_model_deployment="gpt-5-nano-directive-kb",
        knowledge_model_name="gpt-5-nano",
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


def test_ga_knowledge_base_uses_supported_configuration() -> None:
    definition = _repository()._knowledge_base_definition()

    assert "retrievalInstructions" not in definition
    model = definition["models"][0]["azureOpenAIParameters"]
    assert model["deploymentId"] == "gpt-5-nano-directive-kb"
    assert model["modelName"] == "gpt-5-nano"
