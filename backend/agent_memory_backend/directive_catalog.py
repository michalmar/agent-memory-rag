"""Read-only directive catalog access in the dedicated Cosmos database."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from azure.cosmos import exceptions
from directive_contracts import (
    PUBLISHED_BUNDLE_MAX_BYTES,
    DirectiveMetadata,
    PublishedDirectiveVersion,
    normalize_directive_id,
    published_directive_version_item_id,
    serialized_json_size,
    validate_directive_version_id,
)

from .config import get_settings
from .cosmos_container import CosmosContainerLifecycle
from .directive_errors import DirectiveDataUnavailable

logger = logging.getLogger("directive_catalog")

PublicationGateState = Literal["committed", "activating", "recovery_required"]
_GATE_ID = "directive-publication-gate"
_GATE_PARTITION = "_control"


@dataclass(frozen=True, slots=True)
class PublicationGateSnapshot:
    state: PublicationGateState
    revision: str
    candidate_revision: str | None
    run_id: str


class DirectiveCatalogRepository(CosmosContainerLifecycle):
    _unavailable_error_type = DirectiveDataUnavailable

    async def initialize(self) -> None:
        settings = get_settings()
        if not (
            settings.cosmos_configured
            and settings.directive_cosmos_database
            and settings.directive_catalog_container
        ):
            logger.warning("Directive catalog is not configured")
            return
        await self._initialize_container(
            settings,
            settings.directive_catalog_container,
            database_name=settings.directive_cosmos_database,
        )

    async def health_check(self) -> None:
        container = self._require_initialized_container(
            "Directive catalog is unavailable"
        )
        try:
            await container.read()
            await self.ensure_publication_readable()
        except exceptions.CosmosHttpResponseError as exc:
            raise DirectiveDataUnavailable(
                "Directive catalog health check failed"
            ) from exc

    async def ensure_publication_readable(self) -> None:
        snapshot = await self._read_publication_gate()
        if snapshot is None:
            if not get_settings().directive_publication_gate_enabled:
                return
            raise DirectiveDataUnavailable(
                "Directive publication gate is unavailable"
            )
        if snapshot.state != "committed":
            raise DirectiveDataUnavailable(
                "Directive publication is unavailable pending recovery"
            )

    async def get_published_version(
        self,
        directive_id: str,
        directive_version_id: str,
    ) -> PublishedDirectiveVersion | None:
        await self.ensure_publication_readable()
        directive_id, directive_version_id = _normalize_version_identity(
            directive_id, directive_version_id
        )
        return await self._read_published_version(directive_id, directive_version_id)

    async def get_current_published_version(
        self,
        directive_id: str,
    ) -> PublishedDirectiveVersion | None:
        await self.ensure_publication_readable()
        directive_id = _normalize_directive_id(directive_id)
        container = self._require_initialized_container(
            "Directive catalog is unavailable"
        )
        try:
            pointer = await container.read_item(
                item="current",
                partition_key=directive_id,
            )
        except exceptions.CosmosResourceNotFoundError:
            return None
        except exceptions.CosmosHttpResponseError as exc:
            raise DirectiveDataUnavailable(
                "Current directive lookup failed"
            ) from exc
        version_id = pointer.get("directive_version_id")
        if (
            pointer.get("type") != "current"
            or pointer.get("directive_id") != directive_id
            or not isinstance(version_id, str)
        ):
            return None
        try:
            version_id = validate_directive_version_id(version_id, directive_id)
        except (TypeError, ValueError):
            return None
        return await self._read_published_version(directive_id, version_id)

    async def _read_published_version(
        self,
        directive_id: str,
        directive_version_id: str,
    ) -> PublishedDirectiveVersion | None:
        container = self._require_initialized_container(
            "Directive catalog is unavailable"
        )
        try:
            item = await container.read_item(
                item=published_directive_version_item_id(
                    directive_id, _version_from_id(directive_version_id)
                ),
                partition_key=directive_id,
            )
        except exceptions.CosmosResourceNotFoundError:
            return None
        except exceptions.CosmosHttpResponseError as exc:
            raise DirectiveDataUnavailable(
                "Directive version lookup failed"
            ) from exc
        if (
            item.get("type") != "version"
            or item.get("publication_state") != "published"
            or item.get("directive_id") != directive_id
            or item.get("directive_version_id") != directive_version_id
        ):
            return None
        bundle = _validate_published_bundle(item)
        if (
            bundle.id
            != published_directive_version_item_id(
                directive_id, _version_from_id(directive_version_id)
            )
            or bundle.directive_id != directive_id
            or bundle.directive_version_id != directive_version_id
        ):
            return None
        return bundle

    async def _read_publication_gate(self) -> PublicationGateSnapshot | None:
        container = self._require_initialized_container(
            "Directive catalog is unavailable"
        )
        try:
            item = await container.read_item(
                item=_GATE_ID,
                partition_key=_GATE_PARTITION,
            )
        except exceptions.CosmosResourceNotFoundError:
            return None
        except exceptions.CosmosHttpResponseError as exc:
            raise DirectiveDataUnavailable(
                "Directive publication gate lookup failed"
            ) from exc
        return _parse_publication_gate(item)

    @staticmethod
    def public_version(item: PublishedDirectiveVersion) -> dict[str, Any]:
        fields = {
            name: getattr(item, name)
            for name in DirectiveMetadata.model_fields
        }
        try:
            metadata = DirectiveMetadata.model_validate(fields).model_dump(
                mode="json"
            )
        except ValueError as exc:
            raise DirectiveDataUnavailable(
                "Directive version metadata is invalid"
            ) from exc
        return {
            name: value
            for name, value in metadata.items()
            if name not in {"source_hash", "processing_hash"}
        }


def _normalize_directive_id(value: str) -> str:
    try:
        return normalize_directive_id(value)
    except (TypeError, ValueError) as exc:
        raise DirectiveDataUnavailable("Directive identity is invalid") from exc


def _normalize_version_identity(
    directive_id: str, directive_version_id: str
) -> tuple[str, str]:
    normalized_id = _normalize_directive_id(directive_id)
    try:
        return (
            normalized_id,
            validate_directive_version_id(
                directive_version_id, normalized_id
            ),
        )
    except (TypeError, ValueError) as exc:
        raise DirectiveDataUnavailable(
            "Directive version identity is invalid"
        ) from exc


def _version_from_id(directive_version_id: str) -> str:
    return directive_version_id.rsplit(":v", 1)[1]


def _validate_published_bundle(
    item: dict[str, Any],
) -> PublishedDirectiveVersion:
    application_fields = {
        key: value for key, value in item.items() if not key.startswith("_")
    }
    try:
        bundle = PublishedDirectiveVersion.model_validate(application_fields)
    except ValueError as exc:
        raise DirectiveDataUnavailable(
            "Published directive bundle is invalid"
        ) from exc
    if serialized_json_size(bundle) > PUBLISHED_BUNDLE_MAX_BYTES:
        raise DirectiveDataUnavailable(
            "Published directive bundle exceeds the supported size"
        )
    return bundle


def _parse_publication_gate(item: Any) -> PublicationGateSnapshot:
    if not isinstance(item, dict):
        raise DirectiveDataUnavailable("Directive publication gate is invalid")
    required = {
        "id",
        "directive_id",
        "type",
        "state",
        "committed_revision",
        "candidate_revision",
        "run_id",
        "updated_at",
        "_etag",
    }
    if not required <= set(item):
        raise DirectiveDataUnavailable("Directive publication gate is invalid")
    state = item.get("state")
    revision = item.get("committed_revision")
    candidate_revision = item.get("candidate_revision")
    run_id = item.get("run_id")
    if (
        item.get("id") != _GATE_ID
        or item.get("directive_id") != _GATE_PARTITION
        or item.get("type") != "publication_gate"
        or state not in {"committed", "activating", "recovery_required"}
        or not isinstance(revision, str)
        or not revision
        or not isinstance(run_id, str)
        or not run_id
        or not isinstance(item.get("_etag"), str)
        or not item["_etag"]
    ):
        raise DirectiveDataUnavailable("Directive publication gate is invalid")
    if candidate_revision is not None and (
        not isinstance(candidate_revision, str) or not candidate_revision
    ):
        raise DirectiveDataUnavailable("Directive publication gate is invalid")
    if state != "committed" and candidate_revision is None:
        raise DirectiveDataUnavailable("Directive publication gate is invalid")
    if state == "committed" and candidate_revision is not None:
        raise DirectiveDataUnavailable("Directive publication gate is invalid")
    try:
        updated_at = datetime.fromisoformat(item["updated_at"])
    except (TypeError, ValueError) as exc:
        raise DirectiveDataUnavailable(
            "Directive publication gate is invalid"
        ) from exc
    if updated_at.tzinfo is None:
        raise DirectiveDataUnavailable("Directive publication gate is invalid")
    return PublicationGateSnapshot(
        state=state,
        revision=revision,
        candidate_revision=candidate_revision,
        run_id=run_id,
    )
