"""LLM Token budget control backed by Redis.

Implements daily and monthly spending limits for LLM API calls
(requirements 11.6, 11.7). When the budget is exhausted, non-critical
use cases are paused while critical paths continue to operate.

Key format
----------
``llm:budget:daily:{YYYY-MM-DD}``   — daily spend counter (USD)
``llm:budget:monthly:{YYYY-MM}``    — monthly spend counter (USD)

Configuration
-------------
* ``LLM_DAILY_BUDGET_USD`` — maximum daily spend in USD (default: 10.0)
* ``LLM_MONTHLY_BUDGET_USD`` — maximum monthly spend in USD (default: 200.0)
* ``LLM_CRITICAL_PATHS`` — comma-separated list of use cases that bypass
  budget limits (default: empty)

Design notes
------------
* Uses ``redis.asyncio`` for non-blocking I/O.
* Graceful degradation: Redis failures are logged but never block the
  caller. If Redis is unavailable, budget checks default to "not
  exhausted" (fail-open) to avoid breaking the LLM pipeline.
* Counters use Redis INCRBYFLOAT with automatic TTL expiry so stale
  keys are cleaned up without manual intervention.
* The budget tracks spend in USD (float) rather than tokens, since
  different models have different per-token pricing.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from enum import Enum

import redis.asyncio as aioredis

from app.core.logging import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Redis key prefix for budget counters.
BUDGET_KEY_PREFIX: str = "llm:budget"

#: Default daily budget in USD.
DEFAULT_DAILY_BUDGET_USD: float = 10.0

#: Default monthly budget in USD.
DEFAULT_MONTHLY_BUDGET_USD: float = 200.0

#: TTL for daily counter keys (2 days to handle timezone edge cases).
DAILY_KEY_TTL_SECONDS: int = 2 * 24 * 3600  # 172800

#: TTL for monthly counter keys (35 days to handle month boundaries).
MONTHLY_KEY_TTL_SECONDS: int = 35 * 24 * 3600  # 3024000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class BudgetPeriod(str, Enum):
    """Budget tracking periods."""

    DAILY = "daily"
    MONTHLY = "monthly"


def _daily_key(d: date) -> str:
    """Build the Redis key for a daily budget counter."""
    return f"{BUDGET_KEY_PREFIX}:daily:{d.isoformat()}"


def _monthly_key(d: date) -> str:
    """Build the Redis key for a monthly budget counter."""
    return f"{BUDGET_KEY_PREFIX}:monthly:{d.strftime('%Y-%m')}"


def _today() -> date:
    """Return today's date in UTC."""
    return datetime.now(timezone.utc).date()


# ---------------------------------------------------------------------------
# LLMBudget class
# ---------------------------------------------------------------------------


