"""Tenacity-based retry decorator with exponential backoff.

Design notes
------------
* Retries up to 3 attempts total (1 initial + 2 retries).
* Uses exponential backoff: wait = multiplier × 2^(attempt-1), clamped
  to [min_wait, max_wait] seconds.
* Default configuration: min=2s, max=30s, multiplier=1.
* Retries on httpx.HTTPError, httpx.TimeoutException, and TimeoutError.
* After all retries are exhausted the last exception is re-raised.
* A ``before_sleep`` callback logs each retry attempt at WARNING level.

Usage::

    from app.data.fetchers.retry import retry_on_network_error

    @retry_on_network_error
    async def fetch_data(url: str) -> bytes:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content

    # Or with custom settings:
    from app.data.fetchers.retry import make_retry_decorator

    @make_retry_decorator(max_attempts=5, min_wait=1, max_wait=60)
    async def fetch_with_custom_retry(url: str) -> bytes: ...
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, TypeVar

import httpx
from tenacity import (
    RetryCallState,
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Retryable exception types
# ---------------------------------------------------------------------------
_RETRYABLE_EXCEPTIONS = (
    httpx.HTTPError,
    httpx.TimeoutException,
    TimeoutError,
    ConnectionError,
)

F = TypeVar("F", bound=Callable[..., Any])


def make_retry_decorator(
    *,
    max_attempts: int = 3,
    multiplier: float = 1.0,
    min_wait: float = 2.0,
    max_wait: float = 30.0,
    reraise: bool = True,
) -> Callable[[F], F]:
    """Create a tenacity retry decorator with the given parameters.

    Args:
        max_attempts: Total number of attempts (including the first call).
        multiplier: Exponential backoff multiplier.
        min_wait: Minimum wait time between retries (seconds).
        max_wait: Maximum wait time between retries (seconds).
        reraise: If True, re-raise the last exception after all retries
            are exhausted. If False, tenacity raises ``RetryError``.

    Returns:
        A decorator that wraps async (or sync) callables with retry logic.

    Example::

        @make_retry_decorator(max_attempts=5, min_wait=1, max_wait=60)
        async def my_fetch(url: str) -> str: ...
    """
    return retry(  # type: ignore[return-value]
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=multiplier, min=min_wait, max=max_wait),
        retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=reraise,
    )


def _log_retry(retry_state: RetryCallState) -> None:
    """Log retry attempts with attempt number and exception info."""
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    logger.warning(
        "Retrying %s (attempt %d): %s",
        getattr(retry_state.fn, "__name__", str(retry_state.fn)),
        retry_state.attempt_number,
        exc,
    )


# ---------------------------------------------------------------------------
# Default decorator — use this in most cases
# ---------------------------------------------------------------------------
retry_on_network_error: Callable[[F], F] = make_retry_decorator(
    max_attempts=3,
    multiplier=1.0,
    min_wait=2.0,
    max_wait=30.0,
    reraise=True,
)
"""Default retry decorator: 3 attempts, exponential backoff 2–30 s.

Apply to any async function that makes HTTP requests::

    @retry_on_network_error
    async def fetch(url: str) -> httpx.Response:
        ...
"""
