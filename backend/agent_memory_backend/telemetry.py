"""Privacy-safe Azure Monitor OpenTelemetry setup and span helpers."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator, Mapping

from .config import get_settings

logger = logging.getLogger("telemetry")
_configured = False

_SAFE_ATTRIBUTE_KEYS = frozenset(
    {
        "agent.citation_count",
        "agent.input_tokens",
        "agent.name",
        "agent.output_tokens",
        "agent.release_id",
        "agent.response_length",
        "agent.tool_count",
        "agent.tool.name",
        "agent.type",
        "agent.version",
        "component.name",
        "db.system",
        "dependency.name",
        "error.code",
        "error.type",
        "model.deployment",
        "search.knowledge_base",
        "session.id",
    }
)


def _safe_attributes(
    attributes: Mapping[str, str | int | float | bool] | None,
) -> dict[str, str | int | float | bool]:
    provided = attributes or {}
    dropped = sorted(set(provided) - _SAFE_ATTRIBUTE_KEYS)
    if dropped:
        logger.warning("Dropped unsupported telemetry attributes: %s", ", ".join(dropped))
    return {key: value for key, value in provided.items() if key in _SAFE_ATTRIBUTE_KEYS}


def configure_telemetry() -> bool:
    """Configure Azure Monitor once when an Application Insights target is present."""
    global _configured
    if _configured:
        return True

    settings = get_settings()
    connection_string = settings.applicationinsights_connection_string
    if not connection_string:
        return False

    from azure.monitor.opentelemetry import configure_azure_monitor

    if settings.azure_client_id:
        from azure.identity import ManagedIdentityCredential

        credential = ManagedIdentityCredential(client_id=settings.azure_client_id)
    else:
        from azure.identity import DefaultAzureCredential

        credential = DefaultAzureCredential()

    configure_azure_monitor(
        connection_string=connection_string,
        credential=credential,
        logger_name="",
    )
    _configured = True
    logger.info("Azure Monitor OpenTelemetry configured")
    return True


@contextmanager
def span(
    name: str, attributes: Mapping[str, str | int | float | bool] | None = None
) -> Iterator[object]:
    """Create a span without attaching prompts, profile data, or tool payloads."""
    from opentelemetry import trace

    tracer = trace.get_tracer("agent-memory-backend")
    with tracer.start_as_current_span(name) as current:
        for key, value in _safe_attributes(attributes).items():
            current.set_attribute(key, value)
        yield current
