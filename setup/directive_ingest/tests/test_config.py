from __future__ import annotations

from directive_ingestion.config import IngestionConfig


def test_directive_content_container_is_configurable(monkeypatch) -> None:
    required = {
        "AZURE_CLIENT_ID": "client-id",
        "AZURE_TENANT_ID": "tenant-id",
        "DOCUMENT_INTELLIGENCE_ENDPOINT": "https://document.example",
        "DIRECTIVE_BLOB_ACCOUNT_URL": "https://storage.example",
        "COSMOS_ENDPOINT": "https://cosmos.example",
        "AZURE_SEARCH_ENDPOINT": "https://search.example",
        "AZURE_OPENAI_ENDPOINT": "https://openai.example",
        "DIRECTIVE_CONTENT_CONTAINER": "directive-sections",
    }
    for name, value in required.items():
        monkeypatch.setenv(name, value)

    config = IngestionConfig.from_environment()

    assert config.content_container == "directive-sections"
    assert config.search_api_version == "2026-04-01"
    assert config.source_kind == "local"
    assert config.source_container == "directive-source"


def test_blob_source_configuration_is_loaded(monkeypatch) -> None:
    required = {
        "AZURE_CLIENT_ID": "client-id",
        "AZURE_TENANT_ID": "tenant-id",
        "DOCUMENT_INTELLIGENCE_ENDPOINT": "https://document.example",
        "DIRECTIVE_BLOB_ACCOUNT_URL": "https://storage.example",
        "COSMOS_ENDPOINT": "https://cosmos.example",
        "AZURE_SEARCH_ENDPOINT": "https://search.example",
        "AZURE_OPENAI_ENDPOINT": "https://openai.example",
        "DIRECTIVE_SOURCE_KIND": "azure_blob",
        "DIRECTIVE_SOURCE_CONTAINER": "source-pdfs",
        "DIRECTIVE_SOURCE_PREFIX": "production",
    }
    for name, value in required.items():
        monkeypatch.setenv(name, value)

    config = IngestionConfig.from_environment()

    assert config.source_kind == "azure_blob"
    assert config.source_container == "source-pdfs"
    assert config.source_prefix == "production"
    assert config.source_max_corpus_bytes == 512 * 1024 * 1024


def test_source_location_does_not_change_processing_hash(monkeypatch) -> None:
    required = {
        "AZURE_CLIENT_ID": "client-id",
        "AZURE_TENANT_ID": "tenant-id",
        "DOCUMENT_INTELLIGENCE_ENDPOINT": "https://document.example",
        "DIRECTIVE_BLOB_ACCOUNT_URL": "https://storage.example",
        "COSMOS_ENDPOINT": "https://cosmos.example",
        "AZURE_SEARCH_ENDPOINT": "https://search.example",
        "AZURE_OPENAI_ENDPOINT": "https://openai.example",
    }
    for name, value in required.items():
        monkeypatch.setenv(name, value)
    local = IngestionConfig.from_environment()
    monkeypatch.setenv("DIRECTIVE_SOURCE_KIND", "azure_blob")
    monkeypatch.setenv("DIRECTIVE_SOURCE_PREFIX", "another/location")
    blob = IngestionConfig.from_environment()

    assert local.processing_hash == blob.processing_hash
