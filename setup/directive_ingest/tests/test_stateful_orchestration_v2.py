from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from directive_contracts import (
    DirectiveChunk,
    DirectiveMetadata,
    ReviewFinding,
    DirectiveSummary,
    MandateSnapshot,
)

from directive_ingestion.canonical import CanonicalDirective, ParsedSection
from directive_ingestion.metadata import DirectiveMetadataCandidate
from directive_ingestion.publication_commit_repository import (
    PublicationCommitRepository,
    PublicationResetRequiredError,
)
from directive_ingestion.catalog_repository import CatalogSlotSnapshot
from directive_ingestion.integrity import IntegrityValidationError
from directive_ingestion.reconcile import (
    DirectiveIngestionRunner,
    _public_record_digest,
)
from directive_ingestion.source import SourceDocument, SourceProvenance
from directive_ingestion.source_state_repository import SourceStateRepository


PROCESSING_HASH = "a" * 64


class MemoryBlobs:
    def __init__(self) -> None:
        self.bytes: dict[str, bytes] = {}
        self.json: dict[str, dict[str, object]] = {}
        self.etags: dict[str, str] = {}
        self.write_count = 0
        self._etag_version = 0

    def _write_state(self, name: str, value: dict[str, object]) -> str:
        self.json[name] = deepcopy(value)
        self.write_count += 1
        self._etag_version += 1
        etag = f"etag-{self._etag_version}"
        self.etags[name] = etag
        return etag

    async def put_immutable(
        self, name: str, content: bytes, _content_type: str
    ) -> None:
        existing = self.bytes.get(name)
        if existing is not None and existing != content:
            raise RuntimeError(f"immutable collision: {name}")
        if existing is None:
            self.bytes[name] = content
            self.write_count += 1

    async def validate_hash(self, name: str, expected: str) -> None:
        if await self.content_hash(name) != expected:
            raise RuntimeError(f"hash mismatch: {name}")

    async def content_hash(self, name: str) -> str:
        try:
            return hashlib.sha256(self.bytes[name]).hexdigest()
        except KeyError as exc:
            raise RuntimeError(f"missing artifact: {name}") from exc

    async def exists(self, name: str) -> bool:
        return name in self.bytes

    async def read_text(self, name: str) -> str:
        return self.bytes[name].decode("utf-8")

    async def get_json(self, name: str) -> dict[str, object] | None:
        value = self.json.get(name)
        return deepcopy(value) if value is not None else None

    async def read_bytes_with_etag(
        self, name: str
    ) -> tuple[bytes, str] | None:
        value = self.json.get(name)
        if value is None:
            return None
        return (
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode(),
            self.etags.get(name, "etag-0"),
        )

    async def restore_bytes(
        self, name: str, content: bytes, candidate_etag: str
    ) -> None:
        if self.etags.get(name) != candidate_etag:
            raise RuntimeError("Source state changed concurrently")
        self._write_state(name, json.loads(content))

    async def replace_json(
        self,
        name: str,
        value: dict[str, object],
        *,
        expected_etag: str | None = None,
        require_absent: bool = False,
    ) -> str:
        if require_absent and name in self.json:
            raise RuntimeError("Source state changed concurrently")
        if expected_etag is not None and self.etags.get(name) != expected_etag:
            raise RuntimeError("Source state changed concurrently")
        return self._write_state(name, value)

    async def list_names(self, prefix: str) -> set[str]:
        return {
            name
            for name in {*self.bytes, *self.json}
            if name.startswith(prefix)
        }

    async def delete_names(self, names: set[str]) -> None:
        for name in names:
            if name in self.bytes or name in self.json:
                self.write_count += 1
            self.bytes.pop(name, None)
            self.json.pop(name, None)
            self.etags.pop(name, None)

    async def delete_if_etag(self, name: str, candidate_etag: str) -> None:
        if self.etags.get(name) != candidate_etag:
            raise RuntimeError("Source state changed concurrently")
        await self.delete_names({name})

    async def quarantine(self, *_args: object) -> None:
        raise AssertionError("the valid fake corpus must not be quarantined")


