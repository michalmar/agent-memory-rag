"""Strict, page-aware Document Intelligence Layout extraction."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

_SCOPE = "https://cognitiveservices.azure.com/.default"


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
    bounding_regions: tuple[BoundingRegion, ...]


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
        for span in self.content_spans:
            if span.offset <= offset < span.offset + span.length:
                return span.page_number
        raise ValueError(f"Content offset does not belong to a page: {offset}")

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
            if any(
                region.page_number == page_number
                for region in paragraph.bounding_regions
            )
            and (role is None or paragraph.role == role)
        )

    def label_anchors(
        self, page_number: int, labels: set[str]
    ) -> tuple[ExtractedLine, ...]:
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
        return "\n".join(
            self.markdown[span.offset : span.offset + span.length].strip()
            for span in self.content_spans
            if span.page_number in wanted
        ).strip()

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
        headers = await self._authorization_headers()
        response = await self._request_with_retry(
            "GET",
            f"{self._endpoint}/documentintelligence/documentModels/prebuilt-layout",
            headers=headers,
            params={"api-version": self._api_version},
        )
        if not isinstance(response.json(), dict):
            raise RuntimeError(
                "Document Intelligence model lookup returned an invalid response"
            )

    async def extract(self, pdf: bytes) -> ExtractedDocument:
        headers = await self._authorization_headers()
        response = await self._request_with_retry(
            "POST",
            f"{self._endpoint}/documentintelligence/documentModels/"
            "prebuilt-layout:analyze",
            headers={**headers, "Content-Type": "application/pdf"},
            params={
                "api-version": self._api_version,
                "outputContentFormat": "markdown",
                "stringIndexType": "unicodeCodePoint",
            },
            content=pdf,
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
                f"{response.status_code}"
            )
        return self._parse_result(payload)

    async def _authorization_headers(self) -> dict[str, str]:
        token = await self._credential.get_token(_SCOPE)
        value = getattr(token, "token", None)
        if not isinstance(value, str) or not value:
            raise RuntimeError("Document Intelligence credential returned no token")
        return {"Authorization": f"Bearer {value}"}

    async def _poll(
        self, operation_url: str, headers: dict[str, str]
    ) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + self._timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            response = await self._request_with_retry(
                "GET", operation_url, headers=headers
            )
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError(
                    "Document Intelligence operation returned invalid JSON"
                )
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
            or actual.hostname.casefold()
            != (expected.hostname or "").casefold()
        ):
            raise RuntimeError(
                "Document Intelligence returned an untrusted operation URL"
            )

    @staticmethod
    def _parse_result(payload: dict[str, Any]) -> ExtractedDocument:
        if isinstance(payload, dict) and isinstance(payload.get("response"), dict):
            payload = payload["response"]
        result = _require_object(
            _require_object(payload, "response").get("analyzeResult"), "analyzeResult"
        )
        markdown = _require_text(result.get("content"), "content")
        raw_pages = _required_list(result, "pages")
        pages = tuple(
            _parse_page(value, index + 1)
            for index, value in enumerate(raw_pages)
        )
        if not pages:
            raise RuntimeError("Document Intelligence returned no page information")
        content_spans = tuple(
            span for page in pages for span in page.spans
        )
        _validate_page_spans(content_spans, len(markdown))
        pages_by_number = {page.page_number: page for page in pages}
        lines = tuple(
            line
            for page, raw_page in zip(pages, raw_pages, strict=True)
            for line in _parse_lines(raw_page, page)
        )
        paragraphs = tuple(
            _parse_paragraph(
                value, content_spans, len(markdown), pages_by_number
            )
            for value in _optional_list(result, "paragraphs")
        )
        tables = tuple(
            _parse_table(value, content_spans, len(markdown), pages_by_number)
            for value in _optional_list(result, "tables")
        )
        _validate_nested_records(
            content_spans, lines, paragraphs, tables, len(markdown)
        )
        return ExtractedDocument(
            markdown=markdown,
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
        raise RuntimeError(
            f"Document Intelligence {name} must be an integer >= {minimum}"
        )
    return value


def _require_number(value: Any, name: str, *, positive: bool = False) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise RuntimeError(f"Document Intelligence {name} must be a number")
    result = float(value)
    if result != result or result in {float("inf"), float("-inf")}:
        raise RuntimeError(f"Document Intelligence {name} must be finite")
    if positive and result <= 0:
        raise RuntimeError(
            f"Document Intelligence {name} must be finite and positive"
        )
    return result


def _require_text(value: Any, name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise RuntimeError(f"Document Intelligence {name} must be non-empty text")
    return value


def _parse_spans(
    raw: Any,
    name: str,
    *,
    allow_empty: bool = False,
    allow_zero_length: bool = False,
) -> tuple[tuple[int, int], ...]:
    if not isinstance(raw, list) or (not raw and not allow_empty):
        raise RuntimeError(f"Document Intelligence {name} must be a non-empty array")
    values = tuple(
        (
            _require_int(
                _require_object(value, name).get("offset"), f"{name}.offset"
            ),
            _require_int(
                _require_object(value, name).get("length"),
                f"{name}.length",
                minimum=0 if allow_zero_length else 1,
            ),
        )
        for value in raw
    )
    if tuple(sorted(values)) != values:
        raise RuntimeError(f"Document Intelligence {name} must be offset-ordered")
    return values


def _parse_polygon(raw: Any, name: str) -> tuple[float, ...]:
    if not isinstance(raw, list) or len(raw) < 8 or len(raw) % 2:
        raise RuntimeError(f"Document Intelligence {name} must contain polygon pairs")
    return tuple(_require_number(value, name) for value in raw)


def _validate_polygon_in_page(
    polygon: tuple[float, ...], page: ExtractedPage, name: str
) -> None:
    # Layout independently rounds page-edge geometry by small fractions.
    tolerance = 0.05
    x_values = polygon[::2]
    y_values = polygon[1::2]
    if (
        min(x_values) < -tolerance
        or max(x_values) > page.width + tolerance
        or min(y_values) < -tolerance
        or max(y_values) > page.height + tolerance
    ):
        raise RuntimeError(f"Document Intelligence {name} is outside page bounds")


def _parse_bounding_region(
    value: Any, name: str, pages_by_number: dict[int, ExtractedPage]
) -> BoundingRegion:
    region = _require_object(value, name)
    page_number = _require_int(
        region.get("pageNumber"), f"{name}.pageNumber", minimum=1
    )
    page = pages_by_number.get(page_number)
    if page is None:
        raise RuntimeError(f"Document Intelligence {name} references an unknown page")
    polygon = _parse_polygon(region.get("polygon"), f"{name}.polygon")
    _validate_polygon_in_page(polygon, page, f"{name}.polygon")
    return BoundingRegion(page_number=page_number, polygon=polygon)


def _parse_page(value: Any, expected_page: int) -> ExtractedPage:
    page = _require_object(value, "page")
    page_number = _require_int(page.get("pageNumber"), "page.pageNumber", minimum=1)
    if page_number != expected_page:
        raise RuntimeError(
            "Document Intelligence pages must be ordered by page number"
        )
    unit = page.get("unit", "")
    if not isinstance(unit, str):
        raise RuntimeError("Document Intelligence page.unit must be a string")
    return ExtractedPage(
        page_number=page_number,
        width=_require_number(page.get("width"), "page.width", positive=True),
        height=_require_number(page.get("height"), "page.height", positive=True),
        unit=unit,
        spans=tuple(
            ContentSpan(offset, length, page_number)
            for offset, length in _parse_spans(page.get("spans"), "page.spans")
        ),
    )


def _parse_lines(
    raw_page: Any, page: ExtractedPage
) -> tuple[ExtractedLine, ...]:
    values = _optional_list(_require_object(raw_page, "page"), "lines")
    return tuple(
        ExtractedLine(
            page_number=page.page_number,
            text=_require_text(
                _require_object(value, "line").get("content"), "line.content"
            ),
            spans=tuple(
                ContentSpan(offset, length, page.page_number)
                for offset, length in _parse_spans(
                    _require_object(value, "line").get("spans"), "line.spans"
                )
            ),
            polygon=_parse_line_polygon(value, page),
        )
        for value in values
    )


def _parse_line_polygon(value: Any, page: ExtractedPage) -> tuple[float, ...]:
    polygon = _parse_polygon(
        _require_object(value, "line").get("polygon"), "line.polygon"
    )
    _validate_polygon_in_page(polygon, page, "line.polygon")
    return polygon


def _parse_paragraph(
    value: Any,
    page_spans: tuple[ContentSpan, ...],
    content_length: int,
    pages_by_number: dict[int, ExtractedPage],
) -> ExtractedParagraph:
    paragraph = _require_object(value, "paragraph")
    regions = tuple(
        _parse_bounding_region(
            region, "paragraph.boundingRegion", pages_by_number
        )
        for region in _required_list(paragraph, "boundingRegions")
    )
    role = paragraph.get("role")
    if role is not None and not isinstance(role, str):
        raise RuntimeError("Document Intelligence paragraph.role must be a string")
    return ExtractedParagraph(
        text=_require_text(paragraph.get("content"), "paragraph.content"),
        role=role,
        spans=_assign_span_pages(
            tuple(
            ContentSpan(offset, length, 0)
            for offset, length in _parse_spans(
                paragraph.get("spans"), "paragraph.spans"
            )
            ),
            page_spans,
            content_length,
            "paragraph.spans",
        ),
        bounding_regions=regions,
    )


def _parse_table(
    value: Any,
    page_spans: tuple[ContentSpan, ...],
    content_length: int,
    pages_by_number: dict[int, ExtractedPage],
) -> ExtractedTable:
    table = _require_object(value, "table")
    row_count = _require_int(table.get("rowCount"), "table.rowCount", minimum=1)
    column_count = _require_int(
        table.get("columnCount"), "table.columnCount", minimum=1
    )
    raw_cells: list[
        tuple[dict[str, Any], BoundingRegion, int, int, int, int, int]
    ] = []
    for value in _required_list(table, "cells"):
        cell = _require_object(value, "table.cell")
        regions = _required_list(cell, "boundingRegions")
        if len(regions) != 1:
            raise RuntimeError(
                "Document Intelligence table cell needs one bounding region"
            )
        region = _parse_bounding_region(
            regions[0], "table.cell.boundingRegion", pages_by_number
        )
        page_number = region.page_number
        row_index = _require_int(cell.get("rowIndex"), "table.cell.rowIndex")
        column_index = _require_int(
            cell.get("columnIndex"), "table.cell.columnIndex"
        )
        row_span = _require_int(
            cell.get("rowSpan", 1), "table.cell.rowSpan", minimum=1
        )
        column_span = _require_int(
            cell.get("columnSpan", 1), "table.cell.columnSpan", minimum=1
        )
        if row_index + row_span > row_count or column_index + column_span > column_count:
            raise RuntimeError("Document Intelligence table cell is outside table bounds")
        raw_cells.append(
            (
                cell,
                region,
                page_number,
                row_index,
                column_index,
                row_span,
                column_span,
            )
        )
    table_spans = _assign_span_pages(
        tuple(
            ContentSpan(offset, length, 0)
            for offset, length in _parse_spans(
                table.get("spans"), "table.spans", allow_empty=True
            )
        ),
        page_spans,
        content_length,
        "table.spans",
    )
    regions = tuple(
        _parse_bounding_region(
            value, "table.boundingRegion", pages_by_number
        )
        for value in _required_list(table, "boundingRegions")
    )
    table_pages = {span.page_number for span in table_spans}
    region_pages = {region.page_number for region in regions}
    if table_pages and not table_pages <= region_pages:
        raise RuntimeError(
            "Document Intelligence table spans and regions disagree"
        )
    cells: list[ExtractedTableCell] = []
    for (
        cell,
        region,
        page_number,
        row_index,
        column_index,
        row_span,
        column_span,
    ) in raw_cells:
        text = _require_text(
            cell.get("content"), "table.cell.content", allow_empty=True
        )
        spans = _assign_span_pages(
            tuple(
                ContentSpan(offset, length, page_number)
                for offset, length in _parse_spans(
                    cell.get("spans"),
                    "table.cell.spans",
                    allow_empty=not text,
                    allow_zero_length=not text,
                )
            ),
            page_spans,
            content_length,
            "table.cell.spans",
        )
        if text and any(span.length == 0 for span in spans):
            raise RuntimeError(
                "Document Intelligence non-empty table cell needs positive spans"
            )
        _validate_cell_in_table(spans, region, table_spans, regions)
        cells.append(
            ExtractedTableCell(
                page_number=page_number,
                row_index=row_index,
                column_index=column_index,
                row_span=row_span,
                column_span=column_span,
                text=text,
                polygon=region.polygon,
                spans=spans,
            )
        )
    if tuple(
        sorted(cells, key=lambda cell: (cell.row_index, cell.column_index))
    ) != tuple(cells):
        raise RuntimeError("Document Intelligence table cells must be row-ordered")
    _validate_table_occupancy(cells, row_count, column_count)
    return ExtractedTable(
        row_count=row_count,
        column_count=column_count,
        cells=tuple(cells),
        spans=table_spans,
        bounding_regions=regions,
    )


def _validate_table_occupancy(
    cells: list[ExtractedTableCell], row_count: int, column_count: int
) -> None:
    occupied: set[tuple[int, int]] = set()
    for cell in cells:
        for row in range(cell.row_index, cell.row_index + cell.row_span):
            for column in range(cell.column_index, cell.column_index + cell.column_span):
                coordinate = (row, column)
                if coordinate in occupied:
                    raise RuntimeError(
                        "Document Intelligence table cells must not overlap"
                    )
                occupied.add(coordinate)
    if occupied != {
        (row, column)
        for row in range(row_count)
        for column in range(column_count)
    }:
        raise RuntimeError("Document Intelligence table cells must cover the table")


def _validate_cell_in_table(
    cell_spans: tuple[ContentSpan, ...],
    cell_region: BoundingRegion,
    table_spans: tuple[ContentSpan, ...],
    table_regions: tuple[BoundingRegion, ...],
) -> None:
    same_page_regions = [
        region
        for region in table_regions
        if region.page_number == cell_region.page_number
    ]
    if not same_page_regions:
        raise RuntimeError(
            "Document Intelligence table cell page is outside table regions"
        )
    spans_are_owned = bool(cell_spans) and bool(table_spans)
    if spans_are_owned:
        for cell_span in cell_spans:
            if not any(
                table_span.page_number == cell_span.page_number
                and table_span.offset <= cell_span.offset
                and cell_span.offset + cell_span.length
                <= table_span.offset + table_span.length
                for table_span in table_spans
            ):
                raise RuntimeError(
                    "Document Intelligence table cell span is outside table spans"
                )
    if not spans_are_owned and not any(
        _polygons_overlap(region.polygon, cell_region.polygon)
        for region in same_page_regions
    ):
        raise RuntimeError(
            "Document Intelligence table cell has no table association"
        )


def _polygons_overlap(
    left: tuple[float, ...], right: tuple[float, ...]
) -> bool:
    left_x, left_y = left[::2], left[1::2]
    right_x, right_y = right[::2], right[1::2]
    return (
        max(min(left_x), min(right_x)) <= min(max(left_x), max(right_x))
        and max(min(left_y), min(right_y)) <= min(max(left_y), max(right_y))
    )


def _validate_page_spans(
    spans: tuple[ContentSpan, ...], content_length: int
) -> None:
    previous_end = 0
    for span in spans:
        if span.offset != previous_end or span.offset + span.length > content_length:
            raise RuntimeError(
                "Document Intelligence page spans must cover content in order"
            )
        previous_end = span.offset + span.length
    if previous_end != content_length:
        raise RuntimeError(
            "Document Intelligence page spans must cover all content"
        )


def _validate_nested_records(
    page_spans: tuple[ContentSpan, ...],
    lines: tuple[ExtractedLine, ...],
    paragraphs: tuple[ExtractedParagraph, ...],
    tables: tuple[ExtractedTable, ...],
    content_length: int,
) -> None:
    page_numbers = {span.page_number for span in page_spans}
    _validate_ordered_items(
        ((line.page_number, line.spans, "line") for line in lines),
        page_spans,
        content_length,
    )
    previous_offset = -1
    for paragraph in paragraphs:
        region_pages = {region.page_number for region in paragraph.bounding_regions}
        if not region_pages <= page_numbers:
            raise RuntimeError(
                "Document Intelligence paragraph references an unknown page"
            )
        parsed = _assign_span_pages(
            paragraph.spans, page_spans, content_length, "paragraph.spans"
        )
        if not {span.page_number for span in parsed} <= region_pages:
            raise RuntimeError(
                "Document Intelligence paragraph spans and regions disagree"
            )
        if parsed and parsed[0].offset < previous_offset:
            raise RuntimeError(
                "Document Intelligence paragraphs must be content-ordered"
            )
        if parsed:
            previous_offset = parsed[-1].offset
    previous_offset = -1
    for table in tables:
        if table.spans and table.spans[0].offset < previous_offset:
            raise RuntimeError(
                "Document Intelligence tables must be content-ordered"
            )
        if table.spans:
            previous_offset = table.spans[-1].offset
        _validate_ordered_items(
            ((cell.page_number, cell.spans, "table.cell") for cell in table.cells),
            page_spans,
            content_length,
        )


def _validate_ordered_items(
    values: Any,
    page_spans: tuple[ContentSpan, ...],
    content_length: int,
) -> None:
    prior_by_page: dict[int, int] = {}
    for page_number, spans, name in values:
        if page_number not in {span.page_number for span in page_spans}:
            raise RuntimeError(f"Document Intelligence {name} references an unknown page")
        parsed = _assign_span_pages(
            spans, page_spans, content_length, f"{name}.spans"
        )
        if any(span.page_number != page_number for span in parsed):
            raise RuntimeError(f"Document Intelligence {name} spans cross pages")
        if not parsed:
            continue
        if parsed[0].offset < prior_by_page.get(page_number, -1):
            raise RuntimeError(f"Document Intelligence {name}s must be content-ordered")
        prior_by_page[page_number] = parsed[0].offset


def _assign_span_pages(
    spans: tuple[ContentSpan, ...],
    page_spans: tuple[ContentSpan, ...],
    content_length: int,
    name: str,
) -> tuple[ContentSpan, ...]:
    assigned: list[ContentSpan] = []
    for span in spans:
        if span.offset + span.length > content_length:
            raise RuntimeError(f"Document Intelligence {name} exceeds content")
        matched = [
            page
            for page in page_spans
            if page.offset <= span.offset
            and (
                span.offset + span.length <= page.offset + page.length
                if span.length
                else span.offset < page.offset + page.length
            )
        ]
        if len(matched) != 1:
            raise RuntimeError(
                f"Document Intelligence {name} must belong to exactly one page"
            )
        assigned.append(
            ContentSpan(span.offset, span.length, matched[0].page_number)
        )
    if tuple(sorted(assigned, key=lambda span: span.offset)) != tuple(assigned):
        raise RuntimeError(f"Document Intelligence {name} must be offset-ordered")
    return tuple(assigned)
