"""Deterministic direct hybrid retrieval for directive evidence."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any
from urllib.parse import quote

import httpx
from azure.core.exceptions import ClientAuthenticationError
from azure.identity import CredentialUnavailableError
from directive_contracts import normalize_directive_id, validate_directive_version_id

from .config import get_settings
from .directive_errors import DirectiveDataUnavailable
from .telemetry import span

logger = logging.getLogger("directive_search")

_SEARCH_SCOPE = "https://search.azure.com/.default"
_RETRYABLE_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 5
_PER_INTENT_CANDIDATES = 50
_RRF_CONSTANT = 60
_VECTOR_FIELD = "content_vector"
_SEMANTIC_CONFIGURATION = "semantic_config"
_SELECT_FIELDS = (
    "id",
    "content",
    "directive_id",
    "directive_version_id",
    "version_label",
    "title",
    "is_current",
    "is_valid",
    "effective_from",
    "effective_to",
    "section_id",
    "section_number",
    "section_title",
    "page_from",
    "page_to",
)
_SOURCE_DATA_FIELDS = _SELECT_FIELDS[2:]


@dataclass(frozen=True)
class _RequestResult:
    response: dict[str, Any]
    retries: int
    latency_ms: int


@dataclass(frozen=True)
class _IntentResult:
    references: list[dict[str, Any]]
    retries: int
    latency_ms: int


@dataclass
class _FusedReference:
    reference: dict[str, Any]
    score: float = 0.0
    best_rank: int = _PER_INTENT_CANDIDATES + 1
    intent_indexes: set[int] = field(default_factory=set)


@dataclass(frozen=True)
class _FusionResult:
    references: list[dict[str, Any]]
    unique_count: int


class DirectiveSearchRepository:
    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._endpoint = ""
        self._index_name = ""
        self._api_version = ""
        self._max_results = 0

    @property
    def enabled(self) -> bool:
        return self._client is not None

    async def initialize(self) -> None:
        settings = get_settings()
        if not (settings.search_endpoint and settings.directive_search_index):
            logger.warning("Directive Search is not configured")
            return
        self._endpoint = settings.search_endpoint.rstrip("/")
        self._index_name = settings.directive_search_index
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
        normalized_intents = [intent.strip() for intent in intents]
        try:
            normalized_ids = [
                normalize_directive_id(value) for value in directive_ids or []
            ]
            if directive_version_id is not None and len(normalized_ids) != 1:
                raise ValueError(
                    "Exact directive version search requires one directive ID"
                )
            normalized_version_id = (
                validate_directive_version_id(
                    directive_version_id, normalized_ids[0]
                )
                if directive_version_id is not None
                else None
            )
        except (TypeError, ValueError) as exc:
            raise DirectiveDataUnavailable("Directive search identity is invalid") from exc
        bounded_results = min(max_results, self._max_results)
        filter_expression = _build_filter(
            current_only=current_only,
            directive_ids=normalized_ids,
            directive_version_id=normalized_version_id,
            section_ids=section_ids or [],
        )
        started = perf_counter()
        with span(
            "directive.search.retrieve",
            {
                "search.retrieval_mode": "direct_hybrid",
                "search.intent_count": len(normalized_intents),
            },
        ):
            intent_results = await asyncio.gather(
                *(
                    self._search_intent(
                        intent,
                        intent_index=intent_index,
                        filter_expression=filter_expression,
                    )
                    for intent_index, intent in enumerate(normalized_intents)
                )
            )

        ranked_references = [
            result.references for result in intent_results
        ]
        fusion = _fuse_references(ranked_references, bounded_results)
        references = fusion.references if include_references else []
        aggregate_count = sum(len(values) for values in ranked_references)
        deduplicated_count = aggregate_count - fusion.unique_count
        logger.info(
            "Directive Search retrieval completed mode=direct_hybrid "
            "intent_count=%d per_intent_counts=%s aggregate_count=%d "
            "deduplicated_count=%d returned_count=%d retry_count=%d "
            "latency_ms=%d empty_result=%s",
            len(normalized_intents),
            [len(values) for values in ranked_references],
            aggregate_count,
            deduplicated_count,
            len(fusion.references),
            sum(result.retries for result in intent_results),
            round((perf_counter() - started) * 1000),
            not fusion.references,
        )
        return {
            "intents": normalized_intents,
            "filter": {
                "current_only": current_only,
                "directive_ids": normalized_ids,
                "directive_version_id": normalized_version_id,
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

    async def _search_intent(
        self,
        intent: str,
        *,
        intent_index: int,
        filter_expression: str,
    ) -> _IntentResult:
        payload = {
            "search": intent,
            "filter": filter_expression,
            "vectorFilterMode": "preFilter",
            "vectorQueries": [
                {
                    "kind": "text",
                    "text": intent,
                    "fields": _VECTOR_FIELD,
                    "k": _PER_INTENT_CANDIDATES,
                }
            ],
            "queryType": "semantic",
            "semanticConfiguration": _SEMANTIC_CONFIGURATION,
            "select": ",".join(_SELECT_FIELDS),
            "top": _PER_INTENT_CANDIDATES,
        }
        result = await self._request(payload, intent_index=intent_index)
        references = _normalize_search_response(
            result.response,
            intent_index=intent_index,
        )
        logger.info(
            "Directive Search intent completed mode=direct_hybrid "
            "intent_index=%d result_count=%d retry_count=%d latency_ms=%d "
            "empty_result=%s",
            intent_index,
            len(references),
            result.retries,
            result.latency_ms,
            not references,
        )
        return _IntentResult(
            references=references,
            retries=result.retries,
            latency_ms=result.latency_ms,
        )

    async def _request(
        self,
        payload: dict[str, Any],
        *,
        intent_index: int,
    ) -> _RequestResult:
        if self._client is None:
            raise DirectiveDataUnavailable("Directive Search is unavailable")
        from .azure_clients import get_credential

        try:
            token = await get_credential().get_token(_SEARCH_SCOPE)
        except (ClientAuthenticationError, CredentialUnavailableError) as exc:
            raise DirectiveDataUnavailable(
                "Directive Search authentication failed"
            ) from exc
        headers = {
            "Authorization": f"Bearer {token.token}",
            "Content-Type": "application/json",
        }
        encoded_index = quote(self._index_name, safe="")
        url = f"{self._endpoint}/indexes/{encoded_index}/docs/search"
        started = perf_counter()
        retries = 0
        for attempt in range(_MAX_ATTEMPTS):
            try:
                response = await self._client.post(
                    url,
                    params={"api-version": self._api_version},
                    headers=headers,
                    json=payload,
                )
            except httpx.HTTPError as exc:
                if attempt == _MAX_ATTEMPTS - 1:
                    raise DirectiveDataUnavailable(
                        "Directive Search request failed after retries"
                    ) from exc
                retries += 1
                logger.warning(
                    "Directive Search transport retry mode=direct_hybrid "
                    "intent_index=%d attempt=%d",
                    intent_index,
                    attempt + 1,
                )
                await asyncio.sleep(min(2**attempt, 30))
                continue

            if response.status_code == 200:
                try:
                    value = response.json()
                except ValueError as exc:
                    raise DirectiveDataUnavailable(
                        "Directive Search returned invalid JSON"
                    ) from exc
                if not isinstance(value, dict):
                    raise DirectiveDataUnavailable(
                        "Directive Search returned an invalid response"
                    )
                return _RequestResult(
                    response=value,
                    retries=retries,
                    latency_ms=round((perf_counter() - started) * 1000),
                )
            if response.status_code not in _RETRYABLE_STATUSES:
                raise DirectiveDataUnavailable(
                    "Directive Search rejected the retrieval request "
                    f"(status={response.status_code})"
                )
            if attempt == _MAX_ATTEMPTS - 1:
                break
            retries += 1
            delay = _retry_delay(response.headers, attempt)
            logger.warning(
                "Directive Search HTTP retry mode=direct_hybrid "
                "intent_index=%d status=%d attempt=%d delay_seconds=%.3f",
                intent_index,
                response.status_code,
                attempt + 1,
                delay,
            )
            await asyncio.sleep(delay)
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
    filters = ["publication_state eq 'published'", "is_valid eq true"]
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


def _normalize_search_response(
    response: dict[str, Any],
    *,
    intent_index: int,
) -> list[dict[str, Any]]:
    raw_documents = response.get("value")
    if not isinstance(raw_documents, list):
        raise DirectiveDataUnavailable(
            "Directive Search returned an invalid document list"
        )

    references: list[dict[str, Any]] = []
    for raw in raw_documents:
        if not isinstance(raw, dict):
            raise DirectiveDataUnavailable(
                "Directive Search returned an invalid document"
            )
        document_id = raw.get("id")
        if not isinstance(document_id, str) or not document_id:
            raise DirectiveDataUnavailable(
                "Directive Search returned a document without an ID"
            )
        content = raw.get("content")
        if content is not None and not isinstance(content, str):
            raise DirectiveDataUnavailable(
                "Directive Search returned invalid document content"
            )
        references.append(
            {
                "ref_id": document_id,
                "content": content,
                "source_data": {
                    key: raw[key]
                    for key in _SOURCE_DATA_FIELDS
                    if key in raw
                },
                "matched_intent_indexes": [intent_index],
            }
        )
    return references


def _fuse_references(
    ranked_references: list[list[dict[str, Any]]],
    limit: int,
) -> _FusionResult:
    fused: dict[str, _FusedReference] = {}
    for intent_index, references in enumerate(ranked_references):
        for rank, reference in enumerate(references, start=1):
            ref_id = str(reference["ref_id"])
            candidate = fused.get(ref_id)
            if candidate is None:
                candidate = _FusedReference(reference=dict(reference))
                fused[ref_id] = candidate
            candidate.score += 1.0 / (_RRF_CONSTANT + rank)
            candidate.best_rank = min(candidate.best_rank, rank)
            candidate.intent_indexes.add(intent_index)

    ordered = sorted(
        fused.values(),
        key=lambda candidate: (
            -candidate.score,
            candidate.best_rank,
            -len(candidate.intent_indexes),
            str(candidate.reference["ref_id"]),
        ),
    )
    references: list[dict[str, Any]] = []
    for candidate in ordered[:limit]:
        reference = dict(candidate.reference)
        reference["matched_intent_indexes"] = sorted(candidate.intent_indexes)
        references.append(reference)
    return _FusionResult(
        references=references,
        unique_count=len(fused),
    )


def _retry_delay(headers: Any, attempt: int) -> float:
    retry_after = headers.get("retry-after")
    if retry_after:
        try:
            return min(max(float(retry_after), 0.0), 30.0)
        except (TypeError, ValueError):
            pass
    return float(min(2**attempt, 30))
