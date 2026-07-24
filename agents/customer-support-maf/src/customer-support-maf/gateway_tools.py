"""Local Hosted Agent tools backed by the private application gateway."""

from __future__ import annotations

from typing import Any

from agent_framework import tool
from maf_hosting import invoke_gateway_tool


async def _invoke(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return await invoke_gateway_tool(tool_name, arguments)


@tool(approval_mode="never_require")
async def get_user_context() -> dict[str, Any]:
    """Get the authenticated user's minimal profile context."""
    return await _invoke("get_user_context", {})


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