class MemoryCatalog:
    def __init__(self) -> None:
        self.bundles: dict[tuple[str, str], object] = {}
        self.current: dict[str, dict[str, str]] = {}
        self.recorded_runs: list[dict[str, object]] = []
        self.write_count = 0

    async def get_published_version(self, directive_id: str, version_id: str):
        return self.bundles.get((directive_id, version_id))

    async def get_current(self, directive_id: str):
        current = self.current.get(directive_id)
        return dict(current) if current is not None else None

    async def stage_version(self, *_args: object) -> None:
        self.write_count += 1

    async def publish_version(self, bundle, _relations: object) -> None:
        self.bundles[(bundle.directive_id, bundle.directive_version_id)] = bundle
        self.write_count += 1

    async def validate_published(self, bundle) -> None:
        if (
            self.bundles.get((bundle.directive_id, bundle.directive_version_id))
            != bundle
        ):
            raise RuntimeError("catalog bundle is not published")

    async def activate_current(self, metadata, _run_id: str) -> None:
        bundle = await self.get_published_version(
            metadata.directive_id, metadata.directive_version_id
        )
        if bundle is None:
            raise RuntimeError("cannot activate an unpublished bundle")
        self.current[metadata.directive_id] = {
            "directive_version_id": bundle.directive_version_id,
            "source_hash": bundle.source_hash,
            "processing_hash": bundle.processing_hash,
            "artifact_generation_id": bundle.artifact_generation_id,
        }
        self.write_count += 1

    async def restore_current(self, directive_id: str, previous) -> None:
        if previous is None:
            self.current.pop(directive_id, None)
        else:
            self.current[directive_id] = dict(previous)
        self.write_count += 1

    async def restore_version(self, bundle, previous) -> None:
        key = (bundle.directive_id, bundle.directive_version_id)
        if previous is None:
            self.bundles.pop(key, None)
        else:
            self.bundles[key] = previous
        self.write_count += 1

    async def list_published_versions(self):
        return list(self.bundles.values())

    async def list_published_directive_ids(self) -> set[str]:
        return {bundle.directive_id for bundle in self.bundles.values()}

    async def list_published_version_labels(self) -> set[tuple[str, str]]:
        return {
            (bundle.directive_id, bundle.version_label)
            for bundle in self.bundles.values()
        }

    async def list_current_pointers(self):
        return {
            directive_id: (
                value["directive_version_id"],
                value["source_hash"],
                value["processing_hash"],
                value["artifact_generation_id"],
            )
            for directive_id, value in self.current.items()
        }

    async def list_published_relations(self):
        return []

    async def delete_versions(self, bundles) -> None:
        for bundle in bundles:
            self.bundles.pop(
                (bundle.directive_id, bundle.directive_version_id), None
            )
            self.write_count += 1

    async def record_run(self, run_id: str, **details: object) -> None:
        self.recorded_runs.append({"run_id": run_id, **details})
        self.write_count += 1


class CorruptSlotMemoryCatalog(MemoryCatalog):
    """Catalog fake that preserves a raw malformed slot for ETag replacement."""

    def __init__(self) -> None:
        super().__init__()
        self.corrupt_slots: dict[tuple[str, str], CatalogSlotSnapshot] = {}
        self._candidate_version = 0

    def add_corrupt_slot(self, metadata: DirectiveMetadata) -> None:
        key = (metadata.directive_id, metadata.directive_version_id)
        self.corrupt_slots[key] = CatalogSlotSnapshot(
            metadata.directive_id,
            metadata.directive_version_id,
            {"id": "corrupt-version", "type": "version", "malformed": True},
            "corrupt-etag",
        )

    async def get_published_version(self, directive_id: str, version_id: str):
        if (directive_id, version_id) in self.corrupt_slots:
            raise IntegrityValidationError("malformed catalog descriptor")
        return await super().get_published_version(directive_id, version_id)

    async def snapshot_version(self, directive_id: str, version_id: str):
        return self.corrupt_slots.get((directive_id, version_id))

    async def publish_version(
        self, bundle, _relations: object, *, expected_snapshot=None
    ) -> str:
        key = (bundle.directive_id, bundle.directive_version_id)
        corrupt_slot = self.corrupt_slots.get(key)
        if corrupt_slot is not None:
            assert expected_snapshot == corrupt_slot
            del self.corrupt_slots[key]
        elif expected_snapshot is not None:
            raise RuntimeError("unexpected catalog snapshot")
        self.bundles[key] = bundle
        self.write_count += 1
        self._candidate_version += 1
        return f"candidate-etag-{self._candidate_version}"

    async def restore_version(
        self, bundle, previous, _candidate_etag: str | None = None
    ) -> None:
        await super().restore_version(bundle, previous)


