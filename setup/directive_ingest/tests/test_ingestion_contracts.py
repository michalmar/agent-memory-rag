from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from directive_contracts import (
    DirectiveMetadata,
    DirectiveSummary,
    directive_storage_key,
    directive_version_storage_key,
    published_directive_version_item_id,
)

from directive_ingestion.reconcile import _build_artifact_locators


def test_catalog_item_identity_uses_shared_storage_key() -> None:
    assert published_directive_version_item_id("Č/12", "01.00") == (
        f"version:{directive_version_storage_key('Č/12', '1')}"
    )


def test_artifact_identity_never_places_human_identifier_in_path() -> None:
    metadata = DirectiveMetadata(
        directive_id="Č/12",
        directive_version_id="Č/12:v1",
        version_label="1",
        title="Test",
        status="Current",
        is_current=True,
        is_valid=True,
        effective_from=date(2026, 1, 1),
        source_filename="opaque.pdf",
        source_hash="a" * 64,
        processing_hash="b" * 64,
    )
    locators = _build_artifact_locators(
        SimpleNamespace(metadata=metadata), "c" * 64
    )

    assert locators.source_blob_name == (
        f"directives/{directive_storage_key('Č/12')}/"
        f"{directive_version_storage_key('Č/12', '1')}/{'a' * 64}/source.pdf"
    )
    assert "Č/12" not in locators.canonical_blob_name


def test_summary_keeps_public_identity_without_storage_keys() -> None:
    summary = DirectiveSummary(
        directive_id="Č/12",
        directive_version_id="Č/12:v1",
        source_hash="a" * 64,
        summary="Bezpečné shrnutí",
        covered_section_ids=[],
        total_section_count=0,
        input_token_count=0,
        strategy="full_document",
        model_deployment="test",
    )

    assert "storage_key" not in summary.model_dump()
