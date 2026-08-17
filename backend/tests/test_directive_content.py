from __future__ import annotations

import asyncio
import hashlib
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from azure.cosmos import exceptions
from directive_contracts import (
    DirectiveArtifactLocators,
    DirectiveManifest,
    DirectiveSection,
    DirectiveSectionContent,
    DirectiveSectionContentDescriptor,
    DirectiveSummary,
    PublishedDirectiveVersion,
    build_section_content_items,
    published_directive_version_item_id,
)

from agent_memory_backend.directive_catalog import DirectiveCatalogRepository
from agent_memory_backend.directive_content import DirectiveContentRepository
from agent_memory_backend.directive_errors import DirectiveDataUnavailable

_DIRECTIVE_ID = "ČD/42-A"
_VERSION_ID = "ČD/42-A:v1"
_SOURCE_HASH = "a" * 64
_PROCESSING_HASH = "b" * 64
_GENERATION_ID = "c" * 64


def _bundle_and_items(
    contents: list[str],
    *,
    max_item_bytes: int = 1_500_000,
) -> tuple[
    PublishedDirectiveVersion,
    tuple[DirectiveSectionContent, ...],
]:
    sections: list[DirectiveSection] = []
    content_items: list[DirectiveSectionContent] = []
    descriptors: dict[str, DirectiveSectionContentDescriptor] = {}
    for ordinal, content in enumerate(contents):
        section_id = f"s{ordinal:04d}"
        sections.append(
            DirectiveSection(
                section_id=section_id,
                ordinal=ordinal,
                number=str(ordinal + 1),
                title=f"Section {ordinal + 1}",
                path=[f"Section {ordinal + 1}"],
                page_from=ordinal + 1,
                page_to=ordinal + 1,
                token_count=1,
                content_hash=hashlib.sha256(
                    content.encode("utf-8")
                ).hexdigest(),
                chunk_ids=[f"chunk-{ordinal}"],
            )
        )
        items = build_section_content_items(
            directive_id=_DIRECTIVE_ID,
            directive_version_id=_VERSION_ID,
            artifact_generation_id=_GENERATION_ID,
            section_id=section_id,
            section_ordinal=ordinal,
            content=content,
            run_id="test-run",
            created_at=datetime(2026, 7, 25, tzinfo=UTC),
            max_item_bytes=max_item_bytes,
        )
        content_items.extend(items)
        descriptors[section_id] = DirectiveSectionContentDescriptor(
            part_count=len(items)
        )
    manifest = DirectiveManifest(
        directive_id=_DIRECTIVE_ID,
        directive_version_id=_VERSION_ID,
        source_hash=_SOURCE_HASH,
        artifact_generation_id=_GENERATION_ID,
        total_pages=max(1, len(sections)),
        total_tokens=len(sections),
        sections=sections,
    )
    summary = DirectiveSummary(
        directive_id=_DIRECTIVE_ID,
        directive_version_id=_VERSION_ID,
        source_hash=_SOURCE_HASH,
        summary="Summary",
        covered_section_ids=[section.section_id for section in sections],
        total_section_count=len(sections),
        input_token_count=len(sections),
        strategy="full_document",
        model_deployment="test",
    )
    bundle = PublishedDirectiveVersion(
        id=published_directive_version_item_id(_DIRECTIVE_ID, "1"),
        directive_id=_DIRECTIVE_ID,
        directive_version_id=_VERSION_ID,
        version_label="1",
        title="Test directive",
        status="Current",
        is_current=True,
        is_valid=True,
        effective_from="2026-01-01",
        source_filename="test.pdf",
        source_hash=_SOURCE_HASH,
        processing_hash=_PROCESSING_HASH,
        artifact_generation_id=_GENERATION_ID,
        manifest=manifest,
        summary=summary,
        artifacts=DirectiveArtifactLocators(
            canonical_blob_name="directives/document.md",
            source_blob_name="directives/source.pdf",
        ),
        section_content=descriptors,
        run_id="test-run",
        published_at="2026-07-25T12:00:00Z",
    )
    return bundle, tuple(content_items)


class _CatalogContainer:
    def __init__(self, items: dict[tuple[str, str], dict]) -> None:
        self.items = items
        self.reads: list[tuple[str, str]] = []

    async def read_item(self, *, item: str, partition_key: str):
        self.reads.append((item, partition_key))
        value = self.items.get((item, partition_key))
        if value is None:
            raise exceptions.CosmosResourceNotFoundError(message="missing")
        return value


class _ContentContainer:
    def __init__(self, items: tuple[DirectiveSectionContent, ...]) -> None:
        self.items = {
            item.id: item.model_dump(mode="json") for item in items
        }
        self.reads: list[tuple[str, str]] = []
        self.active = 0
        self.max_active = 0

    async def read_item(self, *, item: str, partition_key: str):
        self.reads.append((item, partition_key))
        value = self.items.get(item)
        if value is None:
            raise exceptions.CosmosResourceNotFoundError(message="missing")
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(
                0.001 * (int(value["part_ordinal"]) % 3 + 1)
            )
            return value
        finally:
            self.active -= 1


class DirectiveCatalogBundleTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _committed_gate() -> dict[str, str | None]:
        return {
            "id": "directive-publication-gate",
            "directive_id": "_control",
            "type": "publication_gate",
            "state": "committed",
            "committed_revision": "rev-1",
            "candidate_revision": None,
            "run_id": "run-1",
            "updated_at": "2026-08-16T12:00:00+00:00",
            "_etag": "gate-etag",
        }

    async def test_exact_version_reads_gate_then_bundle(self) -> None:
        bundle, _ = _bundle_and_items(["content"])
        container = _CatalogContainer(
            {
                ("directive-publication-gate", "_control"): self._committed_gate(),
                (
                    published_directive_version_item_id(_DIRECTIVE_ID, "1"),
                    _DIRECTIVE_ID,
                ): {**bundle.model_dump(mode="json"), "_etag": "etag"},
            }
        )
        repository = DirectiveCatalogRepository()
        repository._container = container

        result = await repository.get_published_version(
            _DIRECTIVE_ID, _VERSION_ID
        )

        self.assertEqual(result, bundle)
        self.assertEqual(
            container.reads,
            [
                ("directive-publication-gate", "_control"),
                (
                    published_directive_version_item_id(_DIRECTIVE_ID, "1"),
                    _DIRECTIVE_ID,
                ),
            ],
        )

    async def test_legacy_version_is_rejected_without_fallback(self) -> None:
        container = _CatalogContainer(
            {
                ("directive-publication-gate", "_control"): self._committed_gate(),
                (
                    published_directive_version_item_id(_DIRECTIVE_ID, "1"),
                    _DIRECTIVE_ID,
                ): {
                    "id": f"version:{_VERSION_ID}",
                    "type": "version",
                    "directive_id": _DIRECTIVE_ID,
                    "directive_version_id": _VERSION_ID,
                    "publication_state": "published",
                }
            }
        )
        repository = DirectiveCatalogRepository()
        repository._container = container

        with self.assertRaises(DirectiveDataUnavailable):
            await repository.get_published_version(
                _DIRECTIVE_ID, _VERSION_ID
            )

        self.assertEqual(len(container.reads), 2)

    async def test_catalog_rejects_returned_human_identity_mismatch(self) -> None:
        bundle, _ = _bundle_and_items(["content"])
        item = bundle.model_dump(mode="json")
        item["directive_version_id"] = "ČD/42-A:v2"
        container = _CatalogContainer(
            {
                ("directive-publication-gate", "_control"): self._committed_gate(),
                (
                    published_directive_version_item_id(_DIRECTIVE_ID, "1"),
                    _DIRECTIVE_ID,
                ): item
            }
        )
        repository = DirectiveCatalogRepository()
        repository._container = container

        result = await repository.get_published_version(
            _DIRECTIVE_ID, _VERSION_ID
        )

        self.assertIsNone(result)

    async def test_publication_gate_blocks_online_reads(self) -> None:
        bundle, _ = _bundle_and_items(["content"])
        container = _CatalogContainer(
            {
                ("directive-publication-gate", "_control"): {
                    "id": "directive-publication-gate",
                    "directive_id": "_control",
                    "type": "publication_gate",
                    "state": "activating",
                    "committed_revision": "rev-1",
                    "candidate_revision": "rev-2",
                    "run_id": "run-1",
                    "updated_at": "2026-08-16T12:00:00+00:00",
                    "_etag": "gate-etag",
                },
                (
                    published_directive_version_item_id(_DIRECTIVE_ID, "1"),
                    _DIRECTIVE_ID,
                ): bundle.model_dump(mode="json"),
            }
        )
        repository = DirectiveCatalogRepository()
        repository._container = container

        with self.assertRaisesRegex(
            DirectiveDataUnavailable,
            "publication is unavailable",
        ):
            await repository.get_published_version(_DIRECTIVE_ID, _VERSION_ID)

    async def test_missing_publication_gate_fails_closed(self) -> None:
        bundle, _ = _bundle_and_items(["content"])
        container = _CatalogContainer(
            {
                (
                    published_directive_version_item_id(_DIRECTIVE_ID, "1"),
                    _DIRECTIVE_ID,
                ): bundle.model_dump(mode="json")
            }
        )
        repository = DirectiveCatalogRepository()
        repository._container = container

        with patch(
            "agent_memory_backend.directive_catalog.get_settings",
            return_value=SimpleNamespace(
                directive_publication_gate_enabled=True
            ),
        ):
            with self.assertRaisesRegex(
                DirectiveDataUnavailable,
                "publication gate is unavailable",
            ):
                await repository.get_published_version(
                    _DIRECTIVE_ID, _VERSION_ID
                )
        self.assertEqual(
            container.reads,
            [("directive-publication-gate", "_control")],
        )

    async def test_missing_publication_gate_is_legacy_committed_when_disabled(
        self,
    ) -> None:
        bundle, _ = _bundle_and_items(["content"])
        container = _CatalogContainer(
            {
                (
                    published_directive_version_item_id(_DIRECTIVE_ID, "1"),
                    _DIRECTIVE_ID,
                ): bundle.model_dump(mode="json")
            }
        )
        repository = DirectiveCatalogRepository()
        repository._container = container

        with patch(
            "agent_memory_backend.directive_catalog.get_settings",
            return_value=SimpleNamespace(
                directive_publication_gate_enabled=False
            ),
        ):
            result = await repository.get_published_version(
                _DIRECTIVE_ID, _VERSION_ID
            )

        self.assertEqual(result, bundle)
        self.assertEqual(
            container.reads,
            [
                ("directive-publication-gate", "_control"),
                (
                    published_directive_version_item_id(_DIRECTIVE_ID, "1"),
                    _DIRECTIVE_ID,
                ),
            ],
        )

    async def test_malformed_publication_gate_fails_closed(self) -> None:
        bundle, _ = _bundle_and_items(["content"])
        container = _CatalogContainer(
            {
                ("directive-publication-gate", "_control"): {
                    "id": "directive-publication-gate",
                    "directive_id": "_control",
                    "type": "publication_gate",
                    "state": "committed",
                    "committed_revision": "",
                    "candidate_revision": None,
                    "run_id": "run-1",
                    "updated_at": "2026-08-16T12:00:00+00:00",
                    "_etag": "gate-etag",
                },
                (
                    published_directive_version_item_id(_DIRECTIVE_ID, "1"),
                    _DIRECTIVE_ID,
                ): bundle.model_dump(mode="json"),
            }
        )
        repository = DirectiveCatalogRepository()
        repository._container = container

        with self.assertRaisesRegex(
            DirectiveDataUnavailable,
            "publication gate is invalid",
        ):
            await repository.get_published_version(_DIRECTIVE_ID, _VERSION_ID)
        self.assertEqual(
            container.reads,
            [("directive-publication-gate", "_control")],
        )

    async def test_recovery_required_publication_gate_fails_closed(self) -> None:
        bundle, _ = _bundle_and_items(["content"])
        container = _CatalogContainer(
            {
                ("directive-publication-gate", "_control"): {
                    "id": "directive-publication-gate",
                    "directive_id": "_control",
                    "type": "publication_gate",
                    "state": "recovery_required",
                    "committed_revision": "rev-1",
                    "candidate_revision": "rev-2",
                    "run_id": "run-1",
                    "updated_at": "2026-08-16T12:00:00+00:00",
                    "_etag": "gate-etag",
                },
                (
                    published_directive_version_item_id(_DIRECTIVE_ID, "1"),
                    _DIRECTIVE_ID,
                ): bundle.model_dump(mode="json"),
            }
        )
        repository = DirectiveCatalogRepository()
        repository._container = container

        with self.assertRaisesRegex(
            DirectiveDataUnavailable,
            "publication is unavailable",
        ):
            await repository.get_published_version(_DIRECTIVE_ID, _VERSION_ID)
        self.assertEqual(
            container.reads,
            [("directive-publication-gate", "_control")],
        )

    def test_public_catalog_metadata_omits_internal_hashes(self) -> None:
        bundle, _ = _bundle_and_items(["content"])

        public = DirectiveCatalogRepository.public_version(bundle)

        self.assertNotIn("source_hash", public)
        self.assertNotIn("processing_hash", public)


class DirectiveContentRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_split_parts_reconstruct_in_manifest_order(self) -> None:
        expected = "line 😀 with escaped \"text\"\n" * 40
        bundle, items = _bundle_and_items(
            [expected],
            max_item_bytes=900,
        )
        self.assertGreater(len(items), 1)
        container = _ContentContainer(items)
        repository = DirectiveContentRepository()
        repository._container = container

        values = await repository.read_sections(
            bundle, list(bundle.manifest.sections)
        )

        self.assertEqual(values, [expected])
        self.assertEqual(len(container.reads), len(items))

    async def test_reads_are_bounded_and_preserve_section_order(self) -> None:
        expected = [f"content-{index}" for index in range(20)]
        bundle, items = _bundle_and_items(expected)
        container = _ContentContainer(items)
        repository = DirectiveContentRepository()
        repository._container = container

        values = await repository.read_sections(
            bundle, list(bundle.manifest.sections)
        )

        self.assertEqual(values, expected)
        self.assertEqual(len(container.reads), 20)
        self.assertGreater(container.max_active, 1)
        self.assertLessEqual(container.max_active, 8)

    async def test_missing_part_never_returns_partial_content(self) -> None:
        bundle, items = _bundle_and_items(
            ["large section\n" * 60],
            max_item_bytes=900,
        )
        container = _ContentContainer(items)
        container.items.pop(items[-1].id)
        repository = DirectiveContentRepository()
        repository._container = container

        with self.assertRaises(DirectiveDataUnavailable):
            await repository.read_sections(
                bundle, list(bundle.manifest.sections)
            )
