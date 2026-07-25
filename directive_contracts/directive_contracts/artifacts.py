"""Deterministic directive artifact identities and serialization helpers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from .models import DirectiveSectionContent

PUBLISHED_BUNDLE_MAX_BYTES = 1_800_000
SECTION_CONTENT_MAX_BYTES = 1_500_000


def canonical_json_bytes(value: Any) -> bytes:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def serialized_json_size(value: Any) -> int:
    return len(canonical_json_bytes(value))


def canonical_json_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def calculate_artifact_generation_id(
    processing_hash: str,
    canonical_markdown_hash: str,
    summary_hash: str,
) -> str:
    value = (
        f"{processing_hash}|{canonical_markdown_hash}|{summary_hash}"
    ).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def section_content_item_id(
    artifact_generation_id: str,
    section_id: str,
    part_ordinal: int,
) -> str:
    identity = (
        f"{artifact_generation_id}\0{section_id}\0{part_ordinal}"
    ).encode("utf-8")
    return f"section:{hashlib.sha256(identity).hexdigest()}"


def build_section_content_items(
    *,
    directive_id: str,
    directive_version_id: str,
    artifact_generation_id: str,
    section_id: str,
    section_ordinal: int,
    content: str,
    run_id: str,
    created_at: datetime,
    max_item_bytes: int = SECTION_CONTENT_MAX_BYTES,
) -> tuple[DirectiveSectionContent, ...]:
    section_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

    def build_item(
        part: str, part_ordinal: int, part_count: int
    ) -> DirectiveSectionContent:
        return DirectiveSectionContent(
            id=section_content_item_id(
                artifact_generation_id, section_id, part_ordinal
            ),
            directive_id=directive_id,
            directive_version_id=directive_version_id,
            artifact_generation_id=artifact_generation_id,
            section_id=section_id,
            section_ordinal=section_ordinal,
            part_ordinal=part_ordinal,
            part_count=part_count,
            part_hash=hashlib.sha256(part.encode("utf-8")).hexdigest(),
            section_hash=section_hash,
            content=part,
            run_id=run_id,
            created_at=created_at,
        )

    single = build_item(content, 0, 1)
    if serialized_json_size(single) <= max_item_bytes:
        return (single,)

    part_count_hint = 2
    for _ in range(16):
        parts = _split_to_serialized_limit(
            content,
            part_count_hint,
            max_item_bytes,
            build_item,
        )
        actual_part_count = len(parts)
        items = tuple(
            build_item(part, ordinal, actual_part_count)
            for ordinal, part in enumerate(parts)
        )
        if all(
            serialized_json_size(item) <= max_item_bytes for item in items
        ):
            if "".join(item.content for item in items) != content:
                raise ValueError("Section splitting did not preserve content")
            return items
        part_count_hint = actual_part_count
    raise ValueError(
        f"Unable to split section {section_id!r} below "
        f"{max_item_bytes} serialized bytes"
    )


def _split_to_serialized_limit(
    content: str,
    part_count_hint: int,
    max_item_bytes: int,
    build_item: Callable[[str, int, int], DirectiveSectionContent],
) -> list[str]:
    parts: list[str] = []
    offset = 0
    while offset < len(content):
        ordinal = len(parts)
        remaining = content[offset:]
        upper = _largest_fitting_prefix(
            remaining,
            lambda candidate: serialized_json_size(
                build_item(
                    candidate,
                    ordinal,
                    max(part_count_hint, ordinal + 1),
                )
            )
            <= max_item_bytes,
        )
        if upper == 0:
            raise ValueError(
                "Section-content metadata exceeds the serialized item ceiling"
            )
        boundary = remaining.rfind("\n", 0, upper)
        take = (
            upper
            if upper == len(remaining)
            else boundary + 1 if boundary >= 0 else upper
        )
        parts.append(remaining[:take])
        offset += take
    return parts


def _largest_fitting_prefix(
    value: str, fits: Callable[[str], bool]
) -> int:
    low = 0
    high = len(value)
    while low < high:
        middle = (low + high + 1) // 2
        if fits(value[:middle]):
            low = middle
        else:
            high = middle - 1
    return low
