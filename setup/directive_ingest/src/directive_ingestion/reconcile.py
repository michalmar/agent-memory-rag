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
    section_content_item_id,
    serialized_json_size,
    source_fingerprint,
)
from openai import APIError

from .blob_repository import BlobArtifactRepository
from .canonical import CanonicalDirective, parse_canonical
from .catalog_repository import (
    CatalogSlotSnapshot,
    DirectiveCatalogRepository,
)
from .chunking import TextChunk, chunk_sections
from .clients import IngestionClients
from .config import IngestionConfig
from .content_repository import DirectiveContentRepository
from .document_intelligence import DocumentIntelligenceExtractor
from .integrity import CatalogResetRequiredError, IntegrityValidationError
from .mandate_projection import MandateRepository, parse_mandates
from .publication_commit_repository import PublicationCommitRepository
from .search_repository import DirectiveSearchRepository
from .source_state_repository import (
    PublishedSourceState,
    SourceStateRepository,
    SourceStateSnapshot,
)
from .source import (
    BlobDirectiveSource,
    DirectiveSource,
    LocalDirectiveSource,
    SourceDocument,
)
from .summaries import SummaryGenerator

# This keeps worst-case four-byte UTF-8 directive/version ID arrays below the
# 64 KiB producer-record ceiling while leaving room for the fixed schema.
MAX_PUBLIC_DIRECTIVES = 32
MAX_PUBLIC_RECORD_BYTES = 65_536


@dataclass(frozen=True)
class PreparedDirective:
    source: SourceDocument
    canonical: CanonicalDirective
    text_chunks: list[TextChunk]
    search_chunks: list[DirectiveChunk]
    bundle: PublishedDirectiveVersion
    content_items: tuple[DirectiveSectionContent, ...]
    findings: tuple[ReviewFinding, ...]
    repair_generation_salt: str | None = None


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


@dataclass(frozen=True)
class PublicationSnapshot:
    item: PreparedDirective
    previous_version: PublishedDirectiveVersion | None
    previous_catalog_slot: CatalogSlotSnapshot | None
    previous_current: dict[str, Any] | None
    previous_current_bundle: PublishedDirectiveVersion | None
    previous_source_state: SourceStateSnapshot | None
    previous_source_artifact: SourceArtifactSnapshot | None
    preserve_candidate_generation: bool = False
    candidate_catalog_etag: str | None = None
    candidate_source_artifact_etag: str | None = None
    candidate_source_state_etag: str | None = None


@dataclass(frozen=True)
class SourceArtifactSnapshot:
    blob_name: str
    content: bytes
    etag: str


@dataclass(frozen=True)
class MandatePublicationSnapshot:
    snapshot: MandateSnapshot
    previous_active: dict[str, Any] | None
    changed: bool
    run_id: str
    candidate_active_etag: str | None = None


@dataclass(frozen=True)
class DailyRunApproval:
    """Operator approval evidence required for a guarded daily run."""

    validation_digest: str
    environment_digest: str
    source_inventory_digest: str


@dataclass(frozen=True)
class ValidationSnapshot:
    """The complete metadata-only input that an approval authorizes."""

    sources: list[SourceDocument]
    metadata: list[SourceMetadata]
    mandates: Any
    payload: dict[str, object]

    @property
    def validation_digest(self) -> str:
        return str(self.payload["validation_digest"])


def _publication_snapshot(
    snapshots: list[PublicationSnapshot] | None,
    item: PreparedDirective,
) -> PublicationSnapshot | None:
    return next(
        (snapshot for snapshot in snapshots or [] if snapshot.item is item),
        None,
    )


def _replace_publication_snapshot(
    snapshots: list[PublicationSnapshot],
    item: PreparedDirective,
    **updates: object,
) -> None:
    for index, snapshot in enumerate(snapshots):
        if snapshot.item is item:
            snapshots[index] = replace(snapshot, **updates)
            return
    raise RuntimeError("Publication snapshot is missing its prepared directive")


