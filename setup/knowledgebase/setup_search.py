"""Idempotent async setup for Search indexes and the Foundry IQ knowledge base."""
from __future__ import annotations

import asyncio
import glob
import json
import os
from dataclasses import dataclass
from typing import Any

import httpx
from azure.identity.aio import (
    DefaultAzureCredential,
    get_bearer_token_provider,
)
from openai import AsyncAzureOpenAI

EMBED_DIM = 3072
HERE = os.path.dirname(__file__)

@dataclass(frozen=True)
class SearchSource:
    document_directory: str
    index_name: str
    knowledge_source_name: str
    keyword_field: str
    filterable_fields: tuple[str, ...]
    retrieval_instruction: str

    @property
    def source_fields(self) -> tuple[str, ...]:
        return ("id", *self.filterable_fields, "page_chunk")


@dataclass(frozen=True)
class SearchSetupConfig:
    search_endpoint: str
    openai_endpoint: str
    openai_resource_uri: str
    embed_deployment: str
    chat_deployment: str
    chat_model: str
    openai_api_version: str
    search_api_version: str
    knowledge_api_version: str
    knowledge_base: str
    azure_client_id: str
    sources: tuple[SearchSource, ...]

    @classmethod
    def from_environment(cls) -> SearchSetupConfig:
        openai_endpoint = os.environ["AZURE_OPENAI_ENDPOINT"].rstrip("/")
        chat_deployment = os.environ.get(
            "AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-4o-mini"
        )
        return cls(
            search_endpoint=os.environ["SEARCH_ENDPOINT"].rstrip("/"),
            openai_endpoint=openai_endpoint,
            openai_resource_uri=os.environ.get(
                "AZURE_OPENAI_RESOURCE_URI", openai_endpoint
            ).rstrip("/"),
            embed_deployment=os.environ.get(
                "AZURE_OPENAI_EMBED_DEPLOYMENT",
                "text-embedding-3-large",
            ),
            chat_deployment=chat_deployment,
            chat_model=os.environ.get(
                "AZURE_OPENAI_CHAT_MODEL", chat_deployment
            ),
            openai_api_version=os.environ.get(
                "AZURE_OPENAI_API_VERSION", "2024-10-21"
            ),
            search_api_version=os.environ.get(
                "SEARCH_API_VERSION", "2024-07-01"
            ),
            knowledge_api_version=os.environ.get(
                "SEARCH_KNOWLEDGE_API_VERSION", "2026-05-01-preview"
            ),
            knowledge_base=os.environ.get(
                "SEARCH_KB", "customer-support-kb"
            ),
            azure_client_id=os.environ.get("AZURE_CLIENT_ID", "").strip(),
            sources=(
                SearchSource(
                    document_directory="orders",
                    index_name=os.environ.get(
                        "SEARCH_ORDERS_INDEX", "orders"
                    ),
                    knowledge_source_name=os.environ.get(
                        "SEARCH_ORDERS_KNOWLEDGE_SOURCE", "orders-ks"
                    ),
                    keyword_field="order_id",
                    filterable_fields=("order_id", "category"),
                    retrieval_instruction=(
                        "Use the orders source for order and shipping questions."
                    ),
                ),
                SearchSource(
                    document_directory="policies",
                    index_name=os.environ.get(
                        "SEARCH_POLICY_INDEX", "return-policy"
                    ),
                    knowledge_source_name=os.environ.get(
                        "SEARCH_POLICY_KNOWLEDGE_SOURCE",
                        "return-policy-ks",
                    ),
                    keyword_field="section",
                    filterable_fields=("section",),
                    retrieval_instruction=(
                        "Use the return-policy source for returns, refunds, "
                        "and eligibility questions."
                    ),
                ),
            ),
        )


def _credential(config: SearchSetupConfig) -> DefaultAzureCredential:
    return DefaultAzureCredential(
        managed_identity_client_id=config.azure_client_id or None,
    )


