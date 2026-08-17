"""Canonical Markdown generation for page-aware directive extraction."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable

import tiktoken
from directive_contracts import DirectiveMetadata, DirectiveRelation, ReviewFinding

from .document_intelligence import ExtractedDocument, ExtractedLine
from .metadata import DirectiveMetadataCandidate, extract_metadata
from .source import SourceDocument

_MARKDOWN_HEADING = re.compile(
    r"^(?P<marks>#{2,6})\s+(?P<title>.+?)\s*$", re.MULTILINE
)
_NUMBERED_TITLE = re.compile(
    r"^(?P<number>\d+(?:\.\d+)*)(?:[.)])?\s+(?P<title>\S.*?)\s*$"
)
_PAGE_COUNTER = re.compile(
    r"^\s*(?:strana\s*)?\d+\s*(?:/|z)\s*\d+\s*$", re.IGNORECASE
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
    provenance: tuple["ProvenanceSegment", ...] = ()

    def page_range_for(self, start: int, end: int) -> tuple[int, int]:
        pages = [
            segment.page_number
            for segment in self.provenance
            if segment.output_start < end and segment.output_end > start
        ]
        if not pages:
            return self.page_from, self.page_to
        return min(pages), max(pages)


@dataclass(frozen=True, slots=True)
class ProvenanceSegment:
    output_start: int
    output_end: int
    source_start: int
    source_end: int
    page_number: int

    def __post_init__(self) -> None:
        if (
            self.output_start < 0
            or self.output_end <= self.output_start
            or self.source_start < 0
            or self.source_end <= self.source_start
            or self.page_number < 1
        ):
            raise ValueError("Canonical provenance segment is invalid")


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
    provenance: tuple[ProvenanceSegment, ...]

    def offset_for_index(self, index: int) -> int:
        if not self.provenance:
            return 0
        bounded = min(max(index, 0), max(len(self.text) - 1, 0))
        for segment in self.provenance:
            if segment.output_start <= bounded < segment.output_end:
                source_length = segment.source_end - segment.source_start
                output_length = segment.output_end - segment.output_start
                relative = bounded - segment.output_start
                return segment.source_start + min(
                    source_length - 1,
                    int(relative * source_length / output_length),
                )
        return self.provenance[-1].source_end - 1

    def index_for_source_offset(self, offset: int) -> int | None:
        for segment in self.provenance:
            if offset <= segment.source_start:
                return segment.output_start
            if segment.source_start < offset < segment.source_end:
                source_length = segment.source_end - segment.source_start
                output_length = segment.output_end - segment.output_start
                return segment.output_start + min(
                    output_length - 1,
                    int(
                        (offset - segment.source_start)
                        * output_length
                        / source_length
                    ),
                )
        return None

    def segments_for_range(
        self,
        start: int,
        end: int,
        *,
        output_start: int = 0,
    ) -> tuple[ProvenanceSegment, ...]:
        values: list[ProvenanceSegment] = []
        for segment in self.provenance:
            overlap_start = max(start, segment.output_start)
            overlap_end = min(end, segment.output_end)
            if overlap_start >= overlap_end:
                continue
            source_length = segment.source_end - segment.source_start
            output_length = segment.output_end - segment.output_start
            source_start = segment.source_start + int(
                (overlap_start - segment.output_start)
                * source_length
                / output_length
            )
            source_end = segment.source_start + max(
                1,
                int(
                    (overlap_end - segment.output_start)
                    * source_length
                    / output_length
                ),
            )
            values.append(
                ProvenanceSegment(
                    output_start=(
                        output_start + overlap_start - start
                    ),
                    output_end=output_start + overlap_end - start,
                    source_start=source_start,
                    source_end=min(segment.source_end, source_end),
                    page_number=segment.page_number,
                )
            )
        return tuple(values)


def normalize_markdown(markdown: str) -> str:
    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n")
    normalized = "\n".join(line.rstrip() for line in normalized.splitlines())
    return normalized.strip() + "\n"


def parse_canonical(
    source: SourceDocument,
    extraction: ExtractedDocument,
    processing_hash: str,
    *,
    metadata_candidate: DirectiveMetadataCandidate | None = None,
) -> CanonicalDirective:
    candidate = metadata_candidate or extract_metadata(
        source,
        extraction,
        processing_hash,
    )
    if candidate.metadata.source_hash != source.source_hash:
        raise ValueError("Metadata candidate does not match the source identity")
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
    body = _source_body(extraction)
    if not body.text:
        body = _line_body(extraction)
    repeated_edge_offsets = _repeated_header_footer_offsets(extraction)
    counter_offsets = _edge_counter_offsets(extraction)
    output: list[str] = []
    output_provenance: list[ProvenanceSegment] = []
    position = 0
    output_length = 0
    for line in body.text.splitlines(keepends=True):
        line_text = line.rstrip("\n")
        line_start = position
        line_end = position + len(line_text)
        position += len(line)
        if _is_decorative_body_line(
            body,
            line_start,
            line_end,
            repeated_edge_offsets,
            counter_offsets,
        ):
            continue
        rendered = line_text
        if not rendered:
            continue
        if output:
            output.append("\n")
            origin = body.offset_for_index(line_start)
            page = extraction.page_for_offset(origin)
            output_provenance.append(
                ProvenanceSegment(
                    output_start=output_length,
                    output_end=output_length + 1,
                    source_start=origin,
                    source_end=origin + 1,
                    page_number=page,
                )
            )
            output_length += 1
        output.append(rendered)
        output_provenance.extend(
            body.segments_for_range(
                line_start,
                line_end,
                output_start=output_length,
            )
        )
        output_length += len(rendered)
    return _BodyMarkdown(
        "".join(output),
        tuple(output_provenance),
    )


def _source_body(extraction: ExtractedDocument) -> _BodyMarkdown:
    spans = sorted(
        (
            span
            for span in extraction.content_spans
            if span.page_number >= 3
        ),
        key=lambda span: (span.offset, span.length),
    )
    pieces: list[str] = []
    provenance: list[ProvenanceSegment] = []
    output_length = 0
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
            provenance.append(
                ProvenanceSegment(
                    output_start=output_length,
                    output_end=output_length + 1,
                    source_start=span.offset + leading,
                    source_end=span.offset + leading + 1,
                    page_number=span.page_number,
                )
            )
            output_length += 1
        pieces.append(text)
        provenance.append(
            ProvenanceSegment(
                output_start=output_length,
                output_end=output_length + len(text),
                source_start=span.offset + leading,
                source_end=span.offset + leading + len(text),
                page_number=span.page_number,
            )
        )
        output_length += len(text)
        previous_end = span.offset + span.length
    return _BodyMarkdown("".join(pieces), tuple(provenance))


def _line_body(extraction: ExtractedDocument) -> _BodyMarkdown:
    values = [
        line
        for line in extraction.lines
        if line.page_number >= 3 and line.text.strip()
    ]
    pieces: list[str] = []
    provenance: list[ProvenanceSegment] = []
    output_length = 0
    for index, line in enumerate(values):
        origin = _source_offset(
            extraction, line.page_number, line.polygon, line.spans
        )
        if index:
            pieces.append("\n")
            provenance.append(
                ProvenanceSegment(
                    output_start=output_length,
                    output_end=output_length + 1,
                    source_start=origin,
                    source_end=origin + 1,
                    page_number=line.page_number,
                )
            )
            output_length += 1
        pieces.append(line.text)
        provenance.append(
            ProvenanceSegment(
                output_start=output_length,
                output_end=output_length + len(line.text),
                source_start=origin,
                source_end=origin + max(1, len(line.text)),
                page_number=line.page_number,
            )
        )
        output_length += len(line.text)
    return _BodyMarkdown("".join(pieces), tuple(provenance))


def _is_decorative_body_line(
    body: _BodyMarkdown,
    line_start: int,
    line_end: int,
    repeated_edge_offsets: set[int],
    counter_offsets: set[int],
) -> bool:
    if line_start >= line_end:
        return False
    source_start = body.offset_for_index(line_start)
    source_end = body.offset_for_index(line_end - 1) + 1
    return any(source_start <= offset < source_end for offset in counter_offsets) or any(
        source_start <= offset < source_end for offset in repeated_edge_offsets
    )


def _repeated_header_footer_offsets(extraction: ExtractedDocument) -> set[int]:
    candidates: dict[str, list[tuple[int, int]]] = {}
    for line in extraction.lines:
        if line.page_number < 3 or not line.polygon:
            continue
        if not _is_edge_line(line, extraction):
            continue
        value = _comparison_text(line.text)
        if value:
            candidates.setdefault(value, []).append(
                (
                    line.page_number,
                    _source_offset(
                        extraction, line.page_number, line.polygon, line.spans
                    ),
                )
            )
    return {
            offset
            for entries in candidates.values()
            if len({page_number for page_number, _ in entries}) >= 2
            for _, offset in entries
    }


def _edge_counter_offsets(extraction: ExtractedDocument) -> set[int]:
    return {
        _source_offset(extraction, line.page_number, line.polygon, line.spans)
        for line in extraction.lines
        if line.page_number >= 3
        and line.polygon
        and _PAGE_COUNTER.fullmatch(line.text)
        and _is_edge_line(line, extraction)
    }


def _is_edge_line(line: ExtractedLine, extraction: ExtractedDocument) -> bool:
    page = extraction.page(line.page_number)
    vertical = line.polygon[1] / page.height
    return vertical <= 0.12 or vertical >= 0.88


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
        region = next(
            (
                item
                for item in paragraph.bounding_regions
                if item.page_number >= 3
            ),
            None,
        )
        if region is None:
            continue
        index = body.index_for_source_offset(
            _source_offset(
                extraction,
                region.page_number,
                region.polygon,
                paragraph.spans,
            )
        )
        if index is None:
            continue
        if not any(position == index for position, _, _ in headings):
            headings.append((index, paragraph.text.strip(), 2))
    return sorted(headings, key=lambda item: item[0])


def _source_offset(
    extraction: ExtractedDocument,
    page_number: int,
    polygon: tuple[float, ...],
    spans: tuple,
) -> int:
    page_offsets = [
        span.offset for span in spans if span.page_number == page_number
    ]
    if page_offsets:
        return min(page_offsets)
    page_spans = [
        span
        for span in extraction.content_spans
        if span.page_number == page_number
    ]
    if not page_spans:
        return 0
    page = extraction.page(page_number)
    page_start = min(span.offset for span in page_spans)
    page_length = sum(span.length for span in page_spans)
    vertical = min(polygon[1::2]) / page.height if polygon else 0
    return page_start + int(max(0, min(1, vertical)) * max(page_length - 1, 0))


def _build_sections(
    markdown: str,
    extraction: ExtractedDocument,
    specs: list[tuple[int, int, str, int]],
    body_start: int,
    body: _BodyMarkdown,
) -> Iterable[ParsedSection]:
    hierarchy: dict[int, str] = {}
    for ordinal, (start, end, raw_title, level) in enumerate(specs):
        raw_content = markdown[start:end]
        leading = len(raw_content) - len(raw_content.lstrip())
        stripped = raw_content.strip()
        content = stripped + "\n"
        numbered = _NUMBERED_TITLE.fullmatch(raw_title)
        number = numbered.group("number") if numbered else None
        title = numbered.group("title") if numbered else raw_title
        hierarchy[level] = title
        for depth in tuple(hierarchy):
            if depth > level:
                del hierarchy[depth]
        path = tuple(hierarchy[depth] for depth in sorted(hierarchy))
        if ordinal == 0:
            page_from = 1
            page_to = min(2, extraction.total_pages)
            provenance = tuple(
                ProvenanceSegment(
                    output_start=0,
                    output_end=max(1, len(content)),
                    source_start=page.spans[0].offset,
                    source_end=page.spans[-1].offset + page.spans[-1].length,
                    page_number=page.page_number,
                )
                for page in extraction.pages[:2]
                if page.spans
            )
        else:
            body_range_start = max(0, start + leading - body_start)
            body_range_end = max(
                body_range_start + 1,
                start + leading + len(stripped) - body_start,
            )
            provenance = body.segments_for_range(
                body_range_start,
                body_range_end,
            )
            pages = [segment.page_number for segment in provenance]
            if pages:
                page_from, page_to = min(pages), max(pages)
            else:
                page_from = extraction.page_for_offset(
                    body.offset_for_index(start - body_start)
                )
                page_to = extraction.page_for_offset(
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
            provenance=provenance,
        )


def _slug(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_value = decomposed.encode("ascii", "ignore").decode("ascii").casefold()
    return re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")[:60]


def _comparison_text(value: str) -> str:
    return " ".join(value.casefold().split())
