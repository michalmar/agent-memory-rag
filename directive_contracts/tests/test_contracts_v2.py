from datetime import date, datetime
from decimal import getcontext, setcontext

import pytest
from pydantic import ValidationError

from directive_contracts import (
    DirectiveChunk,
    DirectiveMetadata,
    DirectiveSectionContent,
    MandateAssignment,
    build_directive_version_id,
    directive_storage_key,
    directive_version_storage_key,
    mandate_assignment_item_id,
    normalize_directive_id,
    normalize_directive_version,
    published_directive_version_item_id,
    source_fingerprint,
    validate_directive_source_basename,
)


@pytest.mark.parametrize(
    "value",
    [
        "policy.pdf",
        "Český název ... v2.PDF",
        " spaced name_01.final.PdF",
    ],
)
def test_source_basename_accepts_safe_exact_spelling(value: str) -> None:
    assert validate_directive_source_basename(value) == value


@pytest.mark.parametrize(
    "value",
    ["", ".", "..", "not-pdf.txt", "a/b.pdf", r"a\b.pdf", "a\x00.pdf", "a\n.pdf"],
)
def test_source_basename_rejects_unsafe_names(value: str) -> None:
    with pytest.raises((TypeError, ValueError)):
        validate_directive_source_basename(value)


def test_identity_normalizes_unicode_whitespace_and_separators() -> None:
    assert normalize_directive_id("  č  /  12 - a  ") == "Č/12-A"
    assert normalize_directive_id("Ａ．１２＿b") == "A.12_B"


def test_versions_compare_by_decimal_and_keep_display_spelling_separate() -> None:
    assert normalize_directive_version("01.00") == "1"
    assert normalize_directive_version("1.2300") == "1.23"
    assert build_directive_version_id("č/12", "01.00") == "Č/12:v1"


def test_versions_keep_distinct_long_decimal_values_exactly() -> None:
    first = "1." + ("2" * 28) + "1"
    second = "1." + ("2" * 28) + "2"
    assert normalize_directive_version(first) != normalize_directive_version(second)


def test_version_normalization_is_independent_of_decimal_context() -> None:
    original = getcontext().copy()
    try:
        getcontext().prec = 2
        low_precision = normalize_directive_version("000123.4500")
        getcontext().prec = 80
        high_precision = normalize_directive_version("000123.4500")
    finally:
        setcontext(original)
    assert low_precision == high_precision == "123.45"


def test_version_length_limit_is_enforced_before_normalization() -> None:
    with pytest.raises(ValueError):
        normalize_directive_version("1" * 65)


def test_versions_reject_non_ascii_decimal_digits() -> None:
    with pytest.raises(ValueError):
        normalize_directive_version("１２.０")


def test_storage_keys_are_full_deterministic_lowercase_hashes() -> None:
    key = directive_storage_key("č/12")
    version_key = directive_version_storage_key("č/12", "01.00")
    fingerprint = source_fingerprint("Český název.PDF", "a" * 64)
    assert len(key) == len(version_key) == len(fingerprint) == 64
    assert key == directive_storage_key(" Č / 12 ")
    assert all(char in "0123456789abcdef" for char in version_key)
    assert "/" not in published_directive_version_item_id("č/12", "1")
    assignment_id = mandate_assignment_item_id("snapshot-1", "č/12")
    assert assignment_id == f"assignment:snapshot-1:{key}"
    assert "Č/12" not in assignment_id


def _metadata(**overrides: object) -> DirectiveMetadata:
    values: dict[str, object] = {
        "directive_id": "č/12",
        "directive_version_id": "Č/12:v1",
        "version_label": "01.00",
        "title": "Title",
        "status": "Current",
        "is_current": True,
        "is_valid": True,
        "effective_from": date(2026, 1, 1),
        "language": "cs",
        "document_type": "directive",
        "source_filename": "source.PDF",
        "source_hash": "a" * 64,
        "processing_hash": "b" * 64,
    }
    values.update(overrides)
    return DirectiveMetadata(**values)


def test_metadata_v2_preserves_fields_and_enforces_current_valid_contract() -> None:
    metadata = _metadata()
    assert metadata.schema_version == "2.0"
    assert metadata.directive_id == "Č/12"
    assert metadata.version_label == "01.00"
    assert metadata.is_valid is True
    assert metadata.effective_to is None

    with pytest.raises(ValidationError):
        _metadata(status="Retired")
    with pytest.raises(ValidationError):
        _metadata(is_valid=False)
    with pytest.raises(ValidationError):
        _metadata(effective_to=date(2027, 1, 1))


def test_public_models_accept_slash_bearing_ids_without_storage_fields() -> None:
    chunk = DirectiveChunk(
        id="chunk-1",
        directive_id="č/12",
        directive_version_id="Č/12:v1",
        version_label="1",
        title="Title",
        aliases=[],
        is_current=True,
        is_valid=True,
        status="Current",
        effective_from=date(2026, 1, 1),
        section_id="s1",
        section_title="Section",
        section_path=["Section"],
        chunk_ordinal=0,
        content_kind="prose",
        page_from=1,
        page_to=1,
        content="content",
        content_vector=[0.1],
        language="cs",
        source_hash="a" * 64,
        processing_hash="b" * 64,
    )
    section = DirectiveSectionContent(
        id="section-1",
        directive_id="č/12",
        directive_version_id="Č/12:v1",
        artifact_generation_id="c" * 64,
        section_id="s1",
        section_ordinal=0,
        part_ordinal=0,
        part_count=1,
        part_hash="d" * 64,
        section_hash="e" * 64,
        content="content",
        run_id="run",
        created_at=datetime(2026, 1, 1),
    )
    assignment = MandateAssignment(user_id="user", directive_id="č/12")
    assert chunk.is_valid is True
    assert section.directive_id == "Č/12"
    assert assignment.directive_id == "Č/12"
    assert "storage_key" not in _metadata().model_dump()
