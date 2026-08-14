"""Shared directive identity and version normalization rules."""

from __future__ import annotations

import hashlib
import re
import unicodedata

MAX_DIRECTIVE_ID_LENGTH = 128
MAX_DIRECTIVE_VERSION_LENGTH = 64
MAX_DIRECTIVE_VERSION_ID_LENGTH = 200
DIRECTIVE_VERSION_ID_PREFIX = ":v"

_VERSION_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9]+)?$")
_SEPARATOR_SPACING_PATTERN = re.compile(r"\s*([/._-])\s*")


def normalize_directive_id(value: str) -> str:
    """Return the canonical Unicode directive identifier."""
    if not isinstance(value, str):
        raise TypeError("Directive ID must be a string")
    normalized = unicodedata.normalize("NFKC", value)
    if any(unicodedata.category(char) == "Cc" for char in normalized):
        raise ValueError("Directive ID must not contain control characters")
    normalized = " ".join(normalized.strip().split())
    normalized = _SEPARATOR_SPACING_PATTERN.sub(r"\1", normalized).upper()
    if not normalized:
        raise ValueError("Directive ID must not be empty")
    if len(normalized) > MAX_DIRECTIVE_ID_LENGTH:
        raise ValueError(
            f"Directive ID must not exceed {MAX_DIRECTIVE_ID_LENGTH} characters"
        )
    if ":" in normalized:
        raise ValueError("Directive ID must not contain ':'")
    if any(
        not (
            char == " "
            or char in "/._-"
            or unicodedata.category(char).startswith("L")
            or unicodedata.category(char) == "Nd"
        )
        for char in normalized
    ):
        raise ValueError(
            "Directive ID may contain only Unicode letters, digits, spaces, "
            "/._- separators"
        )
    return normalized


def normalize_directive_version(value: str) -> str:
    """Return the canonical decimal spelling used for directive identity."""
    if not isinstance(value, str):
        raise TypeError("Directive version must be a string")
    if (
        not value
        or len(value) > MAX_DIRECTIVE_VERSION_LENGTH
        or _VERSION_PATTERN.fullmatch(value) is None
    ):
        raise ValueError(
            "Directive version must match digits with an optional decimal fraction"
        )
    integer, separator, fraction = value.partition(".")
    integer = integer.lstrip("0") or "0"
    if not separator:
        return integer
    fraction = fraction.rstrip("0")
    return f"{integer}.{fraction}" if fraction else integer


def build_directive_version_id(
    directive_id: str,
    version: str,
) -> str:
    """Build the canonical public directive-version identity."""
    normalized_id = normalize_directive_id(directive_id)
    normalized_version = normalize_directive_version(version)
    value = f"{normalized_id}{DIRECTIVE_VERSION_ID_PREFIX}{normalized_version}"
    if len(value) > MAX_DIRECTIVE_VERSION_ID_LENGTH:
        raise ValueError(
            "Directive version ID exceeds the 200-character contract limit"
        )
    return value


def validate_directive_version_id(
    value: str,
    directive_id: str | None = None,
) -> str:
    """Validate a canonical public version ID and optionally its parent ID."""
    if not isinstance(value, str):
        raise TypeError("Directive version ID must be a string")
    if not value or len(value) > MAX_DIRECTIVE_VERSION_ID_LENGTH:
        raise ValueError("Directive version ID must be 1..200 characters")
    marker = value.rfind(DIRECTIVE_VERSION_ID_PREFIX)
    if marker <= 0:
        raise ValueError("Directive version ID must use '<directive_id>:v<version>'")
    embedded_id = value[:marker]
    embedded_version = value[marker + len(DIRECTIVE_VERSION_ID_PREFIX) :]
    expected = build_directive_version_id(embedded_id, embedded_version)
    if value != expected:
        raise ValueError("Directive version ID is not canonical")
    if directive_id is not None:
        expected_for_parent = build_directive_version_id(
            directive_id, embedded_version
        )
        if value != expected_for_parent:
            raise ValueError(
                "Directive version ID does not belong to directive ID"
            )
    return value


def directive_storage_key(directive_id: str) -> str:
    """Return the full lowercase storage key for a directive."""
    normalized_id = normalize_directive_id(directive_id)
    return hashlib.sha256(normalized_id.encode("utf-8")).hexdigest()


def directive_version_storage_key(
    directive_id: str,
    version: str,
) -> str:
    """Return the full lowercase storage key for a directive version."""
    normalized_id = normalize_directive_id(directive_id)
    normalized_version = normalize_directive_version(version)
    value = f"{normalized_id}\0{normalized_version}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def source_fingerprint(source_filename: str, source_hash: str) -> str:
    """Return the source identity hash without exposing source internals."""
    from .source_files import validate_directive_source_basename

    filename = validate_directive_source_basename(source_filename)
    if (
        not isinstance(source_hash, str)
        or re.fullmatch(r"[0-9a-fA-F]{64}", source_hash) is None
    ):
        raise ValueError("Source hash must be a 64-character hexadecimal string")
    value = f"{filename}\0{source_hash}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def published_directive_version_item_id(
    directive_id: str,
    version: str,
) -> str:
    """Build the safe published catalog item ID for a directive version."""
    return f"version:{directive_version_storage_key(directive_id, version)}"


def validate_published_directive_version_item_id(
    value: str,
    directive_id: str,
    version: str,
) -> str:
    """Validate a safe published catalog item ID."""
    expected = published_directive_version_item_id(directive_id, version)
    if value != expected:
        raise ValueError("Published version item ID does not match version")
    return value