def _current_matches_bundle(
    current: dict[str, Any] | None,
    bundle: PublishedDirectiveVersion,
) -> bool:
    return bool(
        current
        and current.get("directive_version_id")
        == bundle.directive_version_id
        and current.get("source_hash") == bundle.source_hash
        and current.get("processing_hash") == bundle.processing_hash
        and current.get("artifact_generation_id")
        == bundle.artifact_generation_id
    )


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
        self.commits = PublicationCommitRepository(self.blobs)
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

    async def verify(
        self,
        *,
        validation_digest: str | None = None,
        expected_validation_digest: str | None = None,
    ) -> dict[str, object]:
        expected_validation_digest = _expected_validation_digest(
            validation_digest, expected_validation_digest
        )
        run_id = _run_id()
        sources = await self.discover_sources()
        _validate_public_corpus_limit(sources)
        environment = _safe_environment(self.config)
        environment_digest = _public_record_digest(environment)
        source_inventory_digest = _public_record_digest(
            _source_inventory(sources)
        )
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
        if expected_validation_digest is not None and any(
            state.validation_digest != expected_validation_digest
            for state in source_states
        ):
            raise RuntimeError(
                "Source-state records do not match the approved validation digest"
            )
        expected_state_names = {
            self.source_states.blob_name(source, self.config.processing_hash)
            for source in sources
        }
        if await self.source_states.list_names() != expected_state_names:
            raise RuntimeError(
                "Source-state records do not exactly match discovered sources"
            )
        directive_ids = await self.catalog.list_published_directive_ids()
        bundles = await self.catalog.list_published_versions()
        current = await self.catalog.list_current_pointers()
        relations = await self.catalog.list_published_relations()
        expected_mandates = parse_mandates(
            self.config.mandate_csv,
            self.config.azure_tenant_id,
            directive_ids,
        )
        if expected_validation_digest is not None:
            await self._validate_published_approval(
                expected_validation_digest,
                environment_digest,
                source_inventory_digest,
                expected_mandates.checksum,
            )
            if any(
                state.validation_digest != expected_validation_digest
                or state.mandate_checksum != expected_mandates.checksum
                for state in source_states
            ):
                raise RuntimeError(
                    "Source-state records do not match the approved validation "
                    "or mandate checksum"
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
        expected_content_identities: set[tuple[str, str, str, str, str]] = set()
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
            canonical_hash = hashlib.sha256(
                (
                    bundle.source_filename
                    + "\0"
                    + (await self.blobs.read_text(
                        bundle.artifacts.canonical_blob_name
                    ))
                ).encode("utf-8")
            ).hexdigest()
            base_generation_id = calculate_artifact_generation_id(
                bundle.processing_hash,
                canonical_hash,
                canonical_json_hash(bundle.summary),
            )
            expected_generation_id = _expected_live_generation_id(
                bundle, base_generation_id, canonical_hash
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
            for section_id, descriptor in bundle.section_content.items():
                for ordinal in range(descriptor.part_count):
                    item_id = section_content_item_id(
                        bundle.artifact_generation_id, section_id, ordinal
                    )
                    item = await self.content.read_item(
                        bundle.directive_version_id, item_id
                    )
                    expected_content_identities.add(
                        (
                            bundle.directive_version_id,
                            item_id,
                            item.directive_id,
                            item.section_hash,
                            item.part_hash,
                        )
                    )
            await self.search.validate_current_generation(bundle)
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
        if (
            await self.content.list_identities()
            != expected_content_identities
        ):
            raise RuntimeError(
                "Section-content records do not exactly match published bundles"
            )

        search = await self.search.verification_summary()
        await self.search.validate_exact_published(bundles)
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

        mandates = await self.mandates.validate_exact(expected_mandates)
        if (
            mandates["assignment_count"]
            != len(expected_mandates.assignments)
            or mandates["user_count"] != expected_mandates.user_count
        ):
            raise RuntimeError(
                "Active mandate snapshot does not match the source CSV"
            )

        inventory = _source_inventory(sources)
        normalized_ids = sorted(directive_ids)
        version_ids = sorted(
            bundle.directive_version_id for bundle in bundles
        )
        artifact_identities = sorted(
            [
                (
                    name,
                    await self.blobs.content_hash(name),
                )
                for name in required_artifacts
            ]
        )
        cross_store = {
            "catalog": {
                "directive_count": len(directive_ids),
                "version_count": len(bundles),
                "current_count": len(current),
                "identity_digest": _public_record_digest(
                    [
                        {
                            "directive_id": bundle.directive_id,
                            "directive_version_id": bundle.directive_version_id,
                            "artifact_generation_id": bundle.artifact_generation_id,
                        }
                        for bundle in sorted(
                            bundles,
                            key=lambda value: (
                                value.directive_id,
                                value.directive_version_id,
                            ),
                        )
                    ]
                ),
            },
            "content": {
                "item_count": len(expected_content_identities),
                "section_count": content_sections,
                "part_count": content_parts,
                "identity_digest": _public_record_digest(
                    sorted(expected_content_identities)
                ),
            },
            "artifacts": {
                "object_count": len(existing_artifacts),
                "required_count": len(required_artifacts),
                "identity_digest": _public_record_digest(artifact_identities),
            },
            "source_state": {
                "record_count": len(source_states),
                "identity_digest": _public_record_digest(
                    sorted(
                        (
                            state.source_filename,
                            state.source_hash,
                            state.processing_hash,
                            state.artifact_generation_id,
                        )
                        for state in source_states
                    )
                ),
            },
            "search": {
                "document_count": int(search["published_chunks"]),
                "current_document_count": int(search["current_chunks"]),
                "directive_count": int(search["published_directives"]),
                "version_count": int(search["published_versions"]),
                "vector_dimensions": int(search["vector_dimensions"]),
                "vector_profile": str(search["vector_profile"]),
                "vectorizer": str(search["vectorizer"]),
                "semantic_configuration": str(search["semantic_configuration"]),
                "direct_hybrid_query": str(search["direct_hybrid_query"]),
                "identity_digest": _public_record_digest(
                    sorted(
                        chunk_id
                        for bundle in bundles
                        for section in bundle.manifest.sections
                        for chunk_id in section.chunk_ids
                    )
                ),
            },
            "mandates": {
                "snapshot_id": str(mandates["snapshot_id"]),
                "checksum": str(mandates["checksum"]),
                "assignment_count": int(mandates["assignment_count"]),
                "user_count": int(mandates["user_count"]),
                "identity_digest": _public_record_digest(
                    [
                        (assignment.user_id, assignment.directive_id)
                        for assignment in expected_mandates.assignments
                    ]
                ),
            },
        }
        payload: dict[str, object] = {
            "record_schema": "directive.verify.v2",
            "success": True,
            "run_id": run_id,
            "environment": environment,
            "environment_digest": environment_digest,
            "processing_version": self.config.processing_version,
            "processing_hash": self.config.processing_hash,
            "search_index": self.config.search_index,
            "source_inventory_digest": source_inventory_digest,
            "source_count": len(sources),
            "directive_count": len(directive_ids),
            "normalized_directive_ids": normalized_ids,
            "directive_version_ids": version_ids,
            "mandate_checksum": expected_mandates.checksum,
            "warnings": [],
            "warning_count": 0,
            "cross_store": cross_store,
            "validation_digest": expected_validation_digest,
        }
        if cross_store["mandates"]["checksum"] != payload["mandate_checksum"]:
            raise RuntimeError(
                "Active mandate checksum does not match the verified top-level "
                "mandate checksum"
            )
        payload["state_digest"] = _public_record_digest(
            {
                key: payload[key]
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
        payload["verify_digest"] = _public_record_digest(payload)
        format_result(payload)
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
        sources = await self.discover_sources(source_directory)
        _validate_public_corpus_limit(sources)
        snapshot = await self._metadata_validation_snapshot(
            sources, _run_id(), mandate_csv
        )
        format_result(snapshot.payload)
        return snapshot.payload

    async def run_daily(
        self,
        source_directory: Path | None = None,
        mandate_csv: Path | None = None,
        *,
        approved_validation_digest: str | None = None,
        approved_environment_digest: str | None = None,
        approved_source_inventory_digest: str | None = None,
    ) -> ReconcileResult:
        run_id = _run_id()
        sources = await self.discover_sources(source_directory)
        _validate_public_corpus_limit(sources)
        approval = _daily_run_approval(
            approved_validation_digest,
            approved_environment_digest,
            approved_source_inventory_digest,
        )
        self._validate_daily_approval(approval, sources)
        snapshot = await self._metadata_validation_snapshot(
            sources, run_id, mandate_csv
        )
        metadata = snapshot.metadata
        parsed_mandates = snapshot.mandates
        known_ids = {item.metadata.directive_id for item in metadata}
        self._validate_daily_validation_snapshot(approval, snapshot)
        if approval is not None:
            await self._validate_published_approval(
                approval.validation_digest,
                approval.environment_digest,
                approval.source_inventory_digest,
                parsed_mandates.checksum,
            )
        marker_before = await self.commits.load()
        self._validate_pending_marker_corpus(
            metadata,
            marker_before,
            approval.validation_digest if approval is not None else None,
            parsed_mandates.checksum if approval is not None else None,
        )
        prepared = await self.prepare_changed_documents(metadata, run_id)
        await self._validate_relations(
            prepared,
            [item.metadata for item in metadata],
            known_ids,
        )
        mandates_current = await self.mandates.is_current(parsed_mandates)
        replaced: list[PublishedDirectiveVersion] = []
        mandate_transaction: MandatePublicationSnapshot | None = None
        if prepared or not mandates_current:
            await self.search.ensure_resources()
            replaced, mandate_transaction = await self._publish_transaction(
                prepared,
                parsed_mandates if not mandates_current else None,
                run_id,
                validation_digest=(
                    approval.validation_digest if approval is not None else None
                ),
                mandate_checksum=(
                    parsed_mandates.checksum if approval is not None else None
                ),
            )
        await self._reconcile_after_publication(
            metadata,
            replaced,
            run_id,
            mandate_transaction,
            marker_before,
            approval.validation_digest if approval is not None else None,
            parsed_mandates.checksum if approval is not None else None,
        )
        if mandate_transaction is not None:
            snapshot = mandate_transaction.snapshot
            cleanup_changed = await self.mandates.cleanup(snapshot.snapshot_id)
            mandate_changed = mandate_transaction.changed or cleanup_changed
        elif mandates_current:
            summary = await self.mandates.validate_exact(parsed_mandates)
            snapshot = MandateSnapshot(
                snapshot_id=str(summary["snapshot_id"]),
                checksum=str(summary["checksum"]),
                assignment_count=int(summary["assignment_count"]),
                user_count=int(summary["user_count"]),
                complete=True,
            )
            mandate_changed = False
        elif getattr(self.catalog, "snapshot_version", None) is None:
            raise RuntimeError("Mandate publication transaction did not complete")
        if approval is not None:
            await self._bind_source_state_validation_digest(
                metadata, approval.validation_digest, parsed_mandates.checksum
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
        await self.verify(
            expected_validation_digest=(
                approval.validation_digest if approval is not None else None
            )
        )
        if await self.commits.load() is not None:
            await self.commits.clear()
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
        _validate_public_corpus_limit(sources)
        metadata = await self.extract_or_load_metadata(sources, run_id)
        await self._validate_and_quarantine(metadata, run_id)
        marker_before = await self.commits.load()
        self._validate_pending_marker_corpus(metadata, marker_before)
        prepared = await self.prepare_changed_documents(metadata, run_id)
        await self._validate_relations(
            prepared,
            [item.metadata for item in metadata],
            {item.metadata.directive_id for item in metadata},
        )
        replaced: list[PublishedDirectiveVersion] = []
        if prepared:
            await self.search.ensure_resources()
            replaced, _ = await self._publish_transaction(prepared, None, run_id)
        await self._reconcile_after_publication(
            metadata, replaced, run_id, None, marker_before
        )
        await self.verify()
        if await self.commits.load() is not None:
            await self.commits.clear()
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

    async def _reconcile_after_publication(
        self,
        metadata: list[SourceMetadata],
        replaced: list[PublishedDirectiveVersion],
        run_id: str,
        mandates: MandatePublicationSnapshot | None,
        marker_before: object | None,
        validation_digest: str | None = None,
        mandate_checksum: str | None = None,
    ) -> None:
        """Rollback only before the durable cleanup marker is written."""
        try:
            await self.reconcile_exact_corpus(
                metadata,
                replaced,
                run_id,
                force_commit=mandates is not None,
                validation_digest=validation_digest,
                mandate_checksum=mandate_checksum,
            )
        except Exception:
            marker_after = await self.commits.load()
            if marker_before is None and marker_after is None:
                snapshots = getattr(self, "_publication_snapshots", [])
                if snapshots or (
                    mandates is not None and mandates.changed
                ):
                    await self._rollback_publication(snapshots, mandates)
            raise

    def _validate_pending_marker_corpus(
        self,
        metadata: list[SourceMetadata],
        marker: object | None,
        validation_digest: str | None = None,
        mandate_checksum: str | None = None,
    ) -> None:
        """Do not activate another corpus until a pending cleanup can resume."""
        if marker is None:
            return
        expected_names = {
            self.source_states.blob_name(item.source, self.config.processing_hash)
            for item in metadata
        }
        marker_names = getattr(marker, "expected_state_names", None)
        if marker_names != expected_names:
            raise RuntimeError(
                "Publication cleanup marker does not match the source corpus"
            )
        marker_validation_digest = getattr(marker, "validation_digest", None)
        if (
            validation_digest is not None
            and marker_validation_digest != validation_digest
        ):
            raise RuntimeError(
                "Publication cleanup marker does not match the approved "
                "validation"
            )
        if (
            mandate_checksum is not None
            and getattr(marker, "mandate_checksum", None) != mandate_checksum
        ):
            raise RuntimeError(
                "Publication cleanup marker does not match the approved mandates"
            )

    def _validate_daily_approval(
        self,
        approval: DailyRunApproval | None,
        sources: list[SourceDocument],
    ) -> None:
        """Reject changed deployment identities before document processing."""
        if approval is None:
            return
        expected_environment = _public_record_digest(
            _safe_environment(self.config)
        )
        expected_inventory = _public_record_digest(_source_inventory(sources))
        if approval.environment_digest != expected_environment:
            raise ValueError(
                "Approved environment digest does not match this deployment"
            )
        if approval.source_inventory_digest != expected_inventory:
            raise ValueError(
                "Approved source inventory digest does not match discovered "
                "sources"
            )

    async def _metadata_validation_snapshot(
        self,
        sources: list[SourceDocument],
        run_id: str,
        mandate_csv: Path | None,
    ) -> ValidationSnapshot:
        """Build the common metadata-only snapshot before any summary work."""
        metadata = await self.extract_or_load_metadata(sources, run_id)
        await self._validate_and_quarantine(metadata, run_id)
        known_ids = {item.metadata.directive_id for item in metadata}
        mandates = parse_mandates(
            mandate_csv or self.config.mandate_csv,
            self.config.azure_tenant_id,
            known_ids,
        )
        warnings = _validation_warnings(metadata, self.config.processing_hash)
        payload = _validation_payload(
            self.config,
            run_id,
            sources,
            metadata,
            mandates,
            warnings,
        )
        return ValidationSnapshot(sources, metadata, mandates, payload)

    def _validate_daily_validation_snapshot(
        self, approval: DailyRunApproval | None, snapshot: ValidationSnapshot
    ) -> None:
        if approval is None:
            return
        if approval.validation_digest != snapshot.validation_digest:
            raise ValueError(
                "Approved validation digest does not match the metadata snapshot"
            )

    async def _validate_published_approval(
        self,
        validation_digest: str,
        environment_digest: str,
        source_inventory_digest: str,
        mandate_checksum: str,
    ) -> None:
        """Read only the approval named by the expected immutable digest."""
        marker = await self.blobs.get_json(
            f"publication-approval/{validation_digest}.json"
        )
        expected = {
            "record_schema": "directive.approval.v2",
            "validation_digest": validation_digest,
            "environment_digest": environment_digest,
            "source_inventory_digest": source_inventory_digest,
            "processing_hash": self.config.processing_hash,
            "mandate_checksum": mandate_checksum,
        }
        if marker != expected:
            raise RuntimeError(
                "Published approval marker does not exactly match the expected "
                "validation snapshot"
            )

    async def _bind_source_state_validation_digest(
        self,
        metadata: list[SourceMetadata],
        validation_digest: str,
        mandate_checksum: str,
    ) -> None:
        """Persist the approved snapshot identity with every live source state."""
        for item in metadata:
            state = await self.source_states.load(
                item.source, self.config.processing_hash
            )
            if state is None or not await self._state_has_live_publication(
                item.source, state
            ):
                raise RuntimeError(
                    "Source-state does not match a live published bundle: "
                    f"{item.source.source_name}"
                )
            if (
                state.validation_digest == validation_digest
                and state.mandate_checksum == mandate_checksum
            ):
                continue
            snapshot = await self.source_states.snapshot(
                item.source, self.config.processing_hash
            )
            if snapshot is None:
                raise RuntimeError(
                    "Source-state changed while binding validation approval"
                )
            await self.source_states.record(
                item.source,
                state.directive_metadata,
                state.artifact_generation_id,
                pending_cleanup=state.pending_cleanup,
                validation_digest=validation_digest,
                mandate_checksum=mandate_checksum,
                expected_etag=snapshot.etag,
            )

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
        if len(metadata) > MAX_PUBLIC_DIRECTIVES:
            raise _SourceSetValidationError(
                [
                    (item.source, "metadata_corpus_limit_exceeded")
                    for item in metadata
                ]
            )
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
                if await self._repair_source_state_if_live(item):
                    continue
                canonical = parse_canonical(
                    item.source, item.extraction, self.config.processing_hash
                )
                try:
                    existing_bundle = await self.catalog.get_published_version(
                        canonical.metadata.directive_id,
                        canonical.metadata.directive_version_id,
                    )
                except IntegrityValidationError:
                    snapshot_method = getattr(
                        self.catalog, "snapshot_version", None
                    )
                    if snapshot_method is None:
                        raise CatalogResetRequiredError(
                            "Catalog repair requires a raw ETag snapshot: "
                            f"{canonical.metadata.directive_version_id}"
                        ) from None
                    if await snapshot_method(
                        canonical.metadata.directive_id,
                        canonical.metadata.directive_version_id,
                    ) is None:
                        raise CatalogResetRequiredError(
                            "Corrupt catalog slot disappeared before repair: "
                            f"{canonical.metadata.directive_version_id}"
                        ) from None
                    # The source document, not a malformed stored descriptor,
                    # is authoritative for a deterministic replacement.
                    existing_bundle = None
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
                generation_id = _generation_id(canonical, summary)
                repair_salt: str | None = None
                if (
                    existing_bundle is not None
                    and existing_bundle.artifact_generation_id == generation_id
                ):
                    repair_salt = _repair_generation_salt(
                        canonical.metadata, generation_id
                    )
                    generation_id = _generation_id(
                        canonical, summary, repair_salt
                    )
                text_chunks = _generation_scoped_chunks(
                    text_chunks, generation_id
                )
                search_chunks = await self.search.build_chunks(
                    canonical, text_chunks
                )
                manifest = _build_manifest(
                    canonical, text_chunks, summary, repair_salt
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
                        repair_generation_salt=repair_salt,
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

    async def _repair_source_state_if_live(self, item: SourceMetadata) -> bool:
        """Repair only the private state record when every public store is exact."""
        try:
            bundle = await self.catalog.get_published_version(
                item.metadata.directive_id, item.metadata.directive_version_id
            )
        except IntegrityValidationError:
            return False
        if bundle is None:
            return False
        state = PublishedSourceState(
            source_filename=item.source.source_name,
            source_hash=item.source.source_hash,
            source_fingerprint=source_fingerprint(
                item.source.source_name, item.source.source_hash
            ),
            processing_hash=item.metadata.processing_hash,
            directive_metadata=item.metadata,
            artifact_generation_id=bundle.artifact_generation_id,
            publication_state="published",
        )
        if not await self._state_has_live_publication(item.source, state):
            return False
        await self.source_states.record(
            item.source, item.metadata, bundle.artifact_generation_id
        )
        return True

    async def stage_documents(
        self,
        prepared: list[PreparedDirective],
        snapshots: list[PublicationSnapshot] | None = None,
    ) -> None:
        for item in prepared:
            snapshot = _publication_snapshot(snapshots, item)
            candidate_etag = await self._publish_artifacts(
                item,
                snapshot.previous_source_artifact if snapshot else None,
            )
            if snapshots is not None and snapshot is not None:
                _replace_publication_snapshot(
                    snapshots,
                    item,
                    candidate_source_artifact_etag=(
                        candidate_etag
                        if isinstance(candidate_etag, str)
                        else None
                    ),
                )
            for content_item in item.content_items:
                await self.content.create_or_compare(content_item)
            await self.content.validate_bundle(item.bundle)
            await self.search.stage_chunks(item.search_chunks)
            await self.catalog.stage_version(
                item.bundle,
                item.canonical.relations,
                item.findings,
            )

    async def publish_documents(
        self,
        prepared: list[PreparedDirective],
        snapshots: list[PublicationSnapshot] | None = None,
    ) -> None:
        try:
            for item in prepared:
                await self.search.publish_chunks(item.search_chunks)
                await self.search.validate_published_chunk_ids(
                    item.canonical,
                    (chunk.id for chunk in item.search_chunks),
                )
                snapshot = _publication_snapshot(snapshots, item)
                if snapshot is None or snapshot.previous_catalog_slot is None:
                    candidate_etag = await self.catalog.publish_version(
                        item.bundle,
                        item.canonical.relations,
                    )
                else:
                    candidate_etag = await self.catalog.publish_version(
                        item.bundle,
                        item.canonical.relations,
                        expected_snapshot=snapshot.previous_catalog_slot,
                    )
                if snapshots is not None and snapshot is not None:
                    _replace_publication_snapshot(
                        snapshots,
                        item,
                        candidate_catalog_etag=(
                            candidate_etag
                            if isinstance(candidate_etag, str)
                            else None
                        ),
                    )
                await self.catalog.validate_published(item.bundle)
        except (RuntimeError, cosmos_exceptions.CosmosHttpResponseError):
            for item in prepared:
                await self.search.retire_chunks(item.search_chunks)
            raise

    async def activate_documents(self, prepared: list[PreparedDirective]) -> None:
        for item in prepared:
            await self.catalog.activate_current(
                item.canonical.metadata, item.bundle.run_id
            )
            await self.search.reconcile_current(item.bundle)

    async def _publish_transaction(
        self,
        prepared: list[PreparedDirective],
        mandates: Any | None = None,
        run_id: str = "",
        *,
        validation_digest: str | None = None,
        mandate_checksum: str | None = None,
    ) -> tuple[
        list[PublishedDirectiveVersion], MandatePublicationSnapshot | None
    ]:
        """Preserve stable catalog slots and current pointers on any late failure."""
        snapshots: list[PublicationSnapshot] = []
        for item in prepared:
            snapshot_method = getattr(self.catalog, "snapshot_version", None)
            previous_catalog_slot = (
                await snapshot_method(
                    item.bundle.directive_id, item.bundle.directive_version_id
                )
                if snapshot_method is not None
                else None
            )
            try:
                previous_version = await self.catalog.get_published_version(
                    item.bundle.directive_id, item.bundle.directive_version_id
                )
            except IntegrityValidationError:
                if previous_catalog_slot is None:
                    raise CatalogResetRequiredError(
                        "Catalog repair requires a raw ETag snapshot: "
                        f"{item.bundle.directive_version_id}"
                    ) from None
                previous_version = None
            previous_current = await self.catalog.get_current(
                item.bundle.directive_id
            )
            previous_current_bundle = None
            preserve_candidate_generation = False
            if isinstance(
                (version_id := (previous_current or {}).get(
                    "directive_version_id"
                )),
                str,
            ):
                try:
                    previous_current_bundle = (
                        await self.catalog.get_published_version(
                            item.bundle.directive_id, version_id
                        )
                    )
                except IntegrityValidationError:
                    if (
                        version_id == item.bundle.directive_version_id
                        and _current_matches_bundle(
                            previous_current, item.bundle
                        )
                    ):
                        preserve_candidate_generation = True
                    else:
                        raise CatalogResetRequiredError(
                            "Corrupt current catalog descriptor requires "
                            "operator reset before publication: "
                            f"{version_id}"
                        ) from None
            state_snapshot_method = getattr(self.source_states, "snapshot", None)
            previous_source_state = (
                await state_snapshot_method(
                    item.source, item.canonical.metadata.processing_hash
                )
                if state_snapshot_method is not None
                else None
            )
            artifact_snapshot_method = getattr(
                self.blobs, "read_bytes_with_etag", None
            )
            artifact_snapshot = (
                await artifact_snapshot_method(
                    item.bundle.artifacts.source_blob_name
                )
                if artifact_snapshot_method is not None
                else None
            )
            previous_source_artifact = (
                SourceArtifactSnapshot(
                    item.bundle.artifacts.source_blob_name,
                    artifact_snapshot[0],
                    artifact_snapshot[1],
                )
                if artifact_snapshot is not None
                else None
            )
            snapshots.append(
                PublicationSnapshot(
                    item=item,
                    previous_version=previous_version,
                    previous_catalog_slot=previous_catalog_slot,
                    previous_current=previous_current,
                    previous_current_bundle=previous_current_bundle,
                    previous_source_state=previous_source_state,
                    previous_source_artifact=previous_source_artifact,
                    preserve_candidate_generation=preserve_candidate_generation,
                )
            )
        mandate_snapshot: MandatePublicationSnapshot | None = None
        try:
            if mandates is not None:
                snapshot, previous_active, changed = await self.mandates.stage(
                    mandates, run_id
                )
                mandate_snapshot = MandatePublicationSnapshot(
                    snapshot, previous_active, changed, run_id
                )
            await self.stage_documents(prepared, snapshots)
            await self.publish_documents(prepared, snapshots)
            await self.activate_documents(prepared)
            if mandate_snapshot is not None and mandate_snapshot.changed:
                candidate_active_etag = await self.mandates.activate(
                    mandate_snapshot.snapshot,
                    run_id,
                    mandate_snapshot.previous_active,
                )
                if not isinstance(candidate_active_etag, str) or not candidate_active_etag:
                    raise RuntimeError(
                        "Mandate activation did not return a candidate ETag"
                    )
                mandate_snapshot = replace(
                    mandate_snapshot,
                    candidate_active_etag=candidate_active_etag,
                )
            await self.record_source_states(
                prepared,
                snapshots,
                validation_digest=validation_digest,
                mandate_checksum=mandate_checksum,
            )
        except Exception:
            await self._rollback_publication(snapshots, mandate_snapshot)
            raise
        self._publication_snapshots = snapshots
        replaced = {
            (
                bundle.directive_id,
                bundle.directive_version_id,
                bundle.artifact_generation_id,
            ): bundle
            for snapshot in snapshots
            for bundle in (
                snapshot.previous_version,
                snapshot.previous_current_bundle,
            )
            if bundle is not None
            and bundle.artifact_generation_id
            != snapshot.item.bundle.artifact_generation_id
        }
        return (
            list(replaced.values()),
            mandate_snapshot,
        )

    async def _rollback_publication(
        self,
        snapshots: list[PublicationSnapshot],
        mandate_snapshot: MandatePublicationSnapshot | None,
    ) -> None:
        """Restore live descriptors before discarding new generation payloads."""
        for snapshot in reversed(snapshots):
            item = snapshot.item
            await self.catalog.restore_current(
                item.bundle.directive_id, snapshot.previous_current
            )
            if snapshot.candidate_catalog_etag is not None:
                await self.catalog.restore_version(
                    item.bundle,
                    snapshot.previous_catalog_slot,
                    snapshot.candidate_catalog_etag,
                )
            elif getattr(self.catalog, "snapshot_version", None) is None:
                # Compatibility with non-ETag test doubles; production
                # publication always records the replacement ETag above.
                await self.catalog.restore_version(
                    item.bundle, snapshot.previous_version
                )
            if snapshot.candidate_source_state_etag is not None:
                restore_method = getattr(self.source_states, "restore", None)
                if restore_method is None:
                    raise RuntimeError(
                        "Source-state rollback requires ETag-safe restoration"
                    )
                await restore_method(
                    snapshot.previous_source_state,
                    item.source,
                    item.canonical.metadata.processing_hash,
                    snapshot.candidate_source_state_etag,
                )
            if snapshot.candidate_source_artifact_etag is not None:
                if snapshot.previous_source_artifact is None:
                    await self.blobs.delete_if_etag(
                        item.bundle.artifacts.source_blob_name,
                        snapshot.candidate_source_artifact_etag,
                    )
                else:
                    await self.blobs.restore_bytes(
                        snapshot.previous_source_artifact.blob_name,
                        snapshot.previous_source_artifact.content,
                        snapshot.candidate_source_artifact_etag,
                        content_type="application/pdf",
                    )
            if (
                snapshot.preserve_candidate_generation
                or (
                    snapshot.previous_version is not None
                    and snapshot.previous_version.artifact_generation_id
                    == item.bundle.artifact_generation_id
                )
            ):
                continue
            if snapshot.previous_current_bundle is not None:
                await self.search.restore_current_generation(
                    snapshot.previous_current_bundle
                )
            await self.search.delete_chunks(item.search_chunks)
            await self.content.delete_bundle(item.bundle)
            artifact_names = {item.bundle.artifacts.canonical_blob_name}
            referenced_source_blobs = {
                bundle.artifacts.source_blob_name
                for bundle in (
                    snapshot.previous_version,
                    snapshot.previous_current_bundle,
                )
                if bundle is not None
            }
            if (
                item.bundle.artifacts.source_blob_name
                not in referenced_source_blobs
            ):
                artifact_names.add(item.bundle.artifacts.source_blob_name)
            await self.blobs.delete_names(
                artifact_names
            )
        if (
            mandate_snapshot is not None
            and mandate_snapshot.changed
            and mandate_snapshot.candidate_active_etag is not None
        ):
            await self.mandates.restore_active(
                mandate_snapshot.previous_active,
                mandate_snapshot.candidate_active_etag,
            )
        if mandate_snapshot is not None and mandate_snapshot.changed:
            await self.mandates.discard_staged(
                mandate_snapshot.snapshot, mandate_snapshot.run_id
            )

    async def record_source_states(
        self,
        prepared: list[PreparedDirective],
        snapshots: list[PublicationSnapshot] | None = None,
        *,
        validation_digest: str | None = None,
        mandate_checksum: str | None = None,
    ) -> None:
        """Commit private idempotency records only after live publication checks."""
        for item in prepared:
            await self.catalog.validate_published(item.bundle)
            await self.content.validate_bundle(item.bundle)
            await self.search.validate_published_chunk_ids(
                item.canonical,
                (chunk.id for chunk in item.search_chunks),
            )
            snapshot = next(
                (
                    candidate
                    for candidate in snapshots or []
                    if candidate.item is item
                ),
                None,
            )
            record_kwargs: dict[str, object] = {
                "pending_cleanup": tuple(
                    bundle
                    for snapshot in snapshots or []
                    if snapshot.item is item
                    for bundle in (
                        snapshot.previous_version,
                        snapshot.previous_current_bundle,
                    )
                    if bundle is not None
                    and bundle.artifact_generation_id
                    != item.bundle.artifact_generation_id
                ),
                "expected_etag": (
                    snapshot.previous_source_state.etag
                    if snapshot and snapshot.previous_source_state is not None
                    else None
                ),
                "require_absent": (
                    snapshots is not None
                    and snapshot is not None
                    and snapshot.previous_source_state is None
                ),
            }
            if validation_digest is not None:
                record_kwargs["validation_digest"] = validation_digest
                record_kwargs["mandate_checksum"] = mandate_checksum
            candidate_etag = await self.source_states.record(
                item.source,
                item.canonical.metadata,
                item.bundle.artifact_generation_id,
                **record_kwargs,
            )
            if snapshots is not None:
                for index, snapshot in enumerate(snapshots):
                    if snapshot.item is item:
                        snapshots[index] = replace(
                            snapshot,
                            candidate_source_state_etag=candidate_etag,
                        )
                        break

    async def reconcile_exact_corpus(
        self,
        metadata: list[SourceMetadata],
        replaced: list[PublishedDirectiveVersion] | None = None,
        run_id: str | None = None,
        force_commit: bool = False,
        validation_digest: str | None = None,
        mandate_checksum: str | None = None,
    ) -> None:
        """Retire every store record not represented by validated sources."""
        expected_versions = {
            (item.metadata.directive_id, item.metadata.directive_version_id)
            for item in metadata
        }
        published = await self.catalog.list_published_versions()
        retired = [
            bundle
            for bundle in published
            if (bundle.directive_id, bundle.directive_version_id)
            not in expected_versions
        ]
        surviving_source_blobs = {
            bundle.artifacts.source_blob_name
            for bundle in published
            if bundle not in retired
        }
        stale = {
            (
                bundle.directive_id,
                bundle.directive_version_id,
                bundle.artifact_generation_id,
            ): bundle
            for bundle in [
                *retired,
                *(replaced or []),
                *(
                    bundle
                    for item in metadata
                    if item.source_state is not None
                    for bundle in item.source_state.pending_cleanup
                ),
            ]
        }
        expected_state_names = {
            self.source_states.blob_name(item.source, self.config.processing_hash)
            for item in metadata
        }
        marker = await self.commits.load()
        if marker is None:
            candidates = [
                snapshot.item.bundle
                for snapshot in getattr(self, "_publication_snapshots", [])
            ]
            if candidates:
                await self._validate_candidate_documents(metadata, candidates)
            existing_state_names = await self.source_states.list_names()
            if (
                not stale
                and existing_state_names == expected_state_names
                and not force_commit
            ):
                return
            record_args = (
                run_id or _run_id(),
                list(stale.values()),
                expected_state_names,
            )
            marker = (
                await self.commits.record(
                    *record_args,
                    validation_digest=validation_digest,
                    mandate_checksum=mandate_checksum,
                )
                if validation_digest is not None
                else await self.commits.record(*record_args)
            )
        elif marker.expected_state_names != expected_state_names:
            raise RuntimeError(
                "Publication cleanup marker does not match the source corpus"
            )
        elif (
            validation_digest is not None
            and marker.validation_digest != validation_digest
        ):
            raise RuntimeError(
                "Publication cleanup marker does not match the approved "
                "validation"
            )
        elif (
            mandate_checksum is not None
            and marker.mandate_checksum != mandate_checksum
        ):
            raise RuntimeError(
                "Publication cleanup marker does not match the approved mandates"
            )
        stale = {
            (
                bundle.directive_id,
                bundle.directive_version_id,
                bundle.artifact_generation_id,
            ): bundle
            for bundle in marker.stale_bundles
        }
        for bundle in stale.values():
            await self.search.delete_generation(bundle)
            await self.content.delete_bundle(bundle)
            artifact_names = {bundle.artifacts.canonical_blob_name}
            if bundle.artifacts.source_blob_name not in surviving_source_blobs:
                artifact_names.add(bundle.artifacts.source_blob_name)
            await self.blobs.delete_names(artifact_names)
        await self.source_states.prune(expected_state_names)
        await self.catalog.delete_versions(retired)
        for item in metadata:
            state = await self.source_states.load(
                item.source, self.config.processing_hash
            )
            if state is not None and state.pending_cleanup:
                await self.source_states.clear_pending(
                    item.source,
                    state.directive_metadata,
                    state.artifact_generation_id,
                    validation_digest=(
                        validation_digest
                        if validation_digest is not None
                        else state.validation_digest
                    ),
                    mandate_checksum=(
                        mandate_checksum
                        if mandate_checksum is not None
                        else state.mandate_checksum
                    ),
                )

    async def _validate_candidate_documents(
        self,
        metadata: list[SourceMetadata],
        replaced: list[PublishedDirectiveVersion],
    ) -> None:
        """Validate each activated candidate before cleanup can be committed."""
        expected = {
            (item.metadata.directive_id, item.metadata.directive_version_id)
            for item in metadata
        }
        replaced_identities = {
            (bundle.directive_id, bundle.directive_version_id)
            for bundle in replaced
        }
        for item in metadata:
            identity = (
                item.metadata.directive_id,
                item.metadata.directive_version_id,
            )
            if identity not in replaced_identities:
                continue
            state = await self.source_states.load(
                item.source, self.config.processing_hash
            )
            if state is None or not await self._state_has_live_publication(
                item.source, state
            ):
                raise RuntimeError(
                    "Candidate publication does not match its source state"
                )
        if not replaced_identities <= expected:
            raise RuntimeError(
                "Candidate publication contains an unexpected directive version"
            )

    async def _state_has_live_publication(
        self,
        source: SourceDocument,
        state: PublishedSourceState,
    ) -> bool:
        """A state record is insufficient unless its published bundle is live."""
        try:
            metadata = state.directive_metadata
            bundle = await self.catalog.get_published_version(
                metadata.directive_id, metadata.directive_version_id
            )
            if bundle is None:
                raise IntegrityValidationError(
                    "Expected catalog version is missing: "
                    f"{metadata.directive_version_id}"
                )
            if (
                bundle.directive_id != metadata.directive_id
                or bundle.directive_version_id != metadata.directive_version_id
                or bundle.source_filename != source.source_name
                or bundle.source_hash != source.source_hash
                or bundle.processing_hash != metadata.processing_hash
                or bundle.artifact_generation_id != state.artifact_generation_id
            ):
                raise IntegrityValidationError(
                    "Catalog version does not match its source state: "
                    f"{metadata.directive_version_id}"
                )
            bundle_metadata = DirectiveMetadata.model_validate(
                {
                    name: getattr(bundle, name)
                    for name in DirectiveMetadata.model_fields
                }
            )
            if bundle_metadata != metadata:
                raise IntegrityValidationError(
                    "Catalog metadata does not match its source state: "
                    f"{metadata.directive_version_id}"
                )
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
                raise IntegrityValidationError(
                    "Current catalog pointer does not match its source state: "
                    f"{metadata.directive_version_id}"
                )
            if not (
                await self.blobs.exists(bundle.artifacts.source_blob_name)
                and await self.blobs.exists(bundle.artifacts.canonical_blob_name)
            ):
                raise IntegrityValidationError(
                    "Expected published artifacts are missing: "
                    f"{metadata.directive_version_id}"
                )
            await self.blobs.validate_hash(
                bundle.artifacts.source_blob_name, source.source_hash
            )
            markdown = await self.blobs.read_text(
                bundle.artifacts.canonical_blob_name
            )
            canonical_hash = hashlib.sha256(
                f"{source.source_name}\0{markdown}".encode("utf-8")
            ).hexdigest()
            base_generation_id = calculate_artifact_generation_id(
                metadata.processing_hash,
                canonical_hash,
                canonical_json_hash(bundle.summary),
            )
            expected_generation_id = _expected_live_generation_id(
                bundle, base_generation_id, canonical_hash
            )
            if expected_generation_id != bundle.artifact_generation_id:
                raise IntegrityValidationError(
                    "Published artifact generation does not match its content: "
                    f"{metadata.directive_version_id}"
                )
            await self.content.validate_bundle(bundle)
            await self.search.validate_current_generation(bundle)
        except (AttributeError, ValueError) as exc:
            return False
        except IntegrityValidationError:
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
        await self._publish_transaction(prepared, None, run_id)

    async def _publish_artifacts(
        self,
        item: PreparedDirective,
        source_snapshot: SourceArtifactSnapshot | None = None,
    ) -> str | None:
        artifacts = item.bundle.artifacts
        candidate_source_etag: str | None = None
        if source_snapshot is None:
            candidate_source_etag = await self.blobs.put_immutable(
                artifacts.source_blob_name,
                item.source.content,
                "application/pdf",
            )
        elif source_snapshot.content != item.source.content:
            candidate_source_etag = await self.blobs.replace_bytes(
                artifacts.source_blob_name,
                item.source.content,
                "application/pdf",
                expected_etag=source_snapshot.etag,
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
        return candidate_source_etag

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
    repair_salt: str | None = None,
) -> DirectiveManifest:
    metadata = directive.metadata
    generation_id = _generation_id(directive, summary, repair_salt)
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


def _generation_canonical_hash(directive: CanonicalDirective) -> str:
    """Bind a generation to the source filename as well as canonical content."""
    return hashlib.sha256(
        (
            directive.metadata.source_filename
            + "\0"
            + directive.markdown
        ).encode("utf-8")
    ).hexdigest()


def _generation_id(
    directive: CanonicalDirective,
    summary: DirectiveSummary,
    repair_salt: str | None = None,
) -> str:
    canonical_hash = _generation_canonical_hash(directive)
    if repair_salt is not None:
        canonical_hash = hashlib.sha256(
            f"{canonical_hash}\0repair:{repair_salt}".encode("utf-8")
        ).hexdigest()
    return calculate_artifact_generation_id(
        directive.metadata.processing_hash,
        canonical_hash,
        canonical_json_hash(summary),
    )


def _repair_generation_salt(
    metadata: DirectiveMetadata, base_generation_id: str
) -> str:
    return hashlib.sha256(
        (
            "directive-generation-repair\0"
            + metadata.directive_id
            + "\0"
            + metadata.directive_version_id
            + "\0"
            + metadata.source_hash
            + "\0"
            + metadata.processing_hash
            + "\0"
            + base_generation_id
        ).encode("utf-8")
    ).hexdigest()


def _expected_live_generation_id(
    bundle: PublishedDirectiveVersion,
    base_generation_id: str,
    canonical_hash: str,
) -> str:
    if bundle.artifact_generation_id == base_generation_id:
        return base_generation_id
    repair_salt = _repair_generation_salt(bundle, base_generation_id)
    canonical_hash = hashlib.sha256(
        (
            canonical_hash
            + "\0repair:"
            + repair_salt
        ).encode("utf-8")
    ).hexdigest()
    return calculate_artifact_generation_id(
        bundle.processing_hash,
        canonical_hash,
        canonical_json_hash(bundle.summary),
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
        raise IntegrityValidationError("Published source artifact locator is unsafe")
    if bundle.artifacts.canonical_blob_name != (
        f"{source_base}/generations/{bundle.artifact_generation_id}/document.md"
    ):
        raise IntegrityValidationError(
            "Published canonical artifact locator is unsafe"
        )


def _safe_environment(config: IngestionConfig) -> dict[str, str]:
    return {
        "source_kind": config.source_kind,
        "source_storage_account": config.source_storage_account,
        "source_container": config.source_container,
        "source_prefix": config.source_prefix,
        "artifact_storage_account": config.artifact_storage_account,
        "artifact_container": config.blob_container,
        "cosmos_account": config.cosmos_account,
        "cosmos_database": config.cosmos_database,
        "catalog_container": config.catalog_container,
        "content_container": config.content_container,
        "mandate_container": config.mandate_container,
        "search_service": config.search_service,
        "search_index": config.search_index,
    }


def _daily_run_approval(
    validation_digest: str | None,
    environment_digest: str | None,
    source_inventory_digest: str | None,
) -> DailyRunApproval | None:
    values = (
        validation_digest,
        environment_digest,
        source_inventory_digest,
    )
    if all(value is None for value in values):
        return None
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError(
            "Daily run approvals must include nonempty validation, environment, "
            "and source inventory digests"
        )
    return DailyRunApproval(
        validation_digest=(validation_digest or "").strip(),
        environment_digest=(environment_digest or "").strip(),
        source_inventory_digest=(source_inventory_digest or "").strip(),
    )


def _expected_validation_digest(
    validation_digest: str | None, expected_validation_digest: str | None
) -> str | None:
    values = [
        value.strip()
        for value in (validation_digest, expected_validation_digest)
        if isinstance(value, str) and value.strip()
    ]
    if len(values) != len(
        [
            value
            for value in (validation_digest, expected_validation_digest)
            if value is not None
        ]
    ):
        raise ValueError("Expected validation digest must not be empty")
    if len(set(values)) > 1:
        raise ValueError("Expected validation digest values do not match")
    return values[0] if values else None


def _validation_payload(
    config: IngestionConfig,
    run_id: str,
    sources: list[SourceDocument],
    metadata: list[SourceMetadata],
    mandates: Any,
    warnings: list[dict[str, str]],
) -> dict[str, object]:
    environment = _safe_environment(config)
    known_ids = {item.metadata.directive_id for item in metadata}
    payload: dict[str, object] = {
        "record_schema": "directive.validate.v2",
        "success": True,
        "run_id": run_id,
        "environment": environment,
        "environment_digest": _public_record_digest(environment),
        "processing_version": config.processing_version,
        "processing_hash": config.processing_hash,
        "search_index": config.search_index,
        "source_count": len(sources),
        "directive_count": len(known_ids),
        "normalized_directive_ids": sorted(known_ids),
        "directive_version_ids": sorted(
            item.metadata.directive_version_id for item in metadata
        ),
        "mandate_count": len(mandates.assignments),
        "mandate_user_count": mandates.user_count,
        "mandate_checksum": _mandate_checksum(mandates),
        "warnings": warnings,
        "warning_count": len(warnings),
        "failures": [],
        "source_inventory_digest": _public_record_digest(
            _source_inventory(sources)
        ),
    }
    payload["validation_digest"] = _public_record_digest(
        _validation_digest_projection(payload)
    )
    return payload


def _validation_digest_projection(payload: dict[str, object]) -> dict[str, object]:
    """Only hash the public, stable validation contract—not run metadata."""
    fields = (
        "record_schema",
        "success",
        "environment",
        "environment_digest",
        "processing_version",
        "processing_hash",
        "search_index",
        "source_count",
        "directive_count",
        "normalized_directive_ids",
        "directive_version_ids",
        "mandate_count",
        "mandate_user_count",
        "mandate_checksum",
        "warnings",
        "warning_count",
        "failures",
        "source_inventory_digest",
    )
    return {field: payload[field] for field in fields}


def _mandate_checksum(mandates: Any) -> str:
    """Bind approval to canonical tenant-qualified mandate assignments."""
    checksum = getattr(mandates, "checksum", None)
    if _is_checksum(checksum):
        return checksum
    raise ValueError("Mandate checksum is unavailable or invalid")


def _is_checksum(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


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


def _validate_public_corpus_limit(sources: list[SourceDocument]) -> None:
    if len(sources) > MAX_PUBLIC_DIRECTIVES:
        raise ValueError(
            "Directive source corpus exceeds the public producer limit of "
            f"{MAX_PUBLIC_DIRECTIVES} sources"
        )


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


def _public_record_digest(value: object) -> str:
    _reject_floats(value)
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _reject_floats(value: object) -> None:
    if isinstance(value, float):
        raise ValueError("Public producer records must not contain floats")
    if isinstance(value, dict):
        for child in value.values():
            _reject_floats(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_floats(child)


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
    _reject_floats(value)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if len(encoded.encode("utf-8")) > MAX_PUBLIC_RECORD_BYTES:
        raise ValueError(
            f"Public directive record exceeds {MAX_PUBLIC_RECORD_BYTES} bytes"
        )
    return encoded
