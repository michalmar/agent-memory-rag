"""Private Hosted Agent tool gateway with app-only authorization."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from agent_contracts import (
    AgentType,
    COMMON_TOOL_DEFINITIONS,
    DIRECTIVE_TOOL_DEFINITIONS,
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


class AgentToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1, max_length=256)
    session_id: str = Field(min_length=1, max_length=256)
    call_id: str = Field(min_length=1, max_length=256)
    arguments: dict[str, Any]


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
