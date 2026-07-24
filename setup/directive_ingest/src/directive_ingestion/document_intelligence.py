"""Document Intelligence Layout extraction over Entra-authenticated REST."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx


@dataclass(frozen=True)
class PageSpan:
    offset: int
    length: int
    page_number: int


@dataclass(frozen=True)
class ExtractedDocument:
    markdown: str
    total_pages: int
    page_spans: tuple[PageSpan, ...]
    table_count: int

    def page_for_offset(self, offset: int) -> int:
        matching = [
            span
            for span in self.page_spans
            if span.offset <= offset < span.offset + span.length
        ]
        if matching:
            return matching[0].page_number
        preceding = [span for span in self.page_spans if span.offset <= offset]
        if preceding:
            return max(preceding, key=lambda span: span.offset).page_number
        return 1


class DocumentIntelligenceExtractor:
    def __init__(
        self,
        endpoint: str,
        api_version: str,
        credential: Any,
        *,
        timeout_seconds: float = 1200,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._api_version = api_version
        self._credential = credential
        self._timeout_seconds = timeout_seconds
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(120))

    async def close(self) -> None:
        await self._client.aclose()

    async def check_access(self) -> None:
        token = await self._credential.get_token(
            "https://cognitiveservices.azure.com/.default"
        )
        response = await self._request_with_retry(
            "GET",
            (
                f"{self._endpoint}/documentintelligence/documentModels/"
                "prebuilt-layout"
            ),
            headers={"Authorization": f"Bearer {token.token}"},
            params={"api-version": self._api_version},
        )
        if not isinstance(response.json(), dict):
            raise RuntimeError(
                "Document Intelligence model lookup returned an invalid response"
            )

    async def extract(self, pdf: bytes) -> ExtractedDocument:
        token = await self._credential.get_token(
            "https://cognitiveservices.azure.com/.default"
        )
        url = (
            f"{self._endpoint}/documentintelligence/documentModels/"
            "prebuilt-layout:analyze"
        )
        headers = {
            "Authorization": f"Bearer {token.token}",
            "Content-Type": "application/pdf",
        }
        params = {
            "api-version": self._api_version,
            "outputContentFormat": "markdown",
        }
        response = await self._request_with_retry(
            "POST", url, headers=headers, params=params, content=pdf
        )
        if response.status_code == 200:
            payload = response.json()
        elif response.status_code == 202:
            operation_url = response.headers.get("operation-location", "")
            self._validate_operation_url(operation_url)
            payload = await self._poll(operation_url, headers)
        else:
            raise RuntimeError(
                "Document Intelligence analyze returned unexpected HTTP "
                f"{response.status_code}: {response.text}"
            )
        return self._parse_result(payload)

    async def _poll(
        self, operation_url: str, headers: dict[str, str]
    ) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._timeout_seconds
        while loop.time() < deadline:
            response = await self._request_with_retry(
                "GET", operation_url, headers=headers
            )
            payload = response.json()
            status = str(payload.get("status", "")).casefold()
            if status == "succeeded":
                return payload
            if status in {"failed", "canceled"}:
                raise RuntimeError(
                    "Document Intelligence analysis "
                    f"{status}: {payload.get('error', payload)}"
                )
            await asyncio.sleep(2)
        raise TimeoutError("Document Intelligence analysis timed out")

    async def _request_with_retry(
        self, method: str, url: str, **kwargs: Any
    ) -> httpx.Response:
        for attempt in range(5):
            response = await self._client.request(method, url, **kwargs)
            if response.status_code not in {408, 429, 500, 502, 503, 504}:
                response.raise_for_status()
                return response
            if attempt == 4:
                response.raise_for_status()
            retry_after = response.headers.get("retry-after")
            delay = float(retry_after) if retry_after else 2**attempt
            await asyncio.sleep(min(delay, 30))
        raise AssertionError("unreachable")

    def _validate_operation_url(self, operation_url: str) -> None:
        expected = urlparse(self._endpoint)
        actual = urlparse(operation_url)
        if (
            actual.scheme != "https"
            or actual.hostname is None
            or actual.hostname.casefold() != expected.hostname.casefold()
        ):
            raise RuntimeError(
                "Document Intelligence returned an untrusted operation URL"
            )

    @staticmethod
    def _parse_result(payload: dict[str, Any]) -> ExtractedDocument:
        result = payload.get("analyzeResult")
        if not isinstance(result, dict):
            raise RuntimeError(
                "Document Intelligence response has no analyzeResult"
            )
        markdown = str(result.get("content", "")).strip()
        pages = result.get("pages")
        if not markdown:
            raise RuntimeError(
                "Document Intelligence returned empty Markdown content"
            )
        if not isinstance(pages, list) or not pages:
            raise RuntimeError(
                "Document Intelligence returned no page information"
            )
        spans: list[PageSpan] = []
        for paragraph in result.get("paragraphs", []):
            regions = paragraph.get("boundingRegions") or []
            paragraph_spans = paragraph.get("spans") or []
            if not regions or not paragraph_spans:
                continue
            page_number = int(regions[0].get("pageNumber", 1))
            for span in paragraph_spans:
                spans.append(
                    PageSpan(
                        offset=int(span.get("offset", 0)),
                        length=int(span.get("length", 0)),
                        page_number=page_number,
                    )
                )
        return ExtractedDocument(
            markdown=markdown + "\n",
            total_pages=len(pages),
            page_spans=tuple(sorted(spans, key=lambda span: span.offset)),
            table_count=len(result.get("tables", [])),
        )