class LLMBudget:
    """Redis-backed LLM spending budget controller.

    Tracks daily and monthly spend in USD. When limits are reached,
    non-critical use cases are blocked while critical paths bypass the
    budget.

    Args:
        redis: An async Redis client instance. If not provided, one will
            be created from the REDIS_URL environment variable on first use.
        daily_usd_limit: Maximum daily spend in USD. Defaults to the
            ``LLM_DAILY_BUDGET_USD`` environment variable or 10.0.
        monthly_usd_limit: Maximum monthly spend in USD. Defaults to the
            ``LLM_MONTHLY_BUDGET_USD`` environment variable or 200.0.
        critical_paths: Set of use case identifiers that bypass budget
            limits. Defaults to the ``LLM_CRITICAL_PATHS`` environment
            variable (comma-separated) or an empty set.
    """

    def __init__(
        self,
        redis: aioredis.Redis | None = None,
        daily_usd_limit: float | None = None,
        monthly_usd_limit: float | None = None,
        critical_paths: set[str] | None = None,
    ) -> None:
        self._redis = redis

        # Load from env vars with fallback to defaults
        self.daily_usd_limit: float = (
            daily_usd_limit
            if daily_usd_limit is not None
            else float(os.environ.get("LLM_DAILY_BUDGET_USD", DEFAULT_DAILY_BUDGET_USD))
        )
        self.monthly_usd_limit: float = (
            monthly_usd_limit
            if monthly_usd_limit is not None
            else float(
                os.environ.get("LLM_MONTHLY_BUDGET_USD", DEFAULT_MONTHLY_BUDGET_USD)
            )
        )

        if critical_paths is not None:
            self.critical_paths: set[str] = critical_paths
        else:
            env_paths = os.environ.get("LLM_CRITICAL_PATHS", "")
            self.critical_paths = (
                {p.strip() for p in env_paths.split(",") if p.strip()}
                if env_paths
                else set()
            )

    async def _get_redis(self) -> aioredis.Redis:
        """Return the Redis client, creating one from env if needed."""
        if self._redis is None:
            redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
            self._redis = aioredis.from_url(
                redis_url,
                decode_responses=True,
                encoding="utf-8",
            )
        return self._redis

    async def _get_counter(self, key: str) -> float:
        """Read a budget counter from Redis. Returns 0.0 on miss or error."""
        try:
            client = await self._get_redis()
            value = await client.get(key)
            if value is not None:
                return float(value)
            return 0.0
        except Exception as exc:
            log.warning("llm_budget.get_counter.error", key=key, error=str(exc))
            return 0.0

    async def is_exhausted(self, use_case: str | None = None) -> bool:
        """Check if the budget is exhausted for the given use case.

        Critical use cases (those in ``critical_paths``) always return
        False, meaning they are never blocked by budget limits.

        Args:
            use_case: The LLM use case identifier. If provided and it's
                in ``critical_paths``, the budget check is bypassed.

        Returns:
            True if the budget is exhausted and the call should be
            blocked, False otherwise.
        """
        # Critical paths bypass budget limits
        if use_case and use_case in self.critical_paths:
            return False

        today = _today()

        try:
            daily_spend = await self._get_counter(_daily_key(today))
            if daily_spend >= self.daily_usd_limit:
                log.info(
                    "llm_budget.daily_exhausted",
                    use_case=use_case,
                    daily_spend=daily_spend,
                    daily_limit=self.daily_usd_limit,
                )
                return True

            monthly_spend = await self._get_counter(_monthly_key(today))
            if monthly_spend >= self.monthly_usd_limit:
                log.info(
                    "llm_budget.monthly_exhausted",
                    use_case=use_case,
                    monthly_spend=monthly_spend,
                    monthly_limit=self.monthly_usd_limit,
                )
                return True

            return False
        except Exception as exc:
            # Fail-open: if we can't check the budget, allow the call
            log.warning("llm_budget.is_exhausted.error", error=str(exc))
            return False

    async def consume(self, cost_usd: float) -> None:
        """Record a spend amount against both daily and monthly counters.

        Uses Redis INCRBYFLOAT to atomically increment the counters.
        Sets TTL on keys to ensure automatic cleanup.

        Args:
            cost_usd: The cost in USD to record. Must be non-negative.
        """
        if cost_usd <= 0:
            return

        today = _today()
        daily_key = _daily_key(today)
        monthly_key = _monthly_key(today)

        try:
            client = await self._get_redis()

            # Increment daily counter
            await client.incrbyfloat(daily_key, cost_usd)
            # Set TTL only if the key doesn't already have one
            ttl = await client.ttl(daily_key)
            if ttl == -1:  # No expiry set
                await client.expire(daily_key, DAILY_KEY_TTL_SECONDS)

            # Increment monthly counter
            await client.incrbyfloat(monthly_key, cost_usd)
            ttl = await client.ttl(monthly_key)
            if ttl == -1:  # No expiry set
                await client.expire(monthly_key, MONTHLY_KEY_TTL_SECONDS)

            log.debug(
                "llm_budget.consume",
                cost_usd=cost_usd,
                daily_key=daily_key,
                monthly_key=monthly_key,
            )
        except Exception as exc:
            log.warning("llm_budget.consume.error", cost_usd=cost_usd, error=str(exc))

    async def get_usage(self) -> dict:
        """Return current budget usage and limits.

        Returns:
            A dictionary with daily and monthly spend, limits, and
            remaining budget.
        """
        today = _today()

        daily_spend = await self._get_counter(_daily_key(today))
        monthly_spend = await self._get_counter(_monthly_key(today))

        return {
            "daily_spend_usd": round(daily_spend, 6),
            "daily_limit_usd": self.daily_usd_limit,
            "daily_remaining_usd": round(
                max(0.0, self.daily_usd_limit - daily_spend), 6
            ),
            "monthly_spend_usd": round(monthly_spend, 6),
            "monthly_limit_usd": self.monthly_usd_limit,
            "monthly_remaining_usd": round(
                max(0.0, self.monthly_usd_limit - monthly_spend), 6
            ),
            "date": today.isoformat(),
            "month": today.strftime("%Y-%m"),
        }

    async def close(self) -> None:
        """Close the Redis connection if owned by this instance."""
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

__all__ = [
    "BUDGET_KEY_PREFIX",
    "DEFAULT_DAILY_BUDGET_USD",
    "DEFAULT_MONTHLY_BUDGET_USD",
    "BudgetPeriod",
    "LLMBudget",
]
