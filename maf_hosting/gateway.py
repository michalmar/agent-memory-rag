"""Authenticated transport for invoking application gateway tools."""

from __future__ import annotations

import os
from typing import Any

import httpx
from azure.ai.agentserver.core import get_request_context
from azure.identity.aio import DefaultAzureCredential

_credential = DefaultAzureCredential()


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
