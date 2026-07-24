from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from directive_ingestion.canonical import ParsedSection, parse_canonical
from directive_ingestion.chunking import chunk_sections
from directive_ingestion.document_intelligence import ExtractedDocument
from directive_ingestion.mandate_projection import parse_mandates
from directive_ingestion.reconcile import _build_manifest
from directive_ingestion.source import discover_pdfs
from directive_contracts import DirectiveSummary

ROOT = Path(__file__).parents[3]
FIXTURES = ROOT / "setup" / "directives"
PROCESSING_HASH = hashlib.sha256(b"test-processing").hexdigest()
TENANT_ID = "a7b1484c-f66a-496a-b1cf-35631a50396c"


def _fixture_directives():
    directives = []
    for source in discover_pdfs(FIXTURES / "pdf"):
        markdown_path = FIXTURES / "md" / f"{source.path.stem}.md"
        markdown = markdown_path.read_text(encoding="utf-8")
        extraction = ExtractedDocument(
            markdown=markdown,
            total_pages=3,
            page_spans=(),
            table_count=1,
        )
        directives.append(
            parse_canonical(source, extraction, PROCESSING_HASH)
        )
    return directives


def test_all_fixture_metadata_versions_and_relations_parse() -> None:
    directives = _fixture_directives()

    assert len(directives) == 7
    assert len({item.metadata.directive_id for item in directives}) == 4
    assert sum(item.metadata.is_current for item in directives) == 4
    assert all(len(item.sections) >= 2 for item in directives)
    assert {
        item.metadata.directive_version_id for item in directives
    } == {
        "30336958:v1",
        "36269153:v1",
        "36269153:v2",
        "72403881:v1",
        "72403881:v2",
        "95315332:v1",
        "95315332:v2",
    }

    accepted = [
        relation
        for item in directives
        for relation in item.relations
        if relation.status == "accepted"
    ]
    assert {
        (
            relation.source_directive_id,
            relation.target_directive_id,
            relation.relation_type,
        )
        for relation in accepted
    } == {
        ("72403881", "30336958", "sub_directive"),
        ("30336958", "72403881", "parent"),
    }
    assert len({relation.relation_id for relation in accepted}) == 1


def test_table_larger_than_limit_remains_atomic() -> None:
    table = "<table>" + "".join(
        f"<tr><td>row {number}</td><td>{'value ' * 20}</td></tr>"
        for number in range(10)
    ) + "</table>"
    section = ParsedSection(
        section_id="s0001-table",
        ordinal=1,
        number="1",
        title="Table",
        path=("Table",),
        page_from=1,
        page_to=2,
        content=table,
        token_count=500,
        content_hash=hashlib.sha256(table.encode()).hexdigest(),
    )

    chunks, findings = chunk_sections(
        "12345678:v1",
        hashlib.sha256(b"source").hexdigest(),
        hashlib.sha256(b"processing").hexdigest(),
        (section,),
        token_limit=80,
        overlap_tokens=10,
    )

    assert len(chunks) == 1
    assert chunks[0].content_kind == "table"
    assert chunks[0].content.count("<tr>") == 10
    assert [finding.code for finding in findings] == [
        "oversized_atomic_table"
    ]


def test_chunk_ids_are_deterministic() -> None:
    directive = _fixture_directives()[0]
    first, _ = chunk_sections(
        directive.metadata.directive_version_id,
        directive.metadata.source_hash,
        directive.metadata.processing_hash,
        directive.sections,
        token_limit=800,
        overlap_tokens=120,
    )
    second, _ = chunk_sections(
        directive.metadata.directive_version_id,
        directive.metadata.source_hash,
        directive.metadata.processing_hash,
        directive.sections,
        token_limit=800,
        overlap_tokens=120,
    )
    reprocessed, _ = chunk_sections(
        directive.metadata.directive_version_id,
        directive.metadata.source_hash,
        "e" * 64,
        directive.sections,
        token_limit=800,
        overlap_tokens=120,
    )

    assert [chunk.id for chunk in first] == [chunk.id for chunk in second]
    assert [chunk.content for chunk in first] == [
        chunk.content for chunk in second
    ]
    assert [chunk.id for chunk in first] != [
        chunk.id for chunk in reprocessed
    ]


def test_nondeterministic_summary_gets_distinct_generation_path() -> None:
    directive = _fixture_directives()[0]
    chunks, _ = chunk_sections(
        directive.metadata.directive_version_id,
        directive.metadata.source_hash,
        directive.metadata.processing_hash,
        directive.sections,
        token_limit=800,
        overlap_tokens=120,
    )

    def summary(text: str) -> DirectiveSummary:
        return DirectiveSummary(
            directive_id=directive.metadata.directive_id,
            directive_version_id=directive.metadata.directive_version_id,
            source_hash=directive.metadata.source_hash,
            summary=text,
            covered_section_ids=[
                section.section_id for section in directive.sections
            ],
            total_section_count=len(directive.sections),
            input_token_count=directive.total_tokens,
            strategy="full_document",
            model_deployment="test",
        )

    first = _build_manifest(directive, chunks, summary("first"))
    second = _build_manifest(directive, chunks, summary("second"))

    assert first.source_blob_name == second.source_blob_name
    assert first.manifest_blob_name != second.manifest_blob_name
    assert first.summary_blob_name != second.summary_blob_name


def test_mandate_csv_is_complete_sparse_snapshot() -> None:
    parsed = parse_mandates(
        FIXTURES / "mandatory" / "mand.csv",
        TENANT_ID,
        {"72403881", "95315332", "36269153", "30336958"},
    )

    assert len(parsed.assignments) == 5
    assert parsed.user_count == 2
    assert all(
        assignment.user_id.startswith(f"{TENANT_ID}:")
        for assignment in parsed.assignments
    )
    assert len(parsed.checksum) == 64


def test_mandate_csv_rejects_inconsistent_identity_mapping(
    tmp_path: Path,
) -> None:
    path = tmp_path / "mand.csv"
    path.write_text(
        "person@example.com,9254fe2a-17e2-4326-b724-095edc1d96a8,"
        "72403881,M\n"
        "person@example.com,bb0f05ed-8e4b-4784-926d-fe9e3bfb0cdf,"
        "95315332,M\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="more than one Entra object ID"):
        parse_mandates(
            path,
            TENANT_ID,
            {"72403881", "95315332"},
        )
