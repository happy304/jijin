"""Token-bucket rate limiter, isolated per provider name.

Design notes
------------
* Each provider gets its own independent token bucket so that a slow
  provider does not starve a fast one.
* The bucket is refilled continuously (not in discrete ticks) using
  wall-clock time, which gives smoother throughput than a fixed-window
  counter.
* `acquire()` is an async method that sleeps until a token is available,
  making it safe to call from async code without blocking the event loop.
* Thread-safety: this implementation uses asyncio.Lock and is designed
  for single-process async use. For multi-process deployments a Redis-
  backed implementation would be needed.

Algorithm
---------
    tokens(t) = min(capacity, tokens(t_last) + rate × (t - t_last))

    On acquire():
        refill tokens based on elapsed time
        if tokens >= 1: consume 1 token, return immediately
        else: sleep for (1 - tokens) / rate seconds, then consume
"""

from __future__ import annotations

import asyncio
import time


class TokenBucket:
    """A single token-bucket for one provider.

    Args:
        rate: Tokens added per second (= max sustained requests/s).
        capacity: Maximum burst size (tokens). Defaults to ``rate``.
    """

    def __init__(self, rate: float, capacity: float | None = None) -> None:
        if rate <= 0:
            raise ValueError(f"rate must be positive, got {rate!r}")
        self._rate = rate
        self._capacity = capacity if capacity is not None else rate
        if self._capacity <= 0:
            raise ValueError(f"capacity must be positive, got {self._capacity!r}")
        self._tokens: float = self._capacity  # start full
        self._last_refill: float = time.monotonic()
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _refill(self) -> None:
        """Refill tokens based on elapsed time since last refill.

        Must be called while holding ``self._lock``.
        """
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._last_refill = now

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def rate(self) -> float:
        """Configured token refill rate (tokens/second)."""
        return self._rate

    @property
    def capacity(self) -> float:
        """Maximum bucket capacity (burst size)."""
        return self._capacity

    async def acquire(self, tokens: float = 1.0) -> None:
        """Wait until ``tokens`` tokens are available, then consume them.

        Args:
            tokens: Number of tokens to consume. Defaults to 1.

        Raises:
            ValueError: If ``tokens`` exceeds the bucket capacity.
        """
        if tokens > self._capacity:
            raise ValueError(
                f"Requested {tokens} tokens but bucket capacity is {self._capacity}"
            )
        async with self._lock:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                return
            # Calculate how long to wait for enough tokens
            deficit = tokens - self._tokens
            wait_time = deficit / self._rate
            # Release lock while sleeping so other coroutines can proceed
        await asyncio.sleep(wait_time)
        # Re-acquire and consume
        async with self._lock:
            self._refill()
            self._tokens -= tokens

    def available_tokens(self) -> float:
        """Return the current number of available tokens (approximate).

        This is a non-locking read for monitoring/testing purposes only.
        """
        now = time.monotonic()
        elapsed = now - self._last_refill
        return min(self._capacity, self._tokens + elapsed * self._rate)


class RateLimiter:
    """Per-provider token-bucket rate limiter.

    Each provider name gets its own independent ``TokenBucket``.
    Providers not explicitly configured fall back to the default rate.

    Usage::

        limiter = RateLimiter(default_rate=2.0)
        limiter.configure("eastmoney", rate=2.0)

        # In your fetch loop:
        await limiter.acquire("eastmoney")
        response = await client.get(url)

    Args:
        default_rate: Default tokens/second for unconfigured providers.
        default_capacity: Default burst capacity. Defaults to ``default_rate``.
    """

    def __init__(
        self,
        default_rate: float = 2.0,
        default_capacity: float | None = None,
    ) -> None:
        self._default_rate = default_rate
        self._default_capacity = default_capacity
        self._buckets: dict[str, TokenBucket] = {}

    def configure(
        self,
        provider: str,
        *,
        rate: float,
        capacity: float | None = None,
    ) -> None:
        """Configure (or reconfigure) the rate limit for a provider.

        Args:
            provider: Provider name (case-sensitive).
            rate: Tokens per second.
            capacity: Burst capacity. Defaults to ``rate``.
        """
        self._buckets[provider] = TokenBucket(rate=rate, capacity=capacity)

    def _get_bucket(self, provider: str) -> TokenBucket:
        """Return the bucket for ``provider``, creating it if needed."""
        if provider not in self._buckets:
            self._buckets[provider] = TokenBucket(
                rate=self._default_rate,
                capacity=self._default_capacity,
            )
        return self._buckets[provider]

    async def acquire(self, provider: str, tokens: float = 1.0) -> None:
        """Acquire ``tokens`` tokens for ``provider``, waiting if necessary.

        Args:
            provider: Provider name.
            tokens: Number of tokens to consume.
        """
        bucket = self._get_bucket(provider)
        await bucket.acquire(tokens)

    def available_tokens(self, provider: str) -> float:
        """Return approximate available tokens for ``provider``."""
        return self._get_bucket(provider).available_tokens()

    def get_rate(self, provider: str) -> float:
        """Return the configured rate for ``provider``."""
        return self._get_bucket(provider).rate
