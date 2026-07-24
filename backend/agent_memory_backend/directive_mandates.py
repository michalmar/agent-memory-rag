"""Sparse, point-read-only mandate projection for authenticated users."""

from __future__ import annotations

import logging
from typing import Any

from azure.cosmos import exceptions

from .config import get_settings
from .cosmos_container import CosmosContainerLifecycle
from .directive_errors import DirectiveDataUnavailable

logger = logging.getLogger("directive_mandates")
_CONTROL_PARTITION = "_control"
_ACTIVE_ID = "active-snapshot"


class DirectiveMandateRepository(CosmosContainerLifecycle):
    _unavailable_error_type = DirectiveDataUnavailable

    async def initialize(self) -> None:
        settings = get_settings()
        if not (
            settings.cosmos_configured
            and settings.directive_cosmos_database
            and settings.directive_mandates_container
        ):
            logger.warning("Directive mandates are not configured")
            return
        await self._initialize_container(
            settings,
            settings.directive_mandates_container,
            database_name=settings.directive_cosmos_database,
        )

    async def health_check(self) -> None:
        container = self._require_initialized_container(
            "Directive mandates are unavailable"
        )
        await container.read()

    async def lookup(
        self,
        user_id: str,
        directive_ids: list[str],
    ) -> dict[str, Any]:
        ordered_ids = list(dict.fromkeys(directive_ids))
        container = self._require_initialized_container(
            "Directive mandates are unavailable"
        )
        try:
            active = await container.read_item(
                item=_ACTIVE_ID,
                partition_key=_CONTROL_PARTITION,
            )
        except exceptions.CosmosResourceNotFoundError:
            return _unknown_result(
                ordered_ids,
                "active_snapshot_missing",
            )
        except exceptions.CosmosHttpResponseError:
            logger.exception("Active mandate snapshot lookup failed")
            return _unknown_result(
                ordered_ids,
                "active_snapshot_unavailable",
            )

        snapshot_id = active.get("snapshot_id")
        if (
            active.get("type") != "active_snapshot"
            or active.get("complete") is not True
            or not isinstance(snapshot_id, str)
            or not snapshot_id
        ):
            return _unknown_result(
                ordered_ids,
                "active_snapshot_inconsistent",
            )

        statuses: dict[str, str] = {}
        degraded = False
        for directive_id in ordered_ids:
            try:
                assignment = await container.read_item(
                    item=f"assignment:{snapshot_id}:{directive_id}",
                    partition_key=user_id,
                )
            except exceptions.CosmosResourceNotFoundError:
                statuses[directive_id] = "non_mandatory"
                continue
            except exceptions.CosmosHttpResponseError:
                logger.exception(
                    "Mandate assignment lookup failed for directive %s",
                    directive_id,
                )
                statuses[directive_id] = "unknown"
                degraded = True
                continue
            if (
                assignment.get("type") == "assignment"
                and assignment.get("snapshot_id") == snapshot_id
                and assignment.get("directive_id") == directive_id
                and assignment.get("user_id") == user_id
                and assignment.get("flag") == "M"
            ):
                statuses[directive_id] = "mandatory"
            else:
                statuses[directive_id] = "unknown"
                degraded = True

        return {
            "snapshot_id": snapshot_id,
            "snapshot_complete": True,
            "degraded": degraded,
            "statuses": statuses,
        }


def _unknown_result(
    directive_ids: list[str],
    reason: str,
) -> dict[str, Any]:
    return {
        "snapshot_id": None,
        "snapshot_complete": False,
        "degraded": True,
        "reason": reason,
        "statuses": {
            directive_id: "unknown" for directive_id in directive_ids
        },
    }
