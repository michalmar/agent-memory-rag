"""Canonical Markdown parsing, validation, and relation extraction."""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from html.parser import HTMLParser
from typing import Iterable

import tiktoken
from directive_contracts import (
    DirectiveMetadata,
    DirectiveRelation,
    ReviewFinding,
)

from .document_intelligence import ExtractedDocument
from .source import SourceDocument

_HEADING = re.compile(r"^(?P<marks>#{2,6})\s+(?P<title>.+?)\s*$", re.MULTILINE)
_NUMBERED_TITLE = re.compile(
    r"^(?P<number>\d+(?:\.\d+)*)(?:[.)])?\s+(?P<title>.+)$"
)
_DIRECTIVE_ID = re.compile(r"\b\d{8}\b")
_VERSION = re.compile(r"\bversion\s+(?P<version>\d+(?:\.\d+)?)\b", re.I)
_TOKENIZER = tiktoken.get_encoding("o200k_base")


class _FirstHtmlTable(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._table_depth = 0
        self._finished = False
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        del attrs
        tag = tag.casefold()
        if tag == "table":
            if self._finished:
                return
            self._table_depth += 1
        elif self._table_depth == 1 and tag == "tr":
            self._row = []
        elif self._table_depth == 1 and tag in {"th", "td"}:
            self._cell = []
        elif self._cell is not None and tag == "br":
            self._cell.append(" ")

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if self._table_depth == 1 and tag in {"th", "td"}:
            if self._row is not None and self._cell is not None:
                self._row.append(_clean_value("".join(self._cell)))
            self._cell = None
        elif self._table_depth == 1 and tag == "tr":
            if self._row:
                self.rows.append(self._row)
            self._row = None
        elif tag == "table" and self._table_depth:
            self._table_depth -= 1
            if self._table_depth == 0:
                self._finished = True


@dataclass(frozen=True)
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


@dataclass(frozen=True)
class CanonicalDirective:
    metadata: DirectiveMetadata
    markdown: str
    control: dict[str, str]
    sections: tuple[ParsedSection, ...]
    relations: tuple[DirectiveRelation, ...]
    findings: tuple[ReviewFinding, ...]
    total_pages: int
    total_tokens: int


def normalize_markdown(markdown: str) -> str:
    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n")
    normalized = "\n".join(line.rstrip() for line in normalized.splitlines())
    return normalized.strip() + "\n"


def parse_canonical(
    source: SourceDocument,
    extraction: ExtractedDocument,
    processing_hash: str,
) -> CanonicalDirective:
    markdown = normalize_markdown(extraction.markdown)
    control = parse_document_control(markdown)
    title = _first_h1(markdown)
    directive_id = _required_control(control, "directive id")
    version_label = _required_control(control, "version")
    status = _required_control(control, "status")
    effective_from = _parse_date(
        _required_control(control, "effective date"), "Effective date"
    )
    if directive_id != source.directive_id_hint:
        raise ValueError(
            f"{source.path.name}: extracted Directive ID {directive_id} does "
            f"not match filename ID {source.directive_id_hint}"
        )
    if not source.metadata_version_matches(version_label):
        raise ValueError(
            f"{source.path.name}: extracted version {version_label} does not "
            f"match filename version {source.version_hint}"
        )
    status_key = status.casefold()
    if status_key == "current":
        is_current = True
    elif status_key.startswith("superseded"):
        is_current = False
    else:
        raise ValueError(
            f"{source.path.name}: unsupported publication status {status!r}"
        )
    effective_to_raw = control.get("superseded on")
    effective_to = (
        _parse_date(effective_to_raw, "Superseded on")
        if effective_to_raw
        else None
    )
    if effective_to and effective_to <= effective_from:
        raise ValueError(
            f"{source.path.name}: Superseded on must follow Effective date"
        )
    document_type = (
        "sub_directive"
        if control.get("document type", "").casefold() == "sub-directive"
        else "directive"
    )
    normalized_version = format(Decimal(version_label).normalize(), "f")
    directive_version_id = f"{directive_id}:v{normalized_version}"
    metadata = DirectiveMetadata(
        directive_id=directive_id,
        directive_version_id=directive_version_id,
        version_label=version_label,
        title=title,
        status=status,
        is_current=is_current,
        effective_from=effective_from,
        effective_to=effective_to,
        document_type=document_type,
        source_filename=source.path.name,
        source_hash=source.source_hash,
        processing_hash=processing_hash,
    )
    sections = tuple(_parse_sections(markdown, extraction, directive_version_id))
    findings = list(_quality_findings(extraction, sections))
    relations = tuple(
        _extract_relations(metadata, control, markdown, findings)
    )
    return CanonicalDirective(
        metadata=metadata,
        markdown=markdown,
        control=control,
        sections=sections,
        relations=relations,
        findings=tuple(findings),
        total_pages=extraction.total_pages,
        total_tokens=len(_TOKENIZER.encode(markdown)),
    )


def parse_document_control(markdown: str) -> dict[str, str]:
    html_rows = _parse_html_table(markdown)
    rows = html_rows or _parse_pipe_table(markdown)
    if len(rows) < 2:
        raise ValueError("The first document-control table is missing or empty")
    control: dict[str, str] = {}
    for row in rows:
        if len(row) < 2:
            continue
        key = _clean_value(row[0]).casefold()
        value = _clean_value(row[1])
        if key in {"document control", "---"} or not key:
            continue
        if key in control:
            raise ValueError(f"Duplicate document-control field: {row[0]}")
        control[key] = value
    return control


def _parse_html_table(markdown: str) -> list[list[str]]:
    if "<table" not in markdown.casefold():
        return []
    parser = _FirstHtmlTable()
    parser.feed(markdown)
    return parser.rows


def _parse_pipe_table(markdown: str) -> list[list[str]]:
    rows: list[list[str]] = []
    in_table = False
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [_clean_value(cell) for cell in stripped[1:-1].split("|")]
            if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
                in_table = True
                continue
            rows.append(cells)
            in_table = True
        elif in_table:
            break
    return rows


def _parse_sections(
    markdown: str,
    extraction: ExtractedDocument,
    directive_version_id: str,
) -> Iterable[ParsedSection]:
    matches = list(_HEADING.finditer(markdown))
    raw_sections: list[tuple[int, int, str, int, str]] = []
    first_heading_start = matches[0].start() if matches else len(markdown)
    raw_sections.append((0, first_heading_start, "Document control", 1, ""))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(
            markdown
        )
        raw_sections.append(
            (
                match.start(),
                end,
                match.group("title").strip(),
                len(match.group("marks")),
                match.group(0),
            )
        )
    hierarchy: dict[int, str] = {}
    for ordinal, (start, end, heading, level, _) in enumerate(raw_sections):
        content = markdown[start:end].strip() + "\n"
        if not content.strip():
            continue
        numbered = _NUMBERED_TITLE.fullmatch(heading)
        number = numbered.group("number") if numbered else None
        clean_title = numbered.group("title").strip() if numbered else heading
        hierarchy[level] = clean_title
        for deeper in [key for key in hierarchy if key > level]:
            hierarchy.pop(deeper)
        path = tuple(
            hierarchy[key] for key in sorted(hierarchy) if key >= 2
        )
        if ordinal == 0:
            path = ("Document control",)
        slug = re.sub(r"[^a-z0-9]+", "-", heading.casefold()).strip("-")
        section_id = f"s{ordinal:04d}-{slug[:60] or 'section'}"
        page_from = extraction.page_for_offset(start)
        page_to = extraction.page_for_offset(max(start, end - 1))
        if page_to < page_from:
            page_to = page_from
        yield ParsedSection(
            section_id=section_id,
            ordinal=ordinal,
            number=number,
            title=clean_title,
            path=path,
            page_from=page_from,
            page_to=page_to,
            content=content,
            token_count=len(_TOKENIZER.encode(content)),
            content_hash=hashlib.sha256(content.encode()).hexdigest(),
        )


def _extract_relations(
    metadata: DirectiveMetadata,
    control: dict[str, str],
    markdown: str,
    findings: list[ReviewFinding],
) -> Iterable[DirectiveRelation]:
    accepted_targets: set[str] = set()
    relation_fields = (
        ("parent directive", "parent"),
        ("related sub-directive", "sub_directive"),
    )
    for field, relation_type in relation_fields:
        evidence = control.get(field)
        if not evidence:
            continue
        target_match = _DIRECTIVE_ID.search(evidence)
        if target_match is None:
            findings.append(
                ReviewFinding(
                    code="relation_without_stable_id",
                    severity="warning",
                    message=f"{field} has no exact eight-digit directive ID",
                )
            )
            continue
        target_id = target_match.group(0)
        accepted_targets.add(target_id)
        version_match = _VERSION.search(evidence)
        if relation_type == "parent":
            parent_id, child_id = target_id, metadata.directive_id
        else:
            parent_id, child_id = metadata.directive_id, target_id
        relation_id = hashlib.sha256(
            f"{parent_id}|sub_directive|{child_id}".encode()
        ).hexdigest()
        yield DirectiveRelation(
            relation_id=relation_id,
            source_directive_id=metadata.directive_id,
            source_version_id=metadata.directive_version_id,
            target_directive_id=target_id,
            target_version_label=(
                version_match.group("version") if version_match else None
            ),
            relation_type=relation_type,
            status="accepted",
            evidence=evidence,
        )
    referenced = set(_DIRECTIVE_ID.findall(markdown))
    referenced.discard(metadata.directive_id)
    for target_id in sorted(referenced - accepted_targets):
        relation_id = hashlib.sha256(
            (
                f"{metadata.directive_version_id}|reference|{target_id}"
            ).encode()
        ).hexdigest()
        yield DirectiveRelation(
            relation_id=relation_id,
            source_directive_id=metadata.directive_id,
            source_version_id=metadata.directive_version_id,
            target_directive_id=target_id,
            relation_type="reference",
            status="needs_review",
            evidence=f"Unclassified reference to directive {target_id}",
        )


def _quality_findings(
    extraction: ExtractedDocument, sections: tuple[ParsedSection, ...]
) -> Iterable[ReviewFinding]:
    if extraction.table_count == 0:
        yield ReviewFinding(
            code="no_detected_tables",
            severity="warning",
            message="Layout extraction reported no tables",
        )
    if len(sections) < 2:
        yield ReviewFinding(
            code="no_body_sections",
            severity="error",
            message="No level-two or deeper body headings were extracted",
        )
    numbered = [
        int(section.number)
        for section in sections
        if section.number and section.number.isdigit()
    ]
    if numbered and numbered != list(range(numbered[0], numbered[-1] + 1)):
        yield ReviewFinding(
            code="non_contiguous_top_level_sections",
            severity="warning",
            message="Top-level numeric section headings are not contiguous",
        )


def _first_h1(markdown: str) -> str:
    match = re.search(r"^#\s+(.+?)\s*$", markdown, re.MULTILINE)
    if match is None:
        raise ValueError("Directive Markdown has no level-one title")
    return html.unescape(match.group(1).strip())


def _required_control(control: dict[str, str], key: str) -> str:
    value = control.get(key, "").strip()
    if not value:
        raise ValueError(f"Required document-control field is missing: {key}")
    return value


def _parse_date(raw: str, field: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO date, got {raw!r}") from exc


def _clean_value(value: str) -> str:
    return " ".join(html.unescape(value).strip().split())
