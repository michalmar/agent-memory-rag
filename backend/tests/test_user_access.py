from __future__ import annotations

import unittest

from fastapi import HTTPException

from agent_memory_backend.auth import EntraValidator, User, _validate_auth_configuration
from agent_memory_backend.conversation_history import (
    ConversationHistoryStore,
    public_conversation_detail,
)
from agent_memory_backend import server
from agent_memory_backend.server import UpdateTitleRequest, update_conversation_title


class _FakeHistoryContainer:
    def __init__(self, items: list[dict]) -> None:
        self._items = items
        self.query: str | None = None
        self.parameters: list[dict] | None = None
        self.partition_key: str | None = None

    def query_items(self, *, query: str, parameters: list[dict], partition_key: str):
        self.query = query
        self.parameters = parameters
        self.partition_key = partition_key

        async def results():
            for item in self._items:
                yield dict(item)

        return results()


class ConversationHistoryAccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_is_bound_to_authenticated_partition_and_hides_owner(self) -> None:
        container = _FakeHistoryContainer(
            [
                {
                    "id": "conversation-1",
                    "user_id": "tenant-1:user-1",
                    "title": "Private history",
                }
            ]
        )
        store = ConversationHistoryStore()
        store._container = container

        conversations = await store.list_conversations("tenant-1:user-1")

        self.assertEqual(container.partition_key, "tenant-1:user-1")
        self.assertIn("c.user_id=@uid", container.query or "")
        self.assertEqual(
            conversations,
            [
                {
                    "id": "conversation-1",
                    "title": "Private history",
                    "created_at": None,
                    "updated_at": None,
                    "message_count": 0,
                    "metadata": {
                        "agent_type": None,
                        "agent_label": None,
                        "release_label": None,
                        "agent_version": None,
                    },
                }
            ],
        )

    async def test_list_rejects_any_mismatched_owner(self) -> None:
        store = ConversationHistoryStore()
        store._container = _FakeHistoryContainer(
            [{"id": "conversation-2", "user_id": "tenant-1:user-2"}]
        )

        with self.assertRaisesRegex(RuntimeError, "isolation check failed"):
            await store.list_conversations("tenant-1:user-1")

    def test_full_conversation_response_hides_owner_and_cosmos_metadata(self) -> None:
        public = public_conversation_detail(
            {
                "id": "conversation-1",
                "user_id": "tenant-1:user-1",
                "title": "Private",
                "messages": [
                    {
                        "role": "user",
                        "content": "hello",
                        "created_at": "2026-07-12T10:00:00+00:00",
                        "private": "drop",
                    },
                    {
                        "role": "assistant",
                        "content": "grounded response",
                        "created_at": "2026-07-12T10:00:02+00:00",
                        "usage": {
                            "input_tokens": 12,
                            "output_tokens": 8,
                            "cached_tokens": 3,
                            "private": 99,
                        },
                        "tools": [
                            "knowledge_base_retrieve",
                            "knowledge_base_retrieve",
                        ],
                        "citations": [
                            {
                                "ref_id": "returns-policy",
                                "source_name": "Returns policy",
                                "search_idx": 0,
                                "url": "https://example.test/returns",
                                "content": "private excerpt",
                            }
                        ],
                    },
                    {"role": "tool", "content": "private tool output"},
                ],
                "metadata": {
                    "agent_type": "agent-framework",
                    "release_id": "release-1",
                    "observed_agent_version": "3",
                    "physical_agent_name": "private-name",
                    "runtime_state": {
                        "foundry_conversation_id": "private-foundry-id",
                        "hosted_session_id": "private-session-id",
                    },
                },
                "_etag": "private",
                "_ts": 123,
            }
        )

        self.assertEqual(
            public["messages"],
            [
                {
                    "role": "user",
                    "content": "hello",
                    "created_at": "2026-07-12T10:00:00+00:00",
                },
                {
                    "role": "assistant",
                    "content": "grounded response",
                    "created_at": "2026-07-12T10:00:02+00:00",
                    "usage": {
                        "input_tokens": 12,
                        "output_tokens": 8,
                        "cached_tokens": 3,
                    },
                    "tools": ["knowledge_base_retrieve"],
                    "citations": [
                        {
                            "ref_id": "returns-policy",
                            "source_name": "Returns policy",
                            "search_idx": 0,
                            "url": "https://example.test/returns",
                        }
                    ],
                },
            ],
        )
        self.assertEqual(
            public["metadata"],
            {
                "agent_type": "agent-framework",
                "agent_label": "Hosted Agent Framework",
                "release_label": "release-1",
                "agent_version": "3",
            },
        )
        serialized = str(public)
        self.assertNotIn("tenant-1:user-1", serialized)
        self.assertNotIn("private-foundry-id", serialized)
        self.assertNotIn("private-session-id", serialized)
        self.assertNotIn("physical_agent_name", serialized)
        self.assertNotIn("private excerpt", serialized)

    async def test_title_update_response_hides_owner_and_cosmos_metadata(self) -> None:
        original_store = server.history_store

        class _Store:
            async def update_title(self, session_id: str, user_id: str, title: str) -> dict:
                return {
                    "id": session_id,
                    "user_id": user_id,
                    "title": title,
                    "messages": [],
                    "_etag": "private",
                }

        server.history_store = _Store()
        try:
            result = await update_conversation_title(
                "conversation-1",
                UpdateTitleRequest(title="Renamed"),
                User("tenant-1:user-1", "Alice", "alice@example.com", "A"),
            )
        finally:
            server.history_store = original_store

        self.assertEqual(result["id"], "conversation-1")
        self.assertEqual(result["title"], "Renamed")
        self.assertEqual(result["messages"], [])
        self.assertNotIn("user_id", result)
        self.assertNotIn("_etag", result)


class EndUserIdentityTests(unittest.TestCase):
    def test_entra_subject_is_tenant_scoped(self) -> None:
        validator = object.__new__(EntraValidator)
        validator.tenant_id = "tenant-1"

        user = validator._build_user(
            {
                "tid": "tenant-1",
                "oid": "object-1",
                "name": "Alice",
                "preferred_username": "alice@example.com",
            }
        )

        self.assertEqual(user.user_id, "tenant-1:object-1")

    def test_entra_subject_requires_tenant_claim(self) -> None:
        validator = object.__new__(EntraValidator)
        validator.tenant_id = "tenant-1"

        with self.assertRaises(HTTPException) as raised:
            validator._build_user({"oid": "object-1"})

        self.assertEqual(raised.exception.status_code, 401)

    def test_entra_subject_rejects_another_tenant(self) -> None:
        validator = object.__new__(EntraValidator)
        validator.tenant_id = "tenant-1"

        with self.assertRaises(HTTPException) as raised:
            validator._build_user({"tid": "tenant-2", "oid": "object-1"})

        self.assertEqual(raised.exception.status_code, 401)

    def test_mock_auth_is_forbidden_in_production(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "configure Entra auth"):
            _validate_auth_configuration("mock", "production")

        _validate_auth_configuration("mock", "local")
        _validate_auth_configuration("entra", "production")


if __name__ == "__main__":
    unittest.main()
