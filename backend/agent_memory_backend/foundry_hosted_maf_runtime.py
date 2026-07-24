"""Remote runtime for the Foundry Hosted Microsoft Agent Framework agent."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import replace
from typing import Any
from urllib.parse import quote, urlsplit

from agent_contracts import (
    AgentType,
    Citation,
    CitationsEvent,
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
from .azure_clients import get_credential
from .foundry_runtime_base import (
    completed_events,
    server_tool_events,
    stream_response,
)

_PREVIEW_HEADERS = {"Foundry-Features": "HostedAgents=V1Preview"}
_HEALTH_INPUT = "Health check. Reply exactly OK without calling tools."
_PROBE_CLEANUP_ATTEMPTS = 3
_PROBE_CLEANUP_BACKOFF_SECONDS = 0.5
_DEFAULT_PROGRESS_HEARTBEAT_SECONDS = 10.0
logger = logging.getLogger("foundry_hosted_maf")

_TOOL_STAGES = {
    "resolve_directive": WorkflowStage.RESOLVING,
    "search_directives": WorkflowStage.SEARCHING,
    "get_directive_manifest": WorkflowStage.VERIFYING_COVERAGE,
    "get_directive_content": WorkflowStage.LOADING_CONTENT,
    "search_within_directive": WorkflowStage.SEARCHING,
    "get_related_directives": WorkflowStage.FOLLOWING_REFERENCES,
    "get_precomputed_summary": WorkflowStage.LOADING_CONTENT,
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
        self._endpoint_verified = False
        self._pending_probe_session_id: str | None = None
        self._pending_probe_was_verified = False
        self._verified_probe_reclaimed = False

    async def initialize(self) -> None:
        from azure.ai.projects.aio import AIProjectClient

        self._project = AIProjectClient(
            endpoint=self._project_endpoint,
            credential=get_credential(),
            allow_preview=True,
        )
        self._openai = self._project.get_openai_client(
            agent_name=self._physical_agent_name,
            base_url=self._physical_agent_endpoint,
            default_query={"api-version": "v1"},
        )
        try:
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
        project = self._project
        self._endpoint_verified = False
        errors: list[Exception] = []
        cancellation: asyncio.CancelledError | None = None
        if self._pending_probe_session_id and project is not None:
            pending_probe_was_verified = self._pending_probe_was_verified
            try:
                await self._cleanup_probe_session()
            except asyncio.CancelledError as exc:
                cancellation = exc
            except Exception as exc:
                errors.append(exc)
            else:
                self._verified_probe_reclaimed = pending_probe_was_verified

        self._openai = None
        self._project = None
        for client in (openai, project):
            if client is None:
                continue
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
                    headers=_PREVIEW_HEADERS,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
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
                self._pending_probe_was_verified = False
                return

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
        try:
            conversation = await self._require_openai().conversations.create(
                items=seed_messages or [],
                extra_headers=self._headers(authenticated_user_id),
            )
        except Exception:
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
            foundry_conversation_id=conversation.id,
            hosted_session_id=hosted_session.agent_session_id,
        )

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
            if directive_citations:
                yield CitationsEvent(citations=directive_citations)
            for event in completed_events(completed_response):
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
        if state.foundry_conversation_id:
            await self._require_openai().conversations.delete(
                conversation_id=state.foundry_conversation_id,
                extra_headers=headers,
            )
        if state.hosted_session_id:
            await self._project.agents.delete_session(
                agent_name=self._physical_agent_name,
                session_id=state.hosted_session_id,
                headers=headers,
            )

    async def health_check(self) -> None:
        if self._project is None or self._openai is None:
            raise RuntimeError("Hosted MAF runtime is not initialized")
        if not self._endpoint_verified:
            raise RuntimeError("Hosted MAF Responses endpoint is not verified")


def _enrich_directive_tool_events(
    events: list[NormalizedAgentEvent],
    known_tool_names: dict[str, str],
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
            if isinstance(status_values, dict):
                for directive_id, status in status_values.items():
                    if not isinstance(directive_id, str):
                        continue
                    try:
                        statuses[directive_id] = MandatoryStatus(status)
                    except (TypeError, ValueError):
                        statuses[directive_id] = MandatoryStatus.UNKNOWN
            candidate_snapshot_id = event.result.data.get("snapshot_id")
            if isinstance(candidate_snapshot_id, str):
                snapshot_id = candidate_snapshot_id
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
    return enriched, tuple(citations), coverage


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
