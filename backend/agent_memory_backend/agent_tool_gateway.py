"""Private Hosted Agent tool gateway with app-only authorization."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from agent_contracts import (
    AgentType,
    COMMON_TOOL_DEFINITIONS,
    DIRECTIVE_TOOL_DEFINITIONS,
    InnerStateStatus,
    ToolResultEnvelope,
)
from .agent_tools import ToolExecutionError, ToolExecutor
from .auth import AgentCaller
from .config import get_settings
from .conversation_history import ConversationHistoryStore, runtime_state_from_document
from .directive_tools import DirectiveToolExecutor


class AgentToolExecutor(Protocol):
    async def execute_envelope(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        user_id: str,
    ) -> ToolResultEnvelope: ...


ToolExecutorRegistry = Mapping[AgentType, AgentToolExecutor]

_TOOLS_BY_AGENT = {
    AgentType.AGENT_FRAMEWORK: frozenset(
        definition.name for definition in COMMON_TOOL_DEFINITIONS
    ),
    AgentType.DIRECTIVE_RAG: frozenset(
        definition.name for definition in DIRECTIVE_TOOL_DEFINITIONS
    ),
}
_PENDING_LEASE_TIMEOUT = timedelta(minutes=10)


class AgentToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1, max_length=256)
    session_id: str = Field(min_length=1, max_length=256)
    call_id: str = Field(min_length=1, max_length=256)
    arguments: dict[str, Any]


class AgentStateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1, max_length=256)
    session_id: str = Field(min_length=1, max_length=256)
    call_id: str = Field(min_length=1, max_length=256)
    outer_foundry_conversation_id: str = Field(min_length=1, max_length=256)


async def resolve_agent_state(
    request: AgentStateRequest,
    caller: AgentCaller,
    history_store: ConversationHistoryStore,
) -> dict[str, Any]:
    document, state = await _bound_directive_state(
        request, caller, history_store
    )
    if not state.inner_model_conversation_id or state.inner_state_status is None:
        raise HTTPException(status_code=409, detail="Directive continuation state missing")
    if state.inner_last_completed_call_id == request.call_id:
        raise HTTPException(status_code=409, detail="Directive turn already completed")
    if state.inner_last_failed_call_id == request.call_id:
        raise HTTPException(
            status_code=409,
            detail="Directive turn outcome requires recovery",
        )
    if state.inner_pending_call_id:
        if state.inner_pending_call_id == request.call_id:
            raise HTTPException(
                status_code=409,
                detail="Directive turn outcome requires recovery",
            )
        if not _pending_lease_is_stale(state.inner_pending_started_at):
            raise HTTPException(
                status_code=409,
                detail="Directive conversation is busy",
            )
        state.inner_last_failed_call_id = state.inner_pending_call_id
    state.inner_pending_call_id = request.call_id
    state.inner_pending_started_at = datetime.now(timezone.utc).isoformat()
    state.inner_state_revision += 1
    try:
        await history_store.bind_runtime_state(
            str(document["id"]),
            request.user_id,
            state,
            expected_etag=document.get("_etag"),
        )
    except Exception as exc:
        if getattr(exc, "status_code", None) == 412:
            raise HTTPException(
                status_code=409,
                detail="Directive conversation is busy",
            ) from exc
        raise
    return {
        "inner_model_conversation_id": state.inner_model_conversation_id,
        "bootstrap_required": (
            state.inner_state_status is InnerStateStatus.BOOTSTRAP_REQUIRED
        ),
        "release_id": state.descriptor.release_id,
        "revision": state.inner_state_revision,
    }


async def complete_agent_state_turn(
    request: AgentStateRequest,
    caller: AgentCaller,
    history_store: ConversationHistoryStore,
) -> dict[str, str]:
    document, state = await _bound_directive_state(
        request, caller, history_store
    )
    if state.inner_pending_call_id != request.call_id:
        raise HTTPException(status_code=409, detail="Directive turn lease mismatch")
    state.inner_pending_call_id = None
    state.inner_pending_started_at = None
    state.inner_last_completed_call_id = request.call_id
    state.inner_state_status = InnerStateStatus.READY
    state.inner_state_revision += 1
    await history_store.bind_runtime_state(
        str(document["id"]),
        request.user_id,
        state,
        expected_etag=document.get("_etag"),
    )
    return {"status": "ready"}


async def fail_agent_state_turn(
    request: AgentStateRequest,
    caller: AgentCaller,
    history_store: ConversationHistoryStore,
) -> dict[str, str]:
    document, state = await _bound_directive_state(
        request, caller, history_store
    )
    if state.inner_pending_call_id != request.call_id:
        raise HTTPException(status_code=409, detail="Directive turn lease mismatch")
    state.inner_pending_call_id = None
    state.inner_pending_started_at = None
    state.inner_last_failed_call_id = request.call_id
    state.inner_state_revision += 1
    await history_store.bind_runtime_state(
        str(document["id"]),
        request.user_id,
        state,
        expected_etag=document.get("_etag"),
    )
    return {"status": "failed"}


def _pending_lease_is_stale(started_at: str | None) -> bool:
    if not started_at:
        return False
    try:
        started = datetime.fromisoformat(started_at)
    except ValueError:
        return False
    if started.tzinfo is None:
        return False
    return datetime.now(timezone.utc) - started >= _PENDING_LEASE_TIMEOUT


async def _bound_directive_state(
    request: AgentStateRequest,
    caller: AgentCaller,
    history_store: ConversationHistoryStore,
) -> tuple[dict[str, Any], Any]:
    document = await history_store.get_by_hosted_session(
        request.user_id, request.session_id
    )
    if document is None:
        raise HTTPException(status_code=403, detail="Agent session binding not found")
    state = runtime_state_from_document(document)
    if (
        state is None
        or state.descriptor.agent_type is not AgentType.DIRECTIVE_RAG
        or state.hosted_session_id != request.session_id
        or state.foundry_conversation_id
        != request.outer_foundry_conversation_id
    ):
        raise HTTPException(status_code=403, detail="Invalid directive runtime binding")
    settings = get_settings()
    if caller.principal_id not in settings.directive_hosted_agent_principal_ids:
        raise HTTPException(
            status_code=403,
            detail="Agent principal is not allowed for this runtime",
        )
    return document, state


async def dispatch_agent_tool(
    tool_name: str,
    request: AgentToolRequest,
    caller: AgentCaller,
    history_store: ConversationHistoryStore,
    tool_executors: ToolExecutorRegistry,
) -> ToolResultEnvelope:
    document = await history_store.get_by_hosted_session(
        request.user_id, request.session_id
    )
    if document is None:
        raise HTTPException(status_code=403, detail="Agent session binding not found")
    state = runtime_state_from_document(document)
    if state is None or state.descriptor.agent_type not in _TOOLS_BY_AGENT:
        raise HTTPException(status_code=403, detail="Invalid agent runtime binding")
    if state.hosted_session_id != request.session_id:
        raise HTTPException(status_code=403, detail="Agent session binding mismatch")
    agent_type = state.descriptor.agent_type
    settings = get_settings()
    allowed_principals = (
        settings.support_hosted_agent_principal_ids
        if agent_type is AgentType.AGENT_FRAMEWORK
        else settings.directive_hosted_agent_principal_ids
    )
    if caller.principal_id not in allowed_principals:
        raise HTTPException(
            status_code=403,
            detail="Agent principal is not allowed for this runtime",
        )
    if tool_name not in _TOOLS_BY_AGENT[agent_type]:
        raise HTTPException(
            status_code=403,
            detail="Tool is not allowed for this agent type",
        )
    executor = tool_executors.get(agent_type)
    if executor is None:
        raise HTTPException(
            status_code=503,
            detail="Agent tool executor is unavailable",
        )

    try:
        return await executor.execute_envelope(
            tool_name, request.arguments, user_id=request.user_id
        )
    except ToolExecutionError as exc:
        return ToolResultEnvelope(status="error", error_code=exc.code)
