"""Lazily constructed async Azure clients with deterministic production identity."""
from __future__ import annotations

from typing import Any

from .config import get_settings

_credential: Any = None
_openai_client: Any = None


def get_credential() -> Any:
    """Shared async token credential."""
    global _credential
    if _credential is not None:
        return _credential

    s = get_settings()
    if s.azure_client_id:
        from azure.identity.aio import ManagedIdentityCredential

        _credential = ManagedIdentityCredential(client_id=s.azure_client_id)
    else:
        from azure.identity.aio import DefaultAzureCredential

        _credential = DefaultAzureCredential()
    return _credential


def get_openai_client() -> Any:
    """AsyncAzureOpenAI client (API key if provided, else AAD token provider)."""
    global _openai_client
    if _openai_client is not None:
        return _openai_client

    from openai import AsyncAzureOpenAI

    s = get_settings()
    if s.openai_api_key:
        _openai_client = AsyncAzureOpenAI(
            azure_endpoint=s.openai_endpoint,
            api_key=s.openai_api_key,
            api_version=s.openai_api_version,
        )
        return _openai_client

    from azure.identity.aio import get_bearer_token_provider

    token_provider = get_bearer_token_provider(
        get_credential(), "https://cognitiveservices.azure.com/.default"
    )
    _openai_client = AsyncAzureOpenAI(
        azure_endpoint=s.openai_endpoint,
        azure_ad_token_provider=token_provider,
        api_version=s.openai_api_version,
    )
    return _openai_client


async def embed_text(text: str) -> list[float]:
    """Embed a single string with the configured embedding deployment."""
    s = get_settings()
    client = get_openai_client()
    resp = await client.embeddings.create(model=s.embed_deployment, input=text)
    return resp.data[0].embedding


async def close_azure_clients() -> None:
    """Close shared async clients and their credential."""
    global _credential, _openai_client
    if _openai_client is not None:
        await _openai_client.close()
        _openai_client = None
    if _credential is not None:
        await _credential.close()
        _credential = None
