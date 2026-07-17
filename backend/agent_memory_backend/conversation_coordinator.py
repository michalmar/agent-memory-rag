"""Coordinate application conversations with their remote Foundry runtime state."""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from agent_contracts import AgentType, RuntimeState
from .agent_runtime_contracts import AgentRuntime
from .conversation_history import ConversationHistoryStore, runtime_state_from_document
from .conversation_memory import ConversationMemoryStore
from .conversation_registry import ConversationRegistry, LiveConversation
from fastapi import HTTPException

logger = logging.getLogger("conversation_coordinator")


@dataclass(frozen=True)
class PreparedConversation:
    conversation: LiveConversation
    runtime: AgentRuntime


class ConversationCoordinator:
    def __init__(
        self,
        conversation_registry: ConversationRegistry,
        history_store: ConversationHistoryStore,
        memory_store: ConversationMemoryStore,
        runtime_registry: dict[AgentType, AgentRuntime],
    ) -> None:
        self._registry = conversation_registry
        self._history = history_store
        self._memory = memory_store
        self._runtimes = runtime_registry

    def runtime(self, agent_type: AgentType) -> AgentRuntime:
        runtime = self._runtimes.get(agent_type)
        if runtime is None:
            raise HTTPException(
                status_code=503,
                detail=f"Agent runtime unavailable: {agent_type.value}",
            )
        return runtime

    async def prepare(
        self,
        *,
        conversation_id: str | None,
        agent_type: AgentType,
        user_id: str,
        initial_title: str,
    ) -> PreparedConversation:
        application_id = conversation_id or str(uuid.uuid4())
        self._registry.assert_owner(application_id, user_id)

        if conversation_id and self._history.enabled:
            document = await self._history.get_conversation(
                application_id, user_id
            )
            if document is None:
                raise HTTPException(status_code=404, detail="Conversation not found")
            state, runtime = await self._restore_runtime(
                document, application_id, user_id, agent_type
            )
            conversation = self._registry.get(application_id)
            if conversation is None:
                conversation = self._registry.create(
                    conversation_id=application_id,
                    title=document.get("title"),
                    user_id=user_id,
                    agent_type=state.descriptor.agent_type,
                    runtime_state=state,
                )
            else:
                self._registry.bind_runtime(
                    application_id, state.descriptor.agent_type, state
                )
            return PreparedConversation(conversation, runtime)

        if conversation_id:
            conversation = self._registry.get(application_id)
            if conversation is None or conversation.user_id != user_id:
                raise HTTPException(status_code=404, detail="Conversation not found")
            if (
                conversation.agent_type != agent_type
                or conversation.runtime_state is None
            ):
                raise HTTPException(
                    status_code=409, detail="CONVERSATION_AGENT_IMMUTABLE"
                )
            return PreparedConversation(conversation, self.runtime(agent_type))

        runtime = self.runtime(agent_type)
        state = await runtime.create_state(application_id, user_id)
        conversation = self._registry.create(
            conversation_id=application_id,
            title=initial_title,
            user_id=user_id,
            agent_type=agent_type,
            runtime_state=state,
        )
        try:
            await self._history.create_conversation(
                application_id,
                user_id,
                state,
                title=initial_title,
            )
        except Exception:
            self._registry.delete(application_id)
            await self._cleanup_unpersisted_state(runtime, state, user_id)
            raise
        return PreparedConversation(conversation, runtime)

    async def delete(self, conversation_id: str, user_id: str) -> None:
        document = await self._history.get_conversation(conversation_id, user_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        await self._memory.delete_by_conversation(conversation_id, user_id)
        state = runtime_state_from_document(document)
        if state is not None and state.descriptor.agent_type in self._runtimes:
            await self._runtimes[state.descriptor.agent_type].delete_state(
                state, user_id
            )
        if not await self._history.delete_conversation(conversation_id, user_id):
            raise HTTPException(status_code=404, detail="Conversation not found")
        self._registry.delete(conversation_id)

    async def _restore_runtime(
        self,
        document: dict,
        conversation_id: str,
        user_id: str,
        requested_type: AgentType,
    ) -> tuple[RuntimeState, AgentRuntime]:
        state = runtime_state_from_document(document)
        if state is not None:
            if state.descriptor.agent_type != requested_type:
                raise HTTPException(
                    status_code=409, detail="CONVERSATION_AGENT_IMMUTABLE"
                )
            return state, self.runtime(state.descriptor.agent_type)

        if requested_type is not AgentType.AGENT_FRAMEWORK:
            raise HTTPException(
                status_code=409, detail="CONVERSATION_AGENT_IMMUTABLE"
            )
        runtime = self.runtime(AgentType.AGENT_FRAMEWORK)
        seed_messages = [
            {
                "role": message["role"],
                "content": message["content"],
            }
            for message in document.get("messages") or []
            if message.get("role") in {"user", "assistant"}
            and isinstance(message.get("content"), str)
        ]
        state = await runtime.create_state(
            conversation_id, user_id, seed_messages=seed_messages
        )
        try:
            await self._history.bind_runtime_state(
                conversation_id,
                user_id,
                state,
                expected_etag=document.get("_etag"),
            )
        except Exception:
            await self._cleanup_unpersisted_state(runtime, state, user_id)
            raise
        return state, runtime

    @staticmethod
    async def _cleanup_unpersisted_state(
        runtime: AgentRuntime, state: RuntimeState, user_id: str
    ) -> None:
        try:
            await runtime.delete_state(state, user_id)
        except Exception:
            logger.exception("Failed to clean up unpersisted remote state")
