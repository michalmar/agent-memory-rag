"""Authenticated transport for invoking application gateway tools."""

from __future__ import annotations

import asyncio
import os
import threading
from dataclasses import dataclass
from typing import Any

import httpx
from azure.ai.agentserver.core import get_request_context
from azure.identity.aio import DefaultAzureCredential

_credential = DefaultAzureCredential()

_DEFAULT_CONNECT_TIMEOUT_SECONDS = 5.0
_DEFAULT_MAX_CONNECTIONS = 100
_DEFAULT_MAX_KEEPALIVE_CONNECTIONS = 20
_DEFAULT_KEEPALIVE_EXPIRY_SECONDS = 30.0


@dataclass(frozen=True)
class ResolvedAgentState:
    inner_model_conversation_id: str
    bootstrap_required: bool
    release_id: str
    revision: int


@dataclass(frozen=True)
class GatewayTransportConfig:
    gateway_url: str
    gateway_scope: str
    connect_timeout_seconds: float
    max_connections: int
    max_keepalive_connections: int
    keepalive_expiry_seconds: float


@dataclass(frozen=True)
class GatewayTransport:
    config: GatewayTransportConfig
    client: httpx.AsyncClient


_transport_lock = threading.Lock()
_transport: GatewayTransport | None = None


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


def _read_positive_float_env(name: str, default: float) -> float:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive number") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be a positive number")
    return value


