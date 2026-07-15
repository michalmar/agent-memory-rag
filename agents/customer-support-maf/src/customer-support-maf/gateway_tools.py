"""Local Hosted Agent tools backed by the private application gateway."""

from __future__ import annotations

import os
from typing import Any

import httpx
from agent_framework import tool
from azure.ai.agentserver.core import get_request_context
from azure.identity.aio import DefaultAzureCredential


async def _invoke(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    context = get_request_context()
    if not context.user_id or not context.session_id or not context.call_id:
        raise RuntimeError("Foundry request context is incomplete")

    credential = DefaultAzureCredential()
    try:
        token = await credential.get_token(os.environ["APP_TOOL_GATEWAY_SCOPE"])
    finally:
        await credential.close()

    url = (
        f"{os.environ['APP_TOOL_GATEWAY_URL'].rstrip('/')}/internal/"
        f"agent-tools/{tool_name}"
    )
    async with httpx.AsyncClient(timeout=30.0) as client:
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


@tool(approval_mode="never_require")
async def get_user_context() -> dict[str, Any]:
    """Get the authenticated user's minimal profile context."""
    return await _invoke("get_user_context", {})


@tool(approval_mode="never_require")
async def get_order_status(order_id: str) -> dict[str, Any]:
    """Look up the actual status of an order by ID."""
    return await _invoke("get_order_status", {"order_id": order_id})


@tool(approval_mode="never_require")
async def check_memory(query: str) -> dict[str, Any]:
    """Search memory only when the user explicitly asks to recall a prior chat."""
    return await _invoke("check_memory", {"query": query})


@tool(approval_mode="never_require")
async def update_user_profile(
    basic_info: dict[str, Any] | None = None,
    interests: list[str] | None = None,
    habits: list[str] | None = None,
    preferences: dict[str, Any] | None = None,
    status: dict[str, Any] | None = None,
    facts: list[str] | None = None,
) -> dict[str, Any]:
    """Record only durable personal facts that the user explicitly stated."""
    arguments = {
        key: value
        for key, value in {
            "basic_info": basic_info,
            "interests": interests,
            "habits": habits,
            "preferences": preferences,
            "status": status,
            "facts": facts,
        }.items()
        if value is not None
    }
    return await _invoke("update_user_profile", arguments)