class MemoryContent:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], object] = {}
        self.write_count = 0

    async def create_or_compare(self, item) -> None:
        key = (item.directive_version_id, item.id)
        existing = self.items.get(key)
        if existing is not None and existing != item:
            raise RuntimeError("content collision")
        if existing is None:
            self.items[key] = item
            self.write_count += 1

    async def read_item(self, version_id: str, item_id: str):
        return self.items[(version_id, item_id)]

    async def validate_bundle(self, bundle) -> dict[str, int]:
        part_count = 0
        for section_id, descriptor in bundle.section_content.items():
            parts = [
                await self.read_item(
                    bundle.directive_version_id,
                    _section_content_id(
                        bundle.artifact_generation_id, section_id, ordinal
                    ),
                )
                for ordinal in range(descriptor.part_count)
            ]
            if hashlib.sha256(
                "".join(item.content for item in parts).encode()
            ).hexdigest() != next(
                section.content_hash
                for section in bundle.manifest.sections
                if section.section_id == section_id
            ):
                raise RuntimeError("content reconstruction mismatch")
            part_count += len(parts)
        return {
            "content_sections": len(bundle.section_content),
            "content_parts": part_count,
            "split_sections": 0,
        }

    async def delete_bundle(self, bundle) -> None:
        for section_id, descriptor in bundle.section_content.items():
            for ordinal in range(descriptor.part_count):
                self.items.pop(
                    (
                        bundle.directive_version_id,
                        _section_content_id(
                            bundle.artifact_generation_id, section_id, ordinal
                        ),
                    ),
                    None,
                )
                self.write_count += 1

    async def list_identities(self) -> set[tuple[str, str, str, str, str]]:
        return {
            (
                version_id,
                item_id,
                item.directive_id,
                item.section_hash,
                item.part_hash,
            )
            for (version_id, item_id), item in self.items.items()
        }


class MemorySearch:
    def __init__(self) -> None:
        self.chunks: dict[str, DirectiveChunk] = {}
        self.ensure_count = 0
        self.write_count = 0

    async def ensure_resources(self) -> None:
        self.ensure_count += 1

    async def build_chunks(self, directive, chunks) -> list[DirectiveChunk]:
        return [
            DirectiveChunk(
                id=chunk.id,
                directive_id=directive.metadata.directive_id,
                directive_version_id=directive.metadata.directive_version_id,
                version_label=directive.metadata.version_label,
                title=directive.metadata.title,
                aliases=[],
                is_current=False,
                is_valid=True,
                status="Current",
                effective_from=directive.metadata.effective_from,
                section_id=chunk.section_id,
                section_title="Scope",
                section_path=["Scope"],
                chunk_ordinal=chunk.ordinal,
                content_kind=chunk.content_kind,
                page_from=chunk.page_from,
                page_to=chunk.page_to,
                content=chunk.content,
                content_vector=[0.0, 1.0],
                language="cs",
                source_hash=directive.metadata.source_hash,
                processing_hash=directive.metadata.processing_hash,
            )
            for chunk in chunks
        ]

    async def stage_chunks(self, chunks: list[DirectiveChunk]) -> None:
        self.chunks.update({chunk.id: chunk for chunk in chunks})
        self.write_count += len(chunks)

    async def publish_chunks(self, chunks: list[DirectiveChunk]) -> None:
        for chunk in chunks:
            self.chunks[chunk.id] = chunk.model_copy(
                update={"publication_state": "published"}
            )
            self.write_count += 1

    async def validate_published_chunk_ids(self, _directive, chunk_ids) -> None:
        for chunk_id in chunk_ids:
            if self.chunks[chunk_id].publication_state != "published":
                raise RuntimeError("chunk is not published")

    async def reconcile_current(self, bundle) -> None:
        for chunk_id, chunk in list(self.chunks.items()):
            if chunk.directive_id == bundle.directive_id:
                self.chunks[chunk_id] = chunk.model_copy(
                    update={
                        "is_current": (
                            chunk.directive_version_id
                            == bundle.directive_version_id
                            and chunk.source_hash == bundle.source_hash
                        )
                    }
                )
                self.write_count += 1

    async def validate_current_generation(self, bundle) -> None:
        ids = {
            chunk_id
            for section in bundle.manifest.sections
            for chunk_id in section.chunk_ids
        }
        if not ids or not all(
            self.chunks[chunk_id].is_current
            and self.chunks[chunk_id].publication_state == "published"
            for chunk_id in ids
        ):
            raise RuntimeError("current search generation is incomplete")

    async def delete_generation(self, bundle) -> None:
        for section in bundle.manifest.sections:
            for chunk_id in section.chunk_ids:
                self.chunks.pop(chunk_id, None)
                self.write_count += 1

    async def delete_chunks(self, chunks) -> None:
        for chunk in chunks:
            self.chunks.pop(chunk.id, None)
            self.write_count += 1

    async def restore_current_generation(self, bundle) -> None:
        await self.reconcile_current(bundle)

    async def verification_summary(self) -> dict[str, object]:
        published = [
            chunk
            for chunk in self.chunks.values()
            if chunk.publication_state == "published"
        ]
        current = [chunk for chunk in published if chunk.is_current]
        return {
            "published_chunks": len(published),
            "published_directives": len(
                {chunk.directive_id for chunk in published}
            ),
            "published_versions": len(
                {chunk.directive_version_id for chunk in published}
            ),
            "current_chunks": len(current),
            "current_directives": len(
                {chunk.directive_id for chunk in current}
            ),
            "current_versions": len(
                {chunk.directive_version_id for chunk in current}
            ),
            "vector_dimensions": 2,
            "vector_profile": "directive-vector-profile",
            "vectorizer": "directive-openai-vectorizer",
            "semantic_configuration": "semantic_config",
            "direct_hybrid_query": "ok",
        }

    async def validate_exact_published(self, bundles) -> None:
        expected = {
            chunk_id
            for bundle in bundles
            for section in bundle.manifest.sections
            for chunk_id in section.chunk_ids
        }
        actual = {
            chunk_id
            for chunk_id, chunk in self.chunks.items()
            if chunk.publication_state == "published"
        }
        if actual != expected:
            raise RuntimeError("search records do not match bundles")