def _vector_search() -> dict[str, Any]:
    return {
        "algorithms": [
            {
                "name": "hnsw-config",
                "kind": "hnsw",
                "hnswParameters": {
                    "m": 4,
                    "efConstruction": 400,
                    "efSearch": 500,
                    "metric": "cosine",
                },
            }
        ],
        "profiles": [
            {
                "name": "vector-profile",
                "algorithm": "hnsw-config",
            }
        ],
    }


def _semantic(keyword_field: str) -> dict[str, Any]:
    return {
        "configurations": [
            {
                "name": "semantic_config",
                "prioritizedFields": {
                    "prioritizedContentFields": [{"fieldName": "page_chunk"}],
                    "prioritizedKeywordsFields": [{"fieldName": keyword_field}],
                },
            }
        ]
    }


def _index_definition(source: SearchSource) -> dict[str, Any]:
    return {
        "name": source.index_name,
        "fields": [
            {
                "name": "id",
                "type": "Edm.String",
                "key": True,
            },
            *[
                {
                    "name": field_name,
                    "type": "Edm.String",
                    "filterable": True,
                }
                for field_name in source.filterable_fields
            ],
            {
                "name": "page_chunk",
                "type": "Edm.String",
                "searchable": True,
            },
            {
                "name": "page_embedding",
                "type": "Collection(Edm.Single)",
                "searchable": True,
                "dimensions": EMBED_DIM,
                "vectorSearchProfile": "vector-profile",
            },
        ],
        "vectorSearch": _vector_search(),
        "semantic": _semantic(source.keyword_field),
    }


def _load_docs_sync(subdir: str) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    pattern = os.path.join(HERE, "documents", subdir, "*.json")
    for path in sorted(glob.glob(pattern)):
        with open(path, encoding="utf-8") as source:
            documents.extend(json.load(source))
    return documents


async def _headers(credential: Any) -> dict[str, str]:
    token = await credential.get_token("https://search.azure.com/.default")
    return {
        "Authorization": f"Bearer {token.token}",
        "Content-Type": "application/json",
    }


async def _request(
    client: httpx.AsyncClient,
    credential: Any,
    method: str,
    path: str,
    *,
    api_version: str,
    payload: dict[str, Any] | None = None,
    allow_not_found: bool = False,
) -> dict[str, Any]:
    response = await client.request(
        method,
        path,
        params={"api-version": api_version},
        headers=await _headers(credential),
        json=payload,
    )
    if allow_not_found and response.status_code == 404:
        return {}
    if response.is_error:
        raise RuntimeError(
            f"{method} {path} failed with HTTP {response.status_code}: "
            f"{response.text}"
        )
    return response.json() if response.content else {}


async def _embed(
    openai_client: AsyncAzureOpenAI,
    deployment: str,
    texts: list[str],
) -> list[list[float]]:
    response = await openai_client.embeddings.create(
        model=deployment,
        input=texts,
    )
    return [item.embedding for item in response.data]


async def _create_indexes(
    client: httpx.AsyncClient,
    credential: Any,
    config: SearchSetupConfig,
) -> None:
    for source in config.sources:
        index = _index_definition(source)
        existing = await _request(
            client,
            credential,
            "GET",
            f"/indexes/{index['name']}",
            api_version=config.search_api_version,
            allow_not_found=True,
        )
        if existing:
            existing_fields = {
                field["name"]: field for field in existing.get("fields", [])
            }
            index["fields"] = [
                existing_fields.get(field["name"], field)
                for field in index["fields"]
            ]
        await _request(
            client,
            credential,
            "PUT",
            f"/indexes/{index['name']}",
            api_version=config.search_api_version,
            payload=index,
        )
        print(f"[setup] index ready: {index['name']}")


