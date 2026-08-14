"""Canonical Markdown generation for page-aware directive extraction."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable

import tiktoken
from directive_contracts import DirectiveMetadata, DirectiveRelation, ReviewFinding

from .document_intelligence import ExtractedDocument
from .metadata import DirectiveMetadataCandidate, extract_metadata
from .source import SourceDocument

_MARKDOWN_HEADING = re.compile(
    r"^(?P<marks>#{2,6})\s+(?P<title>.+?)\s*$", re.MULTILINE
)
_NUMBERED_TITLE = re.compile(
    r"^(?P<number>\d+(?:\.\d+)*)(?:[.)])?\s+(?P<title>\S.*?)\s*$"
)
_TOKENIZER = tiktoken.get_encoding("o200k_base")


@dataclass(frozen=True, slots=True)
class ParsedSection:
    section_id: str
    ordinal: int
    number: str | None
    title: str
    path: tuple[str, ...]
    page_from: int
    page_to: int
    content: str
    token_count: int
    content_hash: str


@dataclass(frozen=True, slots=True)
class CanonicalDirective:
    metadata: DirectiveMetadata
    markdown: str
    control: dict[str, str]
    sections: tuple[ParsedSection, ...]
    relations: tuple[DirectiveRelation, ...]
    findings: tuple[ReviewFinding, ...]
    total_pages: int
    total_tokens: int
    metadata_candidate: DirectiveMetadataCandidate


@dataclass(frozen=True, slots=True)
class _BodyMarkdown:
    text: str
    source_offsets: tuple[int, ...]

    def offset_for_index(self, index: int) -> int:
        if not self.source_offsets:
            return 0
        return self.source_offsets[min(max(index, 0), len(self.source_offsets) - 1)]

    def index_for_source_offset(self, offset: int) -> int | None:
        for index, source_offset in enumerate(self.source_offsets):
            if source_offset >= offset:
                return index
        return None


def normalize_markdown(markdown: str) -> str:
    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n")
    normalized = "\n".join(line.rstrip() for line in normalized.splitlines())
    return normalized.strip() + "\n"


def parse_canonical(
    source: SourceDocument,
    extraction: ExtractedDocument,
    processing_hash: str,
) -> CanonicalDirective:
    candidate = extract_metadata(source, extraction, processing_hash)
    body = _body_markdown(extraction)
    metadata_section = _metadata_markdown(candidate)
    markdown = normalize_markdown(
        f"# {candidate.metadata.title}\n\n"
        f"## Metadata\n\n{metadata_section}\n\n"
        f"{body.text}"
    )
    sections, findings = _parse_sections(
        markdown, extraction, candidate.metadata.title, body
    )
    return CanonicalDirective(
        metadata=candidate.metadata,
        markdown=markdown,
        control={},
        sections=sections,
        relations=(),
        findings=tuple([*candidate.findings, *findings]),
        total_pages=extraction.total_pages,
        total_tokens=len(_TOKENIZER.encode(markdown)),
        metadata_candidate=candidate,
    )


def _metadata_markdown(candidate: DirectiveMetadataCandidate) -> str:
    metadata = candidate.metadata
    fields = (
        ("Directive ID", metadata.directive_id),
        ("Version", metadata.version_label),
        ("Effective from", metadata.effective_from.isoformat()),
        ("Language", metadata.language),
    )
    compact = "\n".join(f"- **{label}:** {value}" for label, value in fields)
    return f"{compact}\n\n{candidate.first_two_pages_markdown}"


def _body_markdown(extraction: ExtractedDocument) -> _BodyMarkdown:
    if extraction.total_pages < 3:
        return _BodyMarkdown("", ())
    body, offsets = _source_body(extraction)
    if not body:
        body, offsets = _line_body(extraction)
    decorative_offsets = _repeated_header_footer_offsets(extraction)
    output: list[str] = []
    output_offsets: list[int] = []
    position = 0
    for line in body.splitlines(keepends=True):
        line_text = line.rstrip("\n")
        line_offsets = offsets[position : position + len(line_text)]
        position += len(line)
        if _is_decorative_body_line(
            line_text,
            line_offsets[0] if line_offsets else -1,
            decorative_offsets,
        ):
            continue
        rendered = line_text
        rendered_offsets = line_offsets
        if not rendered:
            continue
        if output:
            output.append("\n")
            output_offsets.append(rendered_offsets[0])
        output.append(rendered)
        output_offsets.extend(rendered_offsets)
    return _BodyMarkdown(
        "".join(output).strip(),
        tuple(output_offsets[: len("".join(output).strip())]),
    )


def _source_body(extraction: ExtractedDocument) -> tuple[str, tuple[int, ...]]:
    spans = sorted(
        (
            span
            for span in extraction.content_spans
            if span.page_number >= 3
        ),
        key=lambda span: (span.offset, span.length),
    )
    pieces: list[str] = []
    offsets: list[int] = []
    previous_end = -1
    for span in spans:
        if span.offset < previous_end:
            continue
        raw = extraction.markdown[span.offset : span.offset + span.length]
        leading = len(raw) - len(raw.lstrip())
        text = raw.strip()
        if not text:
            continue
        if pieces:
            pieces.append("\n")
            offsets.append(span.offset + leading)
        pieces.append(text)
        offsets.extend(range(span.offset + leading, span.offset + leading + len(text)))
        previous_end = span.offset + span.length
    return "".join(pieces), tuple(offsets)


def _line_body(extraction: ExtractedDocument) -> tuple[str, tuple[int, ...]]:
    values = [
        line
        for line in extraction.lines
        if line.page_number >= 3 and line.text.strip()
    ]
    text = "\n".join(line.text for line in values)
    offsets: list[int] = []
    for index, line in enumerate(values):
        origin = line.spans[0].offset
        if index:
            offsets.append(origin)
        offsets.extend(range(origin, origin + len(line.text)))
    return text, tuple(offsets)


def _is_decorative_body_line(
    markdown: str, source_offset: int, repeated_edge_offsets: set[int]
) -> bool:
    counter = re.compile(r"^\s*(?:strana\s*)?\d+\s*(?:/|z)\s*\d+\s*$", re.IGNORECASE)
    return source_offset in repeated_edge_offsets or (
        bool(counter.fullmatch(markdown)) and source_offset in repeated_edge_offsets
    )


def _repeated_header_footer_offsets(extraction: ExtractedDocument) -> set[int]:
    candidates: dict[str, list[tuple[int, int]]] = {}
    for line in extraction.lines:
        if line.page_number < 3 or not line.polygon:
            continue
        page = extraction.page(line.page_number)
        vertical = line.polygon[1] / page.height
        if vertical > 0.12 and vertical < 0.88:
            continue
        value = _comparison_text(line.text)
        if value:
            candidates.setdefault(value, []).append(
                (line.page_number, line.spans[0].offset)
            )
    return {
            offset
            for entries in candidates.values()
            if len({page_number for page_number, _ in entries}) >= 2
            for _, offset in entries
    }


def _parse_sections(
    markdown: str,
    extraction: ExtractedDocument,
    title: str,
    body: _BodyMarkdown,
) -> tuple[tuple[ParsedSection, ...], list[ReviewFinding]]:
    body_start = markdown.find(body.text) if body.text else len(markdown)
    if body_start < 0:
        body_start = len(markdown)
    body_headings = _body_headings(body, extraction)
    findings: list[ReviewFinding] = []
    section_specs: list[tuple[int, int, str, int]] = [
        (0, body_start, "Metadata", 2)
    ]
    if body.text.strip():
        if body_headings:
            first_start = body_headings[0][0]
            if body.text[:first_start].strip():
                section_specs.append(
                    (body_start, body_start + first_start, "Preamble", 2)
                )
            for index, heading in enumerate(body_headings):
                end = (
                    body_headings[index + 1][0]
                    if index + 1 < len(body_headings)
                    else len(body.text)
                )
                section_specs.append(
                    (
                        body_start + heading[0],
                        body_start + end,
                        heading[1],
                        heading[2],
                    )
                )
        else:
            findings.append(
                ReviewFinding(
                    code="body_fallback_section",
                    severity="warning",
                    message="Body has no reliable headings; retained as one section",
                )
            )
            section_specs.append((body_start, len(markdown), title, 2))
    sections = tuple(
        _build_sections(markdown, extraction, section_specs, body_start, body)
    )
    return sections, findings


def _body_headings(
    body: _BodyMarkdown, extraction: ExtractedDocument
) -> list[tuple[int, str, int]]:
    headings = [
        (match.start(), match.group("title"), len(match.group("marks")))
        for match in _MARKDOWN_HEADING.finditer(body.text)
    ]
    role_headings = [
        paragraph
        for page_number in range(3, extraction.total_pages + 1)
        for paragraph in extraction.page_role_paragraphs(
            page_number, "sectionHeading"
        )
    ]
    for paragraph in role_headings:
        index = body.index_for_source_offset(paragraph.spans[0].offset)
        if index is None:
            continue
        if not any(position == index for position, _, _ in headings):
            headings.append((index, paragraph.text.strip(), 2))
    return sorted(headings, key=lambda item: item[0])


def _build_sections(
    markdown: str,
    extraction: ExtractedDocument,
    specs: list[tuple[int, int, str, int]],
    body_start: int,
    body: _BodyMarkdown,
) -> Iterable[ParsedSection]:
    hierarchy: dict[int, str] = {}
    for ordinal, (start, end, raw_title, level) in enumerate(specs):
        content = markdown[start:end].strip() + "\n"
        numbered = _NUMBERED_TITLE.fullmatch(raw_title)
        number = numbered.group("number") if numbered else None
        title = numbered.group("title") if numbered else raw_title
        hierarchy[level] = title
        for depth in tuple(hierarchy):
            if depth > level:
                del hierarchy[depth]
        path = tuple(hierarchy[depth] for depth in sorted(hierarchy))
        page_from = 1 if ordinal == 0 else extraction.page_for_offset(
            body.offset_for_index(start - body_start)
        )
        page_to = 2 if ordinal == 0 else extraction.page_for_offset(
            body.offset_for_index(end - body_start - 1)
        )
        if page_to < page_from:
            page_to = page_from
        slug = _slug(title)
        section_id = f"s{ordinal:04d}-{slug or 'section'}"
        yield ParsedSection(
            section_id=section_id,
            ordinal=ordinal,
            number=number,
            title=title,
            path=path,
            page_from=page_from,
            page_to=page_to,
            content=content,
            token_count=len(_TOKENIZER.encode(content)),
            content_hash=hashlib.sha256(content.encode()).hexdigest(),
        )


def _slug(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_value = decomposed.encode("ascii", "ignore").decode("ascii").casefold()
    return re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")[:60]


def _comparison_text(value: str) -> str:
    return " ".join(value.casefold().split())
