"""Directive tools backed exclusively by the authenticated application gateway."""

from __future__ import annotations

import os
from typing import Any

import httpx
from agent_framework import tool
from azure.ai.agentserver.core import get_request_context
from azure.identity.aio import DefaultAzureCredential

_credential = DefaultAzureCredential()


async def _invoke(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    context = get_request_context()
    if not context.user_id or not context.session_id or not context.call_id:
        raise RuntimeError("Foundry request context is incomplete")
    token = await _credential.get_token(os.environ["APP_TOOL_GATEWAY_SCOPE"])
    url = (
        f"{os.environ['APP_TOOL_GATEWAY_URL'].rstrip('/')}/internal/"
        f"agent-tools/{tool_name}"
    )
    timeout = float(os.environ.get("DIRECTIVE_TOOL_HTTP_TIMEOUT_SECONDS", "180"))
    async with httpx.AsyncClient(timeout=timeout) as client:
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


def _arguments(**values: Any) -> dict[str, Any]:
    return {
        key: value
        for key, value in values.items()
        if value is not None and value != []
    }


@tool(approval_mode="never_require")
async def resolve_directive(
    query: str | None = None,
    directive_id: str | None = None,
    directive_version_id: str | None = None,
    version_label: str | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Resolve a directive and exact version before retrieving its content."""
    return await _invoke(
        "resolve_directive",
        _arguments(
            query=query,
            directive_id=directive_id,
            directive_version_id=directive_version_id,
            version_label=version_label,
            as_of=as_of,
        ),
    )


@tool(approval_mode="never_require")
async def search_directives(
    intents: list[str],
    directive_ids: list[str] | None = None,
    directive_version_id: str | None = None,
    current_only: bool = True,
    max_results: int = 10,
) -> dict[str, Any]:
    """Discover current directives using one or more semantic intents."""
    return await _invoke(
        "search_directives",
        _arguments(
            intents=intents,
            directive_ids=directive_ids,
            directive_version_id=directive_version_id,
            current_only=current_only,
            max_results=max_results,
        ),
    )


@tool(approval_mode="never_require")
async def get_directive_manifest(
    directive_id: str,
    directive_version_id: str,
) -> dict[str, Any]:
    """Get the complete ordered section manifest for an exact version."""
    return await _invoke(
        "get_directive_manifest",
        {
            "directive_id": directive_id,
            "directive_version_id": directive_version_id,
        },
    )


@tool(approval_mode="never_require")
async def get_directive_content(
    directive_id: str,
    directive_version_id: str,
    section_ids: list[str] | None = None,
    cursor: int = 0,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """Read ordered section content with explicit continuation when needed."""
    return await _invoke(
        "get_directive_content",
        _arguments(
            directive_id=directive_id,
            directive_version_id=directive_version_id,
            section_ids=section_ids,
            cursor=cursor,
            max_tokens=max_tokens,
        ),
    )


@tool(approval_mode="never_require")
async def search_within_directive(
    directive_id: str,
    directive_version_id: str,
    intents: list[str],
    section_ids: list[str] | None = None,
    max_results: int = 10,
) -> dict[str, Any]:
    """Search evidence within one exact directive version."""
    return await _invoke(
        "search_within_directive",
        _arguments(
            directive_id=directive_id,
            directive_version_id=directive_version_id,
            intents=intents,
            section_ids=section_ids,
            max_results=max_results,
        ),
    )


@tool(approval_mode="never_require")
async def get_related_directives(
    directive_id: str,
    directive_version_id: str,
    relation_types: list[str] | None = None,
    depth: int = 1,
) -> dict[str, Any]:
    """Traverse accepted directive relationships to a maximum depth of two."""
    return await _invoke(
        "get_related_directives",
        _arguments(
            directive_id=directive_id,
            directive_version_id=directive_version_id,
            relation_types=relation_types,
            depth=depth,
        ),
    )


@tool(approval_mode="never_require")
async def get_precomputed_summary(
    directive_id: str,
    directive_version_id: str,
) -> dict[str, Any]:
    """Get the published generic summary and its coverage metadata."""
    return await _invoke(
        "get_precomputed_summary",
        {
            "directive_id": directive_id,
            "directive_version_id": directive_version_id,
        },
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
    resolve_directive,
    search_directives,
    get_directive_manifest,
    get_directive_content,
    search_within_directive,
    get_related_directives,
    get_precomputed_summary,
    get_user_directive_mandates,
)
