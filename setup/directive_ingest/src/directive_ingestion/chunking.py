"""Structure-aware directive chunking with atomic table blocks."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass

import tiktoken
from directive_contracts import ReviewFinding

from .canonical import ParsedSection

_TOKENIZER = tiktoken.get_encoding("o200k_base")


@dataclass(frozen=True)
class TextChunk:
    id: str
    section_id: str
    ordinal: int
    content: str
    content_kind: str
    page_from: int
    page_to: int


@dataclass(frozen=True, slots=True)
class _Block:
    content: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class _ChunkPart:
    content: str
    start: int
    end: int


def chunk_sections(
    directive_version_id: str,
    source_hash: str,
    processing_hash: str,
    sections: tuple[ParsedSection, ...],
    *,
    token_limit: int,
    overlap_tokens: int,
    table_max_rows_per_part: int = 25,
    table_max_chars_per_part: int = 12000,
) -> tuple[list[TextChunk], list[ReviewFinding]]:
    chunks: list[TextChunk] = []
    findings: list[ReviewFinding] = []
    for section in sections:
        section_chunks, section_findings = _chunk_section(
            directive_version_id,
            source_hash,
            processing_hash,
            section,
            token_limit=token_limit,
            overlap_tokens=overlap_tokens,
            table_max_rows_per_part=table_max_rows_per_part,
            table_max_chars_per_part=table_max_chars_per_part,
        )
        chunks.extend(section_chunks)
        findings.extend(section_findings)
    return chunks, findings


def _chunk_section(
    directive_version_id: str,
    source_hash: str,
    processing_hash: str,
    section: ParsedSection,
    *,
    token_limit: int,
    overlap_tokens: int,
    table_max_rows_per_part: int,
    table_max_chars_per_part: int,
) -> tuple[list[TextChunk], list[ReviewFinding]]:
    blocks = _split_blocks(section.content)
    content_token_limit = token_limit - 1
    groups: list[list[_ChunkPart]] = []
    current: list[_ChunkPart] = []
    findings: list[ReviewFinding] = []
    for block in blocks:
        block_tokens = _token_count(block.content)
        if _is_table(block.content) and (
            block_tokens > content_token_limit
            or len(block.content) > table_max_chars_per_part
            or _table_row_count(block.content) > table_max_rows_per_part
        ):
            if current:
                groups.append(current)
                current = []
            table_parts = _partition_table(
                block,
                token_limit=content_token_limit,
                max_rows=table_max_rows_per_part,
                max_chars=table_max_chars_per_part,
            )
            groups.extend([[part] for part in table_parts])
            findings.append(
                ReviewFinding(
                    code="partitioned_table",
                    severity="warning",
                    message=(
                        f"{section.section_id} table was partitioned into "
                        f"{len(table_parts)} bounded chunks"
                    ),
                )
            )
            continue
        if block_tokens > content_token_limit:
            if _is_table(block.content):
                if current:
                    groups.append(current)
                    current = []
                raise ValueError("Table partitioning produced an oversized chunk")
            if current:
                groups.append(current)
                current = []
            groups.extend(
                [
                    [part]
                    for part in _split_prose_part(
                        block,
                        content_token_limit,
                        overlap_tokens,
                    )
                ]
            )
            continue
        part = _ChunkPart(block.content, block.start, block.end)
        proposed = "\n\n".join(
            [*(item.content for item in current), part.content]
        )
        if current and _token_count(proposed) > content_token_limit:
            groups.append(current)
            overlap = _prose_overlap(
                current[-1].content,
                overlap_tokens,
            )
            candidate = (
                [
                    _ChunkPart(
                        overlap,
                        current[-1].start,
                        current[-1].end,
                    ),
                    part,
                ]
                if overlap
                else [part]
            )
            current = (
                candidate
                if _token_count(
                    "\n\n".join(item.content for item in candidate)
                )
                <= content_token_limit
                else [part]
            )
        else:
            current.append(part)
    if current:
        groups.append(current)

    chunks: list[TextChunk] = []
    for ordinal, group in enumerate(groups):
        content = "\n\n".join(item.content for item in group).strip() + "\n"
        if _token_count(content) > token_limit:
            raise ValueError("Chunk exceeds the configured token limit")
        kinds = {_block_kind(item.content) for item in group}
        if section.ordinal == 0:
            content_kind = "document_control"
        elif kinds == {"table"}:
            content_kind = "table"
        elif "table" in kinds:
            content_kind = "mixed"
        else:
            content_kind = "prose"
        page_from, page_to = section.page_range_for(
            min(item.start for item in group),
            max(item.end for item in group),
        )
        chunk_id = hashlib.sha256(
            (
                f"{directive_version_id}|{source_hash}|"
                f"{processing_hash}|"
                f"{section.section_id}|{ordinal}|"
                f"{hashlib.sha256(content.encode()).hexdigest()}"
            ).encode()
        ).hexdigest()
        chunks.append(
            TextChunk(
                id=chunk_id,
                section_id=section.section_id,
                ordinal=ordinal,
                content=content,
                content_kind=content_kind,
                page_from=page_from,
                page_to=page_to,
            )
        )
    return chunks, findings


def _split_blocks(content: str) -> list[_Block]:
    lines = content.splitlines(keepends=True)
    blocks: list[_Block] = []
    current: list[str] = []
    current_start = 0
    position = 0
    in_html_table = False
    in_pipe_table = False

    def flush() -> None:
        nonlocal current, current_start
        if current:
            raw = "".join(current)
            leading = len(raw) - len(raw.lstrip())
            stripped = raw.strip()
            if stripped:
                blocks.append(
                    _Block(
                        content=stripped,
                        start=current_start + leading,
                        end=current_start + leading + len(stripped),
                    )
                )
            current = []

    for line in lines:
        stripped = line.strip()
        line_start = position
        position += len(line)
        if "<table" in stripped.casefold():
            flush()
            in_html_table = True
        is_pipe = stripped.startswith("|") and stripped.endswith("|")
        if is_pipe and not in_pipe_table and not in_html_table:
            flush()
            in_pipe_table = True
        if in_pipe_table and not is_pipe:
            flush()
            in_pipe_table = False
        if not stripped and not in_html_table and not in_pipe_table:
            flush()
            continue
        if not current:
            current_start = line_start
        current.append(line)
        if in_html_table and "</table>" in stripped.casefold():
            flush()
            in_html_table = False
    flush()
    return blocks


def _split_prose(
    block: str, token_limit: int, overlap_tokens: int
) -> list[str]:
    tokens = _TOKENIZER.encode(block)
    if len(tokens) <= token_limit:
        return [block]
    parts: list[str] = []
    start = 0
    while start < len(tokens):
        end = min(start + token_limit, len(tokens))
        parts.append(_TOKENIZER.decode(tokens[start:end]).strip())
        if end == len(tokens):
            break
        start = max(start + 1, end - overlap_tokens)
    return parts


def _split_prose_part(
    block: _Block,
    token_limit: int,
    overlap_tokens: int,
) -> list[_ChunkPart]:
    tokens = _TOKENIZER.encode(block.content)
    if len(tokens) <= token_limit:
        return [_ChunkPart(block.content, block.start, block.end)]
    parts: list[_ChunkPart] = []
    token_start = 0
    while token_start < len(tokens):
        token_end = min(token_start + token_limit, len(tokens))
        prefix = _TOKENIZER.decode(tokens[:token_start])
        value = _TOKENIZER.decode(tokens[token_start:token_end]).strip()
        leading = len(
            _TOKENIZER.decode(tokens[token_start:token_end])
        ) - len(
            _TOKENIZER.decode(tokens[token_start:token_end]).lstrip()
        )
        char_start = block.start + len(prefix) + leading
        parts.append(
            _ChunkPart(
                value,
                char_start,
                min(block.end, char_start + max(1, len(value))),
            )
        )
        if token_end == len(tokens):
            break
        token_start = max(token_start + 1, token_end - overlap_tokens)
    return parts


def _table_row_count(block: str) -> int:
    if block.lstrip().startswith("|"):
        return sum(
            1
            for line in block.splitlines()
            if line.strip().startswith("|") and line.strip().endswith("|")
        )
    return len(re.findall(r"<tr\b[^>]*>.*?</tr>", block, re.I | re.S))


def _partition_table(
    block: _Block,
    *,
    token_limit: int,
    max_rows: int,
    max_chars: int,
) -> list[_ChunkPart]:
    if block.content.lstrip().startswith("|"):
        parts = _partition_pipe_table(
            block,
            token_limit=token_limit,
            max_rows=max_rows,
            max_chars=max_chars,
        )
    else:
        parts = _partition_html_table(
            block,
            token_limit=token_limit,
            max_rows=max_rows,
            max_chars=max_chars,
        )
    if not parts or any(
        _token_count(part.content) > token_limit
        or len(part.content) > max_chars
        for part in parts
    ):
        return _partition_table_fallback(
            block,
            token_limit=token_limit,
            max_chars=max_chars,
        )
    return parts


def _partition_pipe_table(
    block: _Block,
    *,
    token_limit: int,
    max_rows: int,
    max_chars: int,
) -> list[_ChunkPart]:
    rows = _line_ranges(block)
    if len(rows) < 3:
        return []
    header_count = 2 if re.fullmatch(
        r"\|?[\s:|-]+\|?",
        rows[1][0].strip(),
    ) else 1
    header = "\n".join(value for value, _, _ in rows[:header_count])
    data = rows[header_count:]
    return _partition_table_rows(
        block,
        header=header,
        rows=data,
        token_limit=token_limit,
        max_rows=max_rows,
        max_chars=max_chars,
        render=lambda value, selected, marker: (
            f"{marker}\n{value}\n"
            + "\n".join(row[0] for row in selected)
        ),
    )


def _partition_html_table(
    block: _Block,
    *,
    token_limit: int,
    max_rows: int,
    max_chars: int,
) -> list[_ChunkPart]:
    matches = list(
        re.finditer(r"<tr\b[^>]*>.*?</tr>", block.content, re.I | re.S)
    )
    if len(matches) < 2:
        return []
    rows = [
        (
            match.group(0),
            block.start + match.start(),
            block.start + match.end(),
        )
        for match in matches
    ]
    has_header = any(re.search(r"<th\b", row[0], re.I) for row in rows)
    header_rows = [
        row for row in rows if re.search(r"<th\b", row[0], re.I)
    ]
    if not has_header:
        header_rows = rows[:1]
    data = (
        [row for row in rows if not re.search(r"<th\b", row[0], re.I)]
        if has_header
        else rows[1:]
    )
    open_tag = re.search(r"<table\b[^>]*>", block.content, re.I)
    prefix = open_tag.group(0) if open_tag else "<table>"
    header = "\n".join(row[0] for row in header_rows)
    return _partition_table_rows(
        block,
        header=header,
        rows=data,
        token_limit=token_limit,
        max_rows=max_rows,
        max_chars=max_chars,
        render=lambda value, selected, marker: (
            f"{marker}\n{prefix}\n{value}\n"
            + "\n".join(row[0] for row in selected)
            + "\n</table>"
        ),
    )


def _partition_table_rows(
    block: _Block,
    *,
    header: str,
    rows: list[tuple[str, int, int]],
    token_limit: int,
    max_rows: int,
    max_chars: int,
    render: Callable[
        [str, list[tuple[str, int, int]], str],
        str,
    ],
) -> list[_ChunkPart]:
    if not rows:
        return []
    groups: list[list[tuple[str, int, int]]] = []
    current: list[tuple[str, int, int]] = []
    marker = "<!-- table-continuation part=9999/9999 -->"
    for row in rows:
        candidate = [*current, row]
        content = render(header, candidate, marker)
        if current and (
            len(candidate) > max_rows
            or len(content) > max_chars
            or _token_count(content) > token_limit
        ):
            groups.append(current)
            current = [row]
        else:
            current = candidate
    if current:
        groups.append(current)
    total = len(groups)
    parts: list[_ChunkPart] = []
    for index, group in enumerate(groups, 1):
        content = render(
            header,
            group,
            f"<!-- table-continuation part={index}/{total} -->",
        )
        parts.append(
            _ChunkPart(
                content=content,
                start=min(block.start, min(row[1] for row in group)),
                end=max(row[2] for row in group),
            )
        )
    return parts


def _partition_table_fallback(
    block: _Block,
    *,
    token_limit: int,
    max_chars: int,
) -> list[_ChunkPart]:
    marker_budget = 16
    value_limit = max(1, token_limit - marker_budget)
    tokens = _TOKENIZER.encode(block.content)
    groups: list[tuple[int, int, str]] = []
    start = 0
    while start < len(tokens):
        end = min(start + value_limit, len(tokens))
        value = _TOKENIZER.decode(tokens[start:end])
        while len(value) > max_chars - 64 and end > start + 1:
            end -= 1
            value = _TOKENIZER.decode(tokens[start:end])
        groups.append((start, end, value))
        start = end

    while True:
        total = len(groups)
        for index, (start_token, end_token, value) in enumerate(groups, 1):
            marker = (
                f"<!-- table-continuation part={index}/{total} "
                "representation=fragment -->\n"
            )
            if (
                _token_count(f"{marker}{value}") <= token_limit
                and len(f"{marker}{value}") <= max_chars
            ):
                continue
            if end_token - start_token <= 1:
                raise ValueError(
                    "Table chunk limits cannot fit a continuation marker"
                )
            split_token = end_token - 1
            while split_token > start_token:
                prefix_value = _TOKENIZER.decode(
                    tokens[start_token:split_token]
                )
                if (
                    _token_count(f"{marker}{prefix_value}") <= token_limit
                    and len(f"{marker}{prefix_value}") <= max_chars
                ):
                    break
                split_token -= 1
            if split_token == start_token:
                raise ValueError(
                    "Table chunk limits cannot fit a continuation marker"
                )
            groups[index - 1 : index] = [
                (start_token, split_token, prefix_value),
                (
                    split_token,
                    end_token,
                    _TOKENIZER.decode(tokens[split_token:end_token]),
                ),
            ]
            break
        else:
            break

    total = len(groups)
    parts = []
    for index, (start_token, end_token, value) in enumerate(groups, 1):
        prefix = _TOKENIZER.decode(tokens[:start_token])
        start = block.start + len(prefix)
        content = (
            f"<!-- table-continuation part={index}/{total} "
            "representation=fragment -->\n"
            f"{value}"
        )
        parts.append(
            _ChunkPart(
                content,
                start,
                min(block.end, start + max(1, len(value))),
            )
        )
    return parts


def _line_ranges(block: _Block) -> list[tuple[str, int, int]]:
    values: list[tuple[str, int, int]] = []
    position = 0
    for line in block.content.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        values.append(
            (
                stripped,
                block.start + position,
                block.start + position + len(stripped),
            )
        )
        position += len(line)
    return values


def _prose_overlap(block: str, overlap_tokens: int) -> str:
    if overlap_tokens == 0 or _is_table(block):
        return ""
    tokens = _TOKENIZER.encode(block)
    if not tokens:
        return ""
    return _TOKENIZER.decode(tokens[-overlap_tokens:]).strip()


def _is_table(block: str) -> bool:
    stripped = block.lstrip().casefold()
    return stripped.startswith("<table") or bool(
        re.match(r"^\|.+\|\s*(?:\n|$)", block)
    )


def _block_kind(block: str) -> str:
    return "table" if _is_table(block) else "prose"


def _token_count(text: str) -> int:
    return len(_TOKENIZER.encode(text))
