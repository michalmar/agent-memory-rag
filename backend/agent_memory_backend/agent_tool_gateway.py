"""Private Hosted Agent tool gateway with app-only authorization."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from agent_contracts import AgentType, ToolResultEnvelope
from .agent_tools import ToolExecutionError, ToolExecutor
from .auth import AgentCaller
from .conversation_history import ConversationHistoryStore, runtime_state_from_document


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
    tool_executor: ToolExecutor,
) -> ToolResultEnvelope:
    del caller
    document = await history_store.get_by_hosted_session(
        request.user_id, request.session_id
    )
    if document is None:
        raise HTTPException(status_code=403, detail="Agent session binding not found")
    state = runtime_state_from_document(document)
    if state is None or state.descriptor.agent_type is not AgentType.AGENT_FRAMEWORK:
        raise HTTPException(status_code=403, detail="Invalid agent runtime binding")
    if state.hosted_session_id != request.session_id:
        raise HTTPException(status_code=403, detail="Agent session binding mismatch")

    try:
        return await tool_executor.execute_envelope(
            tool_name, request.arguments, user_id=request.user_id
        )
    except ToolExecutionError as exc:
        return ToolResultEnvelope(status="error", error_code=exc.code)
