"""Directive catalog publication in the dedicated Cosmos database."""

from __future__ import annotations

from dataclasses import dataclass
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

from .integrity import IntegrityValidationError

_SNAPSHOT_UNSET = object()


@dataclass(frozen=True)
class CatalogSlotSnapshot:
    """Raw stable-slot state retained for conditional restoration."""

    directive_id: str
    directive_version_id: str
    payload: dict[str, Any]
    etag: str


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

    async def snapshot_version(
        self, directive_id: str, directive_version_id: str
    ) -> CatalogSlotSnapshot | None:
        """Read a stable slot without interpreting a possibly corrupt payload."""
        payload = await self.get_version(directive_id, directive_version_id)
        if payload is None:
            return None
        etag = payload.get("_etag")
        if not isinstance(etag, str) or not etag:
            raise IntegrityValidationError(
                "Catalog version is missing an ETag: "
                f"{directive_version_id}"
            )
        return CatalogSlotSnapshot(
            directive_id=directive_id,
            directive_version_id=directive_version_id,
            payload=dict(payload),
            etag=etag,
        )

    async def stage_version(
        self,
        bundle: PublishedDirectiveVersion,
        relations: tuple[DirectiveRelation, ...],
        findings: tuple[ReviewFinding, ...],
    ) -> None:
        _validate_empty_relations(relations)
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
    async def publish_version(
        self,
        bundle: PublishedDirectiveVersion,
        relations: tuple[DirectiveRelation, ...],
        *,
        expected_snapshot: CatalogSlotSnapshot | None | object = _SNAPSHOT_UNSET,
    ) -> str:
        _validate_empty_relations(relations)
        if serialized_json_size(bundle) > PUBLISHED_BUNDLE_MAX_BYTES:
            raise RuntimeError(
                "Published directive bundle exceeds "
                f"{PUBLISHED_BUNDLE_MAX_BYTES} bytes: "
                f"{bundle.directive_version_id}"
            )
        return await self._replace_published_bundle(bundle, expected_snapshot)

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
            raise IntegrityValidationError(
                f"Catalog version is not published: "
                f"{expected.directive_version_id}"
            )
        if canonical_json_hash(stored) != canonical_json_hash(expected):
            raise IntegrityValidationError(
                f"Catalog bundle mismatch: {expected.directive_version_id}"
            )

    async def _replace_published_bundle(
        self,
        bundle: PublishedDirectiveVersion,
        expected_snapshot: CatalogSlotSnapshot | None | object = _SNAPSHOT_UNSET,
    ) -> str:
        payload = bundle.model_dump(mode="json")
        if expected_snapshot is _SNAPSHOT_UNSET:
            expected_snapshot = await self.snapshot_version(
                bundle.directive_id, bundle.directive_version_id
            )
        try:
            if expected_snapshot is None:
                response = await self._container.create_item(body=payload)
                return _catalog_response_etag(
                    response, bundle.directive_version_id
                )
            response = await self._container.replace_item(
                item=bundle.id,
                body=payload,
                etag=expected_snapshot.etag,
                match_condition=MatchConditions.IfNotModified,
            )
            return _catalog_response_etag(response, bundle.directive_version_id)
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

    async def remove_absent_versions(
        self, expected: set[tuple[str, str]]
    ) -> list[PublishedDirectiveVersion]:
        """Compatibility helper; prefer enumerate then delete for transactions."""
        retired = await self.list_absent_versions(expected)
        await self.delete_versions(retired)
        return retired

    async def list_absent_versions(
        self, expected: set[tuple[str, str]]
    ) -> list[PublishedDirectiveVersion]:
        """Read stale bundles without mutating live catalog records."""
        return [
            bundle
            for bundle in await self.list_published_versions()
            if (bundle.directive_id, bundle.directive_version_id) not in expected
        ]

    async def delete_versions(
        self, retired: list[PublishedDirectiveVersion]
    ) -> None:
        """Delete stale catalog records only after dependent stores are clean."""
        for bundle in retired:
            current = await self.get_current(bundle.directive_id)
            if (
                current
                and current.get("directive_version_id")
                == bundle.directive_version_id
            ):
                await self._container.delete_item(
                    item="current", partition_key=bundle.directive_id
                )
            await self._container.delete_item(
                item=bundle.id, partition_key=bundle.directive_id
            )

    async def restore_version(
        self,
        expected: PublishedDirectiveVersion,
        previous: CatalogSlotSnapshot | None,
        candidate_etag: str,
    ) -> None:
        """Restore the stable version slot after a failed publication."""
        if previous is not None:
            payload = {
                key: value
                for key, value in previous.payload.items()
                if not key.startswith("_")
            }
            try:
                await self._container.replace_item(
                    item=expected.id,
                    body=payload,
                    etag=candidate_etag,
                    match_condition=MatchConditions.IfNotModified,
                )
            except exceptions.CosmosAccessConditionFailedError as exc:
                raise RuntimeError(
                    "Concurrent catalog publication prevented restoring "
                    f"{expected.directive_version_id}"
                ) from exc
            return
        try:
            await self._container.delete_item(
                item=expected.id,
                partition_key=expected.directive_id,
                etag=candidate_etag,
                match_condition=MatchConditions.IfNotModified,
            )
        except exceptions.CosmosResourceNotFoundError:
            pass
        except exceptions.CosmosAccessConditionFailedError as exc:
            raise RuntimeError(
                "Concurrent catalog publication prevented restoring "
                f"{expected.directive_version_id}"
            ) from exc

    async def restore_current(
        self, directive_id: str, previous: dict[str, Any] | None
    ) -> None:
        """Restore a current pointer snapshot after failed activation."""
        if previous is None:
            try:
                await self._container.delete_item(
                    item="current", partition_key=directive_id
                )
            except exceptions.CosmosResourceNotFoundError:
                pass
            return
        payload = {
            key: value for key, value in previous.items() if not key.startswith("_")
        }
        await self._container.upsert_item(payload)

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

    async def list_relation_record_ids(self) -> set[str]:
        """Enumerate every legacy relation record, regardless of its state."""
        values: set[str] = set()
        query = "SELECT VALUE c.id FROM c WHERE c.type = 'relation'"
        async for value in self._container.query_items(query=query):
            if not isinstance(value, str) or not value:
                raise IntegrityValidationError(
                    "Directive relation record has an invalid identity"
                )
            values.add(value)
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

    async def record_run_metrics(self, payload: dict[str, object]) -> None:
        if (
            payload.get("type") != "ingestion_run"
            or payload.get("status") not in {"succeeded", "failed", "skipped"}
        ):
            raise ValueError("Ingestion metrics payload is incomplete")
        run_id = payload.get("run_id")
        operation = payload.get("operation")
        if (
            not isinstance(run_id, str)
            or not run_id
            or not isinstance(operation, str)
            or not operation
        ):
            raise ValueError("Ingestion metrics identity is invalid")
        item = {
            "id": f"metrics:{run_id}:{operation}",
            "directive_id": "_runs",
            **payload,
        }
        if serialized_json_size(item) > 65_536:
            raise ValueError("Ingestion metrics payload exceeds 64 KiB")
        await self._container.upsert_item(item)


