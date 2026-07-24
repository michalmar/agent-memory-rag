"""Shared hosting primitives for Foundry Hosted MAF agents."""

from .gateway import invoke_gateway_tool
from .identity import (
    Agent365IdentityMiddleware,
    configure_observability_identity,
    install_agent365_identity_middleware,
)
from .runtime import run_hosted_agent

__all__ = [
    "Agent365IdentityMiddleware",
    "configure_observability_identity",
    "install_agent365_identity_middleware",
    "invoke_gateway_tool",
    "run_hosted_agent",
]
