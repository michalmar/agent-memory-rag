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
    body: str | list[str] = "Bez nadpisu těla",
    additional_body_pages: list[list[str]] | None = None,
    section_headings: bool = True,
) -> dict:
    page_lines = [
        fixture["lines"],
        fixture["page_two_lines"],
        [body] if isinstance(body, str) else body,
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


def _wrapped_body_payload(fixture: dict) -> dict:
    header = "Sdílené záhlaví"
    body_pages = [
        [header, header, "Strana 3/4", "Obsah třetí stránky"],
        [header, header, "Strana 4/4", "Obsah čtvrté stránky"],
    ]
    payload = _payload(
        fixture,
        body=body_pages[0],
        additional_body_pages=[body_pages[1]],
        section_headings=False,
    )
    result = payload["analyzeResult"]
    page_offset = result["pages"][2]["spans"][0]["offset"]
    page_raw = [
        "\n".join([f"## {lines[0]}", *lines[1:]]) for lines in body_pages
    ]
    result["content"] = (
        result["content"][:page_offset] + "\n".join(page_raw)
    )
    paragraph_offset = len(fixture["lines"]) + len(fixture["page_two_lines"])
    for page_index, lines in enumerate(body_pages, start=2):
        raw = page_raw[page_index - 2]
        page = result["pages"][page_index]
        page["spans"] = [
            {
                "offset": page_offset,
                "length": len(raw) + (page_index == 2),
            }
        ]
        cursor = 0
        for line_index, text in enumerate(lines):
            raw_prefix = "## " if line_index == 0 else ""
            text_offset = page_offset + cursor + len(raw_prefix)
            polygon_y = 1 if line_index == 0 else 95 if line_index == 2 else 50
            polygon = [0, polygon_y, 10, polygon_y, 10, polygon_y + 1, 0, polygon_y + 1]
            page["lines"][line_index]["spans"] = [
                {"offset": text_offset, "length": len(text)}
            ]
            page["lines"][line_index]["polygon"] = polygon
            paragraph = result["paragraphs"][paragraph_offset]
            paragraph["spans"] = [{"offset": text_offset, "length": len(text)}]
            paragraph["boundingRegions"][0]["polygon"] = polygon
            paragraph_offset += 1
            cursor += len(raw_prefix) + len(text) + (line_index < len(lines) - 1)
        page_offset += len(raw) + 1
    return payload


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
            "boundingRegions": [
                {
                    "pageNumber": 2,
                    "polygon": [0, 0, 10, 0, 10, 100, 0, 100],
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


def test_metadata_keeps_equal_text_outside_the_matching_table_cell() -> None:
    fixture = json.loads((FIXTURES / "directive_v2_layout_a.json").read_text())
    fixture["page_two_lines"] = [
        "Před tabulkou",
        "Verze: 1.00",
        "Verze: 1.00",
        "Za tabulkou",
    ]
    payload = _payload(fixture)
    matching_line = payload["analyzeResult"]["pages"][1]["lines"][1]
    equal_line = payload["analyzeResult"]["pages"][1]["lines"][2]
    payload["analyzeResult"]["tables"] = [
        {
            "rowCount": 1,
            "columnCount": 1,
            "spans": [
                {
                    "offset": matching_line["spans"][0]["offset"],
                    "length": (
                        equal_line["spans"][0]["offset"]
                        + equal_line["spans"][0]["length"]
                        - matching_line["spans"][0]["offset"]
                    ),
                }
            ],
            "boundingRegions": [
                {"pageNumber": 2, "polygon": [0, 0, 10, 0, 10, 100, 0, 100]}
            ],
            "cells": [
                {
                    "content": matching_line["content"],
                    "rowIndex": 0,
                    "columnIndex": 0,
                    "spans": matching_line["spans"],
                    "boundingRegions": [
                        {
                            "pageNumber": 2,
                            "polygon": matching_line["polygon"],
                        }
                    ],
                }
            ],
        }
    ]
    extraction = DocumentIntelligenceExtractor._parse_result(payload)

    directive = parse_canonical(_source(), extraction, PROCESSING_HASH)

    page_two = directive.metadata_candidate.first_two_pages_markdown.split(
        "### Page 2\n\n", 1
    )[1]
    assert page_two.index("Před tabulkou") < page_two.index("| Verze: 1.00 |")
    assert page_two.index("| Verze: 1.00 |") < page_two.rindex("Verze: 1.00")
    assert page_two.rindex("Verze: 1.00") < page_two.index("Za tabulkou")


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


def test_document_intelligence_accepts_empty_table_cell_and_multispan_line() -> None:
    payload = _synthetic_structural_payload(3, 12, 8, 1)
    table = payload["analyzeResult"]["tables"][0]
    empty = table["cells"][1]
    empty["content"] = ""
    empty["spans"] = [
        {"offset": empty["spans"][0]["offset"], "length": 0}
    ]
    line = payload["analyzeResult"]["pages"][0]["lines"][0]
    interleaved_line = payload["analyzeResult"]["pages"][0]["lines"][2]
    line["spans"] = [
        {"offset": line["spans"][0]["offset"], "length": 2},
        {"offset": interleaved_line["spans"][0]["offset"], "length": 2},
    ]

    extraction = DocumentIntelligenceExtractor._parse_result(payload)

    assert extraction.tables[0].cells[1].text == ""
    assert extraction.tables[0].cells[1].spans[0].length == 0
    assert len(extraction.lines[0].spans) == 2
    assert extraction.lines[0].spans[1].offset > extraction.lines[1].spans[0].offset


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload["analyzeResult"]["tables"][0]["cells"][0][
            "spans"
        ][0].update(offset=0),
        lambda payload: payload["analyzeResult"]["tables"][0]["cells"][0][
            "boundingRegions"
        ][0].update(pageNumber=2),
    ],
)
def test_document_intelligence_rejects_cell_outside_table_parent(mutation) -> None:
    payload = _synthetic_structural_payload(3, 12, 8, 1)
    mutation(payload)

    with pytest.raises(RuntimeError, match="table cell"):
        DocumentIntelligenceExtractor._parse_result(payload)


def test_document_intelligence_rejects_same_page_cell_span_outside_parent() -> None:
    payload = _synthetic_structural_payload(3, 12, 8, 1)
    table = payload["analyzeResult"]["tables"][0]
    cell = table["cells"][0]
    cell["spans"][0]["offset"] = table["spans"][0]["offset"] - 1

    with pytest.raises(RuntimeError, match="outside table spans"):
        DocumentIntelligenceExtractor._parse_result(payload)


def test_document_intelligence_rejects_same_page_cell_polygon_outside_parent() -> None:
    payload = _synthetic_structural_payload(3, 12, 8, 1)
    payload["analyzeResult"]["tables"][0]["cells"][0]["boundingRegions"][0][
        "polygon"
    ] = [10.051, 0, 11, 0, 11, 1, 10.051, 1]

    with pytest.raises(RuntimeError, match="polygon is outside"):
        DocumentIntelligenceExtractor._parse_result(payload)


@pytest.mark.parametrize(
    ("left_edge", "matches"),
    [(-0.05, True), (-0.051, False)],
)
def test_document_intelligence_allows_only_shared_edge_rounding(
    left_edge: float, matches: bool
) -> None:
    payload = _synthetic_structural_payload(3, 12, 8, 1)
    payload["analyzeResult"]["tables"][0]["cells"][0]["boundingRegions"][0][
        "polygon"
    ] = [left_edge, 0, 1, 0, 1, 1, left_edge, 1]

    if matches:
        DocumentIntelligenceExtractor._parse_result(payload)
    else:
        with pytest.raises(RuntimeError, match="polygon is outside"):
            DocumentIntelligenceExtractor._parse_result(payload)


def test_document_intelligence_accepts_merged_cells() -> None:
    payload = _synthetic_structural_payload(3, 12, 8, 1)
    table = payload["analyzeResult"]["tables"][0]
    first, second = table["cells"]
    table["rowCount"] = 2
    table["columnCount"] = 2
    first["columnSpan"] = 2
    second["rowIndex"] = 1
    second["columnIndex"] = 0
    table["cells"].append(
        {
            **second,
            "columnIndex": 1,
        }
    )

    extraction = DocumentIntelligenceExtractor._parse_result(payload)

    assert extraction.tables[0].cells[0].column_span == 2


def test_document_intelligence_rejects_span_crossing_root_page() -> None:
    fixture = json.loads((FIXTURES / "directive_v2_layout_a.json").read_text())
    payload = _payload(fixture)
    line = payload["analyzeResult"]["pages"][0]["lines"][-1]
    root_span = payload["analyzeResult"]["pages"][0]["spans"][0]
    line["spans"][0]["length"] = (
        root_span["offset"] + root_span["length"] - line["spans"][0]["offset"] + 1
    )

    with pytest.raises(RuntimeError, match="exactly one page"):
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
    nested_spans = [
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
    assert all(
        sum(
            root.offset <= span.offset
            and span.offset + span.length <= root.offset + root.length
            for root in extraction.content_spans
        )
        == 1
        for span in nested_spans
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
                "boundingRegions": [
                    {
                        "pageNumber": page_number,
                        "polygon": [0, 0, 10, 0, 10, 100, 0, 100],
                    }
                    for page_number in sorted(
                        {cell["pageNumber"] for cell in cells}
                    )
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


def test_repeated_edge_header_does_not_remove_equal_mid_page_body_text() -> None:
    fixture = json.loads((FIXTURES / "directive_v2_layout_a.json").read_text())
    payload = _wrapped_body_payload(fixture)
    directive = parse_canonical(
        _source(),
        DocumentIntelligenceExtractor._parse_result(payload),
        PROCESSING_HASH,
    )

    assert directive.markdown.count("Sdílené záhlaví") == 2
    assert "Strana 3/4" not in directive.markdown
    assert "Strana 4/4" not in directive.markdown
