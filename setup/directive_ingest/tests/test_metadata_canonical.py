from __future__ import annotations

import hashlib
import json
from asyncio import run
from pathlib import Path

import httpx
import pytest

from directive_ingestion.canonical import parse_canonical
from directive_ingestion.document_intelligence import DocumentIntelligenceExtractor
from directive_ingestion.metadata import DirectiveMetadataError, normalize_label
from directive_ingestion.source import SourceDocument, SourceProvenance

FIXTURES = Path(__file__).parent / "fixtures"
PROCESSING_HASH = hashlib.sha256(b"processing").hexdigest()


def _payload(
    fixture: dict,
    *,
    conflict: bool = False,
    body: str = "Bez nadpisu těla",
    additional_body_pages: list[list[str]] | None = None,
    section_headings: bool = True,
) -> dict:
    page_lines = [
        fixture["lines"],
        fixture["page_two_lines"],
        [body],
        *(additional_body_pages or []),
    ]
    if conflict:
        page_lines[1] = [
            "Verze: 9" if text.startswith("Verze") else text
            for text in page_lines[1]
        ]
    content = "\n".join(line for page in page_lines for line in page)
    offset = 0
    pages = []
    paragraphs = []
    for page_number, lines in enumerate(page_lines, 1):
        page_start = offset
        page_entries = []
        for index, text in enumerate(lines):
            actual = text
            text_offset = offset
            offset += len(actual)
            if not (
                page_number == len(page_lines) and index == len(lines) - 1
            ):
                offset += 1
            polygon = [0, float(index + 1), 10, float(index + 1), 10, float(index + 2), 0, float(index + 2)]
            page_entries.append(
                {"content": actual, "spans": [{"offset": text_offset, "length": len(actual)}], "polygon": polygon}
            )
            paragraphs.append(
                {
                    "content": actual,
                    "role": (
                        "title"
                        if page_number == 1 and actual == fixture["title"]
                        else (
                            "sectionHeading"
                            if (
                                section_headings
                                and page_number >= 3
                                and actual.startswith("1. ")
                            )
                            else None
                        )
                    ),
                    "spans": [{"offset": text_offset, "length": len(actual)}],
                    "boundingRegions": [{"pageNumber": page_number, "polygon": polygon}],
                }
            )
        pages.append(
            {
                "pageNumber": page_number,
                "width": 100,
                "height": 100,
                "unit": "pixel",
                "spans": [
                    {
                        "offset": page_start,
                        "length": offset - page_start,
                    }
                ],
                "lines": page_entries,
            }
        )
    return {
        "analyzeResult": {
            "content": content,
            "pages": pages,
            "paragraphs": paragraphs,
            "tables": [],
        }
    }


def _source() -> SourceDocument:
    return SourceDocument(
        source_name="neutrální dokument.pdf",
        source_hash="a" * 64,
        content=b"%PDF-test",
        _provenance=SourceProvenance(kind="test", locator="test"),
    )


@pytest.mark.parametrize("fixture_name", ["directive_v2_layout_a.json", "directive_v2_layout_b.json"])
def test_sanitized_layout_fixtures_extract_metadata_and_preserve_administration(
    fixture_name: str,
) -> None:
    fixture = json.loads((FIXTURES / fixture_name).read_text(encoding="utf-8"))
    extraction = DocumentIntelligenceExtractor._parse_result(_payload(fixture))

    directive = parse_canonical(_source(), extraction, PROCESSING_HASH)

    assert directive.metadata.directive_id == fixture["expected"]["directive_id"]
    assert directive.metadata.version_label == fixture["expected"]["version_label"]
    assert directive.metadata.effective_from.isoformat() == fixture["expected"]["effective_from"]
    assert directive.metadata.is_valid is True
    assert directive.metadata.language == "cs"
    assert (
        directive.metadata_candidate.first_two_pages_markdown
        == fixture["expected"]["first_two_pages_markdown"]
    )
    assert fixture["page_two_lines"][-1] in directive.markdown
    assert directive.relations == ()
    assert any(item.code == "body_fallback_section" for item in directive.findings)