class MemoryMandates:
    def __init__(self) -> None:
        self.active: MandateSnapshot | None = None
        self.staged: MandateSnapshot | None = None
        self.restore_calls: list[tuple[object, str]] = []
        self.discard_calls: list[tuple[object, str]] = []

    async def is_current(self, parsed) -> bool:
        return self.active is not None and self.active.checksum == parsed.checksum

    async def stage(self, parsed, _run_id: str):
        snapshot = MandateSnapshot(
            snapshot_id=f"mandates-{parsed.checksum}",
            checksum=parsed.checksum,
            assignment_count=len(parsed.assignments),
            user_count=parsed.user_count,
            complete=True,
            previous_snapshot_id=(
                self.active.snapshot_id if self.active is not None else None
            ),
        )
        changed = self.active != snapshot
        self.staged = snapshot
        return snapshot, self.active, changed

    async def activate(self, snapshot, _run_id: str, _previous) -> str:
        self.active = snapshot
        return "memory-candidate-etag"

    async def cleanup(self, _snapshot_id: str) -> bool:
        return False

    async def restore_active(self, previous, _candidate_etag: str) -> None:
        self.restore_calls.append((previous, _candidate_etag))
        self.active = previous

    async def discard_staged(self, _snapshot, _run_id: str) -> None:
        self.discard_calls.append((_snapshot, _run_id))
        self.staged = None

    async def validate_exact(self, parsed) -> dict[str, object]:
        assert self.active is not None
        return {
            "snapshot_id": self.active.snapshot_id,
            "checksum": self.active.checksum,
            "assignment_count": self.active.assignment_count,
            "user_count": self.active.user_count,
        }


class StatefulSourceStates(SourceStateRepository):
    def __init__(self, blobs: MemoryBlobs) -> None:
        super().__init__(blobs)
        self.break_candidate_once = False
        self.concurrent_candidate_once = False
        self._hide_next_load = False

    async def record(self, *args, **kwargs) -> str:
        candidate_etag = await super().record(*args, **kwargs)
        if self.concurrent_candidate_once:
            self.concurrent_candidate_once = False
            await super().record(*args, **kwargs)
        if self.break_candidate_once:
            self.break_candidate_once = False
            self._hide_next_load = True
        return candidate_etag

    async def load(self, *args, **kwargs):
        if self._hide_next_load:
            self._hide_next_load = False
            return None
        return await super().load(*args, **kwargs)


def _section_content_id(generation_id: str, section_id: str, ordinal: int) -> str:
    from directive_contracts import section_content_item_id

    return section_content_item_id(generation_id, section_id, ordinal)


