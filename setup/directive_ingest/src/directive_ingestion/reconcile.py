"""End-to-end idempotent directive and mandate reconciliation."""

from __future__ import annotations

import json
import hashlib
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from directive_contracts import (
    DirectiveChunk,
    DirectiveManifest,
    DirectiveMetadata,
    DirectiveRelation,
    DirectiveSection,
    DirectiveSummary,
    MandateSnapshot,
    ReviewFinding,
)
from openai import APIError

from .blob_repository import BlobArtifactRepository
from .canonical import CanonicalDirective, parse_canonical
from .catalog_repository import DirectiveCatalogRepository
from .chunking import TextChunk, chunk_sections
from .clients import IngestionClients
from .config import IngestionConfig
from .document_intelligence import DocumentIntelligenceExtractor
from .mandate_projection import MandateRepository, parse_mandates
from .search_repository import DirectiveSearchRepository
from .source import SourceDocument, discover_pdfs
from .summaries import SummaryGenerator


@dataclass(frozen=True)
class PreparedDirective:
    source: SourceDocument
    canonical: CanonicalDirective
    text_chunks: list[TextChunk]
    search_chunks: list[DirectiveChunk]
    summary: DirectiveSummary
    manifest: DirectiveManifest
    findings: tuple[ReviewFinding, ...]


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
        self.catalog = DirectiveCatalogRepository(
            config.cosmos_endpoint,
            config.cosmos_database,
            config.catalog_container,
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

    async def close(self) -> None:
        await self.search.close()
        await self.mandates.close()
        await self.catalog.close()
        await self.blobs.close()
        await self.extractor.close()
        await self.clients.close()

    async def bootstrap(self) -> None:
        await self.search.ensure_resources()

    async def preflight(self) -> dict[str, str]:
        await self.blobs.check_access()
        await self.catalog.check_access()
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
        await self._preflight_response_model(
            self.config.knowledge_model_deployment, "knowledge planner"
        )
        return {
            "acr_pull": "ok",
            "blob": "ok",
            "cosmos_catalog": "ok",
            "cosmos_mandates": "ok",
            "search": "ok",
            "document_intelligence": "ok",
            "embeddings": "ok",
            "summary_model": "ok",
            "knowledge_planner_model": "ok",
        }

    async def verify(self) -> dict[str, object]:
        sources = discover_pdfs(self.config.source_directory)
        expected_directive_ids = {
            source.directive_id_hint for source in sources
        }
        expected_mandates = parse_mandates(
            self.config.mandate_csv,
            self.config.azure_tenant_id,
            expected_directive_ids,
        )

        directive_ids = await self.catalog.list_published_directive_ids()
        manifests = await self.catalog.list_published_manifests()
        current = await self.catalog.list_current_pointers()
        relations = await self.catalog.list_published_relations()
        canonical_relation_ids = {
            relation.relation_id for relation, _, _ in relations
        }
        if directive_ids != expected_directive_ids:
            raise RuntimeError(
                "Published directive IDs do not match the source corpus"
            )
        if len(manifests) != len(sources):
            raise RuntimeError(
                f"Expected {len(sources)} published manifests, found "
                f"{len(manifests)}"
            )
        if set(current) != expected_directive_ids:
            raise RuntimeError(
                "Current directive pointers do not match the source corpus"
            )

        required_artifacts: set[str] = set()
        expected_chunks = 0
        for manifest in manifests:
            required_artifacts.update(
                {
                    manifest.source_blob_name,
                    manifest.canonical_blob_name,
                    manifest.summary_blob_name,
                    manifest.manifest_blob_name,
                }
            )
            for section in manifest.sections:
                required_artifacts.add(section.blob_name)
                expected_chunks += len(section.chunk_ids)
        existing_artifacts = await self.blobs.list_names("directives/")
        missing = sorted(required_artifacts - existing_artifacts)
        if missing:
            raise RuntimeError(
                "Published manifests reference missing artifacts: "
                + ", ".join(missing)
            )

        search = await self.search.verification_summary()
        if (
            search["published_chunks"] != expected_chunks
            or search["published_directives"] != len(expected_directive_ids)
            or search["published_versions"] != len(sources)
            or search["current_directives"] != len(expected_directive_ids)
            or search["current_versions"] != len(expected_directive_ids)
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

        return {
            "source_versions": len(sources),
            "directive_ids": len(directive_ids),
            "current_versions": len(current),
            "accepted_relations": len(canonical_relation_ids),
            "required_artifacts": len(required_artifacts),
            **search,
            **{
                f"mandate_{key}": value
                for key, value in mandates.items()
            },
        }

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
    ) -> dict[str, int]:
        sources = discover_pdfs(source_directory or self.config.source_directory)
        known_ids = {source.directive_id_hint for source in sources}
        mandates = parse_mandates(
            mandate_csv or self.config.mandate_csv,
            self.config.azure_tenant_id,
            known_ids,
        )
        return {
            "source_count": len(sources),
            "directive_count": len(known_ids),
            "mandate_count": len(mandates.assignments),
            "mandate_user_count": mandates.user_count,
        }

    async def run_daily(
        self,
        source_directory: Path | None = None,
        mandate_csv: Path | None = None,
    ) -> ReconcileResult:
        run_id = _run_id()
        source_path = source_directory or self.config.source_directory
        sources = discover_pdfs(source_path)
        await self.search.ensure_resources()
        prepared, metadata = await self._prepare(sources, run_id)
        await self._validate_source_set(metadata)
        known_ids = {
            item.directive_id for item in metadata
        } | await self.catalog.list_published_directive_ids()
        await self._validate_relations(prepared, metadata, known_ids)
        parsed_mandates = parse_mandates(
            mandate_csv or self.config.mandate_csv,
            self.config.azure_tenant_id,
            known_ids,
        )
        await self._publish_documents(prepared, metadata, run_id)
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
        sources = discover_pdfs(
            source_directory or self.config.source_directory
        )
        await self.search.ensure_resources()
        prepared, metadata = await self._prepare(sources, run_id)
        await self._validate_source_set(metadata)
        known_ids = {
            item.directive_id for item in metadata
        } | await self.catalog.list_published_directive_ids()
        await self._validate_relations(prepared, metadata, known_ids)
        await self._publish_documents(prepared, metadata, run_id)
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

    async def _prepare(
        self, sources: list[SourceDocument], run_id: str
    ) -> tuple[list[PreparedDirective], list[DirectiveMetadata]]:
        prepared: list[PreparedDirective] = []
        all_metadata: list[DirectiveMetadata] = []
        failures: list[tuple[SourceDocument, str]] = []
        for index, source in enumerate(sources, 1):
            if await self.catalog.is_unchanged(
                source, self.config.processing_hash
            ):
                item = await self.catalog.get_version(
                    source.directive_id_hint,
                    source.directive_version_id_hint,
                )
                if item is None:
                    raise RuntimeError(
                        "Catalog version disappeared during unchanged check"
                    )
                all_metadata.append(_metadata_from_catalog(item))
                print(
                    f"[{index}/{len(sources)}] unchanged: "
                    f"{source.path.name}",
                    flush=True,
                )
                continue
            print(
                f"[{index}/{len(sources)}] extracting: {source.path.name}",
                flush=True,
            )
            try:
                extraction = await self.extractor.extract(source.content)
                canonical = parse_canonical(
                    source, extraction, self.config.processing_hash
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
                search_chunks = await self.search.build_chunks(
                    canonical, text_chunks
                )
                manifest = _build_manifest(
                    canonical, text_chunks, summary
                )
                prepared.append(
                    PreparedDirective(
                        source=source,
                        canonical=canonical,
                        text_chunks=text_chunks,
                        search_chunks=search_chunks,
                        summary=summary,
                        manifest=manifest,
                        findings=tuple(findings),
                    )
                )
                all_metadata.append(canonical.metadata)
            except (
                APIError,
                httpx.HTTPError,
                RuntimeError,
                TimeoutError,
                ValueError,
            ) as exc:
                failures.append((source, str(exc)))
        if failures:
            for source, error in failures:
                await self.blobs.quarantine(
                    run_id, source.path.name, source.content, [error]
                )
            names = ", ".join(source.path.name for source, _ in failures)
            raise RuntimeError(
                f"Preflight failed for {len(failures)} directive(s): {names}"
            )
        return prepared, all_metadata

    async def _publish_documents(
        self,
        prepared: list[PreparedDirective],
        metadata: list[DirectiveMetadata],
        run_id: str,
    ) -> None:
        for item in prepared:
            await self._publish_artifacts(item)
            await self.search.stage_chunks(item.search_chunks)
            await self.catalog.stage_version(
                item.canonical.metadata,
                item.manifest,
                item.summary,
                item.canonical.relations,
                item.findings,
                run_id,
            )
        for item in prepared:
            await self.search.publish_chunks(item.search_chunks)
            await self.search.validate_published(
                item.canonical, len(item.search_chunks)
            )
            await self.catalog.publish_version(
                item.canonical.metadata,
                item.manifest,
                item.canonical.relations,
                run_id,
            )
            await self.catalog.validate_published(
                item.canonical.metadata, item.manifest
            )
        for item in metadata:
            await self.catalog.activate_current(item, run_id)
        for item in metadata:
            await self.search.reconcile_current(item)
        for item in metadata:
            await self.search.reconcile_generation(item)

    async def _publish_artifacts(self, item: PreparedDirective) -> None:
        manifest = item.manifest
        await self.blobs.put_immutable(
            manifest.source_blob_name,
            item.source.content,
            "application/pdf",
        )
        await self.blobs.put_immutable(
            manifest.canonical_blob_name,
            item.canonical.markdown.encode(),
            "text/markdown; charset=utf-8",
        )
        section_by_id = {
            section.section_id: section for section in item.canonical.sections
        }
        for section in manifest.sections:
            await self.blobs.put_immutable(
                section.blob_name,
                section_by_id[section.section_id].content.encode(),
                "text/markdown; charset=utf-8",
            )
        await self.blobs.put_json(
            manifest.summary_blob_name,
            item.summary.model_dump(mode="json"),
        )
        await self.blobs.put_json(
            manifest.manifest_blob_name,
            manifest.model_dump(mode="json"),
        )
        required = [
            manifest.source_blob_name,
            manifest.canonical_blob_name,
            manifest.summary_blob_name,
            manifest.manifest_blob_name,
            *(section.blob_name for section in manifest.sections),
        ]
        missing = [
            blob_name
            for blob_name in required
            if not await self.blobs.exists(blob_name)
        ]
        if missing:
            raise RuntimeError(
                "Artifact validation failed; missing: " + ", ".join(missing)
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

        current = await self.catalog.list_current_pointers()
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
    source_base = (
        f"directives/{metadata.directive_id}/"
        f"{metadata.directive_version_id}/{metadata.source_hash}"
    )
    canonical_hash = hashlib.sha256(directive.markdown.encode()).hexdigest()
    summary_json = json.dumps(
        summary.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    summary_hash = hashlib.sha256(summary_json.encode()).hexdigest()
    generation_hash = hashlib.sha256(
        (
            f"{metadata.processing_hash}|{canonical_hash}|{summary_hash}"
        ).encode()
    ).hexdigest()
    base = f"{source_base}/generations/{generation_hash}"
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
            blob_name=(
                f"{base}/sections/{section.ordinal:04d}-"
                f"{section.section_id}.md"
            ),
            chunk_ids=chunk_ids[section.section_id],
        )
        for section in directive.sections
    ]
    return DirectiveManifest(
        directive_id=metadata.directive_id,
        directive_version_id=metadata.directive_version_id,
        source_hash=metadata.source_hash,
        total_pages=directive.total_pages,
        total_tokens=directive.total_tokens,
        canonical_blob_name=f"{base}/document.md",
        source_blob_name=f"{source_base}/source.pdf",
        summary_blob_name=f"{base}/summary.json",
        manifest_blob_name=f"{base}/manifest.json",
        sections=sections,
    )


def _metadata_from_catalog(item: dict[str, Any]) -> DirectiveMetadata:
    values = {
        name: item[name]
        for name in DirectiveMetadata.model_fields
        if name in item
    }
    return DirectiveMetadata.model_validate(values)


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
