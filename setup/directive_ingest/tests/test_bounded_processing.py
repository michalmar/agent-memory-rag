from __future__ import annotations

import hashlib
from datetime import date
from types import SimpleNamespace

import pytest
import tiktoken
from directive_contracts import DirectiveMetadata

from directive_ingestion.canonical import ParsedSection, ProvenanceSegment
from directive_ingestion.chunking import chunk_sections
from directive_ingestion.summaries import SummaryGenerator

_TOKENIZER = tiktoken.get_encoding("o200k_base")


def _section(
    content: str,
    section_id: str = "s0001-table",
) -> ParsedSection:
    third = len(content) // 3
    provenance = (
        ProvenanceSegment(0, third, 0, third, 3),
        ProvenanceSegment(third, third * 2, third, third * 2, 4),
        ProvenanceSegment(
            third * 2,
            len(content),
            third * 2,
            len(content),
            5,
        ),
    )
    return ParsedSection(
        section_id=section_id,
        ordinal=1,
        number="1",
        title="Table",
        path=("Table",),
        page_from=3,
        page_to=5,
        content=content,
        token_count=len(_TOKENIZER.encode(content)),
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        provenance=provenance,
    )


def test_large_table_is_partitioned_without_oversized_chunks() -> None:
    rows = [f"| item-{index:03d} | value-{index:03d} |" for index in range(60)]
    content = "| Item | Value |\n|---|---|\n" + "\n".join(rows) + "\n"
    chunks, findings = chunk_sections(
        "12345678:v1",
        "a" * 64,
        "b" * 64,
        (_section(content),),
        token_limit=90,
        overlap_tokens=10,
        table_max_rows_per_part=8,
        table_max_chars_per_part=1200,
    )

    assert len(chunks) > 1
    assert all(len(_TOKENIZER.encode(chunk.content)) <= 90 for chunk in chunks)
    assert all("| Item | Value |" in chunk.content for chunk in chunks)
    assert all("table-continuation" in chunk.content for chunk in chunks)
    assert any(chunk.page_to < 5 for chunk in chunks)
    assert {finding.code for finding in findings} == {"partitioned_table"}
    for row in rows:
        assert sum(row in chunk.content for chunk in chunks) == 1


def test_table_fallback_reflows_for_exact_marker_budget() -> None:
    content = (
        "<table><tr><td>"
        + " ".join(f"value-{index:03d}" for index in range(200))
        + "</td></tr></table>"
    )
    chunks, findings = chunk_sections(
        "12345678:v1",
        "a" * 64,
        "b" * 64,
        (_section(content),),
        token_limit=40,
        overlap_tokens=5,
        table_max_rows_per_part=25,
        table_max_chars_per_part=300,
    )

    assert len(chunks) > 1
    assert all(len(_TOKENIZER.encode(chunk.content)) <= 40 for chunk in chunks)
    assert all(len(chunk.content) <= 300 for chunk in chunks)
    assert all("representation=fragment" in chunk.content for chunk in chunks)
    assert {finding.code for finding in findings} == {"partitioned_table"}


def test_near_limit_prose_drops_overlap_instead_of_failing() -> None:
    token_limit = 80
    overlap_tokens = 20

    def near_limit_block(word: str) -> str:
        for count in range(1, token_limit * 2):
            value = " ".join([word] * count)
            tokens = len(_TOKENIZER.encode(value))
            if token_limit - overlap_tokens < tokens < token_limit:
                return value
        raise AssertionError("could not construct a near-limit prose block")

    first = near_limit_block("alpha")
    second = near_limit_block("beta")
    chunks, _ = chunk_sections(
        "12345678:v1",
        "a" * 64,
        "b" * 64,
        (_section(f"{first}\n\n{second}", "s0001-prose"),),
        token_limit=token_limit,
        overlap_tokens=overlap_tokens,
        table_max_rows_per_part=25,
        table_max_chars_per_part=12000,
    )

    assert len(chunks) == 2
    assert all(
        len(_TOKENIZER.encode(chunk.content)) <= token_limit
        for chunk in chunks
    )
    assert second in chunks[1].content


class _Responses:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        output_limit = int(kwargs["max_output_tokens"])
        return SimpleNamespace(
            output_text=" ".join("detail" for _ in range(min(output_limit, 40)))
        )


@pytest.mark.asyncio
async def test_summary_hierarchy_never_exceeds_hard_request_limit() -> None:
    responses = _Responses()
    sections = tuple(
        _section(
            f"## Section {index}\n"
            + " ".join(f"policy-{index}-{value}" for value in range(90)),
            f"s{index:04d}-section",
        )
        for index in range(12)
    )
    metadata = DirectiveMetadata(
        directive_id="12345678",
        directive_version_id="12345678:v1",
        version_label="1",
        title="Bounded summary",
        status="Current",
        is_current=True,
        is_valid=True,
        effective_from=date(2026, 1, 1),
        source_filename="directive.pdf",
        source_hash="a" * 64,
        processing_hash="b" * 64,
    )
    directive = SimpleNamespace(
        metadata=metadata,
        sections=sections,
        markdown="\n\n".join(section.content for section in sections),
        total_tokens=sum(section.token_count for section in sections),
    )
    generator = SummaryGenerator(
        SimpleNamespace(responses=responses),
        "summary-model",
        full_document_tokens=100,
        batch_tokens=120,
        max_input_tokens=500,
        max_output_tokens=128,
        concurrency=3,
    )

    summary = await generator.summarize(directive)

    assert summary.strategy == "section_batches"
    assert summary.covered_section_ids == [
        section.section_id for section in sections
    ]
    assert len(responses.calls) > len(sections)
    for call in responses.calls:
        messages = call["input"]
        prompt = "\n".join(
            item["text"]
            for message in messages
            for item in message["content"]
        )
        assert len(_TOKENIZER.encode(prompt)) <= 500
