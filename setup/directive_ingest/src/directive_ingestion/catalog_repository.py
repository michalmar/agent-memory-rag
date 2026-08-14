"""Directive catalog publication in the dedicated Cosmos database."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from azure.core import MatchConditions
from azure.cosmos import exceptions
from azure.cosmos.aio import CosmosClient
from directive_contracts import (
    PUBLISHED_BUNDLE_MAX_BYTES,
    DirectiveManifest,
    DirectiveMetadata,
    DirectiveRelation,
    PublishedDirectiveVersion,
    ReviewFinding,
    canonical_json_hash,
    published_directive_version_item_id,
    serialized_json_size,
)

def version_item_id(directive_id: str, version_label: str) -> str:
    return published_directive_version_item_id(directive_id, version_label)


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
                item=version_item_id(directive_id, directive_version_id.rsplit(":v", 1)[1]),
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

    async def get_published_version(
        self, directive_id: str, directive_version_id: str
    ) -> PublishedDirectiveVersion | None:
        item = await self.get_version(directive_id, directive_version_id)
        if item is None:
            return None
        return _validate_published_bundle(item)

    async def stage_version(
        self,
        bundle: PublishedDirectiveVersion,
        relations: tuple[DirectiveRelation, ...],
        findings: tuple[ReviewFinding, ...],
    ) -> None:
        now = datetime.now(UTC).isoformat()
        await self._container.upsert_item(
            {
                "id": (
                    f"staging:{bundle.id.removeprefix('version:')}:"
                    f"{bundle.artifact_generation_id}"
                ),
                "type": "staging",
                "directive_id": bundle.directive_id,
                "directive_version_id": bundle.directive_version_id,
                "source_hash": bundle.source_hash,
                "processing_hash": bundle.processing_hash,
                "artifact_generation_id": bundle.artifact_generation_id,
                "bundle_hash": canonical_json_hash(bundle),
                "publication_state": "staged",
                "run_id": bundle.run_id,
                "updated_at": now,
            }
        )
        await self._container.upsert_item(
            {
                "id": (
                    f"review:{bundle.id.removeprefix('version:')}:"
                    f"{bundle.artifact_generation_id}"
                ),
                "type": "review",
                "directive_id": bundle.directive_id,
                "directive_version_id": bundle.directive_version_id,
                "source_hash": bundle.source_hash,
                "processing_hash": bundle.processing_hash,
                "artifact_generation_id": bundle.artifact_generation_id,
                "findings": [
                    finding.model_dump(mode="json") for finding in findings
                ],
                "needs_review": any(
                    finding.severity in {"warning", "error"}
                    for finding in findings
                ),
                "run_id": bundle.run_id,
                "updated_at": now,
            }
        )
        for relation in relations:
            await self._container.upsert_item(
                {
                    "id": (
                        f"relation:{relation.relation_id}:"
                        f"{bundle.source_hash}:"
                        f"{bundle.processing_hash}"
                    ),
                    "type": "relation",
                    "directive_id": bundle.directive_id,
                    **relation.model_dump(mode="json"),
                    "source_hash": bundle.source_hash,
                    "processing_hash": bundle.processing_hash,
                    "artifact_generation_id": bundle.artifact_generation_id,
                    "publication_state": "staged",
                    "run_id": bundle.run_id,
                    "updated_at": now,
                }
            )

    async def publish_version(
        self,
        bundle: PublishedDirectiveVersion,
        relations: tuple[DirectiveRelation, ...],
    ) -> None:
        if serialized_json_size(bundle) > PUBLISHED_BUNDLE_MAX_BYTES:
            raise RuntimeError(
                "Published directive bundle exceeds "
                f"{PUBLISHED_BUNDLE_MAX_BYTES} bytes: "
                f"{bundle.directive_version_id}"
            )
        now = datetime.now(UTC).isoformat()
        for relation in relations:
            await self._container.upsert_item(
                {
                    "id": (
                        f"relation:{relation.relation_id}:"
                        f"{bundle.source_hash}:"
                        f"{bundle.processing_hash}"
                    ),
                    "type": "relation",
                    "directive_id": bundle.directive_id,
                    **relation.model_dump(mode="json"),
                    "source_hash": bundle.source_hash,
                    "processing_hash": bundle.processing_hash,
                    "artifact_generation_id": bundle.artifact_generation_id,
                    "publication_state": "published",
                    "run_id": bundle.run_id,
                    "published_at": now,
                }
            )
        await self._replace_published_bundle(bundle)

    async def activate_current(
        self, metadata: DirectiveMetadata, run_id: str
    ) -> bool:
        if not metadata.is_current:
            return False
        version = await self.get_published_version(
            metadata.directive_id, metadata.directive_version_id
        )
        if version is None:
            raise RuntimeError(
                "Cannot activate an unpublished directive version: "
                f"{metadata.directive_version_id}"
            )
        existing = await self.get_current(metadata.directive_id)
        if (
            existing
            and existing.get("directive_version_id")
            == version.directive_version_id
            and existing.get("source_hash") == version.source_hash
            and existing.get("processing_hash") == version.processing_hash
            and existing.get("artifact_generation_id")
            == version.artifact_generation_id
        ):
            return False
        await self._container.upsert_item(
            {
                "id": "current",
                "type": "current",
                "directive_id": version.directive_id,
                "directive_version_id": version.directive_version_id,
                "version_label": version.version_label,
                "source_hash": version.source_hash,
                "processing_hash": version.processing_hash,
                "artifact_generation_id": version.artifact_generation_id,
                "effective_from": version.effective_from.isoformat(),
                "run_id": run_id,
                "activated_at": datetime.now(UTC).isoformat(),
            }
        )
        return True

    async def validate_published(
        self,
        expected: PublishedDirectiveVersion,
    ) -> None:
        stored = await self.get_published_version(
            expected.directive_id, expected.directive_version_id
        )
        if stored is None:
            raise RuntimeError(
                f"Catalog version is not published: "
                f"{expected.directive_version_id}"
            )
        if canonical_json_hash(stored) != canonical_json_hash(expected):
            raise RuntimeError(
                f"Catalog bundle mismatch: {expected.directive_version_id}"
            )

    async def _replace_published_bundle(
        self, bundle: PublishedDirectiveVersion
    ) -> None:
        payload = bundle.model_dump(mode="json")
        existing = await self.get_version(
            bundle.directive_id, bundle.directive_version_id
        )
        try:
            if existing is None:
                await self._container.create_item(body=payload)
                return
            etag = existing.get("_etag")
            if not isinstance(etag, str) or not etag:
                raise RuntimeError(
                    "Existing catalog version is missing an ETag: "
                    f"{bundle.directive_version_id}"
                )
            await self._container.replace_item(
                item=bundle.id,
                body=payload,
                etag=etag,
                match_condition=MatchConditions.IfNotModified,
            )
        except (
            exceptions.CosmosResourceExistsError,
            exceptions.CosmosAccessConditionFailedError,
        ) as exc:
            raise RuntimeError(
                "Concurrent catalog publication prevented replacing "
                f"{bundle.directive_version_id}"
            ) from exc

    async def list_published_directive_ids(self) -> set[str]:
        values: set[str] = set()
        query = (
            "SELECT DISTINCT VALUE c.directive_id FROM c WHERE "
            "c.type = 'version' AND c.publication_state = 'published'"
        )
        async for value in self._container.query_items(query=query):
            if isinstance(value, str):
                values.add(value)
        return values

    async def list_published_versions(
        self,
    ) -> list[PublishedDirectiveVersion]:
        versions: list[PublishedDirectiveVersion] = []
        query = (
            "SELECT * "
            "FROM c WHERE c.type = 'version' "
            "AND c.publication_state = 'published'"
        )
        async for version in self._container.query_items(query=query):
            versions.append(_validate_published_bundle(version))
        return versions

    async def list_published_manifests(self) -> list[DirectiveManifest]:
        return [
            bundle.manifest
            for bundle in await self.list_published_versions()
        ]

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
    ) -> dict[str, tuple[str, str, str, str]]:
        values: dict[str, tuple[str, str, str, str]] = {}
        query = (
            "SELECT c.directive_id, c.directive_version_id, c.source_hash, "
            "c.processing_hash, c.artifact_generation_id "
            "FROM c WHERE c.type = 'current'"
        )
        async for value in self._container.query_items(query=query):
            directive_id = value.get("directive_id")
            version_id = value.get("directive_version_id")
            source_hash = value.get("source_hash")
            processing_hash = value.get("processing_hash")
            artifact_generation_id = value.get("artifact_generation_id")
            if all(
                isinstance(item, str)
                for item in (
                    directive_id,
                    version_id,
                    source_hash,
                    processing_hash,
                    artifact_generation_id,
                )
            ):
                values[directive_id] = (
                    version_id,
                    source_hash,
                    processing_hash,
                    artifact_generation_id,
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


def _validate_published_bundle(
    value: dict[str, Any],
) -> PublishedDirectiveVersion:
    application_fields = {
        key: item for key, item in value.items() if not key.startswith("_")
    }
    try:
        bundle = PublishedDirectiveVersion.model_validate(application_fields)
    except ValueError as exc:
        raise RuntimeError(
            "Published directive version has an invalid artifact schema"
        ) from exc
    size = serialized_json_size(bundle)
    if size > PUBLISHED_BUNDLE_MAX_BYTES:
        raise RuntimeError(
            f"Published directive bundle exceeds "
            f"{PUBLISHED_BUNDLE_MAX_BYTES} bytes: "
            f"{bundle.directive_version_id} ({size} bytes)"
        )
    return bundle
