"""One-time Azure AI Search KB setup (PRD §F5, classic-RAG variant).

Creates two indexes (orders, return-policy) with a 3072-dim vector field (HNSW +
semantic config), embeds each seed chunk app-side with text-embedding-3-large, and
uploads the documents. App-side embeddings keep AI Search fully private — no
Search->OpenAI vectorizer link is required (the classic client sends the query vector).

Run from within the VNet (ACA job / jump host) or with Search + OpenAI public access
temporarily enabled. Requires `az login` (DefaultAzureCredential) or API keys via env.

Env: SEARCH_ENDPOINT, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_EMBED_DEPLOYMENT,
     AZURE_OPENAI_API_VERSION, SEARCH_ORDERS_INDEX, SEARCH_POLICY_INDEX.
"""
from __future__ import annotations

import glob
import json
import os

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    HnswParameters,
    SearchableField,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)
from openai import AzureOpenAI

EMBED_DIM = 3072
HERE = os.path.dirname(__file__)

SEARCH_ENDPOINT = os.environ["SEARCH_ENDPOINT"].rstrip("/")
OPENAI_ENDPOINT = os.environ["AZURE_OPENAI_ENDPOINT"].rstrip("/")
EMBED_DEPLOYMENT = os.environ.get("AZURE_OPENAI_EMBED_DEPLOYMENT", "text-embedding-3-large")
API_VERSION = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21")
ORDERS_INDEX = os.environ.get("SEARCH_ORDERS_INDEX", "orders")
POLICY_INDEX = os.environ.get("SEARCH_POLICY_INDEX", "return-policy")

_cred = DefaultAzureCredential()


def _openai() -> AzureOpenAI:
    api_key = os.environ.get("AZURE_OPENAI_API_KEY")
    if api_key:
        return AzureOpenAI(
            azure_endpoint=OPENAI_ENDPOINT, api_key=api_key, api_version=API_VERSION
        )
    token_provider = get_bearer_token_provider(
        _cred, "https://cognitiveservices.azure.com/.default"
    )
    return AzureOpenAI(
        azure_endpoint=OPENAI_ENDPOINT,
        azure_ad_token_provider=token_provider,
        api_version=API_VERSION,
    )


def _search_cred():
    key = os.environ.get("SEARCH_API_KEY")
    if key:
        from azure.core.credentials import AzureKeyCredential

        return AzureKeyCredential(key)
    return _cred


def _vector_search() -> VectorSearch:
    return VectorSearch(
        algorithms=[
            HnswAlgorithmConfiguration(
                name="hnsw-config",
                parameters=HnswParameters(m=4, ef_construction=400, ef_search=500),
            )
        ],
        profiles=[VectorSearchProfile(name="vector-profile", algorithm_configuration_name="hnsw-config")],
    )


def _semantic(content_field: str, keyword_field: str) -> SemanticSearch:
    return SemanticSearch(
        configurations=[
            SemanticConfiguration(
                name="semantic_config",
                prioritized_fields=SemanticPrioritizedFields(
                    content_fields=[SemanticField(field_name=content_field)],
                    keywords_fields=[SemanticField(field_name=keyword_field)],
                ),
            )
        ]
    )


def _orders_index() -> SearchIndex:
    fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True),
        SimpleField(name="order_id", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="category", type=SearchFieldDataType.String, filterable=True),
        SearchableField(name="page_chunk", type=SearchFieldDataType.String),
        SearchField(
            name="page_embedding",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=EMBED_DIM,
            vector_search_profile_name="vector-profile",
        ),
    ]
    return SearchIndex(
        name=ORDERS_INDEX,
        fields=fields,
        vector_search=_vector_search(),
        semantic_search=_semantic("page_chunk", "order_id"),
    )


def _policy_index() -> SearchIndex:
    fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True),
        SimpleField(name="section", type=SearchFieldDataType.String, filterable=True),
        SearchableField(name="page_chunk", type=SearchFieldDataType.String),
        SearchField(
            name="page_embedding",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=EMBED_DIM,
            vector_search_profile_name="vector-profile",
        ),
    ]
    return SearchIndex(
        name=POLICY_INDEX,
        fields=fields,
        vector_search=_vector_search(),
        semantic_search=_semantic("page_chunk", "section"),
    )


def _load_docs(subdir: str) -> list[dict]:
    docs: list[dict] = []
    for path in sorted(glob.glob(os.path.join(HERE, "documents", subdir, "*.json"))):
        with open(path, "r", encoding="utf-8") as fh:
            docs.extend(json.load(fh))
    return docs


def _embed(client: AzureOpenAI, texts: list[str]) -> list[list[float]]:
    resp = client.embeddings.create(model=EMBED_DEPLOYMENT, input=texts)
    return [d.embedding for d in resp.data]


def main() -> None:
    index_client = SearchIndexClient(endpoint=SEARCH_ENDPOINT, credential=_search_cred())
    openai_client = _openai()

    for index in (_orders_index(), _policy_index()):
        index_client.create_or_update_index(index)
        print(f"[setup] index ready: {index.name}")

    for subdir, index_name in ((("orders"), ORDERS_INDEX), (("policies"), POLICY_INDEX)):
        docs = _load_docs(subdir)
        if not docs:
            print(f"[setup] no docs for {subdir}")
            continue
        embeddings = _embed(openai_client, [d["page_chunk"] for d in docs])
        for d, emb in zip(docs, embeddings):
            d["page_embedding"] = emb
        client = SearchClient(
            endpoint=SEARCH_ENDPOINT, index_name=index_name, credential=_search_cred()
        )
        client.upload_documents(documents=docs)
        print(f"[setup] uploaded {len(docs)} docs -> {index_name}")

    print("[setup] done")


if __name__ == "__main__":
    main()
