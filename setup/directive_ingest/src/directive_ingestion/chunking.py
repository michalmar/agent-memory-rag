"""Structure-aware directive chunking with atomic table blocks."""

from __future__ import annotations

import hashlib
import re
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


def chunk_sections(
    directive_version_id: str,
    source_hash: str,
    processing_hash: str,
    sections: tuple[ParsedSection, ...],
    *,
    token_limit: int,
    overlap_tokens: int,
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
) -> tuple[list[TextChunk], list[ReviewFinding]]:
    blocks = _split_blocks(section.content)
    groups: list[list[str]] = []
    current: list[str] = []
    findings: list[ReviewFinding] = []
    for block in blocks:
        block_tokens = _token_count(block)
        if block_tokens > token_limit:
            if _is_table(block):
                if current:
                    groups.append(current)
                    current = []
                groups.append([block])
                findings.append(
                    ReviewFinding(
                        code="oversized_atomic_table",
                        severity="warning",
                        message=(
                            f"{section.section_id} contains a {block_tokens}-"
                            "token table retained as one atomic chunk"
                        ),
                    )
                )
                continue
            if current:
                groups.append(current)
                current = []
            groups.extend(
                [[part] for part in _split_prose(block, token_limit, overlap_tokens)]
            )
            continue
        proposed = "\n\n".join([*current, block])
        if current and _token_count(proposed) > token_limit:
            groups.append(current)
            overlap = _prose_overlap(current[-1], overlap_tokens)
            current = [overlap, block] if overlap else [block]
        else:
            current.append(block)
    if current:
        groups.append(current)

    chunks: list[TextChunk] = []
    for ordinal, group in enumerate(groups):
        content = "\n\n".join(group).strip() + "\n"
        kinds = {_block_kind(block) for block in group}
        if section.ordinal == 0:
            content_kind = "document_control"
        elif kinds == {"table"}:
            content_kind = "table"
        elif "table" in kinds:
            content_kind = "mixed"
        else:
            content_kind = "prose"
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
                page_from=section.page_from,
                page_to=section.page_to,
            )
        )
    return chunks, findings


def _split_blocks(content: str) -> list[str]:
    lines = content.strip().splitlines()
    blocks: list[str] = []
    current: list[str] = []
    in_html_table = False
    in_pipe_table = False

    def flush() -> None:
        nonlocal current
        if current:
            blocks.append("\n".join(current).strip())
            current = []

    for line in lines:
        stripped = line.strip()
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
        current.append(line)
        if in_html_table and "</table>" in stripped.casefold():
            flush()
            in_html_table = False
    flush()
    return [block for block in blocks if block]


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
