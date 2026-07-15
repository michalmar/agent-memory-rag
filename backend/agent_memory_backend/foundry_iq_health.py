"""Foundry IQ knowledge-base readiness probe."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .config import get_settings

logger = logging.getLogger("foundry_iq_health")


class FoundryIqHealthProbe:
    def __init__(self) -> None:
        self._client: Any = None
        self._health_ok_until = 0.0
        self._health_lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return self._client is not None

    async def initialize(self) -> None:
        settings = get_settings()
        if not settings.search_configured:
            logger.warning("Search not configured; agentic retrieval disabled")
            return

        import httpx

        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.search_retrieval_timeout_seconds)
        )
        logger.info("Agentic retrieval initialized (%s)", settings.search_kb)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _headers(self) -> dict[str, str]:
        from .azure_clients import get_credential

        token = await get_credential().get_token("https://search.azure.com/.default")
        headers = {
            "Content-Type": "application/json",
        }
        headers["Authorization"] = f"Bearer {token.token}"
        return headers

    async def _retrieve(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("FoundryIqHealthProbe is not initialized")

        settings = get_settings()
        url = (
            f"{settings.search_endpoint}/knowledgebases/{settings.search_kb}/retrieve"
        )
        response = await self._client.post(
            url,
            params={"api-version": settings.search_knowledge_api_version},
            headers=await self._headers(),
            json=payload,
        )
        if response.status_code != 200:
            raise RuntimeError(
                "Foundry IQ retrieval failed "
                f"(status={response.status_code}, kb={settings.search_kb})"
            )
        return response.json()

    @staticmethod
    def _source_params(*, include_references: bool) -> list[dict[str, Any]]:
        settings = get_settings()
        return [
            {
                "knowledgeSourceName": name,
                "kind": "searchIndex",
                "includeReferences": include_references,
                "includeReferenceSourceData": include_references,
                "failOnError": True,
                "maxOutputDocuments": 50,
            }
            for name in (
                settings.search_orders_knowledge_source,
                settings.search_policy_knowledge_source,
            )
            if name
        ]

    async def health_check(self) -> None:
        """Exercise the KB with minimal reasoning so no LLM inference is used."""
        if time.monotonic() < self._health_ok_until:
            return
        async with self._health_lock:
            if time.monotonic() < self._health_ok_until:
                return
            payload = {
                "intents": [
                    {
                        "type": "semantic",
                        "search": "healthcheck",
                    }
                ],
                "retrievalReasoningEffort": {"kind": "minimal"},
                "outputMode": "extractiveData",
                "maxRuntimeInSeconds": 11,
                "knowledgeSourceParams": self._source_params(
                    include_references=False
                ),
            }
            await self._retrieve(payload)
            self._health_ok_until = (
                time.monotonic() + get_settings().search_health_cache_seconds
            )
