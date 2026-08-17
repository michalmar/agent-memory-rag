"""Shared typed retry policy for transient provider failures."""

from __future__ import annotations

import asyncio
import email.utils
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeVar

import httpx
from azure.core.exceptions import HttpResponseError, ServiceRequestError

from .config import RetryPolicyConfig

T = TypeVar("T")
_TRANSIENT_STATUS_CODES = {408, 429, 500, 502, 503, 504}


class ProviderRetryExhausted(RuntimeError):
    """A retryable provider operation exhausted its bounded policy."""


@dataclass(slots=True)
class RetryBudget:
    remaining: int

    def consume(self) -> None:
        if self.remaining < 1:
            raise ProviderRetryExhausted("Provider stage retry budget exhausted")
        self.remaining -= 1


async def retry_provider_call(
    operation: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicyConfig,
    budget: RetryBudget,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    random_value: Callable[[], float] = random.random,
    on_attempt: Callable[[], None] | None = None,
    on_retry: Callable[[Exception], None] | None = None,
    on_throttle: Callable[[Exception], None] | None = None,
) -> T:
    try:
        async with asyncio.timeout(policy.operation_timeout_seconds):
            for attempt in range(policy.max_attempts):
                if on_attempt is not None:
                    on_attempt()
                try:
                    return await operation()
                except Exception as exc:
                    if not is_transient_provider_error(exc):
                        raise
                    if _status_code(exc) == 429 and on_throttle is not None:
                        on_throttle(exc)
                    if attempt + 1 >= policy.max_attempts:
                        raise ProviderRetryExhausted(
                            "Transient provider operation exhausted retry attempts"
                        ) from exc
                    budget.consume()
                    if on_retry is not None:
                        on_retry(exc)
                    delay = _retry_after_seconds(exc)
                    if delay is None:
                        exponential = min(
                            policy.max_delay_seconds,
                            policy.base_delay_seconds * (2**attempt),
                        )
                        jitter = exponential * policy.jitter_ratio * random_value()
                        delay = min(
                            policy.max_delay_seconds,
                            exponential + jitter,
                        )
                    await sleep(
                        max(0.0, min(delay, policy.max_delay_seconds))
                    )
    except TimeoutError as exc:
        raise ProviderRetryExhausted(
            "Provider operation exceeded its retry deadline"
        ) from exc
    raise AssertionError("unreachable")


def is_transient_provider_error(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, httpx.TimeoutException, ServiceRequestError)):
        return True
    status_code = _status_code(exc)
    return status_code in _TRANSIENT_STATUS_CODES


def _status_code(exc: Exception) -> int | None:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code
    if isinstance(exc, HttpResponseError):
        return exc.status_code
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    return status_code if isinstance(status_code, int) else None


def _retry_after_seconds(exc: Exception) -> float | None:
    response = (
        exc.response
        if isinstance(exc, httpx.HTTPStatusError)
        else getattr(exc, "response", None)
    )
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    value = headers.get("retry-after") or headers.get("Retry-After")
    if not isinstance(value, str) or not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            retry_at = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        return max(0.0, (retry_at - datetime.now(UTC)).total_seconds())