def test_metadata_rejects_conflicting_confirmation() -> None:
    fixture = json.loads((FIXTURES / "directive_v2_layout_a.json").read_text())
    extraction = DocumentIntelligenceExtractor._parse_result(_payload(fixture, conflict=True))

    with pytest.raises(DirectiveMetadataError, match="Conflicting core field: version"):
        parse_canonical(_source(), extraction, PROCESSING_HASH)


def test_page_one_cover_spelling_wins_over_equivalent_footer_and_page_two() -> None:
    fixture = json.loads((FIXTURES / "directive_v2_layout_a.json").read_text())
    fixture["lines"][1] = "Verze: 01.0"
    fixture["page_two_lines"][0] = "Verze: 1.00"
    extraction = DocumentIntelligenceExtractor._parse_result(_payload(fixture))

    directive = parse_canonical(_source(), extraction, PROCESSING_HASH)

    assert directive.metadata.version_label == "01.0"
    assert directive.metadata.directive_version_id == "MP-TEST/01:v1"


def test_metadata_table_is_interleaved_and_only_overlapping_lines_are_deduplicated() -> None:
    fixture = json.loads((FIXTURES / "directive_v2_layout_a.json").read_text())
    payload = _payload(fixture)
    first, second = payload["analyzeResult"]["pages"][1]["lines"][:2]
    payload["analyzeResult"]["tables"] = [
        {
            "rowCount": 1,
            "columnCount": 2,
            "spans": [
                {
                    "offset": first["spans"][0]["offset"],
                    "length": (
                        second["spans"][0]["offset"]
                        + second["spans"][0]["length"]
                        - first["spans"][0]["offset"]
                    ),
                }
            ],
            "cells": [
                {
                    "content": line["content"],
                    "rowIndex": 0,
                    "columnIndex": index,
                    "spans": line["spans"],
                    "boundingRegions": [{"pageNumber": 2, "polygon": line["polygon"]}],
                }
                for index, line in enumerate((first, second))
            ],
        }
    ]
    extraction = DocumentIntelligenceExtractor._parse_result(payload)

    directive = parse_canonical(_source(), extraction, PROCESSING_HASH)

    page_two = directive.metadata_candidate.first_two_pages_markdown.split(
        "### Page 2\n\n", 1
    )[1]
    assert page_two.index("| Verze: 1.00 | Účinnost od: 01. 02. 2026 |") < page_two.index(
        "Schválil: Testovací role"
    )
    assert page_two.count("Verze: 1.00") == 1


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Účinnost od", "ucinnost od"),
        ("METODICKÝ POKYN Č.", "metodicky pokyn c"),
        ("Účinnost   od:", "ucinnost od"),
    ],
)
def test_label_normalization(raw: str, expected: str) -> None:
    assert normalize_label(raw) == expected


def test_document_intelligence_rejects_malformed_spans() -> None:
    fixture = json.loads((FIXTURES / "directive_v2_layout_a.json").read_text())
    payload = _payload(fixture)
    payload["analyzeResult"]["pages"][0]["spans"][0]["length"] = -1

    with pytest.raises(RuntimeError, match="page.spans"):
        DocumentIntelligenceExtractor._parse_result(payload)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload["analyzeResult"]["pages"][0]["lines"][0][
            "spans"
        ][0].update(offset=999),
        lambda payload: payload["analyzeResult"]["paragraphs"][0][
            "boundingRegions"
        ][0].update(pageNumber=99),
    ],
)
def test_document_intelligence_rejects_invalid_nested_page_references(
    mutation,
) -> None:
    fixture = json.loads((FIXTURES / "directive_v2_layout_a.json").read_text())
    payload = _payload(fixture)
    mutation(payload)

    with pytest.raises(RuntimeError):
        DocumentIntelligenceExtractor._parse_result(payload)


