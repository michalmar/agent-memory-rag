"""Common startup path for hosted MAF agents."""

from __future__ import annotations

from collections.abc import Callable

from .identity import (
    configure_observability_identity,
    install_agent365_identity_middleware,
)


def run_hosted_agent(build_agent: Callable[[], object]) -> None:
    from agent_framework_foundry_hosting import ResponsesHostServer

    tenant_id, agent_id = configure_observability_identity()
    server = ResponsesHostServer(build_agent())
    if agent_id:
        install_agent365_identity_middleware(
            server,
            tenant_id=tenant_id,
            agent_id=agent_id,
        )
    server.run()
