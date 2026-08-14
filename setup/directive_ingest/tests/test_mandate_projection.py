from __future__ import annotations

from collections.abc import AsyncIterator
from copy import deepcopy

import pytest
from azure.cosmos import exceptions
from directive_contracts import MandateAssignment

from directive_ingestion.mandate_projection import (
    MandateRepository,
    ParsedMandates,
)


class MemoryMandateContainer:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, object]] = {}
        self.upserts = 0
        self.deletes = 0

    async def read_item(
        self, *, item: str, partition_key: str
    ) -> dict[str, object]:
        try:
            return deepcopy(self.items[(item, partition_key)])
        except KeyError as exc:
            raise exceptions.CosmosResourceNotFoundError(
                status_code=404, message="not found"
            ) from exc

    async def upsert_item(self, item: dict[str, object]) -> None:
        self.upserts += 1
        self.items[(str(item["id"]), str(item["user_id"]))] = deepcopy(item)

    async def delete_item(self, *, item: str, partition_key: str) -> None:
        self.deletes += 1
        del self.items[(item, partition_key)]

    async def query_items(
        self, *, query: str, parameters: list[dict[str, object]]
    ) -> AsyncIterator[dict[str, object] | int]:
        snapshot_id = str(parameters[0]["value"])
        records = list(self.items.values())
        if "c.snapshot_id != @snapshot" in query:
            records = [
                record
                for record in records
                if record.get("type") in {"assignment", "snapshot"}
                and record.get("snapshot_id") != snapshot_id
            ]
        else:
            records = [
                record
                for record in records
                if record.get("type") in {"assignment", "snapshot"}
                and record.get("snapshot_id") == snapshot_id
            ]
        if "COUNT(1)" in query:
            yield len(records)
            return
        for record in records:
            yield deepcopy(record)


def _parsed() -> ParsedMandates:
    assignments = (
        MandateAssignment(
            user_id="tenant:11111111-1111-1111-1111-111111111111",
            directive_id="72403881",
        ),
        MandateAssignment(
            user_id="tenant:22222222-2222-2222-2222-222222222222",
            directive_id="72403881",
        ),
    )
    return ParsedMandates(
        assignments=assignments,
        checksum="a" * 64,
        user_count=2,
    )


def _repository(container: MemoryMandateContainer) -> MandateRepository:
    repository = object.__new__(MandateRepository)
    repository._container = container
    return repository


@pytest.mark.asyncio
async def test_first_mandate_only_activation_writes_exact_records() -> None:
    container = MemoryMandateContainer()
    repository = _repository(container)
    parsed = _parsed()

    snapshot, changed = await repository.publish(parsed, "run-1")

    assert changed is True
    assert snapshot.snapshot_id == f"mandates-{parsed.checksum}"
    assignment_ids = {
        item["id"]
        for item in container.items.values()
        if item.get("type") == "assignment"
    }
    assert assignment_ids == {
        repository._assignment_item_id(snapshot.snapshot_id, assignment)
        for assignment in parsed.assignments
    }
    controls = [
        item
        for item in container.items.values()
        if item.get("type") == "snapshot"
    ]
    assert len(controls) == 1
    assert controls[0]["id"] == f"snapshot:{snapshot.snapshot_id}"
    assert controls[0]["assignment_count"] == len(parsed.assignments)
    assert await repository.is_current(parsed) is True


@pytest.mark.asyncio
async def test_duplicate_and_arbitrary_snapshot_records_get_salted_repair() -> None:
    container = MemoryMandateContainer()
    repository = _repository(container)
    parsed = _parsed()
    original, _ = await repository.publish(parsed, "run-1")

    assignment = parsed.assignments[0]
    container.items[("assignment:arbitrary", assignment.user_id)] = {
        "id": "assignment:arbitrary",
        "type": "assignment",
        "user_id": assignment.user_id,
        "directive_id": assignment.directive_id,
        "snapshot_id": original.snapshot_id,
        "flag": "M",
    }
    original_control = next(
        item
        for item in container.items.values()
        if item.get("type") == "snapshot"
    )
    arbitrary_control = deepcopy(original_control)
    arbitrary_control["id"] = "snapshot:arbitrary"
    container.items[("snapshot:arbitrary", "_control")] = arbitrary_control

    repaired, changed = await repository.publish(parsed, "run-2")

    assert changed is True
    assert repaired.snapshot_id.startswith(f"mandates-{parsed.checksum}-")
    assert repaired.snapshot_id != original.snapshot_id
    records = [
        item
        for item in container.items.values()
        if item.get("type") in {"assignment", "snapshot"}
    ]
    assert len(records) == len(parsed.assignments) + 1
    assert {item["id"] for item in records} == {
        *{
            repository._assignment_item_id(repaired.snapshot_id, assignment)
            for assignment in parsed.assignments
        },
        f"snapshot:{repaired.snapshot_id}",
    }
    assert await repository.is_current(parsed) is True


@pytest.mark.asyncio
async def test_repaired_active_snapshot_has_a_write_free_subsequent_noop() -> None:
    container = MemoryMandateContainer()
    repository = _repository(container)
    parsed = _parsed()
    original, _ = await repository.publish(parsed, "run-1")
    assignment = parsed.assignments[0]
    container.items[("assignment:arbitrary", assignment.user_id)] = {
        "id": "assignment:arbitrary",
        "type": "assignment",
        "user_id": assignment.user_id,
        "directive_id": assignment.directive_id,
        "snapshot_id": original.snapshot_id,
        "flag": "M",
    }
    repaired, _ = await repository.publish(parsed, "run-2")
    writes_before_noop = (container.upserts, container.deletes)

    snapshot, changed = await repository.publish(parsed, "run-3")

    assert changed is False
    assert snapshot.snapshot_id == repaired.snapshot_id
    assert (container.upserts, container.deletes) == writes_before_noop