def test_document_intelligence_rejects_overlapping_table_cells() -> None:
    payload = _synthetic_structural_payload(3, 12, 8, 1)
    payload["analyzeResult"]["tables"][0]["cells"][1]["columnIndex"] = 0

    with pytest.raises(RuntimeError, match="must not overlap"):
        DocumentIntelligenceExtractor._parse_result(payload)


def test_document_intelligence_uses_acquired_bearer_token() -> None:
    class Credential:
        async def get_token(self, scope: str):
            assert scope == "https://cognitiveservices.azure.com/.default"
            return type("Token", (), {"token": "test-token"})()

    class Extractor(DocumentIntelligenceExtractor):
        async def _request_with_retry(self, method, _url, **kwargs):
            assert method == "POST"
            authorization = kwargs["headers"]["Authorization"]
            assert authorization.startswith("Bearer ")
            assert authorization.endswith("test-token")
            return httpx.Response(200, json=_synthetic_structural_payload(1, 2, 1, 0))

    async def extract() -> int:
        extractor = Extractor(
            "https://document.example.test", "2024-11-30", Credential()
        )
        try:
            return (await extractor.extract(b"%PDF-test")).total_pages
        finally:
            await extractor.close()

    assert run(extract()) == 1


@pytest.mark.parametrize(
    ("page_count", "line_count", "paragraph_count", "table_count"),
    [(7, 263, 183, 3), (10, 398, 257, 4)],
)
def test_synthetic_structural_layouts_are_span_valid(
    page_count: int,
    line_count: int,
    paragraph_count: int,
    table_count: int,
) -> None:
    payload = _synthetic_structural_payload(
        page_count, line_count, paragraph_count, table_count
    )
    extraction = DocumentIntelligenceExtractor._parse_result(payload)

    assert extraction.total_pages == page_count
    assert len(extraction.lines) == line_count
    assert len(extraction.paragraphs) == paragraph_count
    assert extraction.table_count == table_count
    assert all(line.polygon for line in extraction.lines)
    assert all(paragraph.bounding_regions for paragraph in extraction.paragraphs)
    assert all(cell.spans for table in extraction.tables for cell in table.cells)
    assert all(
        extraction.markdown[span.offset : span.offset + span.length]
        for span in [
            *extraction.content_spans,
            *(span for line in extraction.lines for span in line.spans),
            *(span for paragraph in extraction.paragraphs for span in paragraph.spans),
            *(span for table in extraction.tables for span in table.spans),
            *(
                span
                for table in extraction.tables
                for cell in table.cells
                for span in cell.spans
            ),
        ]
    )


