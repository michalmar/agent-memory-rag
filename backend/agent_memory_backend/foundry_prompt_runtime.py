"""Remote runtime for a versioned native Foundry Prompt Agent."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from agent_contracts import (
    AgentType,
    FOUNDRY_PROMPT_VERSION,
    NormalizedAgentEvent,
    RuntimeDescriptor,
    RuntimeState,
    TurnContext,
)
from .azure_clients import get_credential
from .config import get_settings
from .foundry_runtime_base import (
    completed_events,
    server_tool_events,
    stream_response,
)


class FoundryPromptRuntime:
    def __init__(self) -> None:
        self._project = None
        self._openai = None

    async def initialize(self) -> None:
        settings = get_settings()
        if not settings.foundry_project_endpoint:
            raise RuntimeError("FOUNDRY_PROJECT_ENDPOINT is required")
        from azure.ai.projects.aio import AIProjectClient

        self._project = AIProjectClient(
            endpoint=settings.foundry_project_endpoint,
            credential=get_credential(),
        )
        self._openai = self._project.get_openai_client()

    async def close(self) -> None:
        if self._openai is not None:
            await self._openai.close()
            self._openai = None
        if self._project is not None:
            await self._project.close()
            self._project = None

    def _require_openai(self) -> Any:
        if self._openai is None:
            raise RuntimeError("Prompt Agent runtime is not initialized")
        return self._openai

    @staticmethod
    def _headers(user_id: str) -> dict[str, str]:
        return {"x-ms-user-identity": user_id}

    async def create_state(
        self,
        application_conversation_id: str,
        authenticated_user_id: str,
        seed_messages: list[dict[str, str]] | None = None,
    ) -> RuntimeState:
        settings = get_settings()
        conversation = await self._require_openai().conversations.create(
            items=seed_messages or [],
            extra_headers=self._headers(authenticated_user_id),
        )
        return RuntimeState(
            descriptor=RuntimeDescriptor(
                agent_type=AgentType.FOUNDRY_PROMPT,
                physical_agent_name=settings.foundry_prompt_agent_name,
                release_id=settings.agent_release_id,
                prompt_version=FOUNDRY_PROMPT_VERSION,
            ),
            foundry_conversation_id=conversation.id,
        )

    async def stream_turn(
        self, message: str, context: TurnContext
    ) -> AsyncIterator[NormalizedAgentEvent]:
        state = context.runtime_state
        if not state.foundry_conversation_id:
            raise RuntimeError("Prompt Agent conversation mapping is missing")
        settings = get_settings()
        completed_response = None
        async for event in stream_response(
            self._require_openai(),
            input_value=message,
            conversation_id=state.foundry_conversation_id,
            extra_body={
                "agent_reference": {
                    "name": settings.foundry_prompt_agent_name,
                    "type": "agent_reference",
                }
            },
            extra_headers=self._headers(context.authenticated_user_id),
            timeout=settings.agent_request_timeout_seconds,
        ):
            if isinstance(event, tuple):
                completed_response = event[1]
            else:
                yield event
        if completed_response is None:
            raise RuntimeError("Prompt Agent response did not complete")

        state.last_response_id = getattr(completed_response, "id", None)
        for event in server_tool_events(completed_response):
            yield event
        for event in completed_events(completed_response):
            yield event

    async def delete_state(
        self, state: RuntimeState, authenticated_user_id: str
    ) -> None:
        if state.foundry_conversation_id:
            await self._require_openai().conversations.delete(
                conversation_id=state.foundry_conversation_id,
                extra_headers=self._headers(authenticated_user_id),
            )

    async def health_check(self) -> None:
        settings = get_settings()
        if not settings.foundry_prompt_enabled:
            raise RuntimeError("Prompt Agent is disabled")
        if self._project is None or self._openai is None:
            raise RuntimeError("Prompt Agent runtime is not initialized")
