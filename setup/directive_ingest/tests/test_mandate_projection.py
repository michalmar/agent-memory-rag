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
        self.replacements: list[str] = []
        self.delete_etags: list[str | None] = []
        self._etag_sequence = 0

    def _store(self, item: dict[str, object]) -> dict[str, object]:
        self._etag_sequence += 1
        stored = deepcopy(item)
        stored["_etag"] = f"etag-{self._etag_sequence}"
        self.items[(str(stored["id"]), str(stored["user_id"]))] = stored
        return deepcopy(stored)

    async def read_item(
        self, *, item: str, partition_key: str
    ) -> dict[str, object]:
        try:
            return deepcopy(self.items[(item, partition_key)])
        except KeyError as exc:
            raise exceptions.CosmosResourceNotFoundError(
                status_code=404, message="not found"
            ) from exc

    async def upsert_item(self, item: dict[str, object]) -> dict[str, object]:
        self.upserts += 1
        return self._store(item)

    async def create_item(self, *, body: dict[str, object]) -> dict[str, object]:
        key = (str(body["id"]), str(body["user_id"]))
        if key in self.items:
            raise exceptions.CosmosResourceExistsError(
                status_code=409, message="already exists"
            )
        return self._store(body)

    async def replace_item(
        self,
        *,
        item: str,
        body: dict[str, object],
        etag: str,
        match_condition: object,
    ) -> dict[str, object]:
        key = (item, str(body["user_id"]))
        existing = self.items.get(key)
        self.replacements.append(etag)
        if existing is None or existing.get("_etag") != etag:
            raise exceptions.CosmosAccessConditionFailedError(
                status_code=412, message="changed"
            )
        return self._store(body)

    async def delete_item(
        self,
        *,
        item: str,
        partition_key: str,
        etag: str | None = None,
        match_condition: object | None = None,
    ) -> None:
        self.deletes += 1
        key = (item, partition_key)
        existing = self.items.get(key)
        self.delete_etags.append(etag)
        if existing is None:
            raise exceptions.CosmosResourceNotFoundError(
                status_code=404, message="not found"
            )
        if etag is not None and existing.get("_etag") != etag:
            raise exceptions.CosmosAccessConditionFailedError(
                status_code=412, message="changed"
            )
        del self.items[key]

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


def _parsed(checksum: str = "a" * 64) -> ParsedMandates:
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
        checksum=checksum,
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", "snapshot:wrong"),
        ("type", "snapshot"),
    ],
)
async def test_corrupt_active_pointer_envelope_is_rejected(
    field: str, value: str
) -> None:
    container = MemoryMandateContainer()
    repository = _repository(container)
    parsed = _parsed()
    await repository.publish(parsed, "run-1")
    container.items[("active-snapshot", "_control")][field] = value

    assert await repository.is_current(parsed) is False
    with pytest.raises(RuntimeError, match="invalid metadata"):
        await repository.verification_summary()
    with pytest.raises(RuntimeError, match="invalid envelope"):
        await repository.stage(parsed, "run-2")


@pytest.mark.asyncio
async def test_active_pointer_rollback_does_not_overwrite_concurrent_activation() -> None:
    container = MemoryMandateContainer()
    repository = _repository(container)
    await repository.publish(_parsed(), "run-1")

    snapshot, previous, changed = await repository.stage(_parsed("b" * 64), "run-2")
    assert changed is True
    assert previous is not None
    candidate_etag = await repository.activate(snapshot, "run-2", previous)
    assert container.replacements[-1] == previous["_etag"]

    concurrent = {
        "id": "active-snapshot",
        "type": "active_snapshot",
        "user_id": "_control",
        "snapshot_id": "mandates-concurrent",
        "checksum": "c" * 64,
        "assignment_count": 0,
        "user_count": 0,
        "complete": True,
    }
    await container.upsert_item(concurrent)

    with pytest.raises(RuntimeError, match="Concurrent mandate activation"):
        await repository.restore_active(previous, candidate_etag)

    active = container.items[("active-snapshot", "_control")]
    assert active["snapshot_id"] == "mandates-concurrent"
    assert container.replacements[-1] == candidate_etag


@pytest.mark.asyncio
async def test_first_active_pointer_rollback_conditionally_deletes_candidate() -> None:
    container = MemoryMandateContainer()
    repository = _repository(container)
    snapshot, previous, changed = await repository.stage(_parsed(), "run")
    assert changed is True
    assert previous is None

    candidate_etag = await repository.activate(snapshot, "run", previous)
    await repository.restore_active(None, candidate_etag)

    assert ("active-snapshot", "_control") not in container.items
    assert container.delete_etags[-1] == candidate_etag


@pytest.mark.asyncio
async def test_staged_cleanup_does_not_delete_another_runs_records() -> None:
    container = MemoryMandateContainer()
    repository = _repository(container)
    snapshot, _, changed = await repository.stage(_parsed(), "run-1")
    assert changed is True

    for record in [
        record
        for record in container.items.values()
        if record.get("snapshot_id") == snapshot.snapshot_id
    ]:
        replacement = {
            key: value for key, value in record.items() if key != "_etag"
        }
        replacement["run_id"] = "run-2"
        await container.upsert_item(replacement)

    await repository.discard_staged(snapshot, "run-1")

    assert all(
        record.get("run_id") == "run-2"
        for record in container.items.values()
        if record.get("snapshot_id") == snapshot.snapshot_id
    )