def _synthetic_structural_payload(
    page_count: int,
    line_count: int,
    paragraph_count: int,
    table_count: int,
) -> dict:
    line_counts = [
        line_count // page_count + (index < line_count % page_count)
        for index in range(page_count)
    ]
    pages: list[dict] = []
    all_lines: list[dict] = []
    content_parts: list[str] = []
    offset = 0
    for page_number, count in enumerate(line_counts, 1):
        page_offset = offset
        page_lines = []
        for line_number in range(count):
            text = f"Řádek {page_number}-{line_number:03d}"
            if content_parts:
                content_parts.append("\n")
                offset += 1
            line_offset = offset
            content_parts.append(text)
            offset += len(text)
            item = {
                "content": text,
                "spans": [{"offset": line_offset, "length": len(text)}],
                "polygon": [0, line_number, 10, line_number, 10, line_number + 1, 0, line_number + 1],
            }
            page_lines.append(item)
            all_lines.append({**item, "pageNumber": page_number})
        pages.append(
            {
                "pageNumber": page_number,
                "width": 100,
                "height": 100,
                "unit": "pixel",
                "spans": [{"offset": page_offset, "length": offset - page_offset}],
                "lines": page_lines,
            }
        )
    paragraphs = [
        {
            "content": line["content"],
            "spans": line["spans"],
            "boundingRegions": [
                {"pageNumber": line["pageNumber"], "polygon": line["polygon"]}
            ],
        }
        for line in all_lines[:paragraph_count]
    ]
    tables = []
    table_start = line_count - table_count * 2
    for index in range(table_count):
        cells = all_lines[table_start + index * 2 : table_start + index * 2 + 2]
        tables.append(
            {
                "rowCount": 1,
                "columnCount": 2,
                "spans": [
                    {
                        "offset": cells[0]["spans"][0]["offset"],
                        "length": (
                            cells[1]["spans"][0]["offset"]
                            + cells[1]["spans"][0]["length"]
                            - cells[0]["spans"][0]["offset"]
                        ),
                    }
                ],
                "cells": [
                    {
                        "content": cell["content"],
                        "rowIndex": 0,
                        "columnIndex": column,
                        "spans": cell["spans"],
                        "boundingRegions": [
                            {
                                "pageNumber": cell["pageNumber"],
                                "polygon": cell["polygon"],
                            }
                        ],
                    }
                    for column, cell in enumerate(cells)
                ],
            }
        )
    return {
        "analyzeResult": {
            "content": "".join(content_parts),
            "pages": pages,
            "paragraphs": paragraphs,
            "tables": tables,
        }
    }


def test_canonical_sections_use_stable_czech_slug_and_body_page() -> None:
    fixture = json.loads((FIXTURES / "directive_v2_layout_a.json").read_text())
    extraction = DocumentIntelligenceExtractor._parse_result(
        _payload(fixture, body="1. Účel a působnost")
    )

    directive = parse_canonical(_source(), extraction, PROCESSING_HASH)

    assert [section.section_id for section in directive.sections] == [
        "s0000-metadata",
        "s0001-ucel-a-pusobnost",
    ]
    assert directive.sections[1].page_from == 3


def test_body_page_mapping_survives_counter_removal_and_heading_rewrite() -> None:
    fixture = json.loads((FIXTURES / "directive_v2_layout_a.json").read_text())
    extraction = DocumentIntelligenceExtractor._parse_result(
        _payload(
            fixture,
            body="Strana 3/4\nÚvod bez nadpisu",
            additional_body_pages=[["1. Účel", "Obsah kapitoly"]],
        )
    )

    directive = parse_canonical(_source(), extraction, PROCESSING_HASH)

    section = next(item for item in directive.sections if item.title == "Účel")
    assert (section.page_from, section.page_to) == (4, 4)


def test_body_preamble_and_numbered_list_are_preserved_without_heading_promotion() -> None:
    fixture = json.loads((FIXTURES / "directive_v2_layout_a.json").read_text())
    extraction = DocumentIntelligenceExtractor._parse_result(
        _payload(
            fixture,
            body="Úvodní text\n1. První položka\n2. Druhá položka",
            section_headings=False,
        )
    )

    directive = parse_canonical(_source(), extraction, PROCESSING_HASH)

    assert [section.title for section in directive.sections] == [
        "Metadata",
        "Bezpečný metodický pokyn",
    ]
    assert "1. První položka" in directive.sections[1].content
    assert "2. Druhá položka" in directive.sections[1].content


def test_body_preamble_is_chunked_before_first_heading() -> None:
    fixture = json.loads((FIXTURES / "directive_v2_layout_a.json").read_text())
    extraction = DocumentIntelligenceExtractor._parse_result(
        _payload(
            fixture,
            body="Úvodní text\n## Hlavní část\nObsah kapitoly",
            section_headings=False,
        )
    )

    directive = parse_canonical(_source(), extraction, PROCESSING_HASH)

    assert [section.title for section in directive.sections] == [
        "Metadata",
        "Preamble",
        "Hlavní část",
    ]
    assert directive.sections[1].content == "Úvodní text\n"
