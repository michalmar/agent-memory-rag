"""Directive catalog publication in the dedicated Cosmos database."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from azure.cosmos import exceptions
from azure.cosmos.aio import CosmosClient
from directive_contracts import (
    DirectiveManifest,
    DirectiveMetadata,
    DirectiveRelation,
    DirectiveSummary,
    ReviewFinding,
)

from .source import SourceDocument


def version_item_id(directive_version_id: str) -> str:
    return f"version:{directive_version_id}"


class DirectiveCatalogRepository:
    def __init__(
        self,
        endpoint: str,
        database_name: str,
        container_name: str,
        credential: Any,
    ) -> None:
        self._client = CosmosClient(endpoint, credential=credential)
        database = self._client.get_database_client(database_name)
        self._container = database.get_container_client(container_name)

    async def close(self) -> None:
        await self._client.close()

    async def check_access(self) -> None:
        await self._container.read()

    async def get_version(
        self, directive_id: str, directive_version_id: str
    ) -> dict[str, Any] | None:
        try:
            return await self._container.read_item(
                item=version_item_id(directive_version_id),
                partition_key=directive_id,
            )
        except exceptions.CosmosResourceNotFoundError:
            return None

    async def get_current(
        self, directive_id: str
    ) -> dict[str, Any] | None:
        try:
            return await self._container.read_item(
                item="current", partition_key=directive_id
            )
        except exceptions.CosmosResourceNotFoundError:
            return None

    async def is_unchanged(
        self, source: SourceDocument, processing_hash: str
    ) -> bool:
        item = await self.get_version(
            source.directive_id_hint, source.directive_version_id_hint
        )
        return bool(
            item
            and item.get("publication_state") == "published"
            and item.get("source_hash") == source.source_hash
            and item.get("processing_hash") == processing_hash
        )

    async def stage_version(
        self,
        metadata: DirectiveMetadata,
        manifest: DirectiveManifest,
        summary: DirectiveSummary,
        relations: tuple[DirectiveRelation, ...],
        findings: tuple[ReviewFinding, ...],
        run_id: str,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        metadata_json = metadata.model_dump(mode="json")
        await self._container.upsert_item(
            {
                "id": (
                    f"staging:{metadata.directive_version_id}:"
                    f"{metadata.source_hash}"
                ),
                "type": "staging",
                "directive_id": metadata.directive_id,
                **metadata_json,
                "manifest_blob_name": manifest.manifest_blob_name,
                "summary_blob_name": manifest.summary_blob_name,
                "publication_state": "staged",
                "run_id": run_id,
                "updated_at": now,
            }
        )
        await self._container.upsert_item(
            {
                "id": (
                    f"manifest:{metadata.directive_version_id}:"
                    f"{metadata.source_hash}"
                ),
                "type": "manifest",
                "directive_id": metadata.directive_id,
                "directive_version_id": metadata.directive_version_id,
                "source_hash": metadata.source_hash,
                "manifest": manifest.model_dump(mode="json"),
                "run_id": run_id,
                "updated_at": now,
            }
        )
        await self._container.upsert_item(
            {
                "id": (
                    f"summary:{metadata.directive_version_id}:"
                    f"{metadata.source_hash}"
                ),
                "type": "summary",
                "directive_id": metadata.directive_id,
                "directive_version_id": metadata.directive_version_id,
                "source_hash": metadata.source_hash,
                "summary": summary.model_dump(mode="json"),
                "run_id": run_id,
                "updated_at": now,
            }
        )
        await self._container.upsert_item(
            {
                "id": (
                    f"review:{metadata.directive_version_id}:"
                    f"{metadata.source_hash}"
                ),
                "type": "review",
                "directive_id": metadata.directive_id,
                "directive_version_id": metadata.directive_version_id,
                "source_hash": metadata.source_hash,
                "findings": [
                    finding.model_dump(mode="json") for finding in findings
                ],
                "needs_review": any(
                    finding.severity in {"warning", "error"}
                    for finding in findings
                ),
                "run_id": run_id,
                "updated_at": now,
            }
        )
        for relation in relations:
            await self._container.upsert_item(
                {
                    "id": (
                        f"relation:{relation.relation_id}:"
                        f"{metadata.source_hash}:"
                        f"{metadata.processing_hash}"
                    ),
                    "type": "relation",
                    "directive_id": metadata.directive_id,
                    **relation.model_dump(mode="json"),
                    "source_hash": metadata.source_hash,
                    "processing_hash": metadata.processing_hash,
                    "publication_state": "staged",
                    "run_id": run_id,
                    "updated_at": now,
                }
            )

    async def publish_version(
        self,
        metadata: DirectiveMetadata,
        manifest: DirectiveManifest,
        relations: tuple[DirectiveRelation, ...],
        run_id: str,
    ) -> None:
        await self._container.upsert_item(
            {
                "id": version_item_id(metadata.directive_version_id),
                "type": "version",
                "directive_id": metadata.directive_id,
                **metadata.model_dump(mode="json"),
                "manifest_blob_name": manifest.manifest_blob_name,
                "summary_blob_name": manifest.summary_blob_name,
                "publication_state": "published",
                "run_id": run_id,
                "published_at": datetime.now(UTC).isoformat(),
            }
        )
        now = datetime.now(UTC).isoformat()
        for relation in relations:
            await self._container.upsert_item(
                {
                    "id": (
                        f"relation:{relation.relation_id}:"
                        f"{metadata.source_hash}:"
                        f"{metadata.processing_hash}"
                    ),
                    "type": "relation",
                    "directive_id": metadata.directive_id,
                    **relation.model_dump(mode="json"),
                    "source_hash": metadata.source_hash,
                    "processing_hash": metadata.processing_hash,
                    "publication_state": "published",
                    "run_id": run_id,
                    "published_at": now,
                }
            )

    async def activate_current(
        self, metadata: DirectiveMetadata, run_id: str
    ) -> bool:
        if not metadata.is_current:
            return False
        version = await self.get_version(
            metadata.directive_id, metadata.directive_version_id
        )
        if not version or version.get("publication_state") != "published":
            raise RuntimeError(
                "Cannot activate an unpublished directive version: "
                f"{metadata.directive_version_id}"
            )
        existing = await self.get_current(metadata.directive_id)
        if (
            existing
            and existing.get("directive_version_id")
            == metadata.directive_version_id
            and existing.get("source_hash") == metadata.source_hash
            and existing.get("processing_hash") == metadata.processing_hash
        ):
            return False
        await self._container.upsert_item(
            {
                "id": "current",
                "type": "current",
                "directive_id": metadata.directive_id,
                "directive_version_id": metadata.directive_version_id,
                "version_label": metadata.version_label,
                "source_hash": metadata.source_hash,
                "processing_hash": metadata.processing_hash,
                "manifest_blob_name": version["manifest_blob_name"],
                "summary_blob_name": version["summary_blob_name"],
                "effective_from": metadata.effective_from.isoformat(),
                "run_id": run_id,
                "activated_at": datetime.now(UTC).isoformat(),
            }
        )
        return True

    async def validate_published(
        self,
        metadata: DirectiveMetadata,
        manifest: DirectiveManifest,
    ) -> None:
        version = await self.get_version(
            metadata.directive_id, metadata.directive_version_id
        )
        if not version or version.get("publication_state") != "published":
            raise RuntimeError(
                f"Catalog version is not published: "
                f"{metadata.directive_version_id}"
            )
        stored_manifest = await self._container.read_item(
            item=(
                f"manifest:{metadata.directive_version_id}:"
                f"{metadata.source_hash}"
            ),
            partition_key=metadata.directive_id,
        )
        if (
            stored_manifest.get("source_hash") != metadata.source_hash
            or stored_manifest.get("manifest", {}).get("manifest_blob_name")
            != manifest.manifest_blob_name
        ):
            raise RuntimeError(
                f"Catalog manifest mismatch: {metadata.directive_version_id}"
            )

    async def list_published_directive_ids(self) -> set[str]:
        values: set[str] = set()
        query = (
            "SELECT DISTINCT VALUE c.directive_id FROM c WHERE "
            "c.type = 'version' AND c.publication_state = 'published'"
        )
        async for value in self._container.query_items(query=query):
            if isinstance(value, str) and value.isdigit():
                values.add(value)
        return values

    async def list_published_manifests(self) -> list[DirectiveManifest]:
        manifests: list[DirectiveManifest] = []
        query = (
            "SELECT c.directive_id, c.directive_version_id, c.source_hash "
            "FROM c WHERE c.type = 'version' "
            "AND c.publication_state = 'published'"
        )
        async for version in self._container.query_items(query=query):
            directive_id = version.get("directive_id")
            version_id = version.get("directive_version_id")
            source_hash = version.get("source_hash")
            if not all(
                isinstance(value, str)
                for value in (directive_id, version_id, source_hash)
            ):
                raise RuntimeError(
                    "Published catalog version has invalid manifest keys"
                )
            item = await self._container.read_item(
                item=f"manifest:{version_id}:{source_hash}",
                partition_key=directive_id,
            )
            manifests.append(
                DirectiveManifest.model_validate(item.get("manifest"))
            )
        return manifests

    async def list_published_version_labels(self) -> set[tuple[str, str]]:
        values: set[tuple[str, str]] = set()
        query = (
            "SELECT c.directive_id, c.version_label FROM c WHERE "
            "c.type = 'version' AND c.publication_state = 'published'"
        )
        async for value in self._container.query_items(query=query):
            directive_id = value.get("directive_id")
            version_label = value.get("version_label")
            if isinstance(directive_id, str) and isinstance(
                version_label, str
            ):
                values.add((directive_id, version_label))
        return values

    async def list_current_pointers(
        self,
    ) -> dict[str, tuple[str, str, str]]:
        values: dict[str, tuple[str, str, str]] = {}
        query = (
            "SELECT c.directive_id, c.directive_version_id, c.source_hash, "
            "c.processing_hash "
            "FROM c WHERE c.type = 'current'"
        )
        async for value in self._container.query_items(query=query):
            directive_id = value.get("directive_id")
            version_id = value.get("directive_version_id")
            source_hash = value.get("source_hash")
            processing_hash = value.get("processing_hash")
            if all(
                isinstance(item, str)
                for item in (
                    directive_id,
                    version_id,
                    source_hash,
                    processing_hash,
                )
            ):
                values[directive_id] = (
                    version_id,
                    source_hash,
                    processing_hash,
                )
        return values

    async def list_published_relations(
        self,
    ) -> list[tuple[DirectiveRelation, str, str]]:
        values: list[tuple[DirectiveRelation, str, str]] = []
        query = (
            "SELECT c.relation_id, c.source_directive_id, "
            "c.source_version_id, c.target_directive_id, "
            "c.target_version_label, c.relation_type, c.status, "
            "c.evidence, c.source_hash, c.processing_hash FROM c WHERE "
            "c.type = 'relation' "
            "AND c.publication_state = 'published' "
            "AND c.status = 'accepted'"
        )
        async for value in self._container.query_items(query=query):
            source_hash = value.pop("source_hash", None)
            processing_hash = value.pop("processing_hash", None)
            if not isinstance(source_hash, str) or not isinstance(
                processing_hash, str
            ):
                continue
            values.append(
                (
                    DirectiveRelation.model_validate(value),
                    source_hash,
                    processing_hash,
                )
            )
        return values

    async def record_run(
        self,
        run_id: str,
        *,
        status: str,
        source_count: int,
        changed_count: int,
        skipped_count: int,
        chunk_count: int,
        mandate_snapshot_id: str | None,
        error: str | None = None,
    ) -> None:
        await self._container.upsert_item(
            {
                "id": f"run:{run_id}",
                "type": "ingestion_run",
                "directive_id": "_runs",
                "run_id": run_id,
                "status": status,
                "source_count": source_count,
                "changed_count": changed_count,
                "skipped_count": skipped_count,
                "chunk_count": chunk_count,
                "mandate_snapshot_id": mandate_snapshot_id,
                "error": error,
                "recorded_at": datetime.now(UTC).isoformat(),
            }
        )
