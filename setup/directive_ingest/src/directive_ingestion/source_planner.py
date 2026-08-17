"""Descriptor-first source planning with cache-backed validation evidence."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import nullcontext
from dataclasses import dataclass

from .canonical import parse_canonical
from .document_intelligence import DocumentIntelligenceExtractor
from .extraction_cache import (
    CachedExtraction,
    ExtractionCacheRepository,
    ExtractorIdentity,
)
from .metadata import extract_metadata
from .run_metrics import IngestionRunMetrics
from .source import (
    DirectiveSource,
    SourceDescriptor,
    SourceDocument,
    SourceIdentity,
)
from .source_inventory import (
    SourceInventoryEntry,
    SourceInventoryRepository,
    SourceInventorySnapshot,
)
from .source_state_repository import (
    PublishedSourceState,
    SourceStateRepository,
)
from .validation_evidence import ValidationEvidenceDocument

LiveStatePredicate = Callable[
    [SourceIdentity, PublishedSourceState],
    Awaitable[bool],
]


@dataclass(frozen=True, slots=True)
class SourceValidationPlan:
    documents: tuple[ValidationEvidenceDocument, ...]
    inventory_snapshot: SourceInventorySnapshot


class DirectiveSourcePlanner:
    def __init__(
        self,
        *,
        source: DirectiveSource,
        inventory: SourceInventoryRepository,
        states: SourceStateRepository,
        cache: ExtractionCacheRepository,
        extractor: DocumentIntelligenceExtractor,
        extractor_identity: ExtractorIdentity,
        processing_hash: str,
        extraction_concurrency: int,
        is_live: LiveStatePredicate,
        metrics: IngestionRunMetrics | None = None,
    ) -> None:
        if extraction_concurrency < 1:
            raise ValueError("Extraction concurrency must be positive")
        self._source = source
        self._inventory = inventory
        self._states = states
        self._cache = cache
        self._extractor = extractor
        self._extractor_identity = extractor_identity
        self._processing_hash = processing_hash
        self._extraction_semaphore = asyncio.Semaphore(extraction_concurrency)
        self._is_live = is_live
        self._metrics = metrics

    def attach_metrics(self, metrics: IngestionRunMetrics | None) -> None:
        self._metrics = metrics

    async def validate(self) -> SourceValidationPlan:
        with self._stage("source_listing"):
            descriptors = await self._source.list_descriptors()
        self._increment("descriptor_count", len(descriptors))
        self._increment(
            "source_listed_bytes",
            sum(descriptor.size for descriptor in descriptors),
        )
        snapshot = await self._inventory.load_snapshot()
        inventory_by_name = (
            snapshot.inventory.entry_by_name()
            if snapshot.valid and snapshot.inventory is not None
            else {}
        )
        plans = await asyncio.gather(
            *(
                self._validate_descriptor(
                    descriptor,
                    inventory_by_name.get(descriptor.source_name),
                )
                for descriptor in descriptors
            )
        )
        _validate_plan_set(plans)
        return SourceValidationPlan(
            documents=tuple(plans),
            inventory_snapshot=snapshot,
        )

    async def revalidate_descriptors(
        self,
        approved: tuple[ValidationEvidenceDocument, ...],
    ) -> tuple[SourceDescriptor, ...]:
        with self._stage("source_listing"):
            descriptors = tuple(await self._source.list_descriptors())
        self._increment("descriptor_count", len(descriptors))
        self._increment(
            "source_listed_bytes",
            sum(descriptor.size for descriptor in descriptors),
        )
        by_name = {descriptor.source_name: descriptor for descriptor in descriptors}
        approved_by_name = {
            document.descriptor.source_name: document for document in approved
        }
        if set(by_name) != set(approved_by_name):
            raise RuntimeError(
                "Directive source set changed after validation approval"
            )
        for source_name, evidence in approved_by_name.items():
            if by_name[source_name] != evidence.descriptor:
                raise RuntimeError(
                    "Directive source descriptor changed after validation approval"
                )
            if evidence.disposition == "unchanged":
                state = await self._states.load_identity(
                    evidence.identity,
                    self._processing_hash,
                    blob_name=evidence.source_state_blob,
                )
                if (
                    state is None
                    or not state.matches_descriptor(evidence.descriptor)
                    or state.extraction_evidence != evidence.extraction
                    or not await self._is_live(evidence.identity, state)
                ):
                    raise RuntimeError(
                        "Approved unchanged directive state is no longer live"
                    )
        return descriptors

    async def download_approved(
        self,
        evidence: ValidationEvidenceDocument,
    ) -> tuple[SourceDocument, CachedExtraction]:
        with self._stage("download"):
            source = await self._source.download(evidence.descriptor)
        self._increment("source_download_count")
        self._increment("source_download_bytes", len(source.content))
        if source.identity != evidence.identity:
            raise RuntimeError(
                "Directive source content changed after validation approval"
            )
        with self._stage("cache_lookup"):
            cached = await self._cache.load(
                source.identity,
                self._extractor_identity,
                expected_result_hash=evidence.extraction.result_hash,
            )
        self._increment(
            "cache_hit_count" if cached is not None else "cache_miss_count"
        )
        if cached is None or cached.evidence != evidence.extraction:
            raise RuntimeError("Approved extraction cache entry is unavailable")
        return source, cached

    async def _validate_descriptor(
        self,
        descriptor: SourceDescriptor,
        inventory_entry: SourceInventoryEntry | None,
    ) -> ValidationEvidenceDocument:
        unchanged = await self._load_unchanged_state(
            descriptor,
            inventory_entry,
        )
        if unchanged is not None:
            return unchanged
        async with self._extraction_semaphore:
            with self._stage("download"):
                source = await self._source.download(descriptor)
            self._increment("source_download_count")
            self._increment("source_download_bytes", len(source.content))
            with self._stage("cache_lookup"):
                cached = await self._cache.load(
                    source.identity,
                    self._extractor_identity,
                )
            if cached is None:
                self._increment("cache_miss_count")
                self._increment("cache_fallback_count")
                with self._stage("extraction"):
                    extraction = await self._extractor.extract(source.content)
                with self._stage("cache_write"):
                    cached = await self._cache.store(
                        source.identity,
                        self._extractor_identity,
                        extraction,
                    )
            else:
                self._increment("cache_hit_count")
            with self._stage("metadata"):
                candidate = extract_metadata(
                    source,
                    cached.document,
                    self._processing_hash,
                )
            with self._stage("canonicalization"):
                canonical = parse_canonical(
                    source,
                    cached.document,
                    self._processing_hash,
                    metadata_candidate=candidate,
                )
            warnings = tuple(
                sorted(
                    {
                        (finding.code, finding.severity)
                        for finding in canonical.findings
                        if finding.severity == "warning"
                    }
                )[:100]
            )
            return ValidationEvidenceDocument(
                descriptor=descriptor,
                identity=source.identity,
                metadata=candidate.metadata,
                source_state_blob=self._states.blob_name(
                    source.identity,
                    self._processing_hash,
                ),
                disposition="changed",
                extraction=cached.evidence,
                validation_warnings=warnings,
            )

    async def _load_unchanged_state(
        self,
        descriptor: SourceDescriptor,
        inventory_entry: SourceInventoryEntry | None,
    ) -> ValidationEvidenceDocument | None:
        if inventory_entry is None or not inventory_entry.matches(descriptor):
            return None
        identity = SourceIdentity(
            source_name=inventory_entry.source_name,
            source_hash=inventory_entry.source_hash,
        )
        state = await self._states.load_identity(
            identity,
            self._processing_hash,
            blob_name=inventory_entry.source_state_blob,
        )
        if (
            state is None
            or not state.matches_descriptor(descriptor)
            or not await self._is_live(identity, state)
            or state.extractor_identity_hash
            != self._extractor_identity.identity_hash
        ):
            return None
        return ValidationEvidenceDocument(
            descriptor=descriptor,
            identity=identity,
            metadata=state.directive_metadata,
            source_state_blob=inventory_entry.source_state_blob,
            disposition="unchanged",
            extraction=state.extraction_evidence,
            validation_warnings=state.validation_warnings,
        )

    def _stage(self, name: str):
        return (
            self._metrics.stage(name)
            if self._metrics is not None
            else nullcontext()
        )

    def _increment(self, name: str, value: int = 1) -> None:
        if self._metrics is not None:
            self._metrics.increment(name, value)


def _validate_plan_set(
    documents: list[ValidationEvidenceDocument],
) -> None:
    if not documents:
        raise ValueError("No directive source documents were planned")
    names = [document.identity.source_name for document in documents]
    hashes = [document.identity.source_hash for document in documents]
    directive_ids = [
        document.metadata.directive_id for document in documents
    ]
    if len(names) != len(set(names)):
        raise ValueError("Duplicate directive source filenames found")
    if len(hashes) != len(set(hashes)):
        raise ValueError("Duplicate directive source content hashes found")
    if len(directive_ids) != len(set(directive_ids)):
        raise ValueError("Duplicate directive IDs found in the source set")