def _source(content: bytes = b"%PDF-directive-v1") -> SourceDocument:
    return SourceDocument(
        source_name="directive.pdf",
        source_hash=hashlib.sha256(content).hexdigest(),
        content=content,
        _provenance=SourceProvenance(kind="memory", locator="directive.pdf"),
    )


def _metadata(source: SourceDocument) -> DirectiveMetadata:
    return DirectiveMetadata(
        directive_id="72403881",
        directive_version_id="72403881:v1",
        version_label="1",
        title="Memory directive",
        status="Current",
        is_current=True,
        is_valid=True,
        effective_from=date(2026, 1, 1),
        source_filename=source.source_name,
        source_hash=source.source_hash,
        processing_hash=PROCESSING_HASH,
    )


def _canonical(source: SourceDocument) -> CanonicalDirective:
    metadata = _metadata(source)
    content = f"Scope for {source.source_hash}\n"
    section = ParsedSection(
        section_id="scope",
        ordinal=1,
        number="1",
        title="Scope",
        path=("Scope",),
        page_from=1,
        page_to=1,
        content=content,
        token_count=5,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
    )
    candidate = DirectiveMetadataCandidate(
        metadata=metadata,
        evidence=(),
        first_two_pages_markdown="",
        findings=(),
    )
    return CanonicalDirective(
        metadata=metadata,
        markdown=f"# Memory directive\n\n{content}",
        control={},
        sections=(section,),
        relations=(),
        findings=(),
        total_pages=1,
        total_tokens=5,
        metadata_candidate=candidate,
    )


class Harness:
    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        catalog_type: type[MemoryCatalog] = MemoryCatalog,
    ) -> None:
        self.sources = [_source()]
        self.blobs = MemoryBlobs()
        self.catalog = catalog_type()
        self.content = MemoryContent()
        self.search = MemorySearch()
        self.states = StatefulSourceStates(self.blobs)
        self.runner = object.__new__(DirectiveIngestionRunner)
        self.runner.config = SimpleNamespace(
            processing_hash=PROCESSING_HASH,
            processing_version="directive-v2-stateful-regression",
            chunk_token_limit=800,
            chunk_overlap_tokens=120,
            mandate_csv=Path("unused.csv"),
            azure_tenant_id="tenant",
            source_kind="local",
            source_storage_account="source",
            source_container="source-container",
            source_prefix="",
            artifact_storage_account="artifacts",
            blob_container="artifact-container",
            cosmos_account="cosmos",
            cosmos_database="directives",
            catalog_container="catalog",
            content_container="content",
            mandate_container="mandates",
            search_service="search",
            search_index="directive-chunks-v2",
        )
        self.runner.blobs = self.blobs
        self.runner.catalog = self.catalog
        self.runner.content = self.content
        self.runner.search = self.search
        self.runner.source_states = self.states
        self.runner.commits = PublicationCommitRepository(self.blobs)
        self.runner.mandates = MemoryMandates()
        self.runner.extractor = SimpleNamespace(extract=self._extract)
        self.runner.summaries = SimpleNamespace(summarize=self._summarize)
        self.runner.discover_sources = self._discover
        monkeypatch.setattr(
            "directive_ingestion.metadata.extract_metadata",
            lambda source, _extraction, _hash: _canonical(source).metadata_candidate,
        )
        monkeypatch.setattr(
            "directive_ingestion.reconcile.parse_canonical",
            lambda source, _extraction, _hash: _canonical(source),
        )
        monkeypatch.setattr(
            "directive_ingestion.reconcile.parse_mandates",
            lambda *_args: SimpleNamespace(
                assignments=(), checksum="b" * 64, user_count=0
            ),
        )

    async def _discover(self, _source_directory=None):
        return list(self.sources)

    async def _extract(self, _content: bytes):
        return SimpleNamespace()

    async def _summarize(self, directive: CanonicalDirective) -> DirectiveSummary:
        return DirectiveSummary(
            directive_id=directive.metadata.directive_id,
            directive_version_id=directive.metadata.directive_version_id,
            source_hash=directive.metadata.source_hash,
            summary="memory summary",
            covered_section_ids=["scope"],
            total_section_count=1,
            input_token_count=5,
            strategy="full_document",
            model_deployment="memory",
        )


