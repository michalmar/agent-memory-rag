"""Directive tools backed exclusively by the authenticated application gateway."""

from __future__ import annotations

from typing import Any, Literal

from agent_framework import tool
from maf_hosting import invoke_gateway_tool


async def _invoke(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return await invoke_gateway_tool(
        tool_name,
        arguments,
        timeout_env_var="DIRECTIVE_TOOL_HTTP_TIMEOUT_SECONDS",
        default_timeout=180.0,
    )


def _arguments(**values: Any) -> dict[str, Any]:
    return {
        key: value
        for key, value in values.items()
        if value is not None and value != []
    }


@tool(approval_mode="never_require")
async def get_directive(
    directive_id: str,
    view: Literal["metadata", "manifest", "summary"] = "metadata",
) -> dict[str, Any]:
    """Get current directive metadata, manifest, or generic summary."""
    return await _invoke(
        "get_directive",
        _arguments(
            directive_id=directive_id,
            view=view,
        ),
    )


@tool(approval_mode="never_require")
async def search_directives(
    intents: list[str],
    directive_ids: list[str] | None = None,
    section_ids: list[str] | None = None,
    max_results: int = 10,
) -> dict[str, Any]:
    """Discover current directives using one or more semantic intents."""
    return await _invoke(
        "search_directives",
        _arguments(
            intents=intents,
            directive_ids=directive_ids,
            section_ids=section_ids,
            max_results=max_results,
        ),
    )


@tool(approval_mode="never_require")
async def get_directive_content(
    directive_id: str,
    section_ids: list[str] | None = None,
    cursor: int = 0,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """Read ordered section content with explicit continuation when needed."""
    return await _invoke(
        "get_directive_content",
        _arguments(
            directive_id=directive_id,
            section_ids=section_ids,
            cursor=cursor,
            max_tokens=max_tokens,
        ),
    )


@tool(approval_mode="never_require")
async def get_user_directive_mandates(
    directive_ids: list[str],
) -> dict[str, Any]:
    """Check mandatory status only for the selected contributing directives."""
    return await _invoke(
        "get_user_directive_mandates",
        {"directive_ids": directive_ids},
    )


DIRECTIVE_TOOLS = (
    get_directive,
    search_directives,
    get_directive_content,
    get_user_directive_mandates,
)
