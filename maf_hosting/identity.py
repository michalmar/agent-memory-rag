"""Agent identity and observability integration for hosted MAF agents."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from microsoft.opentelemetry.a365.core import BaggageBuilder
from starlette.types import ASGIApp, Receive, Scope, Send

if TYPE_CHECKING:
    from agent_framework_foundry_hosting import ResponsesHostServer


def configure_observability_identity() -> tuple[str, str | None]:
    tenant_id = os.environ.get("FOUNDRY_AGENT_TENANT_ID", "").strip()
    if not tenant_id:
        tenant_id = os.environ.get("ENTRA_TENANT_ID", "").strip()
    if not tenant_id:
        raise RuntimeError(
            "ENTRA_TENANT_ID is required when FOUNDRY_AGENT_TENANT_ID is not provided"
        )

    os.environ["FOUNDRY_AGENT_TENANT_ID"] = tenant_id
    agent_id = os.environ.get("FOUNDRY_AGENT_INSTANCE_CLIENT_ID", "").strip()
    if not agent_id and os.environ.get("FOUNDRY_HOSTING_ENVIRONMENT", "").strip():
        raise RuntimeError(
            "FOUNDRY_AGENT_INSTANCE_CLIENT_ID is required in the hosted environment"
        )
    return tenant_id, agent_id or None


class Agent365IdentityMiddleware:
    def __init__(self, app: ASGIApp, *, tenant_id: str, agent_id: str) -> None:
        self._app = app
        self._tenant_id = tenant_id
        self._agent_id = agent_id

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        baggage = (
            BaggageBuilder()
            .tenant_id(self._tenant_id)
            .agent_id(self._agent_id)
        )
        with baggage.build():
            await self._app(scope, receive, send)


def install_agent365_identity_middleware(
    server: ResponsesHostServer,
    *,
    tenant_id: str,
    agent_id: str,
) -> None:
    create_routes = [
        route
        for route in server.routes
        if getattr(route, "name", None) == "create_response"
    ]
    if len(create_routes) != 1:
        raise RuntimeError(
            "Expected exactly one create_response route for Agent 365 enrichment"
        )

    route = create_routes[0]
    route.app = Agent365IdentityMiddleware(
        route.app,
        tenant_id=tenant_id,
        agent_id=agent_id,
    )
