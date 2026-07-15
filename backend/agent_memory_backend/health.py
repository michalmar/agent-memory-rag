"""Dependency readiness helpers with bounded, sanitized results."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from .telemetry import span

logger = logging.getLogger("health")


async def run_readiness_check(
    name: str,
    check: Callable[[], Awaitable[None]],
    timeout_seconds: float,
) -> tuple[str, dict[str, Any]]:
    started = time.perf_counter()
    try:
        with span("health.dependency", {"dependency.name": name}):
            await asyncio.wait_for(check(), timeout=timeout_seconds)
        return name, {
            "status": "ok",
            "duration_ms": round((time.perf_counter() - started) * 1000),
        }
    except TimeoutError:
        return name, {
            "status": "failed",
            "error": "timeout",
            "duration_ms": round((time.perf_counter() - started) * 1000),
        }
    except Exception as exc:
        logger.warning("Readiness check failed: %s (%s)", name, type(exc).__name__)
        return name, {
            "status": "failed",
            "error": type(exc).__name__,
            "duration_ms": round((time.perf_counter() - started) * 1000),
        }