def _validate_empty_relations(
    relations: tuple[DirectiveRelation, ...],
) -> None:
    if relations:
        raise ValueError(
            "Directive relations are not supported by current-only publication"
        )


def _validate_published_bundle(
    value: dict[str, Any],
) -> PublishedDirectiveVersion:
    if not isinstance(value, dict):
        raise IntegrityValidationError(
            "Published directive version has an invalid artifact schema"
        )
    application_fields = {
        key: item for key, item in value.items() if not key.startswith("_")
    }
    try:
        bundle = PublishedDirectiveVersion.model_validate(application_fields)
    except ValueError as exc:
        raise IntegrityValidationError(
            "Published directive version has an invalid artifact schema"
        ) from exc
    size = serialized_json_size(bundle)
    if size > PUBLISHED_BUNDLE_MAX_BYTES:
        raise IntegrityValidationError(
            f"Published directive bundle exceeds "
            f"{PUBLISHED_BUNDLE_MAX_BYTES} bytes: "
            f"{bundle.directive_version_id} ({size} bytes)"
        )
    return bundle


def _catalog_response_etag(response: object, directive_version_id: str) -> str:
    etag = (
        response.get("_etag") or response.get("etag")
        if isinstance(response, dict)
        else getattr(response, "etag", None)
        or getattr(response, "_etag", None)
    )
    if not isinstance(etag, str) or not etag:
        raise RuntimeError(
            "Catalog version replacement is missing an ETag: "
            f"{directive_version_id}"
        )
    return etag
