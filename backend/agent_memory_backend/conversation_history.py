"""Owner-partitioned asynchronous Cosmos conversation persistence."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from collections.abc import Sequence
from typing import Any

from agent_contracts import AgentType, RuntimeDescriptor, RuntimeState
from .config import get_settings

logger = logging.getLogger("history")

_AGENT_LABELS = {
    AgentType.FOUNDRY_PROMPT.value: "Prompt Agent",
    AgentType.AGENT_FRAMEWORK.value: "Hosted Agent Framework",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runtime_metadata(state: RuntimeState) -> dict[str, Any]:
    descriptor = state.descriptor
    return {
        "schema_version": state.schema_version,
        "agent_type": descriptor.agent_type.value,
        "physical_agent_name": descriptor.physical_agent_name,
        "observed_agent_version": descriptor.observed_agent_version,
        "release_id": descriptor.release_id,
        "prompt_version": descriptor.prompt_version,
        "runtime_state": {
            "foundry_conversation_id": state.foundry_conversation_id,
            "hosted_session_id": state.hosted_session_id,
            "last_response_id": state.last_response_id,
        },
    }


def runtime_state_from_document(document: dict[str, Any]) -> RuntimeState | None:
    metadata = document.get("metadata") or {}
    agent_type = metadata.get("agent_type")
    if not agent_type:
        return None
    private = metadata.get("runtime_state") or {}
    return RuntimeState(
        descriptor=RuntimeDescriptor(
            agent_type=AgentType(agent_type),
            physical_agent_name=str(metadata.get("physical_agent_name") or ""),
            release_id=str(metadata.get("release_id") or ""),
            prompt_version=str(metadata.get("prompt_version") or ""),
            observed_agent_version=metadata.get("observed_agent_version"),
        ),
        foundry_conversation_id=private.get("foundry_conversation_id"),
        hosted_session_id=private.get("hosted_session_id"),
        last_response_id=private.get("last_response_id"),
        schema_version=int(metadata.get("schema_version", 3)),
    )


def _public_agent_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    agent_type = metadata.get("agent_type")
    return {
        "agent_type": agent_type,
        "agent_label": _AGENT_LABELS.get(agent_type),
        "release_label": metadata.get("release_id"),
        "agent_version": metadata.get("observed_agent_version"),
    }


def _public_usage(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    usage: dict[str, int] = {}
    for key in ("input_tokens", "output_tokens", "cached_tokens"):
        count = value.get(key)
        if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
            usage[key] = count
    return usage or None


def _public_citation(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    ref_id = value.get("ref_id")
    source_name = value.get("source_name")
    if not isinstance(ref_id, str) or not isinstance(source_name, str):
        return None
    citation: dict[str, Any] = {
        "ref_id": ref_id,
        "source_name": source_name,
    }
    search_idx = value.get("search_idx")
    if isinstance(search_idx, int) and not isinstance(search_idx, bool):
        citation["search_idx"] = search_idx
    url = value.get("url")
    if isinstance(url, str) and url:
        citation["url"] = url
    return citation


def _public_message(message: Any) -> dict[str, Any] | None:
    if not isinstance(message, dict):
        return None
    role = message.get("role")
    content = message.get("content")
    if role not in {"user", "assistant"} or not isinstance(content, str):
        return None

    result: dict[str, Any] = {"role": role, "content": content}
    created_at = message.get("created_at")
    if isinstance(created_at, str) and created_at:
        result["created_at"] = created_at

    if role == "assistant":
        usage = _public_usage(message.get("usage"))
        if usage:
            result["usage"] = usage

        tool_values = message.get("tools")
        tools: list[str] = []
        for tool in tool_values if isinstance(tool_values, (list, tuple)) else []:
            if isinstance(tool, str) and tool and tool not in tools:
                tools.append(tool)
        if tools:
            result["tools"] = tools

        citation_values = message.get("citations")
        citations = [
            citation
            for value in (
                citation_values
                if isinstance(citation_values, (list, tuple))
                else []
            )
            if (citation := _public_citation(value)) is not None
        ]
        if citations:
            result["citations"] = citations
    return result


def public_conversation_summary(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": document.get("id"),
        "title": document.get("title"),
        "created_at": document.get("created_at"),
        "updated_at": document.get("updated_at"),
        "message_count": int(document.get("message_count", 0)),
        "metadata": _public_agent_metadata(document.get("metadata") or {}),
    }


def public_conversation_detail(document: dict[str, Any]) -> dict[str, Any]:
    result = public_conversation_summary(document)
    result["messages"] = [
        public_message
        for message in document.get("messages") or []
        if (public_message := _public_message(message)) is not None
    ]
    return result


class ConversationHistoryStore:
    def __init__(self) -> None:
        self._client = None
        self._container = None

    async def initialize(self) -> None:
        settings = get_settings()
        if not settings.cosmos_configured:
            logger.warning("Cosmos not configured; history store disabled")
            return
        from azure.cosmos.aio import CosmosClient

        if settings.cosmos_key:
            self._client = CosmosClient(
                settings.cosmos_endpoint, credential=settings.cosmos_key
            )
        else:
            from .azure_clients import get_credential

            self._client = CosmosClient(
                settings.cosmos_endpoint, credential=get_credential()
            )
        database = self._client.get_database_client(settings.cosmos_database)
        self._container = database.get_container_client(
            settings.cosmos_history_container
        )
        logger.info(
            "History store initialized (%s)", settings.cosmos_history_container
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
            self._container = None

    @property
    def enabled(self) -> bool:
        return self._container is not None

    async def health_check(self) -> None:
        if self._container is None:
            raise RuntimeError("Cosmos history container is not initialized")
        await self._container.read()

    async def get_conversation(
        self, conversation_id: str, user_id: str
    ) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        from azure.cosmos.exceptions import CosmosResourceNotFoundError

        try:
            document = await self._container.read_item(
                conversation_id, partition_key=user_id
            )
        except CosmosResourceNotFoundError:
            return None
        if document.get("user_id") != user_id:
            raise RuntimeError("Conversation history isolation check failed")
        return document

    async def create_conversation(
        self,
        conversation_id: str,
        user_id: str,
        runtime_state: RuntimeState,
        *,
        title: str | None = None,
        messages: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        now = _now()
        document = {
            "id": conversation_id,
            "user_id": user_id,
            "title": title,
            "created_at": now,
            "updated_at": now,
            "message_count": len(messages or []),
            "messages": messages or [],
            "metadata": _runtime_metadata(runtime_state),
        }
        return await self._container.create_item(document)

    async def _replace_conversation(
        self,
        existing: dict[str, Any],
        messages: list[dict[str, Any]],
        *,
        title: str | None = None,
        metadata: dict[str, Any] | None = None,
        expected_etag: str | None = None,
    ) -> dict[str, Any]:
        conversation_id = str(existing["id"])
        user_id = str(existing["user_id"])
        document = {
            "id": conversation_id,
            "user_id": user_id,
            "title": title if title is not None else existing.get("title"),
            "created_at": existing.get("created_at", _now()),
            "updated_at": _now(),
            "message_count": len(messages),
            "messages": messages,
            "metadata": {
                **(existing.get("metadata") or {}),
                **(metadata or {}),
            },
        }
        from azure.core import MatchConditions

        return await self._container.replace_item(
            item=conversation_id,
            body=document,
            etag=expected_etag or existing.get("_etag"),
            match_condition=MatchConditions.IfNotModified,
        )

    async def bind_runtime_state(
        self,
        conversation_id: str,
        user_id: str,
        runtime_state: RuntimeState,
        *,
        expected_etag: str | None = None,
    ) -> dict[str, Any] | None:
        existing = await self.get_conversation(conversation_id, user_id)
        if existing is None:
            raise RuntimeError("Conversation does not exist")
        existing_type = (existing.get("metadata") or {}).get("agent_type")
        requested_type = runtime_state.descriptor.agent_type.value
        if existing_type and existing_type != requested_type:
            raise RuntimeError("CONVERSATION_AGENT_IMMUTABLE")
        return await self._replace_conversation(
            existing,
            list(existing.get("messages") or []),
            title=existing.get("title"),
            metadata=_runtime_metadata(runtime_state),
            expected_etag=expected_etag or existing.get("_etag"),
        )

    async def append_messages(
        self,
        conversation_id: str,
        user_id: str,
        new_messages: Sequence[dict[str, Any]],
        runtime_state: RuntimeState,
        *,
        title: str | None = None,
        expected_etag: str | None = None,
    ) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        existing = await self.get_conversation(conversation_id, user_id)
        if existing is None:
            raise RuntimeError("Conversation does not exist")
        messages = list(existing.get("messages") or [])
        messages.extend(dict(message) for message in new_messages)
        return await self._replace_conversation(
            existing,
            messages,
            title=title,
            metadata=_runtime_metadata(runtime_state),
            expected_etag=expected_etag or existing.get("_etag"),
        )

    async def list_conversations(
        self, user_id: str, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        query = (
            "SELECT c.id, c.user_id, c.title, c.created_at, c.updated_at, "
            "c.message_count, c.metadata FROM c WHERE c.user_id=@uid "
            "ORDER BY c.updated_at DESC OFFSET @off LIMIT @lim"
        )
        parameters = [
            {"name": "@uid", "value": user_id},
            {"name": "@off", "value": offset},
            {"name": "@lim", "value": limit},
        ]
        items: list[dict[str, Any]] = []
        async for item in self._container.query_items(
            query=query,
            parameters=parameters,
            partition_key=user_id,
        ):
            if item.get("user_id") != user_id:
                raise RuntimeError("Conversation history isolation check failed")
            items.append(public_conversation_summary(item))
        return items

    async def get_by_hosted_session(
        self, user_id: str, hosted_session_id: str
    ) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        query = (
            "SELECT * FROM c WHERE c.user_id=@uid AND "
            "c.metadata.runtime_state.hosted_session_id=@sid"
        )
        parameters = [
            {"name": "@uid", "value": user_id},
            {"name": "@sid", "value": hosted_session_id},
        ]
        matches: list[dict[str, Any]] = []
        async for item in self._container.query_items(
            query=query,
            parameters=parameters,
            partition_key=user_id,
        ):
            matches.append(item)
        if len(matches) > 1:
            raise RuntimeError("Hosted session is bound to multiple conversations")
        return matches[0] if matches else None

    async def update_title(
        self, conversation_id: str, user_id: str, title: str
    ) -> dict[str, Any] | None:
        document = await self.get_conversation(conversation_id, user_id)
        if document is None:
            return None
        return await self._replace_conversation(
            document,
            list(document.get("messages") or []),
            title=title,
            expected_etag=document.get("_etag"),
        )

    async def delete_conversation(self, conversation_id: str, user_id: str) -> bool:
        if not self.enabled:
            return False
        from azure.cosmos.exceptions import CosmosResourceNotFoundError

        try:
            await self._container.delete_item(conversation_id, partition_key=user_id)
        except CosmosResourceNotFoundError:
            return False
        return True
