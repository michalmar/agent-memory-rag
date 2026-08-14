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
from directive_contracts import (
    MandateAssignment,
    MandateSnapshot,
    directive_storage_key,
    normalize_directive_id,
)

_CONTROL_PARTITION = "_control"
_ACTIVE_ID = "active-snapshot"
_UNSET = object()


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
            directive_id = normalize_directive_id(directive_id)
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

    async def stage(
        self, parsed: ParsedMandates, run_id: str
    ) -> tuple[MandateSnapshot, dict[str, Any] | None, bool]:
        """Write candidate assignments without switching the active pointer."""
        active = await self._read_active()
        active_snapshot_id = (
            active.get("snapshot_id") if active is not None else None
        )
        if (
            active
            and active.get("complete") is True
            and active.get("checksum") == parsed.checksum
            and isinstance(active_snapshot_id, str)
            and await self._snapshot_assignments_match(
                active_snapshot_id, parsed
            )
        ):
            snapshot = self._snapshot_from_active(active)
            if (
                snapshot is not None
                and await self._snapshot_records_match(snapshot, parsed)
            ):
                return snapshot, active, False

        snapshot_id = f"mandates-{parsed.checksum}"
        repair_snapshot_id = (
            active_snapshot_id
            if (
                active
                and active.get("complete") is True
                and active.get("checksum") == parsed.checksum
                and isinstance(active_snapshot_id, str)
            )
            else snapshot_id
        )
        repair_records = await self._snapshot_records(repair_snapshot_id)
        if repair_records or repair_snapshot_id != snapshot_id:
            snapshot_id += "-" + self._snapshot_records_digest(repair_records)
        candidate_records = await self._snapshot_records(snapshot_id)
        if candidate_records:
            snapshot_id += "-" + self._snapshot_records_digest(candidate_records)

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

        published_at = datetime.now(UTC).isoformat()
        for assignment in parsed.assignments:
            await self._container.upsert_item(
                {
                    "id": self._assignment_item_id(snapshot_id, assignment),
                    "type": "assignment",
                    "user_id": assignment.user_id,
                    "directive_id": assignment.directive_id,
                    "snapshot_id": snapshot_id,
                    "flag": "M",
                    "run_id": run_id,
                    "published_at": published_at,
                }
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
        if not await self._snapshot_records_match(snapshot, parsed):
            raise RuntimeError(
                "Mandate snapshot validation failed: assignment or control "
                "records do not match the candidate"
            )
        return snapshot, active, True

    async def activate(
        self, snapshot: MandateSnapshot, run_id: str
    ) -> None:
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

    async def restore_active(self, previous: dict[str, Any] | None) -> None:
        if previous is None:
            try:
                await self._container.delete_item(
                    item=_ACTIVE_ID, partition_key=_CONTROL_PARTITION
                )
            except exceptions.CosmosResourceNotFoundError:
                pass
            return
        await self._container.upsert_item(
            {key: value for key, value in previous.items() if not key.startswith("_")}
        )

    async def discard_staged(self, snapshot: MandateSnapshot) -> None:
        """Idempotently delete a candidate snapshot that was never committed."""
        query = (
            "SELECT c.id, c.user_id FROM c WHERE "
            "(c.type = 'assignment' OR c.type = 'snapshot') AND "
            "c.snapshot_id = @snapshot"
        )
        stale: list[tuple[str, str]] = []
        async for value in self._container.query_items(
            query=query,
            parameters=[{"name": "@snapshot", "value": snapshot.snapshot_id}],
        ):
            item_id = value.get("id")
            user_id = value.get("user_id")
            if isinstance(item_id, str) and isinstance(user_id, str):
                stale.append((item_id, user_id))
        for item_id, user_id in stale:
            try:
                await self._container.delete_item(item=item_id, partition_key=user_id)
            except exceptions.CosmosResourceNotFoundError:
                continue

    async def cleanup(self, active_snapshot_id: str) -> bool:
        return await self._prune_inactive(active_snapshot_id)

    async def publish(
        self, parsed: ParsedMandates, run_id: str
    ) -> tuple[MandateSnapshot, bool]:
        """Compatibility API performing stage, activation, then cleanup."""
        snapshot, _, changed = await self.stage(parsed, run_id)
        if not changed:
            cleanup_changed = await self.cleanup(snapshot.snapshot_id)
            return snapshot, cleanup_changed
        await self.activate(snapshot, run_id)
        cleanup_changed = await self.cleanup(snapshot.snapshot_id)
        return snapshot, changed or cleanup_changed

    async def is_current(self, parsed: ParsedMandates) -> bool:
        active = await self._read_active()
        if not (
            active
            and active.get("complete") is True
            and active.get("checksum") == parsed.checksum
        ):
            return False
        snapshot_id = active.get("snapshot_id")
        if not (
            isinstance(snapshot_id, str)
            and await self._snapshot_assignments_match(snapshot_id, parsed)
        ):
            return False
        snapshot = self._snapshot_from_active(active)
        return (
            snapshot is not None
            and await self._snapshot_records_match(snapshot, parsed)
            and not await self._has_inactive(snapshot_id)
        )

    async def verification_summary(self) -> dict[str, object]:
        active = await self._read_active()
        if not active or active.get("complete") is not True:
            raise RuntimeError("No complete active mandate snapshot exists")
        snapshot = self._snapshot_from_active(active)
        if snapshot is None:
            raise RuntimeError("Active mandate snapshot has invalid metadata")
        if not await self._snapshot_structure_matches(snapshot):
            raise RuntimeError(
                "Active mandate assignment or control records are invalid"
            )
        if await self._has_inactive(snapshot.snapshot_id):
            raise RuntimeError(
                "Inactive mandate assignments or snapshots are present"
            )
        return {
            "snapshot_id": snapshot.snapshot_id,
            "checksum": snapshot.checksum,
            "assignment_count": snapshot.assignment_count,
            "user_count": snapshot.user_count,
        }

    async def validate_exact(self, parsed: ParsedMandates) -> dict[str, object]:
        """Require active assignment content, checksum, and counts to match."""
        summary = await self.verification_summary()
        if summary["checksum"] != parsed.checksum:
            raise RuntimeError("Active mandate snapshot checksum mismatch")
        snapshot_id = summary["snapshot_id"]
        if not await self._snapshot_assignments_match(snapshot_id, parsed):
            raise RuntimeError("Active mandate assignments do not match CSV")
        return summary

    async def _snapshot_assignments_match(
        self, snapshot_id: str, parsed: ParsedMandates
    ) -> bool:
        records = await self._snapshot_records(snapshot_id)
        expected_assignments = sorted(
            (
                "assignment",
                self._assignment_item_id(snapshot_id, assignment),
                assignment.user_id,
                assignment.directive_id,
                "M",
            )
            for assignment in parsed.assignments
        )
        actual_assignments = sorted(
            (
                record.get("type"),
                record.get("id"),
                record.get("user_id"),
                record.get("directive_id"),
                record.get("flag"),
            )
            for record in records
            if record.get("type") == "assignment"
        )
        controls = [
            record for record in records if record.get("type") == "snapshot"
        ]
        return (
            len(records) == len(expected_assignments) + 1
            and actual_assignments == expected_assignments
            and len(controls) == 1
            and self._control_record_matches(
                controls[0],
                snapshot_id,
                parsed.checksum,
                len(parsed.assignments),
                parsed.user_count,
            )
        )

    async def _snapshot_records_match(
        self, snapshot: MandateSnapshot, parsed: ParsedMandates
    ) -> bool:
        if not await self._snapshot_assignments_match(
            snapshot.snapshot_id, parsed
        ):
            return False
        controls = [
            record
            for record in await self._snapshot_records(snapshot.snapshot_id)
            if record.get("type") == "snapshot"
        ]
        return len(controls) == 1 and self._control_record_matches(
            controls[0],
            snapshot.snapshot_id,
            snapshot.checksum,
            snapshot.assignment_count,
            snapshot.user_count,
            snapshot.previous_snapshot_id,
        )

    async def _snapshot_structure_matches(self, snapshot: MandateSnapshot) -> bool:
        records = await self._snapshot_records(snapshot.snapshot_id)
        assignments = [
            record for record in records if record.get("type") == "assignment"
        ]
        controls = [
            record for record in records if record.get("type") == "snapshot"
        ]
        return (
            len(records) == snapshot.assignment_count + 1
            and len(assignments) == snapshot.assignment_count
            and all(
                self._assignment_record_matches(record, snapshot.snapshot_id)
                for record in assignments
            )
            and len(controls) == 1
            and self._control_record_matches(
                controls[0],
                snapshot.snapshot_id,
                snapshot.checksum,
                snapshot.assignment_count,
                snapshot.user_count,
                snapshot.previous_snapshot_id,
            )
        )

    async def _snapshot_records(self, snapshot_id: str) -> list[dict[str, Any]]:
        query = (
            "SELECT c.id, c.type, c.user_id, c.directive_id, c.flag, "
            "c.snapshot_id, c.schema_version, c.checksum, "
            "c.assignment_count, c.user_count, c.complete, "
            "c.previous_snapshot_id FROM c WHERE "
            "(c.type = 'assignment' OR c.type = 'snapshot') AND "
            "c.snapshot_id = @snapshot"
        )
        records: list[dict[str, Any]] = []
        async for value in self._container.query_items(
            query=query,
            parameters=[{"name": "@snapshot", "value": snapshot_id}],
        ):
            if not isinstance(value, dict):
                raise RuntimeError("Mandate snapshot record is invalid")
            records.append(value)
        return records

    @staticmethod
    def _assignment_item_id(
        snapshot_id: str, assignment: MandateAssignment
    ) -> str:
        return (
            f"assignment:{snapshot_id}:"
            f"{directive_storage_key(assignment.directive_id)}"
        )

    @classmethod
    def _assignment_record_matches(
        cls, record: dict[str, Any], snapshot_id: str
    ) -> bool:
        user_id = record.get("user_id")
        directive_id = record.get("directive_id")
        if not isinstance(user_id, str) or not isinstance(directive_id, str):
            return False
        try:
            expected_id = cls._assignment_item_id(
                snapshot_id,
                MandateAssignment(user_id=user_id, directive_id=directive_id),
            )
        except ValueError:
            return False
        return (
            record.get("type") == "assignment"
            and record.get("snapshot_id") == snapshot_id
            and record.get("flag") == "M"
            and record.get("id") == expected_id
        )

    @staticmethod
    def _control_record_matches(
        record: dict[str, Any],
        snapshot_id: str,
        checksum: str,
        assignment_count: int,
        user_count: int,
        previous_snapshot_id: str | None | object = _UNSET,
    ) -> bool:
        return (
            record.get("id") == f"snapshot:{snapshot_id}"
            and record.get("type") == "snapshot"
            and record.get("user_id") == _CONTROL_PARTITION
            and record.get("snapshot_id") == snapshot_id
            and record.get("schema_version") == "1.0"
            and record.get("checksum") == checksum
            and record.get("assignment_count") == assignment_count
            and record.get("user_count") == user_count
            and record.get("complete") is True
            and (
                previous_snapshot_id is _UNSET
                or record.get("previous_snapshot_id") == previous_snapshot_id
            )
        )

    @staticmethod
    def _snapshot_records_digest(records: list[dict[str, Any]]) -> str:
        canonical = "\n".join(
            repr(
                tuple(
                    record.get(field)
                    for field in (
                        "id",
                        "type",
                        "user_id",
                        "directive_id",
                        "flag",
                        "snapshot_id",
                        "schema_version",
                        "checksum",
                        "assignment_count",
                        "user_count",
                        "complete",
                        "previous_snapshot_id",
                    )
                )
            )
            for record in sorted(
                records,
                key=lambda record: (
                    str(record.get("id")),
                    str(record.get("user_id")),
                ),
            )
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()[:16]

    @staticmethod
    def _snapshot_from_active(
        active: dict[str, Any]
    ) -> MandateSnapshot | None:
        try:
            return MandateSnapshot.model_validate(
                {
                    field: active.get(field)
                    for field in MandateSnapshot.model_fields
                    if field in active
                }
            )
        except ValueError:
            return None

    async def _read_active(self) -> dict[str, Any] | None:
        try:
            return await self._container.read_item(
                item=_ACTIVE_ID, partition_key=_CONTROL_PARTITION
            )
        except exceptions.CosmosResourceNotFoundError:
            return None

    async def _has_inactive(self, active_snapshot_id: str) -> bool:
        query = (
            "SELECT VALUE COUNT(1) FROM c WHERE "
            "(c.type = 'assignment' OR c.type = 'snapshot') AND "
            "c.snapshot_id != @snapshot"
        )
        async for value in self._container.query_items(
            query=query,
            parameters=[{"name": "@snapshot", "value": active_snapshot_id}],
        ):
            return int(value) != 0
        raise RuntimeError("Mandate inactive-record query returned no count")

    async def _prune_inactive(self, active_snapshot_id: str) -> bool:
        query = (
            "SELECT c.id, c.user_id FROM c WHERE "
            "(c.type = 'assignment' OR c.type = 'snapshot') AND "
            "c.snapshot_id != @snapshot"
        )
        stale: list[tuple[str, str]] = []
        async for value in self._container.query_items(
            query=query,
            parameters=[{"name": "@snapshot", "value": active_snapshot_id}],
        ):
            item_id = value.get("id")
            user_id = value.get("user_id")
            if not isinstance(item_id, str) or not isinstance(user_id, str):
                raise RuntimeError("Inactive mandate record has invalid identity")
            stale.append((item_id, user_id))
        for item_id, user_id in stale:
            await self._container.delete_item(item=item_id, partition_key=user_id)
        return bool(stale)
