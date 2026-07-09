"""Lazily-constructed Azure SDK clients (credential, OpenAI, embeddings).

All imports are deferred so the offline mock path never requires the Azure SDKs at
import time. A single DefaultAzureCredential (optionally pinned to a user-assigned
managed identity via AZURE_CLIENT_ID) is shared across stores.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from config import get_settings


@lru_cache
def get_credential() -> Any:
    """Shared async token credential."""
    from azure.identity.aio import DefaultAzureCredential

    s = get_settings()
    if s.azure_client_id:
        return DefaultAzureCredential(managed_identity_client_id=s.azure_client_id)
    return DefaultAzureCredential()


@lru_cache
def get_openai_client() -> Any:
    """AsyncAzureOpenAI client (API key if provided, else AAD token provider)."""
    from openai import AsyncAzureOpenAI

    s = get_settings()
    if s.openai_api_key:
        return AsyncAzureOpenAI(
            azure_endpoint=s.openai_endpoint,
            api_key=s.openai_api_key,
            api_version=s.openai_api_version,
        )

    from azure.identity.aio import get_bearer_token_provider

    token_provider = get_bearer_token_provider(
        get_credential(), "https://cognitiveservices.azure.com/.default"
    )
    return AsyncAzureOpenAI(
        azure_endpoint=s.openai_endpoint,
        azure_ad_token_provider=token_provider,
        api_version=s.openai_api_version,
    )


async def embed_text(text: str) -> list[float]:
    """Embed a single string with the configured embedding deployment."""
    s = get_settings()
    client = get_openai_client()
    resp = await client.embeddings.create(model=s.embed_deployment, input=text)
    return resp.data[0].embedding
