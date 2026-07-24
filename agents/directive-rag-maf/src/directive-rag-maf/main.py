"""Foundry Hosted Microsoft Agent Framework directive assistant."""

from __future__ import annotations

import os

from agent_contracts import render_directive_rag_instructions
from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity.aio import DefaultAzureCredential
from gateway_tools import DIRECTIVE_TOOLS
from microsoft.opentelemetry.a365.core import BaggageBuilder
from starlette.types import ASGIApp, Receive, Scope, Send


def _configure_observability_identity() -> tuple[str, str | None]:
    tenant_id = os.environ.get("FOUNDRY_AGENT_TENANT_ID", "").strip()
    if not tenant_id:
        tenant_id = os.environ.get("ENTRA_TENANT_ID", "").strip()
    if not tenant_id:
        raise RuntimeError(
            "ENTRA_TENANT_ID is required when FOUNDRY_AGENT_TENANT_ID "
            "is not provided"
        )
    os.environ["FOUNDRY_AGENT_TENANT_ID"] = tenant_id
    agent_id = os.environ.get("FOUNDRY_AGENT_INSTANCE_CLIENT_ID", "").strip()
    if not agent_id and os.environ.get(
        "FOUNDRY_HOSTING_ENVIRONMENT",
        "",
    ).strip():
        raise RuntimeError(
            "FOUNDRY_AGENT_INSTANCE_CLIENT_ID is required in the hosted "
            "environment"
        )
    return tenant_id, agent_id or None


class _Agent365IdentityMiddleware:
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


def _install_agent365_identity_middleware(
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
    route.app = _Agent365IdentityMiddleware(
        route.app,
        tenant_id=tenant_id,
        agent_id=agent_id,
    )


def _max_iterations() -> int:
    value = int(os.environ.get("DIRECTIVE_MAX_ITERATIONS", "12"))
    if value < 1 or value > 30:
        raise RuntimeError("DIRECTIVE_MAX_ITERATIONS must be in the range 1..30")
    return value


def build_agent() -> Agent:
    client = FoundryChatClient(
        project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        model=os.environ["DIRECTIVE_MODEL_DEPLOYMENT"],
        credential=DefaultAzureCredential(),
        function_invocation_configuration={
            "max_iterations": _max_iterations()
        },
    )
    hosted_agent_id = (
        os.environ.get("FOUNDRY_AGENT_INSTANCE_CLIENT_ID", "").strip() or None
    )
    return Agent(
        client=client,
        id=hosted_agent_id,
        name="directive-rag-maf-hosted",
        instructions=render_directive_rag_instructions(),
        tools=list(DIRECTIVE_TOOLS),
        default_options={"store": False},
    )


def main() -> None:
    tenant_id, agent_id = _configure_observability_identity()
    server = ResponsesHostServer(build_agent())
    if agent_id:
        _install_agent365_identity_middleware(
            server,
            tenant_id=tenant_id,
            agent_id=agent_id,
        )
    server.run()


if __name__ == "__main__":
    main()
