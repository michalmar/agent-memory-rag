"""Deterministic page-aware Czech directive metadata extraction."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date

from directive_contracts import (
    DirectiveMetadata,
    ReviewFinding,
    build_directive_version_id,
    normalize_directive_id,
    normalize_directive_version,
)

from .document_intelligence import (
    ExtractedDocument,
    ExtractedLine,
    ExtractedTable,
)
from .source import SourceDocument


@dataclass(frozen=True, slots=True)
class CoreLabelRegistry:
    version: str
    aliases: dict[str, frozenset[str]]


CORE_LABELS = CoreLabelRegistry(
    version="1",
    aliases={
        "directive_id": frozenset(
            {
                "metodicky pokyn cislo",
                "metodicky pokyn c",
                "cislo metodickeho pokynu",
            }
        ),
        "version": frozenset({"verze"}),
        "effective_from": frozenset({"ucinnost od", "platnost od"}),
    },
)


@dataclass(frozen=True, slots=True)
class FieldEvidence:
    field: str
    value: str
    source_text: str
    page_number: int


@dataclass(frozen=True, slots=True)
class DirectiveMetadataCandidate:
    metadata: DirectiveMetadata
    evidence: tuple[FieldEvidence, ...]
    first_two_pages_markdown: str
    findings: tuple[ReviewFinding, ...]


class DirectiveMetadataError(ValueError):
    """Core directive metadata is missing, malformed, or contradictory."""


_DATE = re.compile(r"(?<!\d)(\d{1,2})\s*\.\s*(\d{1,2})\s*\.\s*(\d{4})(?!\d)")
_COUNTER = re.compile(r"^\s*(?:strana\s*)?\d+\s*(?:/|z)\s*\d+\s*$", re.IGNORECASE)


def normalize_label(value: str) -> str:
    """Normalize only labels; never use this transformation for source values."""
    value = unicodedata.normalize("NFKC", value).casefold()
    value = "".join(
        char
        for char in unicodedata.normalize("NFD", value)
        if unicodedata.category(char) != "Mn"
    )
    value = re.sub(r"[\s.:;]+", " ", value)
    return " ".join(value.split()).strip()


def extract_metadata(
    source: SourceDocument,
    extraction: ExtractedDocument,
    processing_hash: str,
) -> DirectiveMetadataCandidate:
    if extraction.total_pages < 2:
        raise DirectiveMetadataError("Directive metadata requires at least two pages")
    values = {
        field: _find_labelled_values(extraction, field)
        for field in ("directive_id", "version", "effective_from")
    }
    title = _extract_title(extraction)
    evidence = [
        FieldEvidence(
            field="title",
            value=title.value,
            source_text=title.value,
            page_number=title.page_number,
        )
    ]
    if title.page_number != 1:
        raise DirectiveMetadataError("Directive title must be established on page 1")
    for confirmation in extraction.page_role_paragraphs(2, "title"):
        if _normalized_title(confirmation.text) != _normalized_title(title.value):
            raise DirectiveMetadataError("Conflicting core field: title")
    normalized: dict[str, str | date] = {}
    for field, matches in values.items():
        if not matches:
            raise DirectiveMetadataError(f"Missing required core field: {field}")
        if not any(item.page_number == 1 for item in matches):
            raise DirectiveMetadataError(
                f"Missing required page 1 core field: {field}"
            )
        parsed = [_parse_core_value(field, item.value) for item in matches]
        primary = parsed[0]
        if any(value != primary for value in parsed[1:]):
            raise DirectiveMetadataError(f"Conflicting core field: {field}")
        normalized[field] = primary
        chosen = matches[0]
        evidence.append(
            FieldEvidence(
                field=field,
                value=chosen.value,
                source_text=chosen.source_text,
                page_number=chosen.page_number,
            )
        )
    directive_id = str(normalized["directive_id"])
    version_label = next(
        item.value for item in values["version"] if item.page_number == 1
    )
    effective_from = normalized["effective_from"]
    if not isinstance(effective_from, date):
        raise AssertionError("Effective date parser returned non-date")
    metadata = DirectiveMetadata(
        directive_id=directive_id,
        directive_version_id=build_directive_version_id(directive_id, version_label),
        version_label=version_label,
        title=title.value,
        aliases=[],
        status="Current",
        is_current=True,
        is_valid=True,
        effective_from=effective_from,
        effective_to=None,
        language="cs",
        document_type="directive",
        source_filename=source.source_name,
        source_hash=source.source_hash,
        processing_hash=processing_hash,
    )
    rendered, findings = render_first_two_pages(extraction)
    return DirectiveMetadataCandidate(
        metadata=metadata,
        evidence=tuple(evidence),
        first_two_pages_markdown=rendered,
        findings=tuple(findings),
    )


@dataclass(frozen=True, slots=True)
class _ValueMatch:
    value: str
    source_text: str
    page_number: int


def _find_labelled_values(
    extraction: ExtractedDocument, field: str
) -> list[_ValueMatch]:
    aliases = CORE_LABELS.aliases[field]
    matches: list[_ValueMatch] = []
    for table in extraction.tables:
        if any(cell.page_number in {1, 2} for cell in table.cells):
            matches.extend(_table_matches(table, aliases))
    for page_number in (1, 2):
        lines = [
            line for line in extraction.lines if line.page_number == page_number
        ]
        matches.extend(_line_matches(lines, aliases))
    matches.sort(key=lambda item: item.page_number)
    return _deduplicate_matches(matches)


def _table_matches(
    table: ExtractedTable, aliases: frozenset[str]
) -> list[_ValueMatch]:
    values: list[_ValueMatch] = []
    cells = {
        (cell.page_number, cell.row_index, cell.column_index): cell
        for cell in table.cells
        if cell.page_number in {1, 2}
    }
    for cell in table.cells:
        if cell.page_number not in {1, 2}:
            continue
        text = cell.text.strip()
        normalized = normalize_label(text)
        inline = _inline_value(text, aliases)
        if inline:
            values.append(_ValueMatch(inline, text, cell.page_number))
            continue
        if normalized not in aliases:
            continue
        next_cell = cells.get(
            (cell.page_number, cell.row_index, cell.column_index + cell.column_span)
        )
        if next_cell is not None:
            values.append(
                _ValueMatch(
                    next_cell.text.strip(),
                    f"{text} {next_cell.text}",
                    cell.page_number,
                )
            )
    return values


def _line_matches(
    lines: list[ExtractedLine], aliases: frozenset[str]
) -> list[_ValueMatch]:
    values: list[_ValueMatch] = []
    for index, line in enumerate(lines):
        inline = _inline_value(line.text, aliases)
        if inline:
            values.append(_ValueMatch(inline, line.text, line.page_number))
            continue
        if normalize_label(line.text) not in aliases:
            continue
        if index + 1 < len(lines):
            next_line = lines[index + 1]
            values.append(
                _ValueMatch(
                    next_line.text.strip(),
                    f"{line.text} {next_line.text}",
                    line.page_number,
                )
            )
    return values


def _inline_value(text: str, aliases: frozenset[str]) -> str | None:
    for separator in (":", "-"):
        if separator not in text:
            continue
        label, value = text.split(separator, 1)
        if normalize_label(label) in aliases and value.strip():
            return value.strip()
    return None


def _deduplicate_matches(values: list[_ValueMatch]) -> list[_ValueMatch]:
    seen: set[tuple[int, str]] = set()
    result: list[_ValueMatch] = []
    for item in values:
        key = (item.page_number, item.value)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _parse_core_value(field: str, value: str) -> str | date:
    if field == "directive_id":
        try:
            return normalize_directive_id(value)
        except (TypeError, ValueError) as exc:
            raise DirectiveMetadataError("Invalid directive ID") from exc
    if field == "version":
        try:
            return normalize_directive_version(value)
        except (TypeError, ValueError) as exc:
            raise DirectiveMetadataError("Invalid numeric version") from exc
    if field == "effective_from":
        match = _DATE.search(value)
        if match is None:
            raise DirectiveMetadataError("Invalid Czech effective date")
        try:
            return date(*(int(part) for part in reversed(match.groups())))
        except ValueError as exc:
            raise DirectiveMetadataError("Invalid Czech effective date") from exc
    raise AssertionError(f"Unknown core field: {field}")


def _extract_title(extraction: ExtractedDocument) -> _ValueMatch:
    title_paragraphs = extraction.page_role_paragraphs(1, "title")
    if title_paragraphs:
        title = max(title_paragraphs, key=lambda item: len(item.text.strip()))
        return _ValueMatch(title.text.strip(), title.text, 1)
    candidates = [
        line
        for line in extraction.lines
        if line.page_number == 1
        and not _COUNTER.fullmatch(line.text)
        and normalize_label(line.text)
        not in set().union(*CORE_LABELS.aliases.values())
        and _inline_value(
            line.text, frozenset().union(*CORE_LABELS.aliases.values())
        )
        is None
        and len(line.text.strip()) >= 6
    ]
    if not candidates:
        raise DirectiveMetadataError("Missing required core field: title")
    # Relative vertical center works across page dimensions without fixed coordinates.
    page = extraction.page(1)
    central = [
        line
        for line in candidates
        if (
            len(line.polygon) >= 2
            and page.height * 0.15 <= line.polygon[1] <= page.height * 0.70
        )
    ]
    selected = max(
        central or candidates,
        key=lambda item: (len(item.text.strip()), -item.polygon[1]),
    )
    return _ValueMatch(selected.text.strip(), selected.text, 1)


def _normalized_title(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def render_first_two_pages(
    extraction: ExtractedDocument,
) -> tuple[str, list[ReviewFinding]]:
    """Render all non-decorative metadata-region text in source reading order."""
    blocks: list[str] = []
    findings: list[ReviewFinding] = []
    for page_number in (1, 2):
        tables = [
            table
            for table in extraction.tables
            if any(cell.page_number == page_number for cell in table.cells)
        ]
        table_texts = {
            _comparison_text(cell.text)
            for table in tables
            for cell in table.cells
            if cell.page_number == page_number
        }
        page_blocks = [_render_table(table, page_number) for table in tables]
        residual = [
            line.text.strip()
            for line in extraction.lines
            if line.page_number == page_number
            and not _is_decorative(line.text)
            and _comparison_text(line.text) not in table_texts
        ]
        if residual:
            page_blocks.append("\n".join(residual))
        elif not page_blocks:
            raise DirectiveMetadataError(
                f"Page {page_number} has no preservable metadata-region content"
            )
        blocks.append(f"### Page {page_number}\n\n" + "\n\n".join(page_blocks))
        if tables and residual:
            findings.append(
                ReviewFinding(
                    code="metadata_region_mixed_layout",
                    severity="warning",
                    message=(
                        f"Page {page_number} used table and line reconstruction"
                    ),
                )
            )
    return "\n\n".join(blocks), findings


def _render_table(table: ExtractedTable, page_number: int) -> str:
    rows: dict[int, list[str]] = {}
    for cell in table.cells:
        if cell.page_number != page_number:
            continue
        row = rows.setdefault(cell.row_index, [""] * table.column_count)
        row[cell.column_index] = cell.text.strip()
    rendered = [
        "| " + " | ".join(row) + " |"
        for _, row in sorted(rows.items())
    ]
    if len(rendered) >= 2:
        rendered.insert(1, "| " + " | ".join("---" for _ in range(table.column_count)) + " |")
    return "\n".join(rendered)


def _comparison_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _is_decorative(value: str) -> bool:
    return bool(_COUNTER.fullmatch(value))
