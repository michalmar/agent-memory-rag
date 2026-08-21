"""Remote runtime for the Foundry Hosted Microsoft Agent Framework agent."""

from __future__ import annotations

import asyncio
import logging
import math
import re
import unicodedata
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import replace
from typing import Any
from urllib.parse import quote, urlsplit

from agent_contracts import (
    AgentType,
    Citation,
    CitationsEvent,
    InnerStateStatus,
    MandatoryStatus,
    NormalizedAgentEvent,
    RuntimeDescriptor,
    RuntimeState,
    TextDeltaEvent,
    ToolResultEvent,
    ToolStartedEvent,
    TurnContext,
    WorkflowHeartbeatEvent,
    WorkflowProgressEvent,
    WorkflowStage,
    WorkflowStatus,
)
from directive_contracts import normalize_directive_id
from .azure_clients import get_credential
from .foundry_runtime_base import (
    completed_events,
    server_tool_events,
    stream_response,
)

_PREVIEW_HEADERS = {"Foundry-Features": "HostedAgents=V1Preview"}
_HEALTH_INPUT = "Health check. Reply exactly OK without calling tools."
_HEALTH_USER_ID = "runtime-health-probe"
_PROBE_CLEANUP_ATTEMPTS = 3
_PROBE_CLEANUP_BACKOFF_SECONDS = 0.5
_DEFAULT_PROGRESS_HEARTBEAT_SECONDS = 10.0
_PROJECT_INNER_STATE_SCHEMA_VERSION = 7
logger = logging.getLogger("foundry_hosted_maf")
_SECTION_REFERENCE = re.compile(
    r"(?<!\w)(?P<label>"
    r"sections?|articles?|chapters?|secs?\.?|arts?\.?|"
    r"sekc(?:e|í)|čl(?:ánek|ánky|ánku|ánků|\.?)|"
    r"cl(?:anek|anky|anku|anku|\.?)|"
    r"kap(?:\.?)|kapitol(?:a|y|u|ou)"
    r")(?:\s+(?:number|no\.?))?\s+"
    r"(?P<values>"
    r"\d+(?:\.\d+)*"
    r"(?:\s*(?:,|/|&|and|or|a|nebo|či)\s*\d+(?:\.\d+)*)*"
    r")",
    re.IGNORECASE,
)
_SECTION_NUMBER = re.compile(r"\d+(?:\.\d+)*")

_TOOL_STAGES = {
    "get_directive": WorkflowStage.RESOLVING,
    "search_directives": WorkflowStage.SEARCHING,
    "get_directive_content": WorkflowStage.LOADING_CONTENT,
    "get_user_directive_mandates": WorkflowStage.CHECKING_MANDATORY_STATUS,
}
_STAGE_MESSAGES = {
    WorkflowStage.RESOLVING: "Resolving directive scope",
    WorkflowStage.SEARCHING: "Searching published directives",
    WorkflowStage.LOADING_CONTENT: "Loading directive content",
    WorkflowStage.FOLLOWING_REFERENCES: "Following directive references",
    WorkflowStage.COMPARING_VERSIONS: "Comparing directive versions",
    WorkflowStage.CHECKING_MANDATORY_STATUS: "Checking mandatory status",
    WorkflowStage.VERIFYING_COVERAGE: "Verifying source coverage",
    WorkflowStage.PREPARING_ANSWER: "Preparing answer",
}


