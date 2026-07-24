"""Complete sparse mandate-snapshot validation and publication."""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from azure.cosmos import exceptions
from azure.cosmos.aio import CosmosClient
from directive_contracts import MandateAssignment, MandateSnapshot

_CONTROL_PARTITION = "_control"
_ACTIVE_ID = "active-snapshot"


@dataclass(frozen=True)
class ParsedMandates:
    assignments: tuple[MandateAssignment, ...]
    checksum: str
    user_count: int


def parse_mandates(
    path: Path,
    tenant_id: str,
    known_directive_ids: set[str],
) -> ParsedMandates:
    if not path.is_file():
        raise ValueError(f"Mandate CSV does not exist: {path}")
    tenant = str(UUID(tenant_id)).lower()
    upn_to_oid: dict[str, str] = {}
    oid_to_upn: dict[str, str] = {}
    unique: set[tuple[str, str]] = set()
    assignments: list[MandateAssignment] = []
    with path.open(newline="", encoding="utf-8-sig") as source:
        for row_number, row in enumerate(csv.reader(source), 1):
            if len(row) != 4:
                raise ValueError(
                    f"Mandate CSV row {row_number} must have four columns"
                )
            raw_upn, raw_oid, directive_id, flag = (
                value.strip() for value in row
            )
            upn = raw_upn.casefold()
            if not upn or "@" not in upn:
                raise ValueError(
                    f"Mandate CSV row {row_number} has an invalid UPN"
                )
            try:
                oid = str(UUID(raw_oid)).lower()
            except ValueError as exc:
                raise ValueError(
                    f"Mandate CSV row {row_number} has an invalid Entra "
                    "object ID"
                ) from exc
            if flag.upper() != "M":
                raise ValueError(
                    f"Mandate CSV row {row_number} has unsupported flag "
                    f"{flag!r}"
                )
            if directive_id not in known_directive_ids:
                raise ValueError(
                    f"Mandate CSV row {row_number} references unknown "
                    f"directive {directive_id}"
                )
            if upn in upn_to_oid and upn_to_oid[upn] != oid:
                raise ValueError(
                    f"UPN {upn} maps to more than one Entra object ID"
                )
            if oid in oid_to_upn and oid_to_upn[oid] != upn:
                raise ValueError(
                    f"Entra object ID {oid} maps to more than one UPN"
                )
            upn_to_oid[upn] = oid
            oid_to_upn[oid] = upn
            user_id = f"{tenant}:{oid}"
            key = (user_id, directive_id)
            if key in unique:
                continue
            unique.add(key)
            assignments.append(
                MandateAssignment(
                    user_id=user_id,
                    directive_id=directive_id,
                )
            )
    assignments.sort(key=lambda item: (item.user_id, item.directive_id))
    canonical = "\n".join(
        f"{item.user_id},{item.directive_id},M" for item in assignments
    ).encode()
    return ParsedMandates(
        assignments=tuple(assignments),
        checksum=hashlib.sha256(canonical).hexdigest(),
        user_count=len({item.user_id for item in assignments}),
    )


class MandateRepository:
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

    async def publish(
        self, parsed: ParsedMandates, run_id: str
    ) -> tuple[MandateSnapshot, bool]:
        active = await self._read_active()
        snapshot_id = f"mandates-{parsed.checksum}"
        snapshot = MandateSnapshot(
            snapshot_id=snapshot_id,
            checksum=parsed.checksum,
            assignment_count=len(parsed.assignments),
            user_count=parsed.user_count,
            complete=True,
            previous_snapshot_id=(
                active.get("snapshot_id") if active else None
            ),
        )
        if (
            active
            and active.get("complete") is True
            and active.get("checksum") == parsed.checksum
        ):
            return snapshot, False

        published_at = datetime.now(UTC).isoformat()
        for assignment in parsed.assignments:
            await self._container.upsert_item(
                {
                    "id": (
                        f"assignment:{snapshot_id}:"
                        f"{assignment.directive_id}"
                    ),
                    "type": "assignment",
                    "user_id": assignment.user_id,
                    "directive_id": assignment.directive_id,
                    "snapshot_id": snapshot_id,
                    "flag": "M",
                    "run_id": run_id,
                    "published_at": published_at,
                }
            )

        actual_count = 0
        query = (
            "SELECT VALUE COUNT(1) FROM c WHERE "
            "c.type = 'assignment' AND c.snapshot_id = @snapshot"
        )
        parameters = [{"name": "@snapshot", "value": snapshot_id}]
        async for value in self._container.query_items(
            query=query,
            parameters=parameters,
        ):
            actual_count = int(value)
        if actual_count != len(parsed.assignments):
            raise RuntimeError(
                "Mandate snapshot validation failed: expected "
                f"{len(parsed.assignments)} assignments, found {actual_count}"
            )

        await self._container.upsert_item(
            {
                "id": f"snapshot:{snapshot_id}",
                "type": "snapshot",
                "user_id": _CONTROL_PARTITION,
                **snapshot.model_dump(mode="json"),
                "run_id": run_id,
                "published_at": published_at,
            }
        )
        await self._container.upsert_item(
            {
                "id": _ACTIVE_ID,
                "type": "active_snapshot",
                "user_id": _CONTROL_PARTITION,
                **snapshot.model_dump(mode="json"),
                "run_id": run_id,
                "activated_at": datetime.now(UTC).isoformat(),
            }
        )
        return snapshot, True

    async def verification_summary(self) -> dict[str, object]:
        active = await self._read_active()
        if not active or active.get("complete") is not True:
            raise RuntimeError("No complete active mandate snapshot exists")
        snapshot_id = active.get("snapshot_id")
        if not isinstance(snapshot_id, str):
            raise RuntimeError("Active mandate snapshot has no snapshot ID")
        actual_count = 0
        query = (
            "SELECT VALUE COUNT(1) FROM c WHERE "
            "c.type = 'assignment' AND c.snapshot_id = @snapshot"
        )
        async for value in self._container.query_items(
            query=query,
            parameters=[{"name": "@snapshot", "value": snapshot_id}],
        ):
            actual_count = int(value)
        expected_count = int(active.get("assignment_count", -1))
        if actual_count != expected_count:
            raise RuntimeError(
                "Active mandate snapshot count mismatch: expected "
                f"{expected_count}, found {actual_count}"
            )
        return {
            "snapshot_id": snapshot_id,
            "assignment_count": actual_count,
            "user_count": int(active.get("user_count", -1)),
        }

    async def _read_active(self) -> dict[str, Any] | None:
        try:
            return await self._container.read_item(
                item=_ACTIVE_ID, partition_key=_CONTROL_PARTITION
            )
        except exceptions.CosmosResourceNotFoundError:
            return None
