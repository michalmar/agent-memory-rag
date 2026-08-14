"""End-to-end idempotent directive and mandate reconciliation."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from azure.cosmos import exceptions as cosmos_exceptions
from directive_contracts import (
    PUBLISHED_BUNDLE_MAX_BYTES,
    DirectiveArtifactLocators,
    DirectiveChunk,
    DirectiveManifest,
    DirectiveMetadata,
    DirectiveRelation,
    DirectiveSection,
    DirectiveSectionContent,
    DirectiveSectionContentDescriptor,
    DirectiveSummary,
    MandateSnapshot,
    PublishedDirectiveVersion,
    ReviewFinding,
    build_section_content_items,
    calculate_artifact_generation_id,
    canonical_json_hash,
    directive_storage_key,
    directive_version_storage_key,
    serialized_json_size,
)
from openai import APIError

from .blob_repository import BlobArtifactRepository
from .canonical import CanonicalDirective, parse_canonical
from .catalog_repository import DirectiveCatalogRepository
from .chunking import TextChunk, chunk_sections
from .clients import IngestionClients
from .config import IngestionConfig
from .content_repository import DirectiveContentRepository
from .document_intelligence import DocumentIntelligenceExtractor
from .mandate_projection import MandateRepository, parse_mandates
from .search_repository import DirectiveSearchRepository
from .source_state_repository import PublishedSourceState, SourceStateRepository
from .source import (
    BlobDirectiveSource,
    DirectiveSource,
    LocalDirectiveSource,
    SourceDocument,
)
from .summaries import SummaryGenerator


@dataclass(frozen=True)
class PreparedDirective:
    source: SourceDocument
    canonical: CanonicalDirective
    text_chunks: list[TextChunk]
    search_chunks: list[DirectiveChunk]
    bundle: PublishedDirectiveVersion
    content_items: tuple[DirectiveSectionContent, ...]
    findings: tuple[ReviewFinding, ...]


@dataclass(frozen=True)
class SourceMetadata:
    """Metadata-pass result; changed sources retain extraction for pass two."""

    source: SourceDocument
    metadata: DirectiveMetadata
    extraction: Any | None
    source_state: PublishedSourceState | None

    @property
    def changed(self) -> bool:
        return self.source_state is None


@dataclass(frozen=True)
class ReconcileResult:
    run_id: str
    source_count: int
    changed_count: int
    skipped_count: int
    chunk_count: int
    mandate_snapshot_id: str | None
    mandate_changed: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "source_count": self.source_count,
            "changed_count": self.changed_count,
            "skipped_count": self.skipped_count,
            "chunk_count": self.chunk_count,
            "mandate_snapshot_id": self.mandate_snapshot_id,
            "mandate_changed": self.mandate_changed,
        }


class DirectiveIngestionRunner:
    def __init__(self, config: IngestionConfig) -> None:
        self.config = config
        self.clients = IngestionClients(config)
        credential = self.clients.credential
        self.extractor = DocumentIntelligenceExtractor(
            config.document_intelligence_endpoint,
            config.document_intelligence_api_version,
            credential,
        )
        self.blobs = BlobArtifactRepository(
            config.blob_account_url,
            config.blob_container,
            credential,
        )
        self.source_states = SourceStateRepository(self.blobs)
        self.catalog = DirectiveCatalogRepository(
            config.cosmos_endpoint,
            config.cosmos_database,
            config.catalog_container,
            credential,
        )
        self.content = DirectiveContentRepository(
            config.cosmos_endpoint,
            config.cosmos_database,
            config.content_container,
            credential,
        )
        self.mandates = MandateRepository(
            config.cosmos_endpoint,
            config.cosmos_database,
            config.mandate_container,
            credential,
        )
        self.search = DirectiveSearchRepository(
            config, credential, self.clients.openai
        )
        self.summaries = SummaryGenerator(
            self.clients.openai,
            config.summary_deployment,
            full_document_tokens=config.summary_full_document_tokens,
            batch_tokens=config.summary_batch_tokens,
        )
        self.source: DirectiveSource
        if config.source_kind == "local":
            self.source = LocalDirectiveSource(
                config.source_directory,
                config.source_max_corpus_bytes,
            )
        else:
            self.source = BlobDirectiveSource(
                config.blob_account_url,
                config.source_container,
                config.source_prefix,
                credential,
                max_corpus_bytes=config.source_max_corpus_bytes,
            )

    async def close(self) -> None:
        await self.source.close()
        await self.search.close()
        await self.mandates.close()
        await self.content.close()
        await self.catalog.close()
        await self.blobs.close()
        await self.extractor.close()
        await self.clients.close()

    async def bootstrap(self) -> None:
        await self.search.ensure_resources()

    async def preflight(self) -> dict[str, str]:
        await self.source.check_access()
        await self.blobs.check_access()
        await self.catalog.check_access()
        await self.content.check_access()
        await self.mandates.check_access()
        await self.search.check_access()
        await self.extractor.check_access()

        embedding = await self.clients.openai.embeddings.create(
            model=self.config.embedding_deployment,
            input=["directive ingestion managed identity preflight"],
            dimensions=self.config.embedding_dimensions,
        )
        vectors = [item.embedding for item in embedding.data]
        if len(vectors) != 1 or len(vectors[0]) != (
            self.config.embedding_dimensions
        ):
            raise RuntimeError(
                "Embedding preflight returned an unexpected vector shape"
            )

        await self._preflight_response_model(
            self.config.summary_deployment, "summary"
        )
        return {
            "acr_pull": "ok",
            "source": "ok",
            "blob": "ok",
            "cosmos_catalog": "ok",
            "cosmos_content": "ok",
            "cosmos_mandates": "ok",
            "search": "ok",
            "document_intelligence": "ok",
            "embeddings": "ok",
            "summary_model": "ok",
        }

    async def verify(self) -> dict[str, object]:
        run_id = _run_id()
        sources = await self.discover_sources()
        source_states: list[PublishedSourceState] = []
        for source in sources:
            state = await self.source_states.load(
                source, self.config.processing_hash
            )
            if state is None or not await self._state_has_live_publication(
                source, state
            ):
                raise RuntimeError(
                    "Source-state does not match a live published bundle: "
                    f"{source.source_name}"
                )
            source_states.append(state)
        directive_ids = await self.catalog.list_published_directive_ids()
        bundles = await self.catalog.list_published_versions()
        current = await self.catalog.list_current_pointers()
        relations = await self.catalog.list_published_relations()
        expected_mandates = parse_mandates(
            self.config.mandate_csv,
            self.config.azure_tenant_id,
            directive_ids,
        )
        canonical_relation_ids = {
            relation.relation_id for relation, _, _ in relations
        }
        source_directive_ids = {
            state.directive_metadata.directive_id for state in source_states
        }
        if source_directive_ids != directive_ids:
            raise RuntimeError(
                "Source-state directive IDs do not match published directives"
            )
        if {bundle.directive_id for bundle in bundles} != directive_ids:
            raise RuntimeError(
                "Published directive IDs do not match published bundles"
            )
        if set(current) != directive_ids:
            raise RuntimeError(
                "Current directive pointers do not match published directives"
            )

        required_artifacts: set[str] = set()
        expected_chunks = 0
        content_sections = 0
        content_parts = 0
        split_sections = 0
        bundle_index = {
            (bundle.directive_id, bundle.directive_version_id): bundle
            for bundle in bundles
        }
        source_index = {
            (
                state.directive_metadata.directive_id,
                state.directive_metadata.directive_version_id,
            ): state
            for state in source_states
        }
        if len(bundle_index) != len(bundles):
            raise RuntimeError(
                "Duplicate published directive versions were found"
            )
        if set(source_index) != set(bundle_index):
            raise RuntimeError(
                "Source-state versions do not exactly match published versions"
            )
        for identity, state in source_index.items():
            if (
                bundle_index[identity].source_hash
                != state.directive_metadata.source_hash
            ):
                raise RuntimeError(
                    "Published source hash does not match source-state"
                )
        for bundle in bundles:
            manifest = bundle.manifest
            required_artifacts.update(
                {
                    bundle.artifacts.source_blob_name,
                    bundle.artifacts.canonical_blob_name,
                }
            )
            _validate_safe_artifact_paths(bundle)
            source_hash = await self.blobs.content_hash(
                bundle.artifacts.source_blob_name
            )
            canonical_hash = await self.blobs.content_hash(
                bundle.artifacts.canonical_blob_name
            )
            expected_generation_id = calculate_artifact_generation_id(
                bundle.processing_hash,
                canonical_hash,
                canonical_json_hash(bundle.summary),
            )
            if (
                source_hash != bundle.source_hash
                or expected_generation_id
                != bundle.artifact_generation_id
            ):
                raise RuntimeError(
                    "Published bundle Blob hashes do not match generation: "
                    f"{bundle.directive_version_id}"
                )
            content_summary = await self.content.validate_bundle(bundle)
            content_sections += content_summary["content_sections"]
            content_parts += content_summary["content_parts"]
            split_sections += content_summary["split_sections"]
            for section in manifest.sections:
                expected_chunks += len(section.chunk_ids)
        for directive_id, pointer in current.items():
            version_id, source_hash, processing_hash, generation_id = pointer
            bundle = bundle_index.get((directive_id, version_id))
            if (
                bundle is None
                or source_hash != bundle.source_hash
                or processing_hash != bundle.processing_hash
                or generation_id != bundle.artifact_generation_id
            ):
                raise RuntimeError(
                    "Current directive pointer does not match its "
                    f"published bundle: {directive_id}"
                )
        existing_artifacts = await self.blobs.list_names("directives/")
        missing = sorted(required_artifacts - existing_artifacts)
        if missing:
            raise RuntimeError(
                "Published manifests reference missing artifacts: "
                + ", ".join(missing)
            )
        extras = sorted(existing_artifacts - required_artifacts)
        if extras:
            raise RuntimeError(
                "Artifacts exist outside the validated source corpus: "
                + ", ".join(extras)
            )

        search = await self.search.verification_summary()
        if (
            search["published_chunks"] != expected_chunks
            or search["published_directives"] != len(directive_ids)
            or search["published_versions"] != len(bundles)
            or search["current_directives"] != len(current)
            or search["current_versions"] != len(current)
        ):
            raise RuntimeError(
                "Search publication counts do not match catalog manifests"
            )

        mandates = await self.mandates.verification_summary()
        if (
            mandates["assignment_count"]
            != len(expected_mandates.assignments)
            or mandates["user_count"] != expected_mandates.user_count
        ):
            raise RuntimeError(
                "Active mandate snapshot does not match the source CSV"
            )

        payload = {
            "success": True,
            "verify_execution_id": run_id,
            "environment": _safe_environment(self.config),
            "processing_version": self.config.processing_version,
            "processing_hash": self.config.processing_hash,
            "search_index": self.config.search_index,
            "source_inventory_digest": _public_record_digest(
                _source_inventory(sources)
            ),
            "source_count": len(sources),
            "source_versions": len(sources),
            "directive_count": len(directive_ids),
            "directive_ids": len(directive_ids),
            "current_versions": len(current),
            "accepted_relations": len(canonical_relation_ids),
            "required_artifacts": len(required_artifacts),
            "content_sections": content_sections,
            "content_parts": content_parts,
            "split_sections": split_sections,
            **search,
            **{
                f"mandate_{key}": value
                for key, value in mandates.items()
            },
        }
        payload["verify_digest"] = _public_record_digest(payload)
        return payload

    async def _preflight_response_model(
        self, deployment: str, label: str
    ) -> None:
        response = await self.clients.openai.responses.create(
            model=deployment,
            input=(
                "Reply with the single word READY. This is an access preflight "
                "and contains no company data."
            ),
            max_output_tokens=512,
        )
        if not str(getattr(response, "output_text", "") or "").strip():
            raise RuntimeError(f"{label.capitalize()} model returned no text")

    async def validate_inputs(
        self,
        source_directory: Path | None = None,
        mandate_csv: Path | None = None,
    ) -> dict[str, object]:
        execution_id = _run_id()
        sources = await self.discover_sources(source_directory)
        metadata = await self.extract_or_load_metadata(sources, execution_id)
        await self._validate_and_quarantine(metadata, execution_id)
        known_ids = {item.metadata.directive_id for item in metadata}
        mandates = parse_mandates(
            mandate_csv or self.config.mandate_csv,
            self.config.azure_tenant_id,
            known_ids,
        )
        warnings = _validation_warnings(metadata, self.config.processing_hash)
        source_inventory = _source_inventory(sources)
        payload = {
            "success": True,
            "validation_execution_id": execution_id,
            "environment": _safe_environment(self.config),
            "processing_version": self.config.processing_version,
            "processing_hash": self.config.processing_hash,
            "search_index": self.config.search_index,
            "source_count": len(sources),
            "directive_count": len(known_ids),
            "normalized_directive_ids": sorted(known_ids),
            "directive_version_ids": sorted(
                item.metadata.directive_version_id for item in metadata
            ),
            "mandate_count": len(mandates.assignments),
            "mandate_user_count": mandates.user_count,
            "warnings": warnings,
            "warning_count": len(warnings),
            "failures": [],
            "source_inventory_digest": _public_record_digest(source_inventory),
        }
        payload["validation_digest"] = _public_record_digest(
            {"payload": payload, "source_inventory": source_inventory}
        )
        return payload

    async def run_daily(
        self,
        source_directory: Path | None = None,
        mandate_csv: Path | None = None,
    ) -> ReconcileResult:
        run_id = _run_id()
        sources = await self.discover_sources(source_directory)
        metadata = await self.extract_or_load_metadata(sources, run_id)
        await self._validate_and_quarantine(metadata, run_id)
        known_ids = {item.metadata.directive_id for item in metadata}
        parsed_mandates = parse_mandates(
            mandate_csv or self.config.mandate_csv,
            self.config.azure_tenant_id,
            known_ids,
        )
        prepared = await self.prepare_changed_documents(metadata, run_id)
        await self._validate_relations(
            prepared,
            [item.metadata for item in metadata],
            known_ids,
        )
        mandates_current = await self.mandates.is_current(parsed_mandates)
        if prepared:
            await self.search.ensure_resources()
            await self.stage_documents(prepared)
            await self.publish_documents(prepared)
        if prepared:
            await self.record_source_states(prepared)
        await self.reconcile_exact_corpus(metadata)
        if mandates_current:
            snapshot = MandateSnapshot(
                snapshot_id=f"mandates-{parsed_mandates.checksum}",
                checksum=parsed_mandates.checksum,
                assignment_count=len(parsed_mandates.assignments),
                user_count=parsed_mandates.user_count,
                complete=True,
            )
            mandate_changed = False
        else:
            snapshot, mandate_changed = await self.mandates.publish(
                parsed_mandates, run_id
            )
        result = ReconcileResult(
            run_id=run_id,
            source_count=len(sources),
            changed_count=len(prepared),
            skipped_count=len(sources) - len(prepared),
            chunk_count=sum(
                len(item.search_chunks) for item in prepared
            ),
            mandate_snapshot_id=snapshot.snapshot_id,
            mandate_changed=mandate_changed,
        )
        await self.verify()
        if prepared or mandate_changed:
            await self.catalog.record_run(
                run_id,
                status="succeeded",
                source_count=result.source_count,
                changed_count=result.changed_count,
                skipped_count=result.skipped_count,
                chunk_count=result.chunk_count,
                mandate_snapshot_id=result.mandate_snapshot_id,
            )
        return result

    async def reconcile_documents(
        self, source_directory: Path | None = None
    ) -> ReconcileResult:
        run_id = _run_id()
        sources = await self.discover_sources(source_directory)
        metadata = await self.extract_or_load_metadata(sources, run_id)
        await self._validate_and_quarantine(metadata, run_id)
        prepared = await self.prepare_changed_documents(metadata, run_id)
        await self._validate_relations(
            prepared,
            [item.metadata for item in metadata],
            {item.metadata.directive_id for item in metadata},
        )
        if prepared:
            await self.search.ensure_resources()
            await self.stage_documents(prepared)
            await self.publish_documents(prepared)
            await self.record_source_states(prepared)
        await self.reconcile_exact_corpus(metadata)
        result = ReconcileResult(
            run_id=run_id,
            source_count=len(sources),
            changed_count=len(prepared),
            skipped_count=len(sources) - len(prepared),
            chunk_count=sum(
                len(item.search_chunks) for item in prepared
            ),
            mandate_snapshot_id=None,
            mandate_changed=False,
        )
        if prepared:
            await self.catalog.record_run(
                run_id,
                status="succeeded",
                source_count=result.source_count,
                changed_count=result.changed_count,
                skipped_count=result.skipped_count,
                chunk_count=result.chunk_count,
                mandate_snapshot_id=None,
            )
        return result

    async def publish_mandates(
        self, mandate_csv: Path | None = None
    ) -> tuple[MandateSnapshot, bool]:
        run_id = _run_id()
        known_ids = await self.catalog.list_published_directive_ids()
        if not known_ids:
            raise RuntimeError(
                "Cannot publish mandates before directives are published"
            )
        return await self._publish_mandates(
            mandate_csv or self.config.mandate_csv, known_ids, run_id
        )

    async def discover_sources(
        self,
        source_directory: Path | None = None,
    ) -> list[SourceDocument]:
        if source_directory is None:
            return await self.source.discover()
        if self.config.source_kind != "local":
            raise ValueError(
                "--source cannot be used when DIRECTIVE_SOURCE_KIND=azure_blob"
            )
        return await LocalDirectiveSource(source_directory).discover()

    async def _discover_sources(
        self, source_directory: Path | None = None
    ) -> list[SourceDocument]:
        """Compatibility alias for callers that used the v1 private method."""
        return await self.discover_sources(source_directory)

    async def extract_or_load_metadata(
        self, sources: list[SourceDocument], run_id: str
    ) -> list[SourceMetadata]:
        """Complete pass one without summary, embedding, staging, or publication."""
        from .metadata import extract_metadata

        results: list[SourceMetadata] = []
        failures: list[tuple[SourceDocument, str]] = []
        for source in sources:
            try:
                state = await self.source_states.load(
                    source, self.config.processing_hash
                )
                if state is not None and await self._state_has_live_publication(
                    source, state
                ):
                    results.append(
                        SourceMetadata(
                            source=source,
                            metadata=state.directive_metadata,
                            extraction=None,
                            source_state=state,
                        )
                    )
                    continue
                extraction = await self.extractor.extract(source.content)
                candidate = extract_metadata(
                    source, extraction, self.config.processing_hash
                )
                results.append(
                    SourceMetadata(
                        source=source,
                        metadata=candidate.metadata,
                        extraction=extraction,
                        source_state=None,
                    )
                )
            except ValueError as exc:
                failures.append((source, _safe_failure_code(exc)))
            except (APIError, httpx.HTTPError, RuntimeError, TimeoutError):
                raise
        if failures:
            for source, error in failures:
                await self.blobs.quarantine(
                    run_id, source.source_name, source.content, [error]
                )
            raise RuntimeError(
                "Metadata validation failed for "
                f"{len(failures)} directive source(s)"
            )
        return results

    def validate_source_set(self, metadata: list[SourceMetadata]) -> None:
        """Validate all source identities before any expensive model work."""
        failures: list[tuple[SourceDocument, str]] = []
        by_id: dict[str, SourceMetadata] = {}
        versions: set[str] = set()
        for item in metadata:
            value = item.metadata
            if not (value.is_current and value.is_valid and value.status == "Current"):
                failures.append((item.source, "metadata_invalid_current_state"))
            if value.directive_id in by_id:
                failures.extend(
                    [
                        (item.source, "metadata_duplicate_directive_id"),
                        (
                            by_id[value.directive_id].source,
                            "metadata_duplicate_directive_id",
                        ),
                    ]
                )
            else:
                by_id[value.directive_id] = item
            if value.directive_version_id in versions:
                failures.append((item.source, "metadata_duplicate_version_id"))
            versions.add(value.directive_version_id)
        if failures:
            raise _SourceSetValidationError(failures)

    async def _validate_and_quarantine(
        self, metadata: list[SourceMetadata], run_id: str
    ) -> None:
        try:
            self.validate_source_set(metadata)
        except _SourceSetValidationError as exc:
            await self._quarantine_source_set_failures(run_id, exc.failures)
            raise RuntimeError("Source-set validation failed") from exc

    async def _quarantine_source_set_failures(
        self,
        run_id: str,
        failures: list[tuple[SourceDocument, str]],
    ) -> None:
        handled: set[str] = set()
        for source, code in failures:
            if source.source_hash in handled:
                continue
            handled.add(source.source_hash)
            await self.blobs.quarantine(
                run_id, source.source_name, source.content, [code]
            )

    async def prepare_changed_documents(
        self, metadata: list[SourceMetadata], run_id: str
    ) -> list[PreparedDirective]:
        """Pass two: model work is permitted only after corpus validation."""
        prepared: list[PreparedDirective] = []
        failures: list[tuple[SourceDocument, str]] = []
        for item in metadata:
            if not item.changed:
                continue
            try:
                canonical = parse_canonical(
                    item.source, item.extraction, self.config.processing_hash
                )
                text_chunks, chunk_findings = chunk_sections(
                    canonical.metadata.directive_version_id,
                    canonical.metadata.source_hash,
                    canonical.metadata.processing_hash,
                    canonical.sections,
                    token_limit=self.config.chunk_token_limit,
                    overlap_tokens=self.config.chunk_overlap_tokens,
                )
                findings = (*canonical.findings, *chunk_findings)
                fatal = [
                    finding.message
                    for finding in findings
                    if finding.severity == "error"
                ]
                if fatal:
                    raise ValueError("; ".join(fatal))
                summary = await self.summaries.summarize(canonical)
                generation_id = calculate_artifact_generation_id(
                    canonical.metadata.processing_hash,
                    hashlib.sha256(canonical.markdown.encode("utf-8")).hexdigest(),
                    canonical_json_hash(summary),
                )
                text_chunks = _generation_scoped_chunks(
                    text_chunks, generation_id
                )
                search_chunks = await self.search.build_chunks(
                    canonical, text_chunks
                )
                manifest = _build_manifest(
                    canonical, text_chunks, summary
                )
                bundle, content_items = _build_published_bundle(
                    canonical,
                    manifest,
                    summary,
                    run_id,
                )
                prepared.append(
                    PreparedDirective(
                        source=item.source,
                        canonical=canonical,
                        text_chunks=text_chunks,
                        search_chunks=search_chunks,
                        bundle=bundle,
                        content_items=content_items,
                        findings=tuple(findings),
                    )
                )
                if canonical.metadata != item.metadata:
                    raise RuntimeError("Canonical metadata changed after validation")
            except ValueError as exc:
                failures.append((item.source, _safe_failure_code(exc)))
            except (APIError, httpx.HTTPError, RuntimeError, TimeoutError):
                raise
        if failures:
            for source, error in failures:
                await self.blobs.quarantine(
                    run_id, source.source_name, source.content, [error]
                )
            raise RuntimeError(
                f"Document preparation failed for {len(failures)} directive(s)"
            )
        return prepared

    async def stage_documents(self, prepared: list[PreparedDirective]) -> None:
        for item in prepared:
            await self._publish_artifacts(item)
            for content_item in item.content_items:
                await self.content.create_or_compare(content_item)
            await self.content.validate_bundle(item.bundle)
            await self.search.stage_chunks(item.search_chunks)
            await self.catalog.stage_version(
                item.bundle,
                item.canonical.relations,
                item.findings,
            )

    async def publish_documents(self, prepared: list[PreparedDirective]) -> None:
        try:
            for item in prepared:
                await self.search.publish_chunks(item.search_chunks)
                await self.search.validate_published_chunk_ids(
                    item.canonical,
                    (chunk.id for chunk in item.search_chunks),
                )
                await self.catalog.publish_version(
                    item.bundle,
                    item.canonical.relations,
                )
                await self.catalog.validate_published(item.bundle)
            for item in prepared:
                await self.catalog.activate_current(
                    item.canonical.metadata, item.bundle.run_id
                )
                await self.search.reconcile_current(item.bundle)
                await self.search.reconcile_generation(item.bundle)
        except (RuntimeError, cosmos_exceptions.CosmosHttpResponseError):
            for item in prepared:
                await self.search.retire_chunks(item.search_chunks)
            raise

    async def record_source_states(
        self, prepared: list[PreparedDirective]
    ) -> None:
        """Commit private idempotency records only after live publication checks."""
        for item in prepared:
            await self.catalog.validate_published(item.bundle)
            await self.content.validate_bundle(item.bundle)
            await self.search.validate_published_chunk_ids(
                item.canonical,
                (chunk.id for chunk in item.search_chunks),
            )
            await self.source_states.record(
                item.source,
                item.canonical.metadata,
                item.bundle.artifact_generation_id,
            )

    async def reconcile_exact_corpus(
        self, metadata: list[SourceMetadata]
    ) -> None:
        """Retire every store record not represented by validated sources."""
        expected_versions = {
            (item.metadata.directive_id, item.metadata.directive_version_id)
            for item in metadata
        }
        retired = await self.catalog.remove_absent_versions(expected_versions)
        for bundle in retired:
            await self.search.retire_generation(bundle)
            await self.content.delete_bundle(bundle)
            await self.blobs.delete_names(
                {
                    bundle.artifacts.source_blob_name,
                    bundle.artifacts.canonical_blob_name,
                }
            )
        await self.source_states.prune(
            {(item.source.source_name, item.source.source_hash) for item in metadata}
        )

    async def _state_has_live_publication(
        self,
        source: SourceDocument,
        state: PublishedSourceState,
    ) -> bool:
        """A state record is insufficient unless its published bundle is live."""
        metadata = state.directive_metadata
        bundle = await self.catalog.get_published_version(
            metadata.directive_id, metadata.directive_version_id
        )
        if bundle is None:
            return False
        if (
            bundle.directive_id != metadata.directive_id
            or bundle.directive_version_id != metadata.directive_version_id
            or bundle.source_filename != source.source_name
            or bundle.source_hash != source.source_hash
            or bundle.processing_hash != metadata.processing_hash
            or bundle.artifact_generation_id != state.artifact_generation_id
        ):
            return False
        _validate_safe_artifact_paths(bundle)
        current = await self.catalog.get_current(metadata.directive_id)
        if not (
            current
            and current.get("directive_version_id")
            == metadata.directive_version_id
            and current.get("source_hash") == source.source_hash
            and current.get("processing_hash") == metadata.processing_hash
            and current.get("artifact_generation_id")
            == state.artifact_generation_id
        ):
            return False
        try:
            if not (
                await self.blobs.exists(bundle.artifacts.source_blob_name)
                and await self.blobs.exists(bundle.artifacts.canonical_blob_name)
            ):
                return False
            await self.blobs.validate_hash(
                bundle.artifacts.source_blob_name, source.source_hash
            )
            await self.content.validate_bundle(bundle)
            await self.search.validate_current_generation(bundle)
        except RuntimeError:
            return False
        return True

    async def _prepare(
        self, sources: list[SourceDocument], run_id: str
    ) -> tuple[list[PreparedDirective], list[DirectiveMetadata]]:
        """Compatibility wrapper preserving the v1 private test seam."""
        metadata = await self.extract_or_load_metadata(sources, run_id)
        try:
            self.validate_source_set(metadata)
        except _SourceSetValidationError as exc:
            await self._quarantine_source_set_failures(run_id, exc.failures)
            raise RuntimeError("Source-set validation failed") from exc
        prepared = await self.prepare_changed_documents(metadata, run_id)
        return prepared, [item.metadata for item in metadata]

    async def _publish_documents(
        self,
        prepared: list[PreparedDirective],
        metadata: list[DirectiveMetadata],
        run_id: str,
    ) -> None:
        """Compatibility wrapper preserving staged publication rollback semantics."""
        await self.stage_documents(prepared)
        await self.publish_documents(prepared)

    async def _publish_artifacts(self, item: PreparedDirective) -> None:
        artifacts = item.bundle.artifacts
        await self.blobs.put_immutable(
            artifacts.source_blob_name,
            item.source.content,
            "application/pdf",
        )
        await self.blobs.put_immutable(
            artifacts.canonical_blob_name,
            item.canonical.markdown.encode(),
            "text/markdown; charset=utf-8",
        )
        await self.blobs.validate_hash(
            artifacts.source_blob_name,
            item.bundle.source_hash,
        )
        await self.blobs.validate_hash(
            artifacts.canonical_blob_name,
            hashlib.sha256(
                item.canonical.markdown.encode("utf-8")
            ).hexdigest(),
        )

    async def _publish_mandates(
        self,
        path: Path,
        known_ids: set[str],
        run_id: str,
    ) -> tuple[MandateSnapshot, bool]:
        parsed = parse_mandates(
            path, self.config.azure_tenant_id, known_ids
        )
        return await self.mandates.publish(parsed, run_id)

    async def _validate_relations(
        self,
        prepared: list[PreparedDirective],
        metadata: list[DirectiveMetadata],
        known_ids: set[str],
    ) -> None:
        known_versions = {
            (item.directive_id, item.version_label) for item in metadata
        } | await self.catalog.list_published_version_labels()
        prepared_relations = [
            relation
            for item in prepared
            for relation in item.canonical.relations
            if relation.status == "accepted"
        ]
        _validate_relation_records(
            prepared_relations, known_ids, known_versions
        )

        current = {
            directive_id: pointer[:3]
            for directive_id, pointer in (
                await self.catalog.list_current_pointers()
            ).items()
        }
        for item in metadata:
            if item.is_current:
                current[item.directive_id] = (
                    item.directive_version_id,
                    item.source_hash,
                    item.processing_hash,
                )
        prepared_current: dict[str, list[DirectiveRelation]] = {
            item.canonical.metadata.directive_id: [
                relation
                for relation in item.canonical.relations
                if relation.status == "accepted"
            ]
            for item in prepared
            if item.canonical.metadata.is_current
        }
        graph_relations = _select_current_relations(
            prepared_current,
            await self.catalog.list_published_relations(),
            current,
        )
        _validate_relation_records(
            graph_relations, known_ids, known_versions
        )
        _validate_relation_graph(graph_relations)

    async def _validate_source_set(
        self, metadata: list[DirectiveMetadata]
    ) -> None:
        versions = [
            (item.directive_id, item.directive_version_id)
            for item in metadata
        ]
        if len(versions) != len(set(versions)):
            raise ValueError("Duplicate directive version IDs were extracted")
        by_directive: dict[str, list[DirectiveMetadata]] = defaultdict(list)
        for item in metadata:
            by_directive[item.directive_id].append(item)
        for directive_id, items in by_directive.items():
            current = [item for item in items if item.is_current]
            if len(current) > 1:
                raise ValueError(
                    f"Directive {directive_id} has multiple current versions"
                )
            if current:
                continue
            active = await self.catalog.get_current(directive_id)
            present_version_ids = {
                item.directive_version_id for item in items
            }
            if (
                active is None
                or active.get("directive_version_id") in present_version_ids
            ):
                raise ValueError(
                    f"Directive {directive_id} has no current version in the "
                    "source set and no missing-file current version to retain"
                )


def _build_manifest(
    directive: CanonicalDirective,
    chunks: list[TextChunk],
    summary: DirectiveSummary,
) -> DirectiveManifest:
    metadata = directive.metadata
    canonical_hash = hashlib.sha256(
        directive.markdown.encode("utf-8")
    ).hexdigest()
    generation_id = calculate_artifact_generation_id(
        metadata.processing_hash,
        canonical_hash,
        canonical_json_hash(summary),
    )
    chunk_ids: dict[str, list[str]] = defaultdict(list)
    for chunk in chunks:
        chunk_ids[chunk.section_id].append(chunk.id)
    sections = [
        DirectiveSection(
            section_id=section.section_id,
            ordinal=section.ordinal,
            number=section.number,
            title=section.title,
            path=list(section.path),
            page_from=section.page_from,
            page_to=section.page_to,
            token_count=section.token_count,
            content_hash=section.content_hash,
            chunk_ids=chunk_ids[section.section_id],
        )
        for section in directive.sections
    ]
    return DirectiveManifest(
        directive_id=metadata.directive_id,
        directive_version_id=metadata.directive_version_id,
        source_hash=metadata.source_hash,
        artifact_generation_id=generation_id,
        total_pages=directive.total_pages,
        total_tokens=directive.total_tokens,
        sections=sections,
    )


def _generation_scoped_chunks(
    chunks: list[TextChunk], artifact_generation_id: str
) -> list[TextChunk]:
    """Prevent staging a replacement generation from touching live Search IDs."""
    return [
        replace(
            chunk,
            id=hashlib.sha256(
                f"{artifact_generation_id}\0{chunk.id}".encode("utf-8")
            ).hexdigest(),
        )
        for chunk in chunks
    ]


def _build_published_bundle(
    directive: CanonicalDirective,
    manifest: DirectiveManifest,
    summary: DirectiveSummary,
    run_id: str,
) -> tuple[
    PublishedDirectiveVersion,
    tuple[DirectiveSectionContent, ...],
]:
    metadata = directive.metadata
    created_at = datetime.now(UTC)
    content_items = tuple(
        item
        for section in directive.sections
        for item in build_section_content_items(
            directive_id=metadata.directive_id,
            directive_version_id=metadata.directive_version_id,
            artifact_generation_id=manifest.artifact_generation_id,
            section_id=section.section_id,
            section_ordinal=section.ordinal,
            content=section.content,
            run_id=run_id,
            created_at=created_at,
        )
    )
    part_counts: dict[str, int] = {}
    for item in content_items:
        part_counts[item.section_id] = item.part_count
    bundle = PublishedDirectiveVersion(
        id=(
            "version:"
            + directive_version_storage_key(
                metadata.directive_id, metadata.version_label
            )
        ),
        **metadata.model_dump(mode="json"),
        artifact_generation_id=manifest.artifact_generation_id,
        manifest=manifest,
        summary=summary,
        artifacts=_build_artifact_locators(
            directive, manifest.artifact_generation_id
        ),
        section_content={
            section.section_id: DirectiveSectionContentDescriptor(
                part_count=part_counts[section.section_id]
            )
            for section in directive.sections
        },
        run_id=run_id,
        published_at=created_at,
    )
    bundle_size = serialized_json_size(bundle)
    if bundle_size > PUBLISHED_BUNDLE_MAX_BYTES:
        raise ValueError(
            f"Published directive bundle exceeds "
            f"{PUBLISHED_BUNDLE_MAX_BYTES} bytes: "
            f"{metadata.directive_version_id} ({bundle_size} bytes)"
        )
    return bundle, content_items


def _build_artifact_locators(
    directive: CanonicalDirective,
    artifact_generation_id: str,
) -> DirectiveArtifactLocators:
    metadata = directive.metadata
    source_base = (
        f"directives/{directive_storage_key(metadata.directive_id)}/"
        f"{directive_version_storage_key(metadata.directive_id, metadata.version_label)}/"
        f"{metadata.source_hash}"
    )
    return DirectiveArtifactLocators(
        canonical_blob_name=(
            f"{source_base}/generations/"
            f"{artifact_generation_id}/document.md"
        ),
        source_blob_name=f"{source_base}/source.pdf",
    )


def _metadata_from_catalog(item: dict[str, Any]) -> DirectiveMetadata:
    values = {
        name: item[name]
        for name in DirectiveMetadata.model_fields
        if name in item
    }
    return DirectiveMetadata.model_validate(values)


class _SourceSetValidationError(ValueError):
    def __init__(self, failures: list[tuple[SourceDocument, str]]) -> None:
        super().__init__("Source-set validation failed")
        self.failures = failures


def _validate_safe_artifact_paths(bundle: PublishedDirectiveVersion) -> None:
    metadata = bundle
    source_base = (
        f"directives/{directive_storage_key(metadata.directive_id)}/"
        f"{directive_version_storage_key(metadata.directive_id, metadata.version_label)}/"
        f"{metadata.source_hash}"
    )
    if bundle.artifacts.source_blob_name != f"{source_base}/source.pdf":
        raise RuntimeError("Published source artifact locator is unsafe")
    if bundle.artifacts.canonical_blob_name != (
        f"{source_base}/generations/{bundle.artifact_generation_id}/document.md"
    ):
        raise RuntimeError("Published canonical artifact locator is unsafe")


def _safe_environment(config: IngestionConfig) -> dict[str, str]:
    return {
        "source_kind": config.source_kind,
        "source_container": config.source_container,
        "artifact_container": config.blob_container,
        "catalog_container": config.catalog_container,
        "content_container": config.content_container,
        "mandate_container": config.mandate_container,
    }


def _safe_failure_code(exc: BaseException) -> str:
    if isinstance(exc, TimeoutError):
        return "metadata_timeout"
    if isinstance(exc, httpx.HTTPError):
        return "metadata_transport_error"
    if isinstance(exc, APIError):
        return "metadata_service_error"
    if isinstance(exc, ValueError):
        return "metadata_invalid"
    return "metadata_processing_error"


def _source_inventory(sources: list[SourceDocument]) -> list[dict[str, str]]:
    """Deterministic, content-safe source identity used by validation guards."""
    return [
        {"source_name": source.source_name, "source_hash": source.source_hash}
        for source in sorted(sources, key=lambda item: item.source_name)
    ]


def _validation_warnings(
    metadata: list[SourceMetadata], processing_hash: str
) -> list[dict[str, str]]:
    warnings: set[tuple[str, str]] = set()
    for item in metadata:
        if item.extraction is None:
            continue
        canonical = parse_canonical(
            item.source, item.extraction, processing_hash
        )
        warnings.update(
            (finding.code, finding.severity)
            for finding in canonical.findings
            if finding.severity == "warning"
        )
    return [
        {"code": code, "severity": severity}
        for code, severity in sorted(warnings)[:100]
    ]


def _public_record_digest(value: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _validate_relation_records(
    relations: list[DirectiveRelation],
    known_ids: set[str],
    versions: set[tuple[str, str]],
) -> None:
    invalid: list[str] = []
    for relation in relations:
        if relation.target_directive_id not in known_ids:
            invalid.append(
                f"{relation.source_directive_id}->"
                f"{relation.target_directive_id}"
            )
        if (
            relation.target_version_label
            and (
                relation.target_directive_id,
                relation.target_version_label,
            )
            not in versions
        ):
            invalid.append(
                f"{relation.target_directive_id}:v"
                f"{relation.target_version_label}"
            )
    if invalid:
        raise ValueError(
            "Accepted relations reference directives outside the complete "
            "source/version set: " + ", ".join(invalid)
        )


def _validate_relation_graph(relations: list[DirectiveRelation]) -> None:
    edges: dict[tuple[str, str], set[str]] = defaultdict(set)
    child_parents: dict[str, set[str]] = defaultdict(set)
    for relation in relations:
        if relation.relation_type == "parent":
            parent = relation.target_directive_id
            child = relation.source_directive_id
        elif relation.relation_type == "sub_directive":
            parent = relation.source_directive_id
            child = relation.target_directive_id
        else:
            continue
        edges[(parent, child)].add(relation.relation_id)
        child_parents[child].add(parent)
    inconsistent = [
        f"{parent}->{child}"
        for (parent, child), relation_ids in edges.items()
        if len(relation_ids) != 1
    ]
    if inconsistent:
        raise ValueError(
            "Reciprocal relation declarations do not share one canonical ID: "
            + ", ".join(inconsistent)
        )
    multiple_parents = [
        child
        for child, parents in child_parents.items()
        if len(parents) > 1
    ]
    if multiple_parents:
        raise ValueError(
            "A sub-directive cannot have multiple parents: "
            + ", ".join(sorted(multiple_parents))
        )
    graph: dict[str, set[str]] = defaultdict(set)
    for parent, child in edges:
        if parent == child:
            raise ValueError(f"Directive relation cycle: {parent}->{child}")
        graph[parent].add(child)
    _validate_relation_depth(graph)


def _select_current_relations(
    prepared_current: dict[str, list[DirectiveRelation]],
    published: list[tuple[DirectiveRelation, str, str]],
    current: dict[str, tuple[str, str, str]],
) -> list[DirectiveRelation]:
    selected = [
        relation
        for relation, source_hash, processing_hash in published
        if relation.source_directive_id not in prepared_current
        and current.get(relation.source_directive_id)
        == (
            relation.source_version_id,
            source_hash,
            processing_hash,
        )
    ]
    for relations in prepared_current.values():
        selected.extend(relations)
    return selected


def _validate_relation_depth(graph: dict[str, set[str]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str, depth: int) -> None:
        if node in visiting:
            raise ValueError(f"Directive relation cycle includes {node}")
        if depth > 1:
            raise ValueError(
                "Directive relations exceed the two-layer "
                "directive/sub-directive limit"
            )
        if node in visited:
            return
        visiting.add(node)
        for child in graph.get(node, set()):
            visit(child, depth + 1)
        visiting.remove(node)
        visited.add(node)

    children = {child for values in graph.values() for child in values}
    roots = set(graph) - children
    if graph and not roots:
        raise ValueError("Directive relations contain a cycle")
    for root in roots:
        visit(root, 0)
    all_nodes = set(graph) | children
    if visited != all_nodes:
        unresolved = ", ".join(sorted(all_nodes - visited))
        raise ValueError(
            "Directive relations contain a disconnected cycle: " + unresolved
        )


def _run_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{uuid4().hex[:8]}"


def format_result(value: object) -> str:
    if hasattr(value, "as_dict"):
        value = value.as_dict()
    return json.dumps(value, sort_keys=True, default=str)
