from __future__ import annotations

import unittest

from directive_contracts import directive_storage_key

from agent_memory_backend.directive_mandates import DirectiveMandateRepository


class _MandateContainer:
    def __init__(self) -> None:
        self.reads: list[tuple[str, str]] = []

    async def read_item(self, *, item: str, partition_key: str):
        self.reads.append((item, partition_key))
        if item == "active-snapshot":
            return {
                "type": "active_snapshot",
                "complete": True,
                "snapshot_id": "snapshot-1",
            }
        return {
            "type": "assignment",
            "snapshot_id": "snapshot-1",
            "directive_id": "ČD/42-A",
            "user_id": "tenant:user",
            "flag": "M",
        }


class DirectiveV2IdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_mandates_use_safe_item_ids_and_return_human_ids(self) -> None:
        repository = DirectiveMandateRepository()
        container = _MandateContainer()
        repository._container = container

        result = await repository.lookup("tenant:user", [" čd / 42-a "])

        normalized_id = "ČD/42-A"
        self.assertEqual(
            container.reads,
            [
                ("active-snapshot", "_control"),
                (
                    f"assignment:snapshot-1:{directive_storage_key(normalized_id)}",
                    "tenant:user",
                ),
            ],
        )
        self.assertEqual(result["statuses"], {normalized_id: "mandatory"})
        self.assertNotIn(directive_storage_key(normalized_id), str(result))
