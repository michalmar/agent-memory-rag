"""Read-only directive catalog access in the dedicated Cosmos database."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from azure.cosmos import exceptions
from directive_contracts import (
    DirectiveManifest,
    DirectiveMetadata,
    DirectiveRelation,
    DirectiveSummary,
)

from .config import get_settings
from .cosmos_container import CosmosContainerLifecycle
from .directive_errors import DirectiveDataUnavailable

logger = logging.getLogger("directive_catalog")


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
        except exceptions.CosmosHttpResponseError as exc:
            raise DirectiveDataUnavailable(
                "Directive catalog health check failed"
            ) from exc

    async def get_version_record(
        self,
        directive_id: str,
        directive_version_id: str,
    ) -> dict[str, Any] | None:
        container = self._require_initialized_container(
            "Directive catalog is unavailable"
        )
        try:
            item = await container.read_item(
                item=f"version:{directive_version_id}",
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
        return item

    async def get_current_record(
        self,
        directive_id: str,
    ) -> dict[str, Any] | None:
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
            or not isinstance(version_id, str)
        ):
            return None
        return await self.get_version_record(directive_id, version_id)

    async def resolve_version(
        self,
        directive_id: str,
        *,
        directive_version_id: str | None = None,
        version_label: str | None = None,
        as_of: date | None = None,
    ) -> dict[str, Any] | None:
        if directive_version_id:
            return await self.get_version_record(
                directive_id,
                directive_version_id,
            )
        if version_label:
            versions = await self._list_versions(directive_id)
            matches = [
                item
                for item in versions
                if item.get("version_label") == version_label
            ]
            return matches[0] if len(matches) == 1 else None
        if as_of:
            candidates: list[tuple[date, dict[str, Any]]] = []
            for item in await self._list_versions(directive_id):
                effective_from = _catalog_date(item.get("effective_from"))
                effective_to = _catalog_date(item.get("effective_to"))
                if (
                    effective_from is not None
                    and effective_from <= as_of
                    and (effective_to is None or effective_to >= as_of)
                ):
                    candidates.append((effective_from, item))
            if not candidates:
                return None
            candidates.sort(key=lambda pair: pair[0], reverse=True)
            return candidates[0][1]
        return await self.get_current_record(directive_id)

    async def get_manifest(
        self,
        directive_id: str,
        directive_version_id: str,
    ) -> DirectiveManifest | None:
        version = await self.get_version_record(
            directive_id,
            directive_version_id,
        )
        if version is None:
            return None
        source_hash = version.get("source_hash")
        if not isinstance(source_hash, str):
            return None
        container = self._require_initialized_container(
            "Directive catalog is unavailable"
        )
        try:
            item = await container.read_item(
                item=f"manifest:{directive_version_id}:{source_hash}",
                partition_key=directive_id,
            )
            return DirectiveManifest.model_validate(item.get("manifest"))
        except exceptions.CosmosResourceNotFoundError:
            return None
        except exceptions.CosmosHttpResponseError as exc:
            raise DirectiveDataUnavailable(
                "Directive manifest lookup failed"
            ) from exc
        except ValueError as exc:
            raise DirectiveDataUnavailable(
                "Directive manifest is invalid"
            ) from exc

    async def get_summary(
        self,
        directive_id: str,
        directive_version_id: str,
    ) -> DirectiveSummary | None:
        version = await self.get_version_record(
            directive_id,
            directive_version_id,
        )
        if version is None:
            return None
        source_hash = version.get("source_hash")
        if not isinstance(source_hash, str):
            return None
        container = self._require_initialized_container(
            "Directive catalog is unavailable"
        )
        try:
            item = await container.read_item(
                item=f"summary:{directive_version_id}:{source_hash}",
                partition_key=directive_id,
            )
            return DirectiveSummary.model_validate(item.get("summary"))
        except exceptions.CosmosResourceNotFoundError:
            return None
        except exceptions.CosmosHttpResponseError as exc:
            raise DirectiveDataUnavailable(
                "Directive summary lookup failed"
            ) from exc
        except ValueError as exc:
            raise DirectiveDataUnavailable(
                "Directive summary is invalid"
            ) from exc

    async def get_relations(
        self,
        directive_id: str,
        directive_version_id: str,
        relation_types: set[str] | None = None,
    ) -> tuple[DirectiveRelation, ...]:
        version = await self.get_version_record(
            directive_id,
            directive_version_id,
        )
        if version is None:
            return ()
        source_hash = version.get("source_hash")
        processing_hash = version.get("processing_hash")
        if not isinstance(source_hash, str) or not isinstance(
            processing_hash,
            str,
        ):
            return ()
        container = self._require_initialized_container(
            "Directive catalog is unavailable"
        )
        query = (
            "SELECT c.relation_id, c.source_directive_id, "
            "c.source_version_id, c.target_directive_id, "
            "c.target_version_label, c.relation_type, c.status, c.evidence "
            "FROM c WHERE c.type = 'relation' "
            "AND c.publication_state = 'published' "
            "AND c.status = 'accepted' "
            "AND c.source_version_id = @version "
            "AND c.source_hash = @source "
            "AND c.processing_hash = @processing"
        )
        parameters = [
            {"name": "@version", "value": directive_version_id},
            {"name": "@source", "value": source_hash},
            {"name": "@processing", "value": processing_hash},
        ]
        values: list[DirectiveRelation] = []
        try:
            async for item in container.query_items(
                query=query,
                parameters=parameters,
                partition_key=directive_id,
            ):
                relation = DirectiveRelation.model_validate(item)
                if (
                    not relation_types
                    or relation.relation_type in relation_types
                ):
                    values.append(relation)
        except exceptions.CosmosHttpResponseError as exc:
            raise DirectiveDataUnavailable(
                "Directive relation lookup failed"
            ) from exc
        except ValueError as exc:
            raise DirectiveDataUnavailable(
                "Directive relation record is invalid"
            ) from exc
        values.sort(
            key=lambda item: (
                item.relation_type,
                item.target_directive_id,
                item.relation_id,
            )
        )
        return tuple(values)

    async def _list_versions(
        self,
        directive_id: str,
    ) -> list[dict[str, Any]]:
        container = self._require_initialized_container(
            "Directive catalog is unavailable"
        )
        query = (
            "SELECT * FROM c WHERE c.type = 'version' "
            "AND c.publication_state = 'published'"
        )
        values: list[dict[str, Any]] = []
        try:
            async for item in container.query_items(
                query=query,
                partition_key=directive_id,
            ):
                values.append(item)
        except exceptions.CosmosHttpResponseError as exc:
            raise DirectiveDataUnavailable(
                "Directive version listing failed"
            ) from exc
        return values

    @staticmethod
    def public_version(item: dict[str, Any]) -> dict[str, Any]:
        fields = {
            name: item[name]
            for name in DirectiveMetadata.model_fields
            if name in item
        }
        try:
            return DirectiveMetadata.model_validate(fields).model_dump(
                mode="json"
            )
        except ValueError as exc:
            raise DirectiveDataUnavailable(
                "Directive version metadata is invalid"
            ) from exc


def _catalog_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