@pytest.mark.asyncio
async def test_cached_validation_preserves_fresh_canonical_warning_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = Harness(monkeypatch)

    def canonical_with_warning(source: SourceDocument) -> CanonicalDirective:
        return replace(
            _canonical(source),
            findings=(
                ReviewFinding(
                    code="canonical_section_warning",
                    severity="warning",
                    message="Canonical warning",
                ),
            ),
        )

    monkeypatch.setattr(
        "directive_ingestion.reconcile.parse_canonical",
        lambda source, _extraction, _hash: canonical_with_warning(source),
    )
    fresh = await harness.runner.validate_inputs()

    await harness.runner.run_daily()
    cached = await harness.runner.validate_inputs()

    assert fresh["run_id"] != cached["run_id"]
    assert fresh["warnings"] == cached["warnings"] == [
        {"code": "canonical_section_warning", "severity": "warning"}
    ]
    assert fresh["validation_digest"] == cached["validation_digest"]
    state = await harness.states.load(harness.sources[0], PROCESSING_HASH)
    assert state is not None
    assert state.validation_warnings == (("canonical_section_warning", "warning"),)


@pytest.mark.asyncio
async def test_initial_publication_then_noop_daily_run_only_cycles_global_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = Harness(monkeypatch)

    validation = await harness.runner.validate_inputs()
    assert validation["record_schema"] == "directive.validate.v2"
    assert validation["directive_count"] == 1
    assert harness.blobs.write_count == 0

    initial = await harness.runner.run_daily()
    assert initial.changed_count == 1
    assert initial.skipped_count == 0
    assert len(harness.catalog.bundles) == 1
    assert len(harness.search.chunks) == 1

    writes_before_noop = (
        harness.blobs.write_count,
        harness.catalog.write_count,
        harness.content.write_count,
        harness.search.write_count,
    )
    noop = await harness.runner.run_daily()

    assert noop.changed_count == 0
    assert noop.skipped_count == 1
    assert harness.search.ensure_count == 1
    assert harness.blobs.write_count == writes_before_noop[0] + 2
    assert (
        harness.catalog.write_count,
        harness.content.write_count,
        harness.search.write_count,
    ) == writes_before_noop[1:]


@pytest.mark.asyncio
async def test_corrupt_catalog_slot_repair_survives_candidate_cleanup_and_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = Harness(monkeypatch, CorruptSlotMemoryCatalog)
    source = harness.sources[0]
    metadata = _metadata(source)
    previous_generation = "c" * 64
    await harness.states.record(source, metadata, previous_generation)
    harness.catalog.add_corrupt_slot(metadata)
    validation = await harness.runner.validate_inputs()

    repaired = await harness.runner.run_daily()

    assert repaired.changed_count == 1
    bundle = next(iter(harness.catalog.bundles.values()))
    assert bundle.artifact_generation_id != previous_generation
    assert harness.catalog.corrupt_slots == {}
    state = await harness.states.load(source, PROCESSING_HASH)
    assert state is not None
    assert state.artifact_generation_id == bundle.artifact_generation_id
    assert state.repair_generation_salt is not None
    assert state.pending_cleanup == ()
    assert await harness.runner.commits.load() is None
    assert await harness.blobs.list_names("publication-lock/") == set()
    assert await harness.blobs.list_names("publication-claims/") == {
        f"publication-claims/{validation['validation_digest']}.json"
    }

    writes_before_noop = (
        harness.blobs.write_count,
        harness.catalog.write_count,
        harness.content.write_count,
        harness.search.write_count,
    )
    noop = await harness.runner.run_daily()

    assert noop.changed_count == 0
    assert noop.skipped_count == 1
    assert harness.blobs.write_count == writes_before_noop[0] + 2
    assert (
        harness.catalog.write_count,
        harness.content.write_count,
        harness.search.write_count,
    ) == writes_before_noop[1:]
    assert await harness.blobs.list_names("publication-lock/") == set()
    await harness.runner.verify()


@pytest.mark.asyncio
async def test_concurrent_run_with_any_validation_digest_fails_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = Harness(monkeypatch)
    await harness.runner.commits.acquire_publication_lock(
        "first-run", "a" * 64
    )
    writes_before_second_run = (
        harness.catalog.write_count,
        harness.content.write_count,
        harness.search.write_count,
    )

    with pytest.raises(PublicationResetRequiredError, match="reset-required"):
        await harness.runner.run_daily()

    assert (
        harness.catalog.write_count,
        harness.content.write_count,
        harness.search.write_count,
    ) == writes_before_second_run
    assert await harness.blobs.list_names("publication-claims/") == set()


