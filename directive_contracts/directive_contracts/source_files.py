"""Shared directive source basename and prefix validation."""

from __future__ import annotations

import unicodedata


def validate_directive_source_basename(value: str) -> str:
    """Validate and return an exact, storage-safe PDF basename."""
    if not isinstance(value, str):
        raise TypeError("Directive source basename must be a string")
    if not value:
        raise ValueError("Directive PDF filename must not be empty")
    if len(value) > 255:
        raise ValueError("Directive PDF filename must not exceed 255 characters")
    if value in {".", ".."}:
        raise ValueError("Directive PDF filename must not be '.' or '..'")
    if not value.lower().endswith(".pdf"):
        raise ValueError("Directive source basename must end with .pdf")
    if any(
        char in "/\\\0" or unicodedata.category(char) == "Cc"
        for char in value
    ):
        raise ValueError(
            "Directive source basename must not contain path separators, NUL, "
            "or control characters"
        )
    return value


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
