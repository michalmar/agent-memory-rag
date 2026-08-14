from __future__ import annotations

import hashlib
import json
from pathlib import Path

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
) -> dict:
    page_lines = [
        fixture["lines"],
        fixture["page_two_lines"],
        [body],
        *(additional_body_pages or []),
    ]
    content = "\n".join(line for page in page_lines for line in page)
    offset = 0
    pages = []
    paragraphs = []
    for page_number, lines in enumerate(page_lines, 1):
        page_start = offset
        page_entries = []
        for index, text in enumerate(lines):
            actual = "Verze: 9" if conflict and page_number == 2 and text.startswith("Verze") else text
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
                    "role": "title" if page_number == 1 and actual == fixture["title"] else None,
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
                        "length": offset - page_start - (
                            1 if page_number < len(page_lines) else 0
                        ),
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
    assert fixture["page_two_lines"][-1] in directive.markdown
    assert directive.relations == ()
    assert any(item.code == "body_fallback_section" for item in directive.findings)


def test_metadata_rejects_conflicting_confirmation() -> None:
    fixture = json.loads((FIXTURES / "directive_v2_layout_a.json").read_text())
    extraction = DocumentIntelligenceExtractor._parse_result(_payload(fixture, conflict=True))

    with pytest.raises(DirectiveMetadataError, match="Conflicting core field: version"):
        parse_canonical(_source(), extraction, PROCESSING_HASH)


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
    ("fixture_name", "page_count", "line_count", "paragraph_count", "table_count"),
    [
        ("directive_v2_live_structure_1.json", 7, 263, 183, 3),
        ("directive_v2_live_structure_2.json", 10, 398, 257, 4),
    ],
)
def test_sanitized_live_layout_structures_preserve_di_shape(
    fixture_name: str,
    page_count: int,
    line_count: int,
    paragraph_count: int,
    table_count: int,
) -> None:
    payload = json.loads((FIXTURES / fixture_name).read_text(encoding="utf-8"))

    extraction = DocumentIntelligenceExtractor._parse_result(payload["response"])

    assert extraction.total_pages == page_count
    assert len(extraction.lines) == line_count
    assert len(extraction.paragraphs) == paragraph_count
    assert extraction.table_count == table_count
    assert all(line.polygon for line in extraction.lines)
    assert all(paragraph.bounding_regions for paragraph in extraction.paragraphs)
    assert all(cell.spans for table in extraction.tables for cell in table.cells)


def test_canonical_sections_use_stable_czech_slug_and_body_page() -> None:
    fixture = json.loads((FIXTURES / "directive_v2_layout_a.json").read_text())
    extraction = DocumentIntelligenceExtractor._parse_result(
        _payload(fixture, body="1. Účel a působnost\nText těla")
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
