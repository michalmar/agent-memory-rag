"""Coordinate one streamed chat turn and its durable persistence."""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator

from ag_ui.core.events import RunErrorEvent, RunFinishedEvent, RunStartedEvent
from ag_ui.encoder import EventEncoder
from agent_contracts import (
    AgentType,
    RuntimeState,
    TurnContext,
    WorkflowProgressEvent,
    WorkflowStage,
    WorkflowStatus,
)
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from .agui_adapter import to_agui_events
from .conversation_coordinator import ConversationCoordinator, PreparedConversation
from .conversation_history import ConversationHistoryStore
from .conversation_registry import (
    ConversationLease,
    ConversationRegistry,
    LiveConversation,
)
from .telemetry import span
from .turn_accumulator import TurnAccumulator

logger = logging.getLogger("server")


class ConversationPersistenceError(RuntimeError):
    pass


class ChatTurnService:
    def __init__(
        self,
        coordinator: ConversationCoordinator,
        registry: ConversationRegistry,
        history_store: ConversationHistoryStore,
    ) -> None:
        self._coordinator = coordinator
        self._registry = registry
        self._history = history_store

    async def create_response(
        self,
        *,
        message: str,
        conversation_id: str | None,
        agent_type: AgentType,
        user_id: str,
    ) -> StreamingResponse:
        prepared = await self._coordinator.prepare(
            conversation_id=conversation_id,
            agent_type=agent_type,
            user_id=user_id,
            initial_title=message[:80],
        )
        conversation = prepared.conversation
        runtime_state = conversation.runtime_state
        if runtime_state is None:
            raise HTTPException(
                status_code=500,
                detail="Runtime state is missing",
            )

        lease = await self._registry.acquire(conversation.conversation_id)
        events = self._stream_events(
            message=message,
            agent_type=agent_type,
            user_id=user_id,
            prepared=prepared,
            conversation=conversation,
            runtime_state=runtime_state,
            lease=lease,
        )
        return StreamingResponse(
            events,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Conversation-ID": conversation.conversation_id,
            },
            background=BackgroundTask(lease.release),
        )

    async def _stream_events(
        self,
        *,
        message: str,
        agent_type: AgentType,
        user_id: str,
        prepared: PreparedConversation,
        conversation: LiveConversation,
        runtime_state: RuntimeState,
        lease: ConversationLease,
    ) -> AsyncIterator[str]:
        conversation_id = conversation.conversation_id
        run_id = str(uuid.uuid4())
        encoder = EventEncoder()
        turn = TurnAccumulator(message)
        failure_progress_emitted = False
        with span(
            "agent.run",
            {
                "agent.type": agent_type.value,
                "agent.release_id": runtime_state.descriptor.release_id,
                "session.id": conversation_id,
            },
        ) as current_span:
            try:
                yield encoder.encode(
                    RunStartedEvent(
                        thread_id=conversation_id,
                        run_id=run_id,
                    )
                )
                context = TurnContext(
                    application_conversation_id=conversation_id,
                    authenticated_user_id=user_id,
                    runtime_state=runtime_state,
                )
                async for normalized in prepared.runtime.stream_turn(
                    message,
                    context,
                ):
                    if (
                        isinstance(normalized, WorkflowProgressEvent)
                        and normalized.status is WorkflowStatus.FAILED
                    ):
                        failure_progress_emitted = True
                    turn.consume(normalized)
                    for event in to_agui_events(normalized):
                        yield encoder.encode(event)

                try:
                    await self._history.append_messages(
                        conversation_id,
                        user_id,
                        turn.message_records(),
                        runtime_state,
                        title=conversation.title,
                    )
                except Exception as exc:
                    raise ConversationPersistenceError from exc
                conversation.touch()
                current_span.set_attribute(
                    "agent.response_length",
                    len(turn.assistant_text),
                )
                yield encoder.encode(
                    RunFinishedEvent(
                        thread_id=conversation_id,
                        run_id=run_id,
                    )
                )
            except ConversationPersistenceError:
                current_span.set_attribute(
                    "error.type",
                    "ConversationPersistenceError",
                )
                logger.exception(
                    "[chat] conversation persistence failed "
                    "(session=%s agent=%s)",
                    conversation_id,
                    agent_type.value,
                )
                if (
                    agent_type is AgentType.DIRECTIVE_RAG
                    and not failure_progress_emitted
                ):
                    for event in to_agui_events(
                        WorkflowProgressEvent(
                            stage=WorkflowStage.PREPARING_ANSWER,
                            status=WorkflowStatus.FAILED,
                            message="Conversation could not be saved",
                        )
                    ):
                        yield encoder.encode(event)
                yield encoder.encode(
                    RunErrorEvent(
                        message="Conversation could not be saved",
                        code="CONVERSATION_PERSISTENCE_FAILED",
                    )
                )
            except HTTPException as exc:
                current_span.set_attribute("error.type", "HTTPException")
                if (
                    agent_type is AgentType.DIRECTIVE_RAG
                    and not failure_progress_emitted
                ):
                    for event in to_agui_events(
                        WorkflowProgressEvent(
                            stage=WorkflowStage.PREPARING_ANSWER,
                            status=WorkflowStatus.FAILED,
                            message="Directive request failed",
                        )
                    ):
                        yield encoder.encode(event)
                yield encoder.encode(
                    RunErrorEvent(
                        message="Agent run failed",
                        code=str(exc.detail),
                    )
                )
            except asyncio.CancelledError:
                current_span.set_attribute("agent.run.cancelled", True)
                raise
            except Exception as exc:
                current_span.set_attribute(
                    "error.type",
                    type(exc).__name__,
                )
                logger.exception(
                    "[chat] run failed (session=%s agent=%s)",
                    conversation_id,
                    agent_type.value,
                )
                if (
                    agent_type is AgentType.DIRECTIVE_RAG
                    and not failure_progress_emitted
                ):
                    for event in to_agui_events(
                        WorkflowProgressEvent(
                            stage=WorkflowStage.PREPARING_ANSWER,
                            status=WorkflowStatus.FAILED,
                            message="Directive request failed",
                        )
                    ):
                        yield encoder.encode(event)
                yield encoder.encode(
                    RunErrorEvent(
                        message="Agent run failed",
                        code="RUN_ERROR",
                    )
                )
            finally:
                await lease.release()
