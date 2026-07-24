"""Authenticated transport for invoking application gateway tools."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx
from azure.ai.agentserver.core import get_request_context
from azure.identity.aio import DefaultAzureCredential

_credential = DefaultAzureCredential()


@dataclass(frozen=True)
class ResolvedAgentState:
    inner_model_conversation_id: str
    bootstrap_required: bool
    release_id: str
    revision: int


def _resolve_timeout(
    timeout: float | None,
    *,
    timeout_env_var: str | None,
    default_timeout: float,
) -> float:
    if timeout is not None:
        return timeout
    if timeout_env_var:
        return float(os.environ.get(timeout_env_var, str(default_timeout)))
    return default_timeout


async def invoke_gateway_tool(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    timeout: float | None = None,
    timeout_env_var: str | None = None,
    default_timeout: float = 30.0,
) -> dict[str, Any]:
    context = get_request_context()
    if not context.user_id or not context.session_id or not context.call_id:
        raise RuntimeError("Foundry request context is incomplete")

    token = await _credential.get_token(os.environ["APP_TOOL_GATEWAY_SCOPE"])
    url = (
        f"{os.environ['APP_TOOL_GATEWAY_URL'].rstrip('/')}/internal/"
        f"agent-tools/{tool_name}"
    )
    resolved_timeout = _resolve_timeout(
        timeout,
        timeout_env_var=timeout_env_var,
        default_timeout=default_timeout,
    )
    async with httpx.AsyncClient(timeout=resolved_timeout) as client:
        response = await client.post(
            url,
            headers={"Authorization": f"Bearer {token.token}"},
            json={
                "user_id": context.user_id,
                "session_id": context.session_id,
                "call_id": context.call_id,
                "arguments": arguments,
            },
        )
        response.raise_for_status()
        payload = response.json()

    if not isinstance(payload, dict):
        raise RuntimeError("Agent tool gateway returned an invalid response")
    return payload


async def resolve_agent_state(
    outer_foundry_conversation_id: str,
    *,
    timeout: float = 30.0,
) -> ResolvedAgentState:
    payload = await _invoke_state_endpoint(
        "resolve",
        outer_foundry_conversation_id,
        timeout=timeout,
    )
    inner_id = payload.get("inner_model_conversation_id")
    bootstrap_required = payload.get("bootstrap_required")
    release_id = payload.get("release_id")
    revision = payload.get("revision")
    if (
        not isinstance(inner_id, str)
        or not inner_id.startswith("conv_")
        or not isinstance(bootstrap_required, bool)
        or not isinstance(release_id, str)
        or not release_id
        or not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision < 1
    ):
        raise RuntimeError("Agent state gateway returned an invalid response")
    return ResolvedAgentState(
        inner_id,
        bootstrap_required,
        release_id,
        revision,
    )


async def complete_agent_state_turn(
    outer_foundry_conversation_id: str,
    *,
    revision: int,
    timeout: float = 30.0,
) -> None:
    payload = await _invoke_state_endpoint(
        "turn-complete",
        outer_foundry_conversation_id,
        revision=revision,
        timeout=timeout,
    )
    if payload.get("status") not in {"completed", "ready"}:
        raise RuntimeError("Agent state gateway rejected turn completion")


async def fail_agent_state_turn(
    outer_foundry_conversation_id: str,
    *,
    revision: int,
    timeout: float = 30.0,
) -> None:
    payload = await _invoke_state_endpoint(
        "turn-failed",
        outer_foundry_conversation_id,
        revision=revision,
        timeout=timeout,
    )
    if payload.get("status") not in {"failed", "completed", "ready"}:
        raise RuntimeError("Agent state gateway rejected turn failure")


async def _invoke_state_endpoint(
    action: str,
    outer_foundry_conversation_id: str,
    *,
    revision: int | None = None,
    timeout: float,
) -> dict[str, Any]:
    context = get_request_context()
    if not context.user_id or not context.session_id or not context.call_id:
        raise RuntimeError("Foundry request context is incomplete")
    token = await _credential.get_token(os.environ["APP_TOOL_GATEWAY_SCOPE"])
    url = (
        f"{os.environ['APP_TOOL_GATEWAY_URL'].rstrip('/')}/internal/"
        f"agent-state/{action}"
    )
    async with httpx.AsyncClient(timeout=timeout) as client:
        payload: dict[str, Any] = {
            "user_id": context.user_id,
            "session_id": context.session_id,
            "call_id": context.call_id,
            "outer_foundry_conversation_id": outer_foundry_conversation_id,
        }
        if revision is not None:
            payload["revision"] = revision
        response = await client.post(
            url,
            headers={"Authorization": "Bearer " + token.token},
            json=payload,
        )
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Agent state gateway returned an invalid response")
    return payload