def _read_positive_int_env(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be a positive integer")
    return value


def _load_transport_config() -> GatewayTransportConfig:
    return GatewayTransportConfig(
        gateway_url=os.environ["APP_TOOL_GATEWAY_URL"].rstrip("/"),
        gateway_scope=os.environ["APP_TOOL_GATEWAY_SCOPE"],
        connect_timeout_seconds=_read_positive_float_env(
            "APP_TOOL_GATEWAY_CONNECT_TIMEOUT_SECONDS",
            _DEFAULT_CONNECT_TIMEOUT_SECONDS,
        ),
        max_connections=_read_positive_int_env(
            "APP_TOOL_GATEWAY_MAX_CONNECTIONS",
            _DEFAULT_MAX_CONNECTIONS,
        ),
        max_keepalive_connections=_read_positive_int_env(
            "APP_TOOL_GATEWAY_MAX_KEEPALIVE_CONNECTIONS",
            _DEFAULT_MAX_KEEPALIVE_CONNECTIONS,
        ),
        keepalive_expiry_seconds=_read_positive_float_env(
            "APP_TOOL_GATEWAY_KEEPALIVE_EXPIRY_SECONDS",
            _DEFAULT_KEEPALIVE_EXPIRY_SECONDS,
        ),
    )


def _build_transport(
    config: GatewayTransportConfig,
) -> GatewayTransport:
    client = httpx.AsyncClient(
        limits=httpx.Limits(
            max_connections=config.max_connections,
            max_keepalive_connections=config.max_keepalive_connections,
            keepalive_expiry=config.keepalive_expiry_seconds,
        ),
    )
    return GatewayTransport(config=config, client=client)


def _get_gateway_transport() -> GatewayTransport:
    config = _load_transport_config()
    global _transport
    with _transport_lock:
        if _transport is None:
            _transport = _build_transport(config)
        elif _transport.config != config:
            raise RuntimeError(
                "Gateway transport configuration changed without cleanup"
            )
        return _transport


def _request_timeout(
    total_timeout_seconds: float,
    *,
    connect_timeout_seconds: float,
) -> httpx.Timeout:
    return httpx.Timeout(
        total_timeout_seconds,
        connect=connect_timeout_seconds,
    )


async def close_gateway_transport() -> None:
    global _transport
    with _transport_lock:
        transport = _transport
        _transport = None
    if transport is not None:
        await transport.client.aclose()


def close_gateway_transport_sync() -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(close_gateway_transport())
        return
    raise RuntimeError(
        "close_gateway_transport_sync cannot run inside an active event loop"
    )


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

    payload = {
        "user_id": context.user_id,
        "session_id": context.session_id,
        "call_id": context.call_id,
        "arguments": arguments,
    }
    resolved_timeout = _resolve_timeout(
        timeout,
        timeout_env_var=timeout_env_var,
        default_timeout=default_timeout,
    )
    return await _post_gateway_payload(
        f"/internal/agent-tools/{tool_name}",
        payload,
        timeout=resolved_timeout,
        invalid_response_message="Agent tool gateway returned an invalid response",
    )


async def resolve_agent_state(
    outer_foundry_conversation_id: str,
    *,
    timeout: float = 30.0,
) -> ResolvedAgentState:
    payload = await _invoke_state_endpoint(
        "resolve",
        outer_foundry_conversation_id,
        timeout=timeout,
    )
    inner_id = payload.get("inner_model_conversation_id")
    bootstrap_required = payload.get("bootstrap_required")
    release_id = payload.get("release_id")
    revision = payload.get("revision")
    if (
        not isinstance(inner_id, str)
        or not inner_id.startswith("conv_")
        or not isinstance(bootstrap_required, bool)
        or not isinstance(release_id, str)
        or not release_id
        or not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision < 1
    ):
        raise RuntimeError("Agent state gateway returned an invalid response")
    return ResolvedAgentState(
        inner_id,
        bootstrap_required,
        release_id,
        revision,
    )


async def complete_agent_state_turn(
    outer_foundry_conversation_id: str,
    *,
    revision: int,
    timeout: float = 30.0,
) -> None:
    payload = await _invoke_state_endpoint(
        "turn-complete",
        outer_foundry_conversation_id,
        revision=revision,
        timeout=timeout,
    )
    if payload.get("status") not in {"completed", "ready"}:
        raise RuntimeError("Agent state gateway rejected turn completion")


async def fail_agent_state_turn(
    outer_foundry_conversation_id: str,
    *,
    revision: int,
    timeout: float = 30.0,
) -> None:
    payload = await _invoke_state_endpoint(
        "turn-failed",
        outer_foundry_conversation_id,
        revision=revision,
        timeout=timeout,
    )
    if payload.get("status") not in {"failed", "completed", "ready"}:
        raise RuntimeError("Agent state gateway rejected turn failure")


async def _invoke_state_endpoint(
    action: str,
    outer_foundry_conversation_id: str,
    *,
    revision: int | None = None,
    timeout: float,
) -> dict[str, Any]:
    context = get_request_context()
    if not context.user_id or not context.session_id or not context.call_id:
        raise RuntimeError("Foundry request context is incomplete")
    payload: dict[str, Any] = {
        "user_id": context.user_id,
        "session_id": context.session_id,
        "call_id": context.call_id,
        "outer_foundry_conversation_id": outer_foundry_conversation_id,
    }
    if revision is not None:
        payload["revision"] = revision
    return await _post_gateway_payload(
        f"/internal/agent-state/{action}",
        payload,
        timeout=timeout,
        invalid_response_message="Agent state gateway returned an invalid response",
    )


async def _post_gateway_payload(
    path: str,
    payload: dict[str, Any],
    *,
    timeout: float,
    invalid_response_message: str,
) -> dict[str, Any]:
    transport = _get_gateway_transport()
    token = await _credential.get_token(transport.config.gateway_scope)
    response = await transport.client.post(
        f"{transport.config.gateway_url}{path}",
        headers={"Authorization": "Bearer " + token.token},
        json=payload,
        timeout=_request_timeout(
            timeout,
            connect_timeout_seconds=transport.config.connect_timeout_seconds,
        ),
    )
    response.raise_for_status()
    response_payload = response.json()
    if not isinstance(response_payload, dict):
        raise RuntimeError(invalid_response_message)
    return response_payload
