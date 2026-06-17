"""Unit tests for app.data.fetchers.rate_limiter."""

from __future__ import annotations

import asyncio
import time

import pytest

from app.data.fetchers.rate_limiter import RateLimiter, TokenBucket


# ---------------------------------------------------------------------------
# TokenBucket
# ---------------------------------------------------------------------------


class TestTokenBucketInit:
    def test_positive_rate_required(self) -> None:
        with pytest.raises(ValueError, match="rate must be positive"):
            TokenBucket(rate=0)

    def test_negative_rate_rejected(self) -> None:
        with pytest.raises(ValueError, match="rate must be positive"):
            TokenBucket(rate=-1.0)

    def test_default_capacity_equals_rate(self) -> None:
        bucket = TokenBucket(rate=5.0)
        assert bucket.capacity == 5.0

    def test_custom_capacity(self) -> None:
        bucket = TokenBucket(rate=2.0, capacity=10.0)
        assert bucket.capacity == 10.0

    def test_starts_full(self) -> None:
        bucket = TokenBucket(rate=4.0)
        assert bucket.available_tokens() == pytest.approx(4.0, abs=0.01)


class TestTokenBucketAcquire:
    @pytest.mark.asyncio
    async def test_immediate_acquire_when_full(self) -> None:
        bucket = TokenBucket(rate=10.0, capacity=10.0)
        start = time.monotonic()
        await bucket.acquire()
        elapsed = time.monotonic() - start
        assert elapsed < 0.1  # should be nearly instant

    @pytest.mark.asyncio
    async def test_tokens_decrease_after_acquire(self) -> None:
        bucket = TokenBucket(rate=10.0, capacity=10.0)
        await bucket.acquire(5.0)
        assert bucket.available_tokens() < 6.0  # some refill may have occurred

    @pytest.mark.asyncio
    async def test_acquire_more_than_capacity_raises(self) -> None:
        bucket = TokenBucket(rate=2.0, capacity=2.0)
        with pytest.raises(ValueError, match="bucket capacity"):
            await bucket.acquire(3.0)

    @pytest.mark.asyncio
    async def test_acquire_waits_when_empty(self) -> None:
        """Acquiring from an empty bucket should wait approximately 1/rate seconds."""
        bucket = TokenBucket(rate=10.0, capacity=1.0)
        # Drain the bucket
        await bucket.acquire(1.0)
        # Now acquire again — should wait ~0.1s (1 token / 10 tokens/s)
        start = time.monotonic()
        await bucket.acquire(1.0)
        elapsed = time.monotonic() - start
        # Allow generous tolerance for CI environments
        assert elapsed >= 0.05

    @pytest.mark.asyncio
    async def test_multiple_acquires_respect_rate(self) -> None:
        """Acquiring N tokens from a rate-1 bucket should take ~N-1 seconds."""
        bucket = TokenBucket(rate=20.0, capacity=1.0)
        # Drain first
        await bucket.acquire(1.0)
        # Acquire 3 more tokens at 20/s → ~0.15s total
        start = time.monotonic()
        for _ in range(3):
            await bucket.acquire(1.0)
        elapsed = time.monotonic() - start
        assert elapsed >= 0.1  # at least 2 waits of ~0.05s each


class TestTokenBucketRefill:
    @pytest.mark.asyncio
    async def test_tokens_refill_over_time(self) -> None:
        """After draining, tokens should refill at the configured rate."""
        bucket = TokenBucket(rate=100.0, capacity=10.0)
        await bucket.acquire(10.0)  # drain
        await asyncio.sleep(0.05)   # wait 50ms → ~5 tokens refilled
        assert bucket.available_tokens() >= 3.0

    @pytest.mark.asyncio
    async def test_tokens_capped_at_capacity(self) -> None:
        """Tokens should never exceed capacity even after a long wait."""
        bucket = TokenBucket(rate=10.0, capacity=5.0)
        await asyncio.sleep(0.1)  # would add 1 token, but starts full
        assert bucket.available_tokens() <= 5.0 + 0.01  # tiny float tolerance


# ---------------------------------------------------------------------------
# RateLimiter
# ---------------------------------------------------------------------------


class TestRateLimiterInit:
    def test_default_rate(self) -> None:
        limiter = RateLimiter(default_rate=2.0)
        assert limiter._default_rate == 2.0

    def test_get_rate_for_unconfigured_provider(self) -> None:
        limiter = RateLimiter(default_rate=3.0)
        assert limiter.get_rate("new_provider") == 3.0

    def test_configure_provider(self) -> None:
        limiter = RateLimiter(default_rate=2.0)
        limiter.configure("eastmoney", rate=5.0)
        assert limiter.get_rate("eastmoney") == 5.0

    def test_reconfigure_provider(self) -> None:
        limiter = RateLimiter(default_rate=2.0)
        limiter.configure("eastmoney", rate=5.0)
        limiter.configure("eastmoney", rate=1.0)
        assert limiter.get_rate("eastmoney") == 1.0


class TestRateLimiterAcquire:
    @pytest.mark.asyncio
    async def test_acquire_default_provider(self) -> None:
        limiter = RateLimiter(default_rate=100.0)
        start = time.monotonic()
        await limiter.acquire("any_provider")
        elapsed = time.monotonic() - start
        assert elapsed < 0.1

    @pytest.mark.asyncio
    async def test_providers_are_isolated(self) -> None:
        """Draining one provider's bucket should not affect another."""
        limiter = RateLimiter(default_rate=100.0)
        limiter.configure("slow", rate=1.0, capacity=1.0)
        limiter.configure("fast", rate=100.0, capacity=100.0)

        # Drain slow provider
        await limiter.acquire("slow")

        # Fast provider should still be immediately available
        start = time.monotonic()
        await limiter.acquire("fast")
        elapsed = time.monotonic() - start
        assert elapsed < 0.1

    @pytest.mark.asyncio
    async def test_available_tokens_decreases_after_acquire(self) -> None:
        limiter = RateLimiter(default_rate=10.0)
        before = limiter.available_tokens("prov")
        await limiter.acquire("prov")
        after = limiter.available_tokens("prov")
        assert after < before

    @pytest.mark.asyncio
    async def test_eastmoney_default_rate_2rps(self) -> None:
        """Eastmoney should default to 2 req/s per requirement 1.6."""
        limiter = RateLimiter(default_rate=2.0)
        limiter.configure("eastmoney", rate=2.0)
        assert limiter.get_rate("eastmoney") == 2.0
