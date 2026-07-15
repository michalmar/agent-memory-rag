"""Bounded in-memory conversation runtime mappings and locks."""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field

from agent_contracts import AgentType, RuntimeState
from fastapi import HTTPException

MAX_CONVERSATIONS = 1000


@dataclass(slots=True)
class ConversationLease:
    _lock: asyncio.Lock
    _released: bool = False

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._lock.release()


@dataclass
class LiveConversation:
    conversation_id: str
    user_id: str
    title: str | None = None
    last_activity: float = field(default_factory=time.time)
    agent_type: AgentType | None = None
    runtime_state: RuntimeState | None = None

    def touch(self) -> None:
        self.last_activity = time.time()


class ConversationRegistry:
    """Store live runtime mappings with LRU-style eviction."""

    def __init__(self) -> None:
        self._conversations: dict[str, LiveConversation] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def close(self) -> None:
        self._conversations.clear()
        self._locks.clear()

    def create(
        self,
        conversation_id: str | None = None,
        title: str | None = None,
        user_id: str = "",
        agent_type: AgentType | None = None,
        runtime_state: RuntimeState | None = None,
    ) -> LiveConversation:
        conversation = LiveConversation(
            conversation_id=conversation_id or str(uuid.uuid4()),
            user_id=user_id,
            title=title,
            agent_type=agent_type,
            runtime_state=runtime_state,
        )
        self._conversations[conversation.conversation_id] = conversation
        self._evict_if_needed()
        return conversation

    def get(self, conversation_id: str) -> LiveConversation | None:
        conversation = self._conversations.get(conversation_id)
        if conversation is not None:
            conversation.touch()
        return conversation

    def delete(self, conversation_id: str) -> None:
        self._conversations.pop(conversation_id, None)
        self._locks.pop(conversation_id, None)

    def assert_owner(self, conversation_id: str, user_id: str) -> None:
        conversation = self._conversations.get(conversation_id)
        if conversation is not None and conversation.user_id != user_id:
            raise HTTPException(status_code=403, detail="Not the conversation owner")

    def bind_runtime(
        self, conversation_id: str, agent_type: AgentType, runtime_state: RuntimeState
    ) -> None:
        conversation = self._conversations.get(conversation_id)
        if conversation is None:
            raise RuntimeError("Conversation does not exist")
        if (
            conversation.agent_type is not None
            and conversation.agent_type != agent_type
        ):
            raise HTTPException(
                status_code=409, detail="CONVERSATION_AGENT_IMMUTABLE"
            )
        conversation.agent_type = agent_type
        conversation.runtime_state = runtime_state
        conversation.touch()

    async def acquire(self, conversation_id: str) -> ConversationLease:
        lock = self._locks.setdefault(conversation_id, asyncio.Lock())
        if lock.locked():
            raise HTTPException(status_code=409, detail="CONVERSATION_BUSY")
        await lock.acquire()
        return ConversationLease(lock)

    def _evict_if_needed(self) -> None:
        overflow = len(self._conversations) - MAX_CONVERSATIONS
        if overflow <= 0:
            return
        candidates = (
            item
            for item in sorted(
                self._conversations.items(),
                key=lambda item: item[1].last_activity,
            )
            if item[0] not in self._locks or not self._locks[item[0]].locked()
        )
        for conversation_id, _ in list(candidates)[:overflow]:
            self.delete(conversation_id)