@pytest.mark.asyncio
async def test_mandate_prune_remains_inside_global_publication_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = Harness(monkeypatch)
    validation = await harness.runner.validate_inputs()
    observed: list[str] = []

    async def cleanup(snapshot_id: str) -> bool:
        lock = await harness.blobs.get_json("publication-lock/current.json")
        assert lock is not None
        assert lock["validation_digest"] == validation["validation_digest"]
        assert await harness.blobs.list_names("publication-claims/")
        observed.append(snapshot_id)
        return False

    harness.runner.mandates.cleanup = cleanup
    await harness.runner.run_daily()

    assert observed == ["mandates-" + "b" * 64]
    assert await harness.blobs.list_names("publication-lock/") == set()
    assert await harness.blobs.list_names("publication-claims/") == {
        f"publication-claims/{validation['validation_digest']}.json"
    }


@pytest.mark.asyncio
async def test_crash_stale_lock_requires_explicit_reset_that_purges_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = Harness(monkeypatch)
    await harness.runner.commits.acquire_publication_lock("crashed", "c" * 64)
    await harness.runner.commits.create_publication_claim("crashed", "c" * 64)

    with pytest.raises(PublicationResetRequiredError, match="reset-required"):
        await harness.runner.run_daily()

    await harness.runner.reset_publication_guards()

    assert await harness.blobs.list_names("publication-lock/") == set()
    assert await harness.blobs.list_names("publication-claims/") == set()
    await harness.runner.run_daily()


@pytest.mark.asyncio
async def test_verify_exposes_and_binds_the_mandate_checksum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = Harness(monkeypatch)
    await harness.runner.run_daily()

    verification = await harness.runner.verify()

    assert verification["mandate_checksum"] == "b" * 64
    assert verification["cross_store"]["mandates"]["checksum"] == verification[
        "mandate_checksum"
    ]
    assert verification["state_digest"] == _public_record_digest(
        {
            key: verification[key]
            for key in (
                "record_schema",
                "environment",
                "environment_digest",
                "processing_version",
                "processing_hash",
                "search_index",
                "source_count",
                "source_inventory_digest",
                "directive_count",
                "normalized_directive_ids",
                "directive_version_ids",
                "mandate_checksum",
                "cross_store",
                "validation_digest",
            )
        }
    )