class FoundryHostedMafRuntime:
    def __init__(
        self,
        *,
        agent_type: AgentType,
        project_endpoint: str,
        physical_agent_name: str,
        physical_agent_endpoint: str,
        release_id: str,
        prompt_version: str,
        request_timeout_seconds: float,
        progress_heartbeat_seconds: float = _DEFAULT_PROGRESS_HEARTBEAT_SECONDS,
    ) -> None:
        if agent_type is AgentType.FOUNDRY_PROMPT:
            raise ValueError("Hosted MAF runtime cannot use the Prompt Agent type")
        if not project_endpoint:
            raise ValueError("Foundry project endpoint is required")
        if not physical_agent_name:
            raise ValueError("Hosted Agent name is required")
        self._validate_physical_endpoint(
            project_endpoint,
            physical_agent_endpoint,
            physical_agent_name,
        )
        if (
            not math.isfinite(request_timeout_seconds)
            or request_timeout_seconds <= 0
        ):
            raise ValueError("Hosted Agent request timeout must be positive")
        if (
            not math.isfinite(progress_heartbeat_seconds)
            or progress_heartbeat_seconds <= 0
        ):
            raise ValueError("Progress heartbeat interval must be positive")

        self._agent_type = agent_type
        self._project_endpoint = project_endpoint.rstrip("/")
        self._physical_agent_name = physical_agent_name
        self._physical_agent_endpoint = physical_agent_endpoint.rstrip("/")
        self._release_id = release_id
        self._prompt_version = prompt_version
        self._request_timeout_seconds = request_timeout_seconds
        self._progress_heartbeat_seconds = progress_heartbeat_seconds
        self._project = None
        self._openai = None
        self._model_openai = None
        self._endpoint_verified = False
        self._pending_probe_session_id: str | None = None
        self._pending_probe_conversation_id: str | None = None
        self._pending_probe_headers = dict(_PREVIEW_HEADERS)
        self._pending_probe_was_verified = False
        self._verified_probe_reclaimed = False

    async def initialize(self) -> None:
        from azure.ai.projects.aio import AIProjectClient

        self._project = AIProjectClient(
            endpoint=self._project_endpoint,
            credential=get_credential(),
            allow_preview=True,
        )
        try:
            self._openai = self._project.get_openai_client(
                agent_name=self._physical_agent_name,
                base_url=self._physical_agent_endpoint,
                default_query={"api-version": "v1"},
            )
            if self._agent_type is AgentType.DIRECTIVE_RAG:
                self._model_openai = self._project.get_openai_client()
                await self._verify_stateful_endpoint()
            else:
                await self._verify_responses_endpoint()
        except (Exception, asyncio.CancelledError):
            try:
                await self.close()
            except Exception:
                logger.exception(
                    "Failed to close a partially initialized Hosted Agent runtime"
                )
            raise

    async def close(self) -> None:
        openai = self._openai
        model_openai = self._model_openai
        project = self._project
        self._endpoint_verified = False
        errors: list[Exception] = []
        cancellation: asyncio.CancelledError | None = None
        if (
            self._pending_probe_conversation_id
            or self._pending_probe_session_id
        ):
            pending_probe_was_verified = self._pending_probe_was_verified
            if (
                self._pending_probe_conversation_id
                and model_openai is not None
            ):
                try:
                    await self._cleanup_probe_conversation()
                except asyncio.CancelledError as exc:
                    cancellation = exc
                except Exception as exc:
                    errors.append(exc)
            if self._pending_probe_session_id and project is not None:
                try:
                    await self._cleanup_probe_session()
                except asyncio.CancelledError as exc:
                    cancellation = cancellation or exc
                except Exception as exc:
                    errors.append(exc)
            if (
                not self._pending_probe_conversation_id
                and not self._pending_probe_session_id
                and not errors
                and cancellation is None
            ):
                self._verified_probe_reclaimed = pending_probe_was_verified

        self._openai = None
        self._model_openai = None
        self._project = None
        closed_clients: list[Any] = []
        for client in (openai, model_openai, project):
            if client is None:
                continue
            if any(client is closed for closed in closed_clients):
                continue
            closed_clients.append(client)
            try:
                await client.close()
            except asyncio.CancelledError as exc:
                cancellation = cancellation or exc
            except Exception as exc:
                errors.append(exc)
        if cancellation is not None:
            raise cancellation
        if errors:
            raise RuntimeError(
                "Failed to close Hosted Agent runtime cleanly"
            ) from errors[0]

    def _require_openai(self) -> Any:
        if self._openai is None:
            raise RuntimeError("Hosted MAF runtime is not initialized")
        return self._openai

    def _require_model_openai(self) -> Any:
        if self._model_openai is None:
            raise RuntimeError("Hosted MAF model state client is not initialized")
        return self._model_openai

    def _inner_state_openai(self, state: RuntimeState) -> Any:
        if state.schema_version >= _PROJECT_INNER_STATE_SCHEMA_VERSION:
            return self._require_model_openai()
        return self._require_openai()

    @staticmethod
    def _headers(user_id: str) -> dict[str, str]:
        return {**_PREVIEW_HEADERS, "x-ms-user-identity": user_id}

    @staticmethod
    def _validate_physical_endpoint(
        project_endpoint: str,
        endpoint: str,
        agent_name: str,
    ) -> None:
        normalized_project_endpoint = project_endpoint.rstrip("/")
        normalized_endpoint = endpoint.rstrip("/")
        parsed = urlsplit(normalized_endpoint)
        agent_suffix = (
            f"/agents/{quote(agent_name, safe='')}/endpoint/protocols/openai"
        )
        expected_endpoint = f"{normalized_project_endpoint}{agent_suffix}"
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.query
            or parsed.fragment
            or normalized_endpoint != expected_endpoint
        ):
            raise ValueError(
                "Hosted Agent endpoint must be the agent-specific HTTPS "
                f"OpenAI protocol root {expected_endpoint}"
            )

    async def _verify_responses_endpoint(self) -> None:
        if self._verified_probe_reclaimed:
            self._verified_probe_reclaimed = False
            self._endpoint_verified = True
            return
        if self._pending_probe_session_id:
            prior_response_was_verified = self._pending_probe_was_verified
            await self._cleanup_probe_session()
            if prior_response_was_verified:
                self._endpoint_verified = True
                return

        response = await self._require_openai().responses.create(
            input=_HEALTH_INPUT,
            stream=False,
            extra_headers=_PREVIEW_HEADERS,
            timeout=self._request_timeout_seconds,
        )
        model_extra = getattr(response, "model_extra", None) or {}
        session_id = model_extra.get("agent_session_id")
        if isinstance(session_id, str) and session_id:
            self._pending_probe_session_id = session_id
            self._pending_probe_was_verified = False
        try:
            if not getattr(response, "id", None):
                raise RuntimeError(
                    "Hosted Agent endpoint probe returned no response ID"
                )
            if getattr(response, "status", "completed") != "completed":
                raise RuntimeError("Hosted Agent endpoint probe did not complete")
        except Exception:
            await self._cleanup_probe_session(suppress_failure=True)
            raise

        self._pending_probe_was_verified = True
        await self._cleanup_probe_session()
        self._endpoint_verified = True

    async def _verify_stateful_endpoint(self) -> None:
        if self._verified_probe_reclaimed:
            self._verified_probe_reclaimed = False
            self._endpoint_verified = True
            return
        if (
            self._pending_probe_conversation_id
            or self._pending_probe_session_id
        ):
            prior_probe_was_verified = self._pending_probe_was_verified
            if self._pending_probe_conversation_id:
                await self._cleanup_probe_conversation()
            if self._pending_probe_session_id:
                await self._cleanup_probe_session()
            if prior_probe_was_verified:
                self._endpoint_verified = True
                return

        probe_headers = self._headers(_HEALTH_USER_ID)
        self._pending_probe_headers = probe_headers
        session = await self._project.agents.create_session(
            agent_name=self._physical_agent_name,
            body={},
            headers=probe_headers,
        )
        session_id = getattr(session, "agent_session_id", None)
        if not isinstance(session_id, str) or not session_id:
            raise RuntimeError(
                "Hosted Agent state probe returned no session ID"
            )
        self._pending_probe_session_id = session_id

        conversation = await self._require_model_openai().conversations.create(
            items=[],
            extra_headers=probe_headers,
        )
        conversation_id = getattr(conversation, "id", None)
        if not isinstance(conversation_id, str) or not conversation_id:
            raise RuntimeError(
                "Hosted Agent state probe returned no conversation ID"
            )
        self._pending_probe_conversation_id = conversation_id
        self._pending_probe_was_verified = True
        await self._cleanup_probe_conversation()
        await self._cleanup_probe_session()
        self._endpoint_verified = True

    async def _cleanup_probe_conversation(self) -> None:
        conversation_id = self._pending_probe_conversation_id
        if not conversation_id:
            return
        model_openai = self._model_openai
        if model_openai is None:
            raise RuntimeError(
                "Hosted Agent model state client is unavailable for probe cleanup"
            )

        for attempt in range(1, _PROBE_CLEANUP_ATTEMPTS + 1):
            try:
                await model_openai.conversations.delete(
                    conversation_id=conversation_id,
                    extra_headers=self._pending_probe_headers,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if getattr(exc, "status_code", None) == 404:
                    self._pending_probe_conversation_id = None
                    self._reset_probe_tracking_if_clear()
                    return
                if attempt == _PROBE_CLEANUP_ATTEMPTS:
                    raise RuntimeError(
                        "Hosted Agent health-probe conversation cleanup failed"
                    ) from exc
                await asyncio.sleep(
                    _PROBE_CLEANUP_BACKOFF_SECONDS * (2 ** (attempt - 1))
                )
            else:
                self._pending_probe_conversation_id = None
                self._reset_probe_tracking_if_clear()
                return

    async def _cleanup_probe_session(
        self,
        *,
        suppress_failure: bool = False,
    ) -> None:
        session_id = self._pending_probe_session_id
        if not session_id:
            return
        if self._project is None:
            raise RuntimeError(
                "Hosted Agent project client is unavailable for probe cleanup"
            )

        for attempt in range(1, _PROBE_CLEANUP_ATTEMPTS + 1):
            try:
                await self._project.agents.delete_session(
                    agent_name=self._physical_agent_name,
                    session_id=session_id,
                    headers=self._pending_probe_headers,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if getattr(exc, "status_code", None) == 404:
                    self._pending_probe_session_id = None
                    self._reset_probe_tracking_if_clear()
                    return
                if attempt == _PROBE_CLEANUP_ATTEMPTS:
                    if suppress_failure:
                        logger.exception(
                            "Failed to clean up Hosted Agent health-probe session"
                        )
                        return
                    raise RuntimeError(
                        "Hosted Agent health-probe session cleanup failed"
                    ) from exc
                await asyncio.sleep(
                    _PROBE_CLEANUP_BACKOFF_SECONDS * (2 ** (attempt - 1))
                )
            else:
                self._pending_probe_session_id = None
                self._reset_probe_tracking_if_clear()
                return

    def _reset_probe_tracking_if_clear(self) -> None:
        if (
            self._pending_probe_conversation_id
            or self._pending_probe_session_id
        ):
            return
        self._pending_probe_was_verified = False
        self._pending_probe_headers = dict(_PREVIEW_HEADERS)

    async def create_state(
        self,
        application_conversation_id: str,
        authenticated_user_id: str,
        seed_messages: list[dict[str, str]] | None = None,
    ) -> RuntimeState:
        hosted_session = await self._project.agents.create_session(
            agent_name=self._physical_agent_name,
            body={},
            headers=self._headers(authenticated_user_id),
        )
        outer_conversation = None
        try:
            outer_conversation = await self._require_openai().conversations.create(
                items=seed_messages or [],
                extra_headers=self._headers(authenticated_user_id),
            )
            inner_conversation = None
            if self._agent_type is AgentType.DIRECTIVE_RAG:
                inner_conversation = (
                    await self._require_model_openai().conversations.create(
                        items=[],
                        extra_headers=self._headers(authenticated_user_id),
                    )
                )
        except Exception:
            if outer_conversation is not None:
                try:
                    await self._require_openai().conversations.delete(
                        conversation_id=outer_conversation.id,
                        extra_headers=self._headers(authenticated_user_id),
                    )
                except Exception:
                    logger.exception(
                        "Failed to clean up an incomplete outer conversation"
                    )
            try:
                await self._project.agents.delete_session(
                    agent_name=self._physical_agent_name,
                    session_id=hosted_session.agent_session_id,
                    headers=self._headers(authenticated_user_id),
                )
            except Exception:
                logger.exception(
                    "Failed to clean up an incomplete Hosted Agent session"
                )
            raise
        return RuntimeState(
            descriptor=RuntimeDescriptor(
                agent_type=self._agent_type,
                physical_agent_name=self._physical_agent_name,
                release_id=self._release_id,
                prompt_version=self._prompt_version,
            ),
            foundry_conversation_id=outer_conversation.id,
            hosted_session_id=hosted_session.agent_session_id,
            inner_model_conversation_id=(
                inner_conversation.id if inner_conversation is not None else None
            ),
            inner_state_status=(
                InnerStateStatus.BOOTSTRAP_REQUIRED
                if inner_conversation is not None and seed_messages
                else InnerStateStatus.READY
                if inner_conversation is not None
                else None
            ),
        )

    async def allocate_inner_state(
        self,
        state: RuntimeState,
        authenticated_user_id: str,
        *,
        bootstrap_required: bool,
    ) -> str:
        if self._agent_type is not AgentType.DIRECTIVE_RAG:
            raise RuntimeError("Inner model state is directive-only")
        if state.inner_model_conversation_id:
            return state.inner_model_conversation_id
        conversation = await self._require_model_openai().conversations.create(
            items=[],
            extra_headers=self._headers(authenticated_user_id),
        )
        state.inner_model_conversation_id = conversation.id
        state.inner_state_status = (
            InnerStateStatus.BOOTSTRAP_REQUIRED
            if bootstrap_required
            else InnerStateStatus.READY
        )
        state.descriptor = RuntimeDescriptor(
            agent_type=self._agent_type,
            physical_agent_name=self._physical_agent_name,
            release_id=self._release_id,
            prompt_version=self._prompt_version,
            observed_agent_version=state.descriptor.observed_agent_version,
        )
        state.schema_version = _PROJECT_INNER_STATE_SCHEMA_VERSION
        return conversation.id

    async def recover_inner_state(
        self,
        state: RuntimeState,
        authenticated_user_id: str,
        *,
        seed_messages: list[dict[str, str]],
    ) -> str:
        if self._agent_type is not AgentType.DIRECTIVE_RAG:
            raise RuntimeError("Inner model state is directive-only")
        previous_id = state.inner_model_conversation_id
        if not previous_id:
            raise RuntimeError("Directive inner model state is missing")

        previous_openai = self._inner_state_openai(state)
        model_openai = self._require_model_openai()
        replacement = await model_openai.conversations.create(
            items=seed_messages,
            extra_headers=self._headers(authenticated_user_id),
        )
        try:
            await self._delete_conversation_if_present(
                previous_id,
                self._headers(authenticated_user_id),
                openai=previous_openai,
            )
        except Exception:
            try:
                await self._delete_conversation_if_present(
                    replacement.id,
                    self._headers(authenticated_user_id),
                    openai=model_openai,
                )
            except Exception:
                logger.exception(
                    "Failed to clean up an unused recovery conversation"
                )
            raise

        if state.inner_pending_call_id:
            state.inner_last_failed_call_id = state.inner_pending_call_id
        state.inner_model_conversation_id = replacement.id
        state.inner_state_status = InnerStateStatus.READY
        state.inner_pending_call_id = None
        state.inner_pending_started_at = None
        state.inner_pending_revision = None
        state.inner_pending_outcome = None
        state.inner_recovery_started_at = None
        state.inner_state_revision += 1
        state.schema_version = _PROJECT_INNER_STATE_SCHEMA_VERSION
        return replacement.id

    async def delete_inner_state(
        self,
        inner_conversation_id: str,
        authenticated_user_id: str,
    ) -> None:
        await self._delete_conversation_if_present(
            inner_conversation_id,
            self._headers(authenticated_user_id),
            openai=self._require_model_openai(),
        )

    async def _delete_conversation_if_present(
        self,
        conversation_id: str,
        headers: dict[str, str],
        *,
        openai: Any | None = None,
    ) -> None:
        try:
            client = openai if openai is not None else self._require_openai()
            await client.conversations.delete(
                conversation_id=conversation_id,
                extra_headers=headers,
            )
        except Exception as exc:
            if getattr(exc, "status_code", None) != 404:
                raise

    async def stream_turn(
        self, message: str, context: TurnContext
    ) -> AsyncIterator[NormalizedAgentEvent]:
        state = context.runtime_state
        if not state.foundry_conversation_id:
            raise RuntimeError("Hosted Agent conversation mapping is missing")
        directive_runtime = self._agent_type is AgentType.DIRECTIVE_RAG
        active_stage = WorkflowStage.RESOLVING
        live_tool_names: dict[str, str] = {}
        answer_started = False
        assistant_text_parts: list[str] = []
        if directive_runtime:
            yield WorkflowProgressEvent(
                stage=active_stage,
                status=WorkflowStatus.STARTED,
                message=_STAGE_MESSAGES[active_stage],
            )

        try:
            completed_response = None
            response_events = stream_response(
                self._require_openai(),
                input_value=message,
                conversation_id=state.foundry_conversation_id,
                extra_headers=self._headers(context.authenticated_user_id),
                extra_body={"agent_session_id": state.hosted_session_id},
                timeout=self._request_timeout_seconds,
                emit_tool_lifecycle=directive_runtime,
            )
            if directive_runtime:
                iterator = response_events.__aiter__()
                pending: asyncio.Task[Any] | None = None
                try:
                    while True:
                        if pending is None:
                            pending = asyncio.create_task(anext(iterator))
                        done, _ = await asyncio.wait(
                            {pending},
                            timeout=self._progress_heartbeat_seconds,
                        )
                        if not done:
                            yield WorkflowHeartbeatEvent(
                                stage=active_stage,
                                message=_STAGE_MESSAGES[active_stage],
                            )
                            continue
                        try:
                            event = pending.result()
                        except StopAsyncIteration:
                            pending = None
                            break
                        pending = None
                        if isinstance(event, tuple):
                            if event[0] == "completed_response":
                                completed_response = event[1]
                            continue
                        if isinstance(event, ToolStartedEvent):
                            live_tool_names[event.call_id] = event.tool_name
                            next_stage = _TOOL_STAGES.get(event.tool_name)
                            if next_stage is not None:
                                active_stage = next_stage
                                yield WorkflowProgressEvent(
                                    stage=active_stage,
                                    status=WorkflowStatus.IN_PROGRESS,
                                    message=_STAGE_MESSAGES[active_stage],
                                )
                        elif isinstance(event, TextDeltaEvent):
                            assistant_text_parts.append(event.delta)
                            if not answer_started:
                                answer_started = True
                                active_stage = WorkflowStage.PREPARING_ANSWER
                                yield WorkflowProgressEvent(
                                    stage=active_stage,
                                    status=WorkflowStatus.IN_PROGRESS,
                                    message=_STAGE_MESSAGES[active_stage],
                                )
                        yield event
                finally:
                    if pending is not None:
                        pending.cancel()
                        with suppress(asyncio.CancelledError, StopAsyncIteration):
                            await pending
                    with suppress(RuntimeError):
                        await response_events.aclose()
            else:
                async for event in response_events:
                    if isinstance(event, tuple):
                        completed_response = event[1]
                    else:
                        yield event

            if completed_response is None:
                raise RuntimeError("Hosted Agent response did not complete")
            state.last_response_id = getattr(completed_response, "id", None)
            model_extra = getattr(completed_response, "model_extra", None) or {}
            returned_session_id = model_extra.get("agent_session_id")
            if returned_session_id:
                state.hosted_session_id = returned_session_id

            tool_events = server_tool_events(
                completed_response,
                include_function_calls=True,
                started_call_ids=live_tool_names,
            )
            directive_citations: tuple[Citation, ...] = ()
            coverage: tuple[int, int] | None = None
            if directive_runtime:
                tool_events, directive_citations, coverage = (
                    _enrich_directive_tool_events(
                        tool_events,
                        live_tool_names,
                        "".join(assistant_text_parts),
                    )
                )
            for event in tool_events:
                if (
                    directive_runtime
                    and not answer_started
                    and isinstance(event, ToolStartedEvent)
                ):
                    next_stage = _TOOL_STAGES.get(event.tool_name)
                    if next_stage is not None:
                        active_stage = next_stage
                        yield WorkflowProgressEvent(
                            stage=active_stage,
                            status=WorkflowStatus.IN_PROGRESS,
                            message=_STAGE_MESSAGES[active_stage],
                        )
                yield event
            if directive_runtime and coverage is not None:
                active_stage = WorkflowStage.VERIFYING_COVERAGE
                yield WorkflowProgressEvent(
                    stage=active_stage,
                    status=WorkflowStatus.IN_PROGRESS,
                    message=_STAGE_MESSAGES[active_stage],
                    completed_count=coverage[0],
                    total_count=coverage[1],
                )
            if directive_runtime:
                yield CitationsEvent(
                    citations=directive_citations,
                    authoritative=True,
                )
            for event in completed_events(completed_response):
                if directive_runtime and isinstance(event, CitationsEvent):
                    continue
                yield event
            if directive_runtime:
                yield WorkflowProgressEvent(
                    stage=WorkflowStage.PREPARING_ANSWER,
                    status=WorkflowStatus.COMPLETED,
                    message="Answer ready",
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            if directive_runtime:
                yield WorkflowProgressEvent(
                    stage=active_stage,
                    status=WorkflowStatus.FAILED,
                    message="Directive request failed",
                )
            raise

    async def delete_state(
        self, state: RuntimeState, authenticated_user_id: str
    ) -> None:
        headers = self._headers(authenticated_user_id)
        if state.inner_model_conversation_id:
            await self._delete_conversation_if_present(
                state.inner_model_conversation_id,
                headers,
                openai=self._inner_state_openai(state),
            )
        if state.foundry_conversation_id:
            await self._delete_conversation_if_present(
                state.foundry_conversation_id,
                headers,
            )
        if state.hosted_session_id:
            try:
                await self._project.agents.delete_session(
                    agent_name=self._physical_agent_name,
                    session_id=state.hosted_session_id,
                    headers=headers,
                )
            except Exception as exc:
                if getattr(exc, "status_code", None) != 404:
                    raise

    async def health_check(self) -> None:
        if self._project is None or self._openai is None:
            raise RuntimeError("Hosted MAF runtime is not initialized")
        if (
            self._agent_type is AgentType.DIRECTIVE_RAG
            and self._model_openai is None
        ):
            raise RuntimeError("Hosted MAF model state client is not initialized")
        if not self._endpoint_verified:
            raise RuntimeError("Hosted MAF Responses endpoint is not verified")


def _enrich_directive_tool_events(
    events: list[NormalizedAgentEvent],
    known_tool_names: dict[str, str],
    assistant_text: str,
) -> tuple[
    list[NormalizedAgentEvent],
    tuple[Citation, ...],
    tuple[int, int] | None,
]:
    tool_names = dict(known_tool_names)
    for event in events:
        if isinstance(event, ToolStartedEvent):
            tool_names[event.call_id] = event.tool_name

    statuses: dict[str, MandatoryStatus] = {}
    snapshot_id: str | None = None
    coverage: tuple[int, int] | None = None
    for event in events:
        if not isinstance(event, ToolResultEvent):
            continue
        if (
            tool_names.get(event.call_id)
            == "get_user_directive_mandates"
        ):
            status_values = event.result.data.get("statuses")
            final_statuses: dict[str, MandatoryStatus] = {}
            if isinstance(status_values, dict):
                for directive_id, status in status_values.items():
                    if not isinstance(directive_id, str):
                        continue
                    try:
                        final_statuses[directive_id] = MandatoryStatus(status)
                    except (TypeError, ValueError):
                        final_statuses[directive_id] = MandatoryStatus.UNKNOWN
            statuses = final_statuses
            candidate_snapshot_id = event.result.data.get("snapshot_id")
            snapshot_id = (
                candidate_snapshot_id
                if isinstance(candidate_snapshot_id, str)
                else None
            )
        candidate_coverage = _coverage_counts(event.result.data)
        if candidate_coverage is not None:
            coverage = candidate_coverage

    enriched: list[NormalizedAgentEvent] = []
    citations: list[Citation] = []
    citation_keys: set[tuple[Any, ...]] = set()
    for event in events:
        if not isinstance(event, ToolResultEvent):
            enriched.append(event)
            continue
        result_citations = tuple(
            replace(
                citation,
                mandatory_status=statuses.get(
                    citation.directive_id or "",
                    MandatoryStatus.UNKNOWN,
                ),
                mandate_snapshot_id=(
                    snapshot_id
                    if citation.directive_id in statuses
                    else citation.mandate_snapshot_id
                ),
            )
            if citation.directive_id
            else citation
            for citation in event.result.citations
        )
        enriched.append(
            replace(
                event,
                result=replace(
                    event.result,
                    citations=result_citations,
                ),
            )
        )
        for citation in result_citations:
            if not citation.directive_id:
                continue
            key = (
                citation.ref_id,
                citation.directive_version_id,
                citation.section_id,
                citation.page_from,
                citation.page_to,
            )
            if key in citation_keys:
                continue
            citation_keys.add(key)
            citations.append(citation)
    return (
        enriched,
        _select_final_directive_citations(
            citations,
            assistant_text=assistant_text,
            statuses=statuses,
        ),
        coverage,
    )


def _select_final_directive_citations(
    citations: list[Citation],
    *,
    assistant_text: str,
    statuses: dict[str, MandatoryStatus],
) -> tuple[Citation, ...]:
    exact_mentions = [
        (position, index, citation)
        for index, citation in enumerate(citations)
        for position in _citation_marker_positions(
                assistant_text,
                citation.ref_id,
            )
    ]
    mandated = tuple(
        citation
        for citation in citations
        if citation.directive_id and citation.directive_id in statuses
    )
    explicit_directive_ids = list(statuses)
    for _, _, citation in exact_mentions:
        if (
            citation.directive_id
            and citation.directive_id not in explicit_directive_ids
        ):
            explicit_directive_ids.append(citation.directive_id)
    explicit_citations = _select_explicit_section_citations(
        tuple(citations),
        assistant_text=assistant_text,
        directive_ids=tuple(explicit_directive_ids),
    )
    if exact_mentions:
        exact_mentions.sort(key=lambda item: (item[0], item[1]))
        exact_citations = _select_exact_marker_citations(
            exact_mentions,
            available_citations=tuple(citations),
            assistant_text=assistant_text,
        )
        selected = _merge_citation_groups(
            exact_citations,
            explicit_citations,
        )
        return selected

    if mandated:
        selected = _filter_citations_by_section_labels(
            explicit_citations
            or _select_mandated_directive_citations(
                mandated,
                directive_ids=tuple(statuses),
            ),
            assistant_text=assistant_text,
        )
        if selected:
            return selected
        return (_to_document_citation(mandated[0]),)
    if len(citations) == 1:
        return _filter_citations_by_section_labels(
            (citations[0],),
            assistant_text=assistant_text,
        )
    return ()


def _citation_marker_positions(text: str, ref_id: str) -> tuple[int, ...]:
    escaped_ref_id = re.escape(ref_id)
    patterns = (
        re.escape(f"[[cite:{ref_id}]]"),
        re.escape(f"[{ref_id}]"),
        re.escape(f"【{ref_id}】"),
        rf"{escaped_ref_id}(?=|)",
        rf"【\d+:{escaped_ref_id}†",
    )
    positions = {
        match.start()
        for pattern in patterns
        for match in re.finditer(pattern, text)
    }
    return tuple(sorted(positions))


def _explicit_section_numbers(
    text: str,
    *,
    citation: Citation | None = None,
) -> set[str]:
    numbers: set[str] = set()
    for reference in _SECTION_REFERENCE.finditer(text):
        line_start = text.rfind("\n", 0, reference.start()) + 1
        line_end = text.find("\n", reference.end())
        if line_end < 0:
            line_end = len(text)
        normalized_line = _normalize_evidence_text(text[line_start:line_end])
        if not _is_section_reference(reference):
            identifies_citation = bool(
                citation
                and _reference_identifies_citation(
                    text,
                    reference,
                    citation,
                    normalized_line=normalized_line,
                    line_end=line_end,
                )
            )
            has_other_identity = bool(
                citation
                and (
                    _reference_has_following_identity_hint(
                        text,
                        reference,
                        line_end=line_end,
                    )
                    or _reference_has_preceding_identity_hint(text, reference)
                )
            )
            if not identifies_citation and has_other_identity:
                continue
        numbers.update(
            _normalize_section_number(number)
            for number in _SECTION_NUMBER.findall(reference.group("values"))
        )
    return numbers


def _normalize_section_number(value: str) -> str:
    return value.strip().rstrip(".")


def _is_section_reference(reference: re.Match[str]) -> bool:
    normalized_label = _normalize_evidence_text(reference.group("label"))
    return normalized_label.startswith(("section", "sec", "sekc"))


def _is_source_evidence_line(normalized_line: str) -> bool:
    return re.search(r"(?<!\w)(?:source|zdroj)(?!\w)", normalized_line) is not None


def _reference_identifies_citation(
    text: str,
    reference: re.Match[str],
    citation: Citation,
    *,
    normalized_line: str,
    line_end: int,
) -> bool:
    if _is_source_evidence_line(normalized_line):
        line_start = text.rfind("\n", 0, reference.start()) + 1
        segment_start = text.rfind(
            ";",
            line_start,
            reference.start(),
        ) + 1
        preceding_commas = tuple(
            match.start()
            for match in re.finditer(
                ",",
                text[segment_start:reference.start()],
            )
        )
        local_start = (
            segment_start + preceding_commas[-2] + 1
            if len(preceding_commas) >= 2
            else segment_start
        )
        local_prefix = text[local_start:reference.start()]
        if _contains_concrete_directive_identity(local_prefix):
            return _line_identifies_citation(
                _normalize_evidence_text(local_prefix),
                citation,
            )
        segment_end = text.find(";", reference.end(), line_end)
        if segment_end < 0:
            segment_end = line_end
        normalized_segment = _normalize_evidence_text(
            text[segment_start:segment_end]
        )
        if _line_identifies_citation(normalized_segment, citation):
            return True
    suffix_end = _reference_suffix_end(text, reference, line_end=line_end)
    normalized_suffix = _normalize_evidence_text(
        text[reference.end() : suffix_end]
    )
    if _line_identifies_citation(normalized_suffix, citation):
        return True
    prefix_start = _reference_prefix_start(text, reference)
    normalized_prefix = _normalize_evidence_text(
        text[prefix_start:reference.start()]
    )
    return _line_identifies_citation(normalized_prefix, citation)


def _select_explicit_section_citations(
    citations: tuple[Citation, ...],
    *,
    assistant_text: str,
    directive_ids: tuple[str, ...],
) -> tuple[Citation, ...]:
    selected: list[Citation] = []
    selected_keys: set[tuple[str | None, str | None]] = set()
    allowed_directive_ids = set(directive_ids)
    for reference in _SECTION_REFERENCE.finditer(assistant_text):
        line_start = assistant_text.rfind("\n", 0, reference.start()) + 1
        line_end = assistant_text.find("\n", reference.end())
        if line_end < 0:
            line_end = len(assistant_text)
        normalized_line = _normalize_evidence_text(
            assistant_text[line_start:line_end]
        )
        is_section_label = _is_section_reference(reference)
        for number_match in _SECTION_NUMBER.finditer(reference.group("values")):
            section_number = _normalize_section_number(number_match.group())
            candidates = [
                citation
                for citation in citations
                if (
                    citation.directive_id in allowed_directive_ids
                    and citation.section_number
                    and _normalize_section_number(citation.section_number)
                    == section_number
                )
            ]
            if not candidates:
                continue
            identified = [
                citation
                for citation in candidates
                if _reference_identifies_citation(
                    assistant_text,
                    reference,
                    citation,
                    normalized_line=normalized_line,
                    line_end=line_end,
                )
            ]
            identified = _most_specific_identified_citations(
                identified,
                normalized_line=normalized_line,
            )
            candidate_directive_ids = {
                citation.directive_id for citation in candidates
            }
            conflicting_directive_identity = any(
                citation.directive_id not in allowed_directive_ids
                and _line_contains_directive_id(normalized_line, citation)
                for citation in citations
            )
            has_following_identity_hint = _reference_has_following_identity_hint(
                assistant_text,
                reference,
                line_end=line_end,
            )
            has_preceding_identity_hint = _reference_has_preceding_identity_hint(
                assistant_text,
                reference,
            )
            if identified:
                candidates = identified
            elif not (
                is_section_label
                and len(allowed_directive_ids) == 1
                and len(candidate_directive_ids) == 1
                and not conflicting_directive_identity
                and not has_following_identity_hint
                and not has_preceding_identity_hint
            ):
                continue
            candidates.sort(key=_citation_selection_priority)
            citation = candidates[0]
            key = (citation.directive_id, citation.section_id)
            if key in selected_keys:
                continue
            selected_keys.add(key)
            selected.append(citation)
    return tuple(selected)


def _reference_has_following_identity_hint(
    text: str,
    reference: re.Match[str],
    *,
    line_end: int,
) -> bool:
    suffix_end = _reference_suffix_end(text, reference, line_end=line_end)
    suffix = text[reference.end() : suffix_end].lstrip()
    identity_suffix = suffix.split("[", 1)[0].strip()
    normalized_suffix = _normalize_evidence_text(suffix)
    has_identity_cue = re.match(
        (
            r"^(?:of|from|under|in|dle|podle|"
            r"directive|policy|směrnice|smernice|předpisu|predpisu)(?!\w)"
        ),
        normalized_suffix,
    ) is not None
    follows_punctuation = suffix.startswith(("(", ","))
    if not has_identity_cue and not follows_punctuation:
        candidate = re.match(r"[^\s,;:()]+", identity_suffix)
        return bool(
            candidate
            and _contains_concrete_directive_identity(candidate.group())
        )
    if follows_punctuation:
        suffix = suffix[1:].lstrip()
        candidate = re.match(r"[^\s,;:()]+", suffix)
        return bool(
            candidate
            and _contains_concrete_directive_identity(candidate.group())
        )
    return _contains_concrete_directive_identity(identity_suffix)


def _reference_suffix_end(
    text: str,
    reference: re.Match[str],
    *,
    line_end: int,
) -> int:
    suffix = text[reference.end() : line_end]
    boundary = re.search(r"(?:[.!?]\s+|;)", suffix)
    next_reference = _SECTION_REFERENCE.search(
        text,
        reference.end(),
        line_end,
    )
    candidates = [
        position
        for position in (
            reference.end() + boundary.start() if boundary else None,
            next_reference.start() if next_reference else None,
        )
        if position is not None
    ]
    return min(candidates, default=line_end)


def _reference_has_preceding_identity_hint(
    text: str,
    reference: re.Match[str],
) -> bool:
    prefix_start = _reference_prefix_start(text, reference)
    return _contains_concrete_directive_identity(
        text[prefix_start:reference.start()]
    )


def _reference_prefix_start(
    text: str,
    reference: re.Match[str],
) -> int:
    reference_start = reference.start()
    prefix_start = max(
        text.rfind(";", 0, reference_start) + 1,
        text.rfind("\n", 0, reference_start) + 1,
    )
    prefix = text[prefix_start:reference_start]
    boundaries = tuple(
        re.finditer(
            r"(?:[.!?]\s+|(?<![\w/._-])(?:and|or|a|nebo|či)(?![\w/._-])\s+)",
            prefix,
            re.IGNORECASE,
        )
    )
    if boundaries:
        prefix_start += boundaries[-1].end()
    return prefix_start


def _contains_concrete_directive_identity(value: str) -> bool:
    normalized = unicodedata.normalize("NFKC", value)
    if re.search(
        r"(?<!\w)[A-Z][^\W\d_]+(?:\s+[A-Z][^\W_]*)+(?!\w)",
        normalized,
    ):
        return True
    for match in re.finditer(
        r"(?<!\w)[^\W_]+(?:[/._-][^\W_]+)*(?!\w)",
        normalized,
    ):
        token = match.group()
        try:
            normalize_directive_id(token)
        except (TypeError, ValueError):
            continue
        if (
            any(char.isdigit() or char in "/._-" for char in token)
            or (len(token) >= 2 and token.isupper())
        ):
            return True
    return False


def _citation_selection_priority(citation: Citation) -> tuple[int, str]:
    strategy_priority = {
        "section_batch": 0,
        "focused": 1,
        "discovery": 2,
    }
    return (
        strategy_priority.get(citation.retrieval_strategy or "", 3),
        citation.ref_id,
    )


def _merge_citation_groups(
    *groups: tuple[Citation, ...],
) -> tuple[Citation, ...]:
    selected: list[Citation] = []
    selected_keys: set[tuple[str, str | None, str | None]] = set()
    for group in groups:
        for citation in group:
            key = (
                citation.ref_id,
                citation.directive_version_id,
                citation.section_id,
            )
            if key in selected_keys:
                continue
            selected_keys.add(key)
            selected.append(citation)
    return tuple(selected)


def _to_document_citation(
    citation: Citation,
    *,
    preserve_ref_id: bool = False,
) -> Citation:
    return replace(
        citation,
        ref_id=(
            citation.ref_id
            if preserve_ref_id
            else (
                citation.directive_version_id
                or citation.directive_id
                or citation.ref_id
            )
        ),
        section_id=None,
        section_number=None,
        section_title=None,
        page_from=None,
        page_to=None,
        citation_scope="document",
    )


def _select_exact_marker_citations(
    exact_mentions: list[tuple[int, int, Citation]],
    *,
    available_citations: tuple[Citation, ...],
    assistant_text: str,
) -> tuple[Citation, ...]:
    selected: list[Citation] = []
    rejected_count = 0
    for marker_position, _, citation in exact_mentions:
        context = _local_marker_context(assistant_text, marker_position)
        referenced_sections = _explicit_section_numbers(
            context,
            citation=citation,
        )
        conflicting_identity = _context_identifies_other_directive(
            context,
            citation=citation,
            available_citations=available_citations,
        )
        if not conflicting_identity and (
            not referenced_sections
            or (
                citation.section_number
                and _normalize_section_number(citation.section_number)
                in referenced_sections
            )
        ):
            selected.append(citation)
            continue

        directive_id = citation.directive_id
        rejected_count += 1
        if directive_id:
            selected.append(
                _to_document_citation(
                    citation,
                    preserve_ref_id=True,
                )
            )

    if rejected_count:
        logger.warning(
            "Directive citation markers rejected because local section labels "
            "do not match citation metadata rejected_marker_count=%d",
            rejected_count,
        )
    merged = _merge_citation_groups(tuple(selected))
    return tuple(
        citation
        for citation in merged
        if citation.citation_scope == "document"
    ) + tuple(
        citation
        for citation in merged
        if citation.citation_scope != "document"
    )


def _context_identifies_other_directive(
    text: str,
    *,
    citation: Citation,
    available_citations: tuple[Citation, ...],
) -> bool:
    matching_identity_found = False
    conflicting_identity_found = False
    for reference in _SECTION_REFERENCE.finditer(text):
        if citation.section_number and (
            _normalize_section_number(citation.section_number)
            not in {
                _normalize_section_number(number)
                for number in _SECTION_NUMBER.findall(
                    reference.group("values")
                )
            }
        ):
            continue
        line_end = text.find("\n", reference.end())
        if line_end < 0:
            line_end = len(text)
        line_start = text.rfind("\n", 0, reference.start()) + 1
        normalized_line = _normalize_evidence_text(text[line_start:line_end])
        identified_candidates = _most_specific_identified_citations(
            [
                candidate
                for candidate in (citation, *available_citations)
                if _reference_identifies_citation(
                    text,
                    reference,
                    candidate,
                    normalized_line=normalized_line,
                    line_end=line_end,
                )
            ],
            normalized_line=normalized_line,
        )
        if citation in identified_candidates:
            matching_identity_found = True
            continue
        if identified_candidates:
            conflicting_identity_found = True
            continue
        if (
            (
                _reference_has_following_identity_hint(
                    text,
                    reference,
                    line_end=line_end,
                )
                or _reference_has_preceding_identity_hint(
                    text,
                    reference,
                )
            )
        ):
            conflicting_identity_found = True
            continue
    return conflicting_identity_found and not matching_identity_found


def _local_marker_context(text: str, marker_position: int) -> str:
    line_start = text.rfind("\n", 0, marker_position) + 1
    context = text[line_start:marker_position]
    original_context = context
    source_line = _is_source_evidence_line(
        _normalize_evidence_text(context)
    )
    boundaries = tuple(
        re.finditer(
            (
                r"(?:(?<!čl)(?<!cl)(?<!art)(?<!sec)"
                r"(?<!no)(?<!kap)\.\s+"
                r"|[!?]\s+|;\s+)"
            ),
            context,
            re.IGNORECASE,
        )
    )
    if boundaries:
        boundary = boundaries[-1]
        context = context[boundary.end() :]
        if not context.strip():
            previous_end = boundaries[-2].end() if len(boundaries) >= 2 else 0
            context = original_context[previous_end:boundary.start()]
        if source_line and boundary.group().lstrip().startswith(";"):
            context = f"Source: {context}"
    if source_line:
        references = tuple(_SECTION_REFERENCE.finditer(context))
        if references:
            reference_start = references[-1].start()
            preceding_commas = tuple(
                match.start()
                for match in re.finditer(",", context[:reference_start])
            )
            if len(preceding_commas) >= 2:
                context = f"Source: {context[preceding_commas[-2] + 1:]}"
    return context


def _filter_citations_by_section_labels(
    citations: tuple[Citation, ...],
    *,
    assistant_text: str,
) -> tuple[Citation, ...]:
    selected: list[Citation] = []
    rejected_count = 0
    for citation in citations:
        if citation.citation_scope == "document":
            selected.append(citation)
            continue
        referenced_sections = _explicit_section_numbers(
            assistant_text,
            citation=citation,
        )
        conflicting_identity = _context_identifies_other_directive(
            assistant_text,
            citation=citation,
            available_citations=citations,
        )
        if (
            conflicting_identity
            or (
                referenced_sections
                and (
                    not citation.section_number
                    or _normalize_section_number(citation.section_number)
                    not in referenced_sections
                )
            )
        ):
            rejected_count += 1
            continue
        selected.append(citation)
    if rejected_count:
        logger.warning(
            "Directive citations rejected because answer section labels "
            "do not match citation metadata rejected_citation_count=%d "
            "selected_citation_count=%d",
            rejected_count,
            len(selected),
        )
    return tuple(selected)


def _select_mandated_directive_citations(
    citations: tuple[Citation, ...],
    *,
    directive_ids: tuple[str, ...],
) -> tuple[Citation, ...]:
    selected: list[Citation] = []
    for directive_id in directive_ids:
        candidates = [
            citation
            for citation in citations
            if citation.directive_id == directive_id
        ]
        if not candidates:
            continue
        if len(candidates) == 1:
            selected.append(candidates[0])
            continue
        source_candidates = [
            citation
            for citation in candidates
            if citation.section_id
            or citation.section_number
            or citation.section_title
        ]
        if len(source_candidates) == 1:
            selected.append(source_candidates[0])
            continue

        representative = next(
            (
                citation
                for citation in candidates
                if not citation.section_id
            ),
            candidates[0],
        )
        selected.append(_to_document_citation(representative))
    return tuple(selected)


def _line_identifies_citation(
    normalized_line: str,
    citation: Citation,
) -> bool:
    if _line_contains_directive_id(normalized_line, citation):
        return True
    source_tokens = _normalize_evidence_text(citation.source_name).split()
    phrase_length = min(3, len(source_tokens))
    if not phrase_length:
        return False
    return any(
        _contains_normalized_phrase(
            normalized_line,
            " ".join(source_tokens[index : index + phrase_length]),
        )
        for index in range(len(source_tokens) - phrase_length + 1)
    )


def _most_specific_identified_citations(
    citations: list[Citation],
    *,
    normalized_line: str,
) -> list[Citation]:
    if len(citations) <= 1:
        return citations
    scores = {
        id(citation): max(
            (
                len(identity)
                for identity in (
                    _normalize_evidence_text(citation.directive_id or ""),
                    _normalize_evidence_text(citation.source_name),
                )
                if identity
                and _contains_normalized_phrase(normalized_line, identity)
            ),
            default=0,
        )
        for citation in citations
    }
    best_score = max(scores.values())
    return [
        citation
        for citation in citations
        if scores[id(citation)] == best_score
    ]


def _line_contains_directive_id(
    normalized_line: str,
    citation: Citation,
) -> bool:
    if not citation.directive_id:
        return False
    normalized_id = _normalize_evidence_text(citation.directive_id)
    return bool(
        normalized_id
        and _contains_normalized_phrase(normalized_line, normalized_id)
    )


def _contains_normalized_phrase(text: str, phrase: str) -> bool:
    return re.search(
        rf"(?<!\w){re.escape(phrase)}(?!\w)",
        text,
    ) is not None


def _normalize_evidence_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.sub(r"[\W_]+", " ", normalized).split())


def _coverage_counts(data: dict[str, Any]) -> tuple[int, int] | None:
    coverage = data.get("coverage")
    if not isinstance(coverage, dict):
        return None
    for completed_name, total_name in (
        ("returned_section_count", "selected_section_count"),
        ("covered_section_count", "total_section_count"),
        ("processed_sections", "total_sections"),
    ):
        completed = coverage.get(completed_name)
        total = coverage.get(total_name)
        if (
            isinstance(completed, int)
            and not isinstance(completed, bool)
            and isinstance(total, int)
            and not isinstance(total, bool)
            and 0 <= completed <= total
        ):
            return completed, total
    return None
