"""Stateless MCP tools authenticated with the published Agent Identity."""

from __future__ import annotations

import asyncio

from fastapi import HTTPException
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP

from agent_contracts import lookup_order_status

from .auth import validate_agent_token
from .config import get_settings


class AgentMcpTokenVerifier:
    """Adapt the gateway's app-role validator to the MCP SDK auth contract."""

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            caller = await asyncio.to_thread(
                validate_agent_token, f"Bearer {token}"
            )
        except HTTPException:
            return None

        required_role = get_settings().agent_gateway_required_role
        return AccessToken(
            token=token,
            client_id=caller.principal_id,
            subject=caller.principal_id,
            scopes=[required_role],
        )


def _create_mcp_server() -> FastMCP:
    settings = get_settings()
    tenant = settings.entra_tenant_id or "organizations"
    required_role = settings.agent_gateway_required_role
    server = FastMCP(
        name="customer-support-application-tools",
        instructions="Stateless customer-support tools for published agents.",
        token_verifier=AgentMcpTokenVerifier(),
        auth=AuthSettings(
            issuer_url=f"https://login.microsoftonline.com/{tenant}/v2.0",
            required_scopes=[required_role],
            resource_server_url=None,
        ),
        host="0.0.0.0",
        streamable_http_path="/",
        json_response=True,
        stateless_http=True,
    )

    @server.tool()
    def get_order_status(order_id: str) -> dict:
        """Look up the authoritative demo status of an order by ID."""
        return lookup_order_status(order_id)

    return server


application_tools_mcp = _create_mcp_server()
application_tools_mcp_app = application_tools_mcp.streamable_http_app()