@pytest.mark.asyncio
async def test_marker_write_failure_rolls_back_changed_mandates_without_documents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = Harness(monkeypatch)
    previous_active = MandateSnapshot(
        snapshot_id="mandates-" + "b" * 64,
        checksum="b" * 64,
        assignment_count=0,
        user_count=0,
        complete=True,
    )
    harness.runner.mandates.active = previous_active
    documents_before = (
        dict(harness.catalog.bundles),
        deepcopy(harness.catalog.current),
        dict(harness.content.items),
        dict(harness.search.chunks),
    )
    _, mandate_transaction = await harness.runner._publish_transaction(
        [],
        SimpleNamespace(assignments=(), checksum="c" * 64, user_count=0),
        "run",
    )
    assert mandate_transaction is not None
    assert harness.runner.mandates.active != previous_active

    async def fail_marker_write(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("marker write failed")

    harness.runner.commits.record = fail_marker_write

    with pytest.raises(RuntimeError, match="marker write failed"):
        await harness.runner._reconcile_after_publication(
            [], [], "run", mandate_transaction, marker_before=None
        )

    assert harness.runner.mandates.active == previous_active
    assert harness.runner.mandates.staged is None
    assert harness.runner.mandates.restore_calls == [
        (previous_active, "memory-candidate-etag")
    ]
    assert harness.runner.mandates.discard_calls == [
        (mandate_transaction.snapshot, "run")
    ]
    assert (
        dict(harness.catalog.bundles),
        harness.catalog.current,
        dict(harness.content.items),
        dict(harness.search.chunks),
    ) == documents_before
    assert await harness.runner.commits.load() is None


@pytest.mark.asyncio
async def test_pre_marker_rollback_then_post_marker_retry_preserves_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = Harness(monkeypatch)
    harness.runner.mandates.active = MandateSnapshot(
        snapshot_id="mandates-" + "b" * 64,
        checksum="b" * 64,
        assignment_count=0,
        user_count=0,
        complete=True,
    )
    harness.states.break_candidate_once = True

    with pytest.raises(
        RuntimeError, match="Candidate publication does not match its source state"
    ):
        await harness.runner.reconcile_documents()

    assert harness.catalog.bundles == {}
    assert harness.catalog.current == {}
    assert harness.search.chunks == {}
    assert await harness.blobs.list_names("directives/") == set()
    assert await harness.states.list_names() == set()
    assert await harness.runner.commits.load() is None

    await harness.runner.reset_publication_guards()
    await harness.runner.reconcile_documents()
    old_bundle = next(iter(harness.catalog.bundles.values()))
    old_generation = old_bundle.artifact_generation_id

    harness.sources = [_source(b"%PDF-directive-v2")]
    original_delete_generation = harness.search.delete_generation
    failed_cleanup = False

    async def fail_once(bundle) -> None:
        nonlocal failed_cleanup
        if not failed_cleanup:
            failed_cleanup = True
            raise RuntimeError("cleanup interrupted")
        await original_delete_generation(bundle)

    harness.search.delete_generation = fail_once
    with pytest.raises(RuntimeError, match="cleanup interrupted"):
        await harness.runner.reconcile_documents()

    committed = next(iter(harness.catalog.bundles.values()))
    assert committed.artifact_generation_id != old_generation
    assert harness.catalog.current["72403881"]["artifact_generation_id"] == (
        committed.artifact_generation_id
    )
    assert await harness.runner.commits.load() is not None
    assert any(
        chunk.source_hash == committed.source_hash and chunk.is_current
        for chunk in harness.search.chunks.values()
    )

    await harness.runner.reset_publication_guards()
    await harness.runner.reconcile_documents()

    assert await harness.runner.commits.load() is None
    assert all(
        chunk.source_hash == committed.source_hash
        for chunk in harness.search.chunks.values()
    )
    assert old_bundle.artifacts.source_blob_name not in harness.blobs.bytes
    assert old_bundle.artifacts.canonical_blob_name not in harness.blobs.bytes
    assert harness.catalog.current["72403881"]["artifact_generation_id"] == (
        committed.artifact_generation_id
    )


@pytest.mark.asyncio
async def test_source_state_restore_uses_the_candidate_write_etag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = Harness(monkeypatch)
    source = harness.sources[0]
    metadata = _metadata(source)

    await harness.states.record(source, metadata, "old-generation")
    snapshot = await harness.states.snapshot(source, PROCESSING_HASH)
    candidate_etag = await harness.states.record(
        source, metadata, "candidate-generation"
    )

    await harness.states.restore(
        snapshot, source, PROCESSING_HASH, candidate_etag
    )

    restored = await harness.states.load(source, PROCESSING_HASH)
    assert restored is not None
    assert restored.artifact_generation_id == "old-generation"


@pytest.mark.asyncio
async def test_pre_marker_rollback_propagates_concurrent_source_state_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = Harness(monkeypatch)
    harness.runner.mandates.active = MandateSnapshot(
        snapshot_id="mandates-" + "b" * 64,
        checksum="b" * 64,
        assignment_count=0,
        user_count=0,
        complete=True,
    )
    harness.states.break_candidate_once = True
    harness.states.concurrent_candidate_once = True

    with pytest.raises(RuntimeError, match="Source state changed concurrently"):
        await harness.runner.reconcile_documents()

    state_name = harness.states.blob_name(harness.sources[0], PROCESSING_HASH)
    assert state_name in await harness.states.list_names()


@pytest.mark.asyncio
async def test_malformed_source_state_repairs_without_replacing_search_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = Harness(monkeypatch)
    harness.runner.mandates.active = MandateSnapshot(
        snapshot_id="mandates-" + "b" * 64,
        checksum="b" * 64,
        assignment_count=0,
        user_count=0,
        complete=True,
    )
    await harness.runner.reconcile_documents()
    bundle = next(iter(harness.catalog.bundles.values()))
    generation = bundle.artifact_generation_id
    chunk_ids = set(harness.search.chunks)
    search_writes = harness.search.write_count
    catalog_writes = harness.catalog.write_count
    state_name = harness.states.blob_name(harness.sources[0], PROCESSING_HASH)
    harness.blobs.json[state_name] = {"type": "source_state"}

    repaired = await harness.runner.reconcile_documents()

    assert repaired.changed_count == 0
    repaired_state = await harness.states.load(
        harness.sources[0], PROCESSING_HASH
    )
    assert repaired_state is not None
    assert repaired_state.artifact_generation_id == generation
    assert set(harness.search.chunks) == chunk_ids
    assert harness.search.write_count == search_writes
    assert harness.catalog.write_count == catalog_writes
