"""Stable Azure AI Search knowledge-base retrieval for directive evidence."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from .config import get_settings
from .directive_errors import DirectiveDataUnavailable

logger = logging.getLogger("directive_search")


class DirectiveSearchRepository:
    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._endpoint = ""
        self._knowledge_base = ""
        self._knowledge_source = ""
        self._api_version = ""
        self._max_results = 0

    @property
    def enabled(self) -> bool:
        return self._client is not None

    async def initialize(self) -> None:
        settings = get_settings()
        if not (
            settings.search_endpoint
            and settings.directive_search_kb
            and settings.directive_search_knowledge_source
        ):
            logger.warning("Directive Search is not configured")
            return
        self._endpoint = settings.search_endpoint.rstrip("/")
        self._knowledge_base = settings.directive_search_kb
        self._knowledge_source = settings.directive_search_knowledge_source
        self._api_version = settings.directive_search_api_version
        self._max_results = settings.directive_max_search_results
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.directive_tool_timeout_seconds)
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def health_check(self) -> None:
        await self.retrieve(
            intents=["healthcheck"],
            current_only=True,
            max_results=1,
            include_references=False,
        )

    async def retrieve(
        self,
        *,
        intents: list[str],
        current_only: bool,
        max_results: int,
        directive_ids: list[str] | None = None,
        directive_version_id: str | None = None,
        section_ids: list[str] | None = None,
        include_references: bool = True,
    ) -> dict[str, Any]:
        bounded_results = min(max_results, self._max_results)
        filter_expression = _build_filter(
            current_only=current_only,
            directive_ids=directive_ids or [],
            directive_version_id=directive_version_id,
            section_ids=section_ids or [],
        )
        payload = {
            "intents": [
                {"type": "semantic", "search": intent.strip()}
                for intent in intents
            ],
            "maxOutputSizeInTokens": 5000,
            "knowledgeSourceParams": [
                {
                    "knowledgeSourceName": self._knowledge_source,
                    "kind": "searchIndex",
                    "includeReferences": include_references,
                    "includeReferenceSourceData": include_references,
                    "filterAddOn": filter_expression,
                }
            ],
        }
        raw = await self._request(payload)
        references = _normalize_references(raw, bounded_results)
        return {
            "intents": intents,
            "filter": {
                "current_only": current_only,
                "directive_ids": directive_ids or [],
                "directive_version_id": directive_version_id,
                "section_ids": section_ids or [],
            },
            "retrieval_output": [
                {
                    "ref_id": reference["ref_id"],
                    "content": reference["content"],
                }
                for reference in references
            ],
            "references": references,
        }

    async def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._client is None:
            raise DirectiveDataUnavailable("Directive Search is unavailable")
        from .azure_clients import get_credential

        token = await get_credential().get_token(
            "https://search.azure.com/.default"
        )
        headers = {
            "Authorization": f"Bearer {token.token}",
            "Content-Type": "application/json",
        }
        url = (
            f"{self._endpoint}/knowledgebases/{self._knowledge_base}/retrieve"
        )
        for attempt in range(5):
            try:
                response = await self._client.post(
                    url,
                    params={"api-version": self._api_version},
                    headers=headers,
                    json=payload,
                )
            except httpx.HTTPError as exc:
                raise DirectiveDataUnavailable(
                    "Directive Search request failed"
                ) from exc
            if response.status_code == 200:
                value = response.json()
                if not isinstance(value, dict):
                    raise DirectiveDataUnavailable(
                        "Directive Search returned an invalid response"
                    )
                return value
            if response.status_code not in {408, 429, 500, 502, 503, 504}:
                raise DirectiveDataUnavailable(
                    "Directive Search rejected the retrieval request "
                    f"(status={response.status_code})"
                )
            if attempt == 4:
                break
            retry_after = response.headers.get("retry-after")
            delay = float(retry_after) if retry_after else 2**attempt
            await asyncio.sleep(min(delay, 30))
        raise DirectiveDataUnavailable(
            "Directive Search remained unavailable after retries"
        )


def _build_filter(
    *,
    current_only: bool,
    directive_ids: list[str],
    directive_version_id: str | None,
    section_ids: list[str],
) -> str:
    filters = ["publication_state eq 'published'"]
    if current_only:
        filters.append("is_current eq true")
    if directive_ids:
        values = " or ".join(
            f"directive_id eq '{_odata(value)}'" for value in directive_ids
        )
        filters.append(f"({values})")
    if directive_version_id:
        filters.append(
            "directive_version_id eq "
            f"'{_odata(directive_version_id)}'"
        )
    if section_ids:
        values = " or ".join(
            f"section_id eq '{_odata(value)}'" for value in section_ids
        )
        filters.append(f"({values})")
    return " and ".join(filters)


def _odata(value: str) -> str:
    return value.replace("'", "''")


def _normalize_references(
    response: dict[str, Any],
    limit: int,
) -> list[dict[str, Any]]:
    raw_references = response.get("references")
    if not isinstance(raw_references, list):
        return []
    values: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_references):
        if len(values) >= limit or not isinstance(raw, dict):
            break
        source_data = (
            raw.get("sourceData")
            or raw.get("referenceSourceData")
            or raw.get("source_data")
            or {}
        )
        if not isinstance(source_data, dict):
            source_data = {}
        ref_id = raw.get("id") or raw.get("ref_id") or f"reference-{index + 1}"
        content = raw.get("content") or raw.get("text") or raw.get("excerpt")
        values.append(
            {
                "ref_id": str(ref_id),
                "content": str(content) if content is not None else None,
                "source_data": {
                    key: value
                    for key, value in source_data.items()
                    if key
                    in {
                        "directive_id",
                        "directive_version_id",
                        "version_label",
                        "title",
                        "is_current",
                        "effective_from",
                        "effective_to",
                        "section_id",
                        "section_number",
                        "section_title",
                        "page_from",
                        "page_to",
                        "source_hash",
                    }
                },
            }
        )
    return values
