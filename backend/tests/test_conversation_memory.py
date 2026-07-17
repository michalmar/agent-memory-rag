from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from azure.core import MatchConditions
from azure.cosmos.exceptions import (
    CosmosClientTimeoutError,
    CosmosHttpResponseError,
    CosmosResourceExistsError,
    CosmosResourceNotFoundError,
)

from agent_memory_backend.agent_tools import ToolExecutor
from agent_memory_backend.conversation_coordinator import ConversationCoordinator
from agent_memory_backend.conversation_memory import (
    EMBEDDING_DIMENSIONS,
    ConversationMemoryStore,
    MemoryStoreUnavailable,
    public_memory,
)
from agent_memory_backend.conversation_registry import ConversationRegistry


def _embedding(value: float = 0.0) -> list[float]:
    return [value] * EMBEDDING_DIMENSIONS


class _AsyncRows:
    def __init__(self, rows: list[dict], error: Exception | None = None) -> None:
        self._rows = iter(rows)
        self._error = error

    def __aiter__(self):
        return self

    async def __anext__(self) -> dict:
        if self._error is not None:
            error = self._error
            self._error = None
            raise error
        try:
            return dict(next(self._rows))
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _FakeContainer:
    def __init__(self) -> None:
        self.documents: dict[tuple[str, str], dict] = {}
        self.query_rows: list[dict] = []
        self.query_kwargs: dict | None = None
        self.query_error: Exception | None = None
        self.replace_kwargs: dict | None = None
        self.deleted: list[tuple[str, str]] = []

    async def read(self) -> dict:
        return {"id": "memories"}

    async def read_item(self, item: str, partition_key: str) -> dict:
        key = (partition_key, item)
        if key not in self.documents:
            raise CosmosResourceNotFoundError(status_code=404, message="missing")
        return dict(self.documents[key])

    async def create_item(self, body: dict) -> dict:
        key = (body["user_id"], body["id"])
        if key in self.documents:
            raise CosmosResourceExistsError(status_code=409, message="exists")
        stored = {**body, "_etag": "etag-created"}
        self.documents[key] = stored
        return dict(stored)

    async def replace_item(self, **kwargs) -> dict:
        self.replace_kwargs = kwargs
        body = {**kwargs["body"], "_etag": "etag-replaced"}
        self.documents[(body["user_id"], body["id"])] = body
        return dict(body)

    def query_items(self, **kwargs) -> _AsyncRows:
        self.query_kwargs = kwargs
        return _AsyncRows(self.query_rows, self.query_error)

    async def delete_item(self, item: str, partition_key: str) -> None:
        key = (partition_key, item)
        if key not in self.documents:
            raise CosmosResourceNotFoundError(status_code=404, message="missing")
        self.documents.pop(key)
        self.deleted.append(key)


class ConversationMemoryStoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.container = _FakeContainer()
        self.store = ConversationMemoryStore()
        self.store._container = self.container

    def test_public_memory_hides_owner_vector_and_cosmos_metadata(self) -> None:
        public = public_memory(
            {
                "id": "conversation-1",
                "conversation_id": "conversation-1",
                "user_id": "tenant:user",
                "summary": "Summary",
                "embedding": [1.0],
                "_etag": "secret",
            }
        )
        self.assertEqual(
            public,
            {
                "id": "conversation-1",
                "conversation_id": "conversation-1",
                "summary": "Summary",
            },
        )

    async def test_create_and_upsert_preserve_id_owner_and_created_at(self) -> None:
        created = await self.store.create_memory(
            "conversation-1",
            "tenant:user",
            "first",
            _embedding(),
            source_title="Title",
            message_count=2,
        )
        updated = await self.store.create_memory(
            "conversation-1",
            "tenant:user",
            "second",
            _embedding(0.5),
            source_title="Updated",
            message_count=3,
        )

        self.assertEqual(created["id"], "conversation-1")
        self.assertEqual(updated["id"], "conversation-1")
        self.assertEqual(updated["user_id"], "tenant:user")
        self.assertEqual(updated["created_at"], created["created_at"])
        self.assertEqual(updated["summary"], "second")
        self.assertEqual(
            self.container.replace_kwargs["match_condition"],
            MatchConditions.IfNotModified,
        )

    async def test_embedding_shape_and_values_are_validated(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly"):
            await self.store.create_memory(
                "conversation-1", "tenant:user", "summary", [0.0]
            )
        invalid = _embedding()
        invalid[10] = float("nan")
        with self.assertRaisesRegex(ValueError, "finite"):
            await self.store.search("tenant:user", invalid)

    async def test_list_is_owner_partitioned_parameterized_and_ordered(self) -> None:
        self.container.query_rows = [
            {
                "id": "conversation-1",
                "user_id": "tenant:user",
                "conversation_id": "conversation-1",
                "summary": "summary",
            }
        ]

        rows = await self.store.list_memories("tenant:user", limit=10, offset=2)

        self.assertEqual(len(rows), 1)
        self.assertEqual(
            self.container.query_kwargs["partition_key"], "tenant:user"
        )
        self.assertIn("c.user_id=@uid", self.container.query_kwargs["query"])
        self.assertIn(
            "ORDER BY c.created_at DESC", self.container.query_kwargs["query"]
        )
        self.assertIn(
            {"name": "@offset", "value": 2},
            self.container.query_kwargs["parameters"],
        )

    async def test_vector_search_is_bounded_partitioned_and_converts_distance(self) -> None:
        self.container.query_rows = [
            {
                "id": "conversation-1",
                "user_id": "tenant:user",
                "conversation_id": "conversation-1",
                "summary": "summary",
                "distance": 0.25,
            }
        ]

        rows = await self.store.search("tenant:user", _embedding(0.2), limit=3)

        self.assertAlmostEqual(rows[0]["similarity"], 0.75)
        self.assertNotIn("distance", rows[0])
        self.assertEqual(
            self.container.query_kwargs["partition_key"], "tenant:user"
        )
        self.assertIn("TOP @limit", self.container.query_kwargs["query"])
        self.assertIn(
            "VectorDistance(c.embedding, @embedding)",
            self.container.query_kwargs["query"],
        )

    async def test_query_rejects_any_mismatched_owner(self) -> None:
        self.container.query_rows = [
            {
                "id": "conversation-1",
                "user_id": "tenant:attacker",
                "distance": 0.1,
            }
        ]
        with self.assertRaisesRegex(RuntimeError, "isolation"):
            await self.store.search("tenant:user", _embedding())

    async def test_delete_and_conversation_cleanup_use_point_operations(self) -> None:
        await self.store.create_memory(
            "conversation-1", "tenant:user", "summary", _embedding()
        )
        self.assertTrue(
            await self.store.delete_memory("conversation-1", "tenant:user")
        )
        self.assertFalse(
            await self.store.delete_memory("conversation-1", "tenant:user")
        )

        await self.store.create_memory(
            "conversation-2", "tenant:user", "summary", _embedding()
        )
        await self.store.delete_by_conversation("conversation-2", "tenant:user")
        self.assertIn(("tenant:user", "conversation-2"), self.container.deleted)

    async def test_known_cosmos_failure_is_sanitized(self) -> None:
        self.container.read = AsyncMock(
            side_effect=CosmosHttpResponseError(
                status_code=503, message="private dependency detail"
            )
        )
        with self.assertRaises(MemoryStoreUnavailable) as raised:
            await self.store.health_check()
        self.assertNotIn("private dependency", str(raised.exception))

    async def test_cosmos_timeout_during_query_is_sanitized(self) -> None:
        self.container.query_error = CosmosClientTimeoutError(
            "private network detail"
        )

        with self.assertRaises(MemoryStoreUnavailable) as raised:
            await self.store.list_memories("tenant:user")

        self.assertNotIn("private network", str(raised.exception))

    async def test_uninitialized_store_fails_explicitly(self) -> None:
        store = ConversationMemoryStore()
        with self.assertRaises(MemoryStoreUnavailable):
            await store.list_memories("tenant:user")

    async def test_close_releases_client_and_container(self) -> None:
        client = AsyncMock()
        self.store._client = client
        await self.store.close()
        client.close.assert_awaited_once()
        self.assertIsNone(self.store._client)
        self.assertIsNone(self.store._container)


class _UnavailableStore:
    enabled = True

    async def search(self, user_id: str, embedding: list[float], limit: int):
        raise MemoryStoreUnavailable("private detail")

    async def delete_by_conversation(
        self, conversation_id: str, user_id: str
    ) -> None:
        raise MemoryStoreUnavailable("private detail")


class OptionalMemoryBehaviorTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_tool_continues_without_unavailable_memory(self) -> None:
        executor = ToolExecutor(_UnavailableStore(), None)
        with patch(
            "agent_memory_backend.agent_tools.embed_text",
            new=AsyncMock(return_value=_embedding()),
        ):
            result = await executor.execute(
                "check_memory", {"query": "shipping"}, user_id="tenant:user"
            )
        self.assertEqual(result["memories"], [])
        self.assertEqual(result["message"], "No memories available.")

    async def test_conversation_delete_requires_memory_cleanup(self) -> None:
        history = AsyncMock()
        history.get_conversation.return_value = {
            "id": "conversation-1",
            "user_id": "tenant:user",
            "metadata": {},
        }
        history.delete_conversation.return_value = True
        coordinator = ConversationCoordinator(
            ConversationRegistry(),
            history,
            _UnavailableStore(),
            {},
        )

        with self.assertRaises(MemoryStoreUnavailable):
            await coordinator.delete("conversation-1", "tenant:user")

        history.delete_conversation.assert_not_awaited()
