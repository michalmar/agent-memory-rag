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

SEARCH_ENDPOINT = os.environ["SEARCH_ENDPOINT"].rstrip("/")
OPENAI_ENDPOINT = os.environ["AZURE_OPENAI_ENDPOINT"].rstrip("/")
OPENAI_RESOURCE_URI = os.environ.get(
    "AZURE_OPENAI_RESOURCE_URI", OPENAI_ENDPOINT
).rstrip("/")
EMBED_DEPLOYMENT = os.environ.get(
    "AZURE_OPENAI_EMBED_DEPLOYMENT", "text-embedding-3-large"
)
CHAT_DEPLOYMENT = os.environ.get("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-4o-mini")
CHAT_MODEL = os.environ.get("AZURE_OPENAI_CHAT_MODEL", CHAT_DEPLOYMENT)
OPENAI_API_VERSION = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21")
SEARCH_API_VERSION = os.environ.get("SEARCH_API_VERSION", "2024-07-01")
KNOWLEDGE_API_VERSION = os.environ.get(
    "SEARCH_KNOWLEDGE_API_VERSION", "2026-05-01-preview"
)
ORDERS_INDEX = os.environ.get("SEARCH_ORDERS_INDEX", "orders")
POLICY_INDEX = os.environ.get("SEARCH_POLICY_INDEX", "return-policy")
ORDERS_SOURCE = os.environ.get("SEARCH_ORDERS_KNOWLEDGE_SOURCE", "orders-ks")
POLICY_SOURCE = os.environ.get(
    "SEARCH_POLICY_KNOWLEDGE_SOURCE", "return-policy-ks"
)
KNOWLEDGE_BASE = os.environ.get("SEARCH_KB", "customer-support-kb")


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


SEARCH_SOURCES = (
    SearchSource(
        document_directory="orders",
        index_name=ORDERS_INDEX,
        knowledge_source_name=ORDERS_SOURCE,
        keyword_field="order_id",
        filterable_fields=("order_id", "category"),
        retrieval_instruction=(
            "Use the orders source for order and shipping questions."
        ),
    ),
    SearchSource(
        document_directory="policies",
        index_name=POLICY_INDEX,
        knowledge_source_name=POLICY_SOURCE,
        keyword_field="section",
        filterable_fields=("section",),
        retrieval_instruction=(
            "Use the return-policy source for returns, refunds, and "
            "eligibility questions."
        ),
    ),
)


def _credential() -> DefaultAzureCredential:
    client_id = os.environ.get("AZURE_CLIENT_ID", "").strip()
    return DefaultAzureCredential(
        managed_identity_client_id=client_id or None,
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
        f"{SEARCH_ENDPOINT}{path}",
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
    openai_client: AsyncAzureOpenAI, texts: list[str]
) -> list[list[float]]:
    response = await openai_client.embeddings.create(
        model=EMBED_DEPLOYMENT, input=texts
    )
    return [item.embedding for item in response.data]


async def _create_indexes(
    client: httpx.AsyncClient, credential: Any
) -> None:
    for source in SEARCH_SOURCES:
        index = _index_definition(source)
        existing = await _request(
            client,
            credential,
            "GET",
            f"/indexes/{index['name']}",
            api_version=SEARCH_API_VERSION,
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
            api_version=SEARCH_API_VERSION,
            payload=index,
        )
        print(f"[setup] index ready: {index['name']}")


async def _upload_documents(
    client: httpx.AsyncClient,
    credential: Any,
    openai_client: AsyncAzureOpenAI,
) -> None:
    for source in SEARCH_SOURCES:
        documents = await asyncio.to_thread(
            _load_docs_sync, source.document_directory
        )
        if not documents:
            raise RuntimeError(
                f"No documents found for {source.document_directory}"
            )

        embeddings = await _embed(
            openai_client, [document["page_chunk"] for document in documents]
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
            api_version=SEARCH_API_VERSION,
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
    client: httpx.AsyncClient, credential: Any
) -> None:
    for source in SEARCH_SOURCES:
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
            api_version=KNOWLEDGE_API_VERSION,
            payload=payload,
        )
        print(
            "[setup] knowledge source ready: "
            f"{source.knowledge_source_name}"
        )


async def _create_knowledge_base(
    client: httpx.AsyncClient, credential: Any
) -> None:
    payload = {
        "name": KNOWLEDGE_BASE,
        "description": "Customer-support orders and return-policy knowledge.",
        "retrievalInstructions": " ".join(
            source.retrieval_instruction for source in SEARCH_SOURCES
        ),
        "knowledgeSources": [
            {"name": source.knowledge_source_name}
            for source in SEARCH_SOURCES
        ],
        "models": [
            {
                "kind": "azureOpenAI",
                "azureOpenAIParameters": {
                    "resourceUri": OPENAI_RESOURCE_URI,
                    "deploymentId": CHAT_DEPLOYMENT,
                    "modelName": CHAT_MODEL,
                },
            }
        ],
        "retrievalReasoningEffort": {"kind": "low"},
    }
    await _request(
        client,
        credential,
        "PUT",
        f"/knowledgebases/{KNOWLEDGE_BASE}",
        api_version=KNOWLEDGE_API_VERSION,
        payload=payload,
    )
    print(f"[setup] knowledge base ready: {KNOWLEDGE_BASE}")


async def main() -> None:
    credential = _credential()
    token_provider = get_bearer_token_provider(
        credential, "https://cognitiveservices.azure.com/.default"
    )
    openai_client = AsyncAzureOpenAI(
        azure_endpoint=OPENAI_ENDPOINT,
        azure_ad_token_provider=token_provider,
        api_version=OPENAI_API_VERSION,
    )

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120)) as client:
            await _create_indexes(client, credential)
            await _upload_documents(client, credential, openai_client)
            await _create_knowledge_sources(client, credential)
            await _create_knowledge_base(client, credential)
    finally:
        await openai_client.close()
        await credential.close()
    print("[setup] done")


if __name__ == "__main__":
    asyncio.run(main())