async def _upload_documents(
    client: httpx.AsyncClient,
    credential: Any,
    openai_client: AsyncAzureOpenAI,
    config: SearchSetupConfig,
) -> None:
    for source in config.sources:
        documents = await asyncio.to_thread(
            _load_docs_sync, source.document_directory
        )
        if not documents:
            raise RuntimeError(
                f"No documents found for {source.document_directory}"
            )

        embeddings = await _embed(
            openai_client,
            config.embed_deployment,
            [document["page_chunk"] for document in documents],
        )
        actions = []
        for document, embedding in zip(documents, embeddings, strict=True):
            actions.append(
                {
                    **document,
                    "page_embedding": embedding,
                    "@search.action": "mergeOrUpload",
                }
            )
        result = await _request(
            client,
            credential,
            "POST",
            f"/indexes/{source.index_name}/docs/index",
            api_version=config.search_api_version,
            payload={"value": actions},
        )
        failures = [
            item for item in result.get("value", []) if not item.get("status")
        ]
        if failures:
            keys = ", ".join(str(item.get("key")) for item in failures)
            raise RuntimeError(
                f"Document upload failed for {source.index_name}: {keys}"
            )
        print(
            f"[setup] uploaded {len(documents)} docs -> {source.index_name}"
        )


async def _create_knowledge_sources(
    client: httpx.AsyncClient,
    credential: Any,
    config: SearchSetupConfig,
) -> None:
    for source in config.sources:
        payload = {
            "name": source.knowledge_source_name,
            "kind": "searchIndex",
            "description": (
                f"Foundry IQ source for the {source.index_name} index."
            ),
            "searchIndexParameters": {
                "searchIndexName": source.index_name,
                "semanticConfigurationName": "semantic_config",
                "sourceDataFields": [
                    {"name": name} for name in source.source_fields
                ],
                "searchFields": [{"name": "page_chunk"}],
            },
        }
        await _request(
            client,
            credential,
            "PUT",
            f"/knowledgesources/{source.knowledge_source_name}",
            api_version=config.knowledge_api_version,
            payload=payload,
        )
        print(
            "[setup] knowledge source ready: "
            f"{source.knowledge_source_name}"
        )


async def _create_knowledge_base(
    client: httpx.AsyncClient,
    credential: Any,
    config: SearchSetupConfig,
) -> None:
    payload = {
        "name": config.knowledge_base,
        "description": "Customer-support orders and return-policy knowledge.",
        "retrievalInstructions": " ".join(
            source.retrieval_instruction for source in config.sources
        ),
        "knowledgeSources": [
            {"name": source.knowledge_source_name}
            for source in config.sources
        ],
        "models": [
            {
                "kind": "azureOpenAI",
                "azureOpenAIParameters": {
                    "resourceUri": config.openai_resource_uri,
                    "deploymentId": config.chat_deployment,
                    "modelName": config.chat_model,
                },
            }
        ],
        "retrievalReasoningEffort": {"kind": "low"},
    }
    await _request(
        client,
        credential,
        "PUT",
        f"/knowledgebases/{config.knowledge_base}",
        api_version=config.knowledge_api_version,
        payload=payload,
    )
    print(f"[setup] knowledge base ready: {config.knowledge_base}")


async def main() -> None:
    config = SearchSetupConfig.from_environment()
    credential = _credential(config)
    token_provider = get_bearer_token_provider(
        credential, "https://cognitiveservices.azure.com/.default"
    )
    openai_client = AsyncAzureOpenAI(
        azure_endpoint=config.openai_endpoint,
        azure_ad_token_provider=token_provider,
        api_version=config.openai_api_version,
    )

    try:
        async with httpx.AsyncClient(
            base_url=config.search_endpoint,
            timeout=httpx.Timeout(120),
        ) as client:
            await _create_indexes(client, credential, config)
            await _upload_documents(
                client,
                credential,
                openai_client,
                config,
            )
            await _create_knowledge_sources(client, credential, config)
            await _create_knowledge_base(client, credential, config)
    finally:
        await openai_client.close()
        await credential.close()
    print("[setup] done")


if __name__ == "__main__":
    asyncio.run(main())
