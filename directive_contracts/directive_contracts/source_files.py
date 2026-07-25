"""Shared directive source filename and prefix validation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

DIRECTIVE_SOURCE_FILENAME_PATTERN = (
    r"(?i)^\d{8}-[^/\\]+-v\d+(?:\.\d+)?\.pdf$"
)
_SOURCE_FILENAME = re.compile(
    r"^(?P<directive_id>\d{8})-[^/\\]+-v"
    r"(?P<version>\d+(?:\.\d+)?)\.pdf$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class DirectiveSourceIdentity:
    filename: str
    directive_id: str
    version: str

    @property
    def directive_version_id(self) -> str:
        normalized = format(Decimal(self.version).normalize(), "f")
        return f"{self.directive_id}:v{normalized}"


def parse_directive_source_filename(value: str) -> DirectiveSourceIdentity:
    if len(value) > 255:
        raise ValueError("Directive PDF filename must not exceed 255 characters")
    match = _SOURCE_FILENAME.fullmatch(value)
    if match is None:
        raise ValueError(
            "Directive PDF filename must start with an eight-digit ID and "
            f"end with -v<number>.pdf: {value}"
        )
    return DirectiveSourceIdentity(
        filename=value,
        directive_id=match.group("directive_id"),
        version=match.group("version"),
    )


def normalize_directive_source_prefix(value: str | None) -> str:
    normalized = (value or "").strip().strip("/")
    if not normalized:
        return ""
    parts = normalized.split("/")
    if (
        "\\" in normalized
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise ValueError("Directive source prefix must be a Blob path prefix")
    return f"{normalized}/"
