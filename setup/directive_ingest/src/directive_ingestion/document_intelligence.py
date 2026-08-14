"""Document Intelligence Layout extraction over Entra-authenticated REST."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx


@dataclass(frozen=True, slots=True)
class ContentSpan:
    offset: int
    length: int
    page_number: int


@dataclass(frozen=True, slots=True)
class ExtractedPage:
    page_number: int
    width: float
    height: float
    unit: str
    spans: tuple[ContentSpan, ...]


@dataclass(frozen=True, slots=True)
class ExtractedLine:
    page_number: int
    text: str
    spans: tuple[ContentSpan, ...]
    polygon: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class BoundingRegion:
    page_number: int
    polygon: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class ExtractedParagraph:
    text: str
    role: str | None
    spans: tuple[ContentSpan, ...]
    bounding_regions: tuple[BoundingRegion, ...]


@dataclass(frozen=True, slots=True)
class ExtractedTableCell:
    page_number: int
    row_index: int
    column_index: int
    row_span: int
    column_span: int
    text: str
    polygon: tuple[float, ...]
    spans: tuple[ContentSpan, ...]


@dataclass(frozen=True, slots=True)
class ExtractedTable:
    row_count: int
    column_count: int
    cells: tuple[ExtractedTableCell, ...]
    spans: tuple[ContentSpan, ...]


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    markdown: str
    pages: tuple[ExtractedPage, ...]
    lines: tuple[ExtractedLine, ...]
    paragraphs: tuple[ExtractedParagraph, ...]
    tables: tuple[ExtractedTable, ...]
    content_spans: tuple[ContentSpan, ...]

    @property
    def total_pages(self) -> int:
        return len(self.pages)

    @property
    def page_spans(self) -> tuple[ContentSpan, ...]:
        return self.content_spans

    @property
    def table_count(self) -> int:
        return len(self.tables)

    def page_for_offset(self, offset: int) -> int:
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ValueError("Content offset must be a non-negative integer")
        matching = [
            span
            for span in self.content_spans
            if span.offset <= offset < span.offset + span.length
        ]
        if matching:
            return matching[0].page_number
        preceding = [span for span in self.content_spans if span.offset <= offset]
        return max(preceding, key=lambda span: span.offset).page_number if preceding else 1

    def page_text(self, page_number: int) -> str:
        self.page(page_number)
        return "\n".join(
            line.text for line in self.lines if line.page_number == page_number
        )

    def page_role_paragraphs(
        self, page_number: int, role: str | None = None
    ) -> tuple[ExtractedParagraph, ...]:
        self.page(page_number)
        return tuple(
            paragraph
            for paragraph in self.paragraphs
            if any(region.page_number == page_number for region in paragraph.bounding_regions)
            and (role is None or paragraph.role == role)
        )

    def label_anchors(self, page_number: int, labels: set[str]) -> tuple[ExtractedLine, ...]:
        self.page(page_number)
        return tuple(
            line
            for line in self.lines
            if line.page_number == page_number and line.text in labels
        )

    def content_for_pages(self, page_numbers: range | tuple[int, ...]) -> str:
        wanted = set(page_numbers)
        for page_number in wanted:
            self.page(page_number)
        spans = sorted(
            (span for span in self.content_spans if span.page_number in wanted),
            key=lambda span: (span.offset, span.length),
        )
        if not spans:
            return ""
        pieces: list[str] = []
        previous_end = -1
        for span in spans:
            if span.offset < previous_end:
                continue
            pieces.append(self.markdown[span.offset : span.offset + span.length])
            previous_end = span.offset + span.length
        return "\n".join(piece.strip() for piece in pieces if piece.strip()).strip()

    def page(self, page_number: int) -> ExtractedPage:
        if not isinstance(page_number, int) or isinstance(page_number, bool):
            raise TypeError("Page number must be an integer")
        for page in self.pages:
            if page.page_number == page_number:
                return page
        raise ValueError(f"Unknown page number: {page_number}")


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
        await self._credential.get_token(
            "https://cognitiveservices.azure.com/.default"
        )
        response = await self._request_with_retry(
            "GET",
            f"{self._endpoint}/documentintelligence/documentModels/prebuilt-layout",
            headers={"Authorization": "******"},
            params={"api-version": self._api_version},
        )
        if not isinstance(response.json(), dict):
            raise RuntimeError(
                "Document Intelligence model lookup returned an invalid response"
            )

    async def extract(self, pdf: bytes) -> ExtractedDocument:
        await self._credential.get_token(
            "https://cognitiveservices.azure.com/.default"
        )
        url = f"{self._endpoint}/documentintelligence/documentModels/prebuilt-layout:analyze"
        response = await self._request_with_retry(
            "POST",
            url,
            headers={"Authorization": "******", "Content-Type": "application/pdf"},
            params={
                "api-version": self._api_version,
                "outputContentFormat": "markdown",
            },
            content=pdf,
        )
        if response.status_code == 200:
            payload = response.json()
        elif response.status_code == 202:
            operation_url = response.headers.get("operation-location", "")
            self._validate_operation_url(operation_url)
            payload = await self._poll(operation_url, {"Authorization": "******"})
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
            response = await self._request_with_retry("GET", operation_url, headers=headers)
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError("Document Intelligence operation returned invalid JSON")
            status = str(payload.get("status", "")).casefold()
            if status == "succeeded":
                return payload
            if status in {"failed", "canceled"}:
                raise RuntimeError(f"Document Intelligence analysis {status}")
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
            try:
                delay = float(retry_after) if retry_after else 2**attempt
            except ValueError:
                delay = 2**attempt
            await asyncio.sleep(min(delay, 30))
        raise AssertionError("unreachable")

    def _validate_operation_url(self, operation_url: str) -> None:
        expected = urlparse(self._endpoint)
        actual = urlparse(operation_url)
        if (
            actual.scheme != "https"
            or actual.hostname is None
            or actual.hostname.casefold() != (expected.hostname or "").casefold()
        ):
            raise RuntimeError(
                "Document Intelligence returned an untrusted operation URL"
            )

    @staticmethod
    def _parse_result(payload: dict[str, Any]) -> ExtractedDocument:
        if not isinstance(payload, dict):
            raise RuntimeError("Document Intelligence response must be an object")
        result = payload.get("analyzeResult")
        if not isinstance(result, dict):
            raise RuntimeError("Document Intelligence response has no analyzeResult")
        markdown = result.get("content")
        if not isinstance(markdown, str) or not markdown.strip():
            raise RuntimeError("Document Intelligence returned empty Markdown content")
        raw_pages = _required_list(result, "pages")
        pages = tuple(
            _parse_page(item, index + 1) for index, item in enumerate(raw_pages)
        )
        if not pages:
            raise RuntimeError("Document Intelligence returned no page information")
        expected_pages = tuple(range(1, len(pages) + 1))
        if tuple(page.page_number for page in pages) != expected_pages:
            raise RuntimeError("Document Intelligence page numbers must be contiguous")
        lines = tuple(
            line
            for page, item in zip(pages, raw_pages, strict=True)
            for line in _parse_lines(item, page.page_number)
        )
        paragraphs = tuple(
            _parse_paragraph(item) for item in _optional_list(result, "paragraphs")
        )
        tables = tuple(_parse_table(item) for item in _optional_list(result, "tables"))
        content_spans = tuple(
            sorted(
                (span for page in pages for span in page.spans),
                key=lambda span: (span.offset, span.length, span.page_number),
            )
        )
        if not content_spans:
            raise RuntimeError("Document Intelligence page content spans are missing")
        _validate_spans(content_spans, len(markdown))
        return ExtractedDocument(
            markdown=markdown.rstrip() + "\n",
            pages=pages,
            lines=lines,
            paragraphs=paragraphs,
            tables=tables,
            content_spans=content_spans,
        )


def _required_list(container: dict[str, Any], name: str) -> list[Any]:
    value = container.get(name)
    if not isinstance(value, list):
        raise RuntimeError(f"Document Intelligence {name} must be an array")
    return value


def _optional_list(container: dict[str, Any], name: str) -> list[Any]:
    value = container.get(name, [])
    if not isinstance(value, list):
        raise RuntimeError(f"Document Intelligence {name} must be an array")
    return value


def _require_object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(f"Document Intelligence {name} must be an object")
    return value


def _require_int(value: Any, name: str, *, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise RuntimeError(f"Document Intelligence {name} must be an integer >= {minimum}")
    return value


def _require_number(value: Any, name: str, *, positive: bool = False) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise RuntimeError(f"Document Intelligence {name} must be a number")
    result = float(value)
    if result != result or result in {float("inf"), float("-inf")} or (positive and result <= 0):
        raise RuntimeError(f"Document Intelligence {name} must be finite and positive")
    return result


def _parse_spans(
    raw: Any,
    page_number: int,
    name: str,
    *,
    minimum_length: int = 1,
) -> tuple[ContentSpan, ...]:
    if not isinstance(raw, list) or not raw:
        raise RuntimeError(f"Document Intelligence {name} must be a non-empty array")
    return tuple(
        ContentSpan(
            offset=_require_int(_require_object(item, name).get("offset"), f"{name}.offset"),
            length=_require_int(
                _require_object(item, name).get("length"),
                f"{name}.length",
                minimum=minimum_length,
            ),
            page_number=page_number,
        )
        for item in raw
    )


def _parse_polygon(raw: Any, name: str) -> tuple[float, ...]:
    if not isinstance(raw, list) or len(raw) < 8 or len(raw) % 2:
        raise RuntimeError(f"Document Intelligence {name} must contain polygon pairs")
    return tuple(_require_number(value, name) for value in raw)


def _parse_page(value: Any, expected_page: int) -> ExtractedPage:
    item = _require_object(value, "page")
    page_number = _require_int(item.get("pageNumber"), "page.pageNumber", minimum=1)
    if page_number != expected_page:
        raise RuntimeError("Document Intelligence pages must be ordered by page number")
    unit = item.get("unit", "")
    if not isinstance(unit, str):
        raise RuntimeError("Document Intelligence page.unit must be a string")
    return ExtractedPage(
        page_number=page_number,
        width=_require_number(item.get("width"), "page.width", positive=True),
        height=_require_number(item.get("height"), "page.height", positive=True),
        unit=unit,
        spans=_parse_spans(item.get("spans"), page_number, "page.spans"),
    )


def _parse_lines(page: Any, page_number: int) -> tuple[ExtractedLine, ...]:
    page_object = _require_object(page, "page")
    values = _optional_list(page_object, "lines")
    return tuple(
        ExtractedLine(
            page_number=page_number,
            text=_require_text(_require_object(item, "line").get("content"), "line.content"),
            spans=_parse_spans(_require_object(item, "line").get("spans"), page_number, "line.spans"),
            polygon=_parse_polygon(_require_object(item, "line").get("polygon"), "line.polygon"),
        )
        for item in values
    )


def _require_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"Document Intelligence {name} must be non-empty text")
    return value


def _parse_paragraph(value: Any) -> ExtractedParagraph:
    item = _require_object(value, "paragraph")
    regions = _optional_list(item, "boundingRegions")
    if not regions:
        raise RuntimeError("Document Intelligence paragraph.boundingRegions is required")
    parsed_regions = tuple(
        BoundingRegion(
            page_number=_require_int(
                _require_object(region, "paragraph.boundingRegion").get("pageNumber"),
                "paragraph.boundingRegion.pageNumber",
                minimum=1,
            ),
            polygon=_parse_polygon(
                _require_object(region, "paragraph.boundingRegion").get("polygon"),
                "paragraph.boundingRegion.polygon",
            ),
        )
        for region in regions
    )
    role = item.get("role")
    if role is not None and not isinstance(role, str):
        raise RuntimeError("Document Intelligence paragraph.role must be a string")
    return ExtractedParagraph(
        text=_require_text(item.get("content"), "paragraph.content"),
        role=role,
        spans=_parse_spans(item.get("spans"), parsed_regions[0].page_number, "paragraph.spans"),
        bounding_regions=parsed_regions,
    )


def _parse_table(value: Any) -> ExtractedTable:
    item = _require_object(value, "table")
    row_count = _require_int(item.get("rowCount"), "table.rowCount", minimum=1)
    column_count = _require_int(item.get("columnCount"), "table.columnCount", minimum=1)
    cells = _required_list(item, "cells")
    parsed_cells: list[ExtractedTableCell] = []
    for raw_cell in cells:
        cell = _require_object(raw_cell, "table.cell")
        regions = _optional_list(cell, "boundingRegions")
        if len(regions) != 1:
            raise RuntimeError("Document Intelligence table cell needs one bounding region")
        region = _require_object(regions[0], "table.cell.boundingRegion")
        page_number = _require_int(region.get("pageNumber"), "table.cell.pageNumber", minimum=1)
        row_index = _require_int(cell.get("rowIndex"), "table.cell.rowIndex")
        column_index = _require_int(cell.get("columnIndex"), "table.cell.columnIndex")
        row_span = _require_int(cell.get("rowSpan", 1), "table.cell.rowSpan", minimum=1)
        column_span = _require_int(cell.get("columnSpan", 1), "table.cell.columnSpan", minimum=1)
        if row_index + row_span > row_count or column_index + column_span > column_count:
            raise RuntimeError("Document Intelligence table cell is outside table bounds")
        parsed_cells.append(
            ExtractedTableCell(
                page_number=page_number,
                row_index=row_index,
                column_index=column_index,
                row_span=row_span,
                column_span=column_span,
                text=_require_text(cell.get("content"), "table.cell.content"),
                polygon=_parse_polygon(region.get("polygon"), "table.cell.polygon"),
                spans=_parse_spans(
                    cell.get("spans"),
                    page_number,
                    "table.cell.spans",
                    minimum_length=0,
                ),
            )
        )
    return ExtractedTable(
        row_count=row_count,
        column_count=column_count,
        cells=tuple(sorted(parsed_cells, key=lambda cell: (cell.page_number, cell.row_index, cell.column_index))),
        spans=_parse_spans(
            item.get("spans"),
            parsed_cells[0].page_number,
            "table.spans",
            minimum_length=0,
        ),
    )


def _validate_spans(spans: tuple[ContentSpan, ...], content_length: int) -> None:
    for span in spans:
        if span.offset + span.length > content_length:
            raise RuntimeError("Document Intelligence content span exceeds content")
