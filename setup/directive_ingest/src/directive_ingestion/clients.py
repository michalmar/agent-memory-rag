"""Deterministic managed-identity clients for the ingestion job."""

from __future__ import annotations

from typing import Any

from azure.identity.aio import ManagedIdentityCredential, get_bearer_token_provider
from openai import AsyncAzureOpenAI

from .config import IngestionConfig


class IngestionClients:
    def __init__(self, config: IngestionConfig) -> None:
        self.credential = ManagedIdentityCredential(
            client_id=config.azure_client_id
        )
        token_provider = get_bearer_token_provider(
            self.credential, "https://cognitiveservices.azure.com/.default"
        )
        self.openai = AsyncAzureOpenAI(
            azure_endpoint=config.openai_endpoint,
            azure_ad_token_provider=token_provider,
            api_version=config.openai_api_version,
            timeout=1200,
            max_retries=4,
        )
        self._closables: list[Any] = [self.openai, self.credential]

    def register(self, client: Any) -> Any:
        self._closables.insert(0, client)
        return client

    async def close(self) -> None:
        for client in self._closables:
            close = getattr(client, "close", None)
            if close is not None:
                await close()
