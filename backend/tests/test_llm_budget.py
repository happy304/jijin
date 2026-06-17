"""Unit tests for the LLM budget module (app/ai/budget.py).

Tests use a mock Redis client to verify budget control logic without
requiring a live Redis instance.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ai.budget import (
    BUDGET_KEY_PREFIX,
    DAILY_KEY_TTL_SECONDS,
    DEFAULT_DAILY_BUDGET_USD,
    DEFAULT_MONTHLY_BUDGET_USD,
    MONTHLY_KEY_TTL_SECONDS,
    LLMBudget,
    _daily_key,
    _monthly_key,
)


# ---------------------------------------------------------------------------
# Key generation tests
# ---------------------------------------------------------------------------


class TestKeyGeneration:
    """Tests for Redis key generation helpers."""

    def test_daily_key_format(self) -> None:
        """Daily key should follow the expected format."""
        d = date(2024, 6, 15)
        key = _daily_key(d)
        assert key == f"{BUDGET_KEY_PREFIX}:daily:2024-06-15"

    def test_monthly_key_format(self) -> None:
        """Monthly key should follow the expected format."""
        d = date(2024, 6, 15)
        key = _monthly_key(d)
        assert key == f"{BUDGET_KEY_PREFIX}:monthly:2024-06"

    def test_daily_key_different_dates(self) -> None:
        """Different dates should produce different daily keys."""
        key1 = _daily_key(date(2024, 1, 1))
        key2 = _daily_key(date(2024, 1, 2))
        assert key1 != key2

    def test_monthly_key_same_month(self) -> None:
        """Dates in the same month should produce the same monthly key."""
        key1 = _monthly_key(date(2024, 6, 1))
        key2 = _monthly_key(date(2024, 6, 30))
        assert key1 == key2


# ---------------------------------------------------------------------------
# LLMBudget tests
# ---------------------------------------------------------------------------


class TestLLMBudget:
    """Tests for the LLMBudget class."""

    @pytest.fixture
    def mock_redis(self) -> AsyncMock:
        """Create a mock async Redis client."""
        client = AsyncMock()
        client.get = AsyncMock(return_value=None)
        client.set = AsyncMock(return_value=True)
        client.incrbyfloat = AsyncMock(return_value=0.0)
        client.ttl = AsyncMock(return_value=-1)
        client.expire = AsyncMock(return_value=True)
        client.aclose = AsyncMock()
        return client

    @pytest.fixture
    def budget(self, mock_redis: AsyncMock) -> LLMBudget:
        """Create an LLMBudget instance with a mock Redis client."""
        return LLMBudget(
            redis=mock_redis,
            daily_usd_limit=10.0,
            monthly_usd_limit=200.0,
            critical_paths={"announcement_parse", "emergency_alert"},
        )

    # --- Constructor tests ---

    def test_default_limits(self) -> None:
        """LLMBudget should use default limits when not specified."""
        b = LLMBudget(redis=AsyncMock())
        assert b.daily_usd_limit == DEFAULT_DAILY_BUDGET_USD
        assert b.monthly_usd_limit == DEFAULT_MONTHLY_BUDGET_USD

    def test_custom_limits(self) -> None:
        """LLMBudget should accept custom limits."""
        b = LLMBudget(
            redis=AsyncMock(),
            daily_usd_limit=5.0,
            monthly_usd_limit=100.0,
        )
        assert b.daily_usd_limit == 5.0
        assert b.monthly_usd_limit == 100.0

    def test_critical_paths_from_constructor(self) -> None:
        """LLMBudget should accept critical_paths from constructor."""
        paths = {"path_a", "path_b"}
        b = LLMBudget(redis=AsyncMock(), critical_paths=paths)
        assert b.critical_paths == paths

    def test_critical_paths_from_env(self) -> None:
        """LLMBudget should load critical_paths from env var."""
        with patch.dict(
            "os.environ",
            {"LLM_CRITICAL_PATHS": "path_a, path_b, path_c"},
        ):
            b = LLMBudget(redis=AsyncMock())
            assert b.critical_paths == {"path_a", "path_b", "path_c"}

    def test_critical_paths_empty_env(self) -> None:
        """LLMBudget should handle empty LLM_CRITICAL_PATHS env var."""
        with patch.dict("os.environ", {"LLM_CRITICAL_PATHS": ""}):
            b = LLMBudget(redis=AsyncMock())
            assert b.critical_paths == set()

    def test_limits_from_env(self) -> None:
        """LLMBudget should load limits from env vars."""
        with patch.dict(
            "os.environ",
            {
                "LLM_DAILY_BUDGET_USD": "25.5",
                "LLM_MONTHLY_BUDGET_USD": "500.0",
            },
        ):
            b = LLMBudget(redis=AsyncMock())
            assert b.daily_usd_limit == 25.5
            assert b.monthly_usd_limit == 500.0

    # --- is_exhausted() tests ---

    @pytest.mark.asyncio
    async def test_not_exhausted_when_under_limit(
        self, budget: LLMBudget, mock_redis: AsyncMock
    ) -> None:
        """is_exhausted() should return False when spend is under limits."""
        mock_redis.get.return_value = "5.0"  # Under daily limit of 10.0

        result = await budget.is_exhausted("some_use_case")

        assert result is False

    @pytest.mark.asyncio
    async def test_exhausted_when_daily_limit_reached(
        self, budget: LLMBudget, mock_redis: AsyncMock
    ) -> None:
        """is_exhausted() should return True when daily limit is reached."""
        mock_redis.get.return_value = "10.0"  # Exactly at daily limit

        result = await budget.is_exhausted("some_use_case")

        assert result is True

    @pytest.mark.asyncio
    async def test_exhausted_when_daily_limit_exceeded(
        self, budget: LLMBudget, mock_redis: AsyncMock
    ) -> None:
        """is_exhausted() should return True when daily limit is exceeded."""
        mock_redis.get.return_value = "15.0"  # Over daily limit

        result = await budget.is_exhausted("some_use_case")

        assert result is True

    @pytest.mark.asyncio
    async def test_exhausted_when_monthly_limit_reached(
        self, budget: LLMBudget, mock_redis: AsyncMock
    ) -> None:
        """is_exhausted() should return True when monthly limit is reached."""
        # Daily is under limit, but monthly is at limit
        async def mock_get(key: str):
            if "daily" in key:
                return "5.0"  # Under daily limit
            elif "monthly" in key:
                return "200.0"  # At monthly limit
            return None

        mock_redis.get = AsyncMock(side_effect=mock_get)

        result = await budget.is_exhausted("some_use_case")

        assert result is True

    @pytest.mark.asyncio
    async def test_critical_path_bypasses_budget(
        self, budget: LLMBudget, mock_redis: AsyncMock
    ) -> None:
        """is_exhausted() should return False for critical use cases."""
        mock_redis.get.return_value = "999.0"  # Way over any limit

        result = await budget.is_exhausted("announcement_parse")

        assert result is False
        # Redis should not even be queried for critical paths
        mock_redis.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_critical_path_emergency_alert(
        self, budget: LLMBudget, mock_redis: AsyncMock
    ) -> None:
        """Another critical path should also bypass budget."""
        mock_redis.get.return_value = "999.0"

        result = await budget.is_exhausted("emergency_alert")

        assert result is False

    @pytest.mark.asyncio
    async def test_non_critical_path_blocked(
        self, budget: LLMBudget, mock_redis: AsyncMock
    ) -> None:
        """Non-critical use cases should be blocked when budget exhausted."""
        mock_redis.get.return_value = "10.0"

        result = await budget.is_exhausted("factor_brainstorm")

        assert result is True

    @pytest.mark.asyncio
    async def test_none_use_case_checks_budget(
        self, budget: LLMBudget, mock_redis: AsyncMock
    ) -> None:
        """is_exhausted(None) should check budget normally."""
        mock_redis.get.return_value = "10.0"

        result = await budget.is_exhausted(None)

        assert result is True

    @pytest.mark.asyncio
    async def test_is_exhausted_graceful_on_redis_error(
        self, budget: LLMBudget, mock_redis: AsyncMock
    ) -> None:
        """is_exhausted() should return False (fail-open) on Redis errors."""
        mock_redis.get.side_effect = ConnectionError("Redis down")

        result = await budget.is_exhausted("some_use_case")

        assert result is False

    @pytest.mark.asyncio
    async def test_not_exhausted_when_no_spend(
        self, budget: LLMBudget, mock_redis: AsyncMock
    ) -> None:
        """is_exhausted() should return False when no spend recorded."""
        mock_redis.get.return_value = None  # No counter exists

        result = await budget.is_exhausted("some_use_case")

        assert result is False

    # --- consume() tests ---

    @pytest.mark.asyncio
    async def test_consume_increments_counters(
        self, budget: LLMBudget, mock_redis: AsyncMock
    ) -> None:
        """consume() should increment both daily and monthly counters."""
        await budget.consume(0.05)

        # Should call incrbyfloat twice (daily + monthly)
        assert mock_redis.incrbyfloat.call_count == 2

        # Verify the calls include the cost
        calls = mock_redis.incrbyfloat.call_args_list
        assert calls[0][0][1] == 0.05  # daily increment
        assert calls[1][0][1] == 0.05  # monthly increment

    @pytest.mark.asyncio
    async def test_consume_sets_ttl_on_new_keys(
        self, budget: LLMBudget, mock_redis: AsyncMock
    ) -> None:
        """consume() should set TTL on keys that don't have one."""
        mock_redis.ttl.return_value = -1  # No TTL set

        await budget.consume(0.05)

        # Should set expire on both keys
        assert mock_redis.expire.call_count == 2
        expire_calls = mock_redis.expire.call_args_list
        assert expire_calls[0][0][1] == DAILY_KEY_TTL_SECONDS
        assert expire_calls[1][0][1] == MONTHLY_KEY_TTL_SECONDS

    @pytest.mark.asyncio
    async def test_consume_does_not_reset_existing_ttl(
        self, budget: LLMBudget, mock_redis: AsyncMock
    ) -> None:
        """consume() should not reset TTL on keys that already have one."""
        mock_redis.ttl.return_value = 50000  # Already has TTL

        await budget.consume(0.05)

        # Should NOT call expire since TTL already exists
        mock_redis.expire.assert_not_called()

    @pytest.mark.asyncio
    async def test_consume_zero_cost_is_noop(
        self, budget: LLMBudget, mock_redis: AsyncMock
    ) -> None:
        """consume(0) should not interact with Redis."""
        await budget.consume(0.0)

        mock_redis.incrbyfloat.assert_not_called()

    @pytest.mark.asyncio
    async def test_consume_negative_cost_is_noop(
        self, budget: LLMBudget, mock_redis: AsyncMock
    ) -> None:
        """consume() with negative cost should not interact with Redis."""
        await budget.consume(-1.0)

        mock_redis.incrbyfloat.assert_not_called()

    @pytest.mark.asyncio
    async def test_consume_graceful_on_redis_error(
        self, budget: LLMBudget, mock_redis: AsyncMock
    ) -> None:
        """consume() should not raise on Redis errors."""
        mock_redis.incrbyfloat.side_effect = ConnectionError("Redis down")

        # Should not raise
        await budget.consume(0.05)

    # --- get_usage() tests ---

    @pytest.mark.asyncio
    async def test_get_usage_returns_correct_structure(
        self, budget: LLMBudget, mock_redis: AsyncMock
    ) -> None:
        """get_usage() should return a dict with all expected fields."""
        mock_redis.get.return_value = "3.5"

        usage = await budget.get_usage()

        assert "daily_spend_usd" in usage
        assert "daily_limit_usd" in usage
        assert "daily_remaining_usd" in usage
        assert "monthly_spend_usd" in usage
        assert "monthly_limit_usd" in usage
        assert "monthly_remaining_usd" in usage
        assert "date" in usage
        assert "month" in usage

    @pytest.mark.asyncio
    async def test_get_usage_correct_values(
        self, budget: LLMBudget, mock_redis: AsyncMock
    ) -> None:
        """get_usage() should return correct spend and remaining values."""
        async def mock_get(key: str):
            if "daily" in key:
                return "3.5"
            elif "monthly" in key:
                return "50.0"
            return None

        mock_redis.get = AsyncMock(side_effect=mock_get)

        usage = await budget.get_usage()

        assert usage["daily_spend_usd"] == 3.5
        assert usage["daily_limit_usd"] == 10.0
        assert usage["daily_remaining_usd"] == 6.5
        assert usage["monthly_spend_usd"] == 50.0
        assert usage["monthly_limit_usd"] == 200.0
        assert usage["monthly_remaining_usd"] == 150.0

    @pytest.mark.asyncio
    async def test_get_usage_remaining_never_negative(
        self, budget: LLMBudget, mock_redis: AsyncMock
    ) -> None:
        """get_usage() remaining should be 0 when spend exceeds limit."""
        mock_redis.get.return_value = "999.0"

        usage = await budget.get_usage()

        assert usage["daily_remaining_usd"] == 0.0
        assert usage["monthly_remaining_usd"] == 0.0

    @pytest.mark.asyncio
    async def test_get_usage_no_spend(
        self, budget: LLMBudget, mock_redis: AsyncMock
    ) -> None:
        """get_usage() should show full remaining when no spend."""
        mock_redis.get.return_value = None

        usage = await budget.get_usage()

        assert usage["daily_spend_usd"] == 0.0
        assert usage["daily_remaining_usd"] == 10.0
        assert usage["monthly_spend_usd"] == 0.0
        assert usage["monthly_remaining_usd"] == 200.0

    # --- close() tests ---

    @pytest.mark.asyncio
    async def test_close(self, budget: LLMBudget, mock_redis: AsyncMock) -> None:
        """close() should close the Redis connection."""
        await budget.close()

        mock_redis.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_sets_redis_to_none(
        self, budget: LLMBudget, mock_redis: AsyncMock
    ) -> None:
        """close() should set internal redis reference to None."""
        await budget.close()

        assert budget._redis is None

    # --- Auto-create Redis from env ---

    @pytest.mark.asyncio
    async def test_auto_creates_redis_from_env(self) -> None:
        """LLMBudget should create a Redis client from env if none provided."""
        budget = LLMBudget()

        with patch.dict("os.environ", {"REDIS_URL": "redis://testhost:6379/0"}), \
             patch("app.ai.budget.aioredis") as mock_aioredis:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=None)
            mock_aioredis.from_url.return_value = mock_client

            result = await budget.is_exhausted("test")

            assert result is False
            mock_aioredis.from_url.assert_called_once_with(
                "redis://testhost:6379/0",
                decode_responses=True,
                encoding="utf-8",
            )

    # --- Integration-style test (with mock) ---

    @pytest.mark.asyncio
    async def test_consume_then_check_exhausted(
        self, mock_redis: AsyncMock
    ) -> None:
        """After consuming the full budget, is_exhausted should return True."""
        # Simulate a counter that tracks spend
        counters: dict[str, float] = {}

        async def mock_incrbyfloat(key: str, amount: float):
            counters[key] = counters.get(key, 0.0) + amount
            return counters[key]

        async def mock_get(key: str):
            val = counters.get(key)
            return str(val) if val is not None else None

        mock_redis.incrbyfloat = AsyncMock(side_effect=mock_incrbyfloat)
        mock_redis.get = AsyncMock(side_effect=mock_get)
        mock_redis.ttl = AsyncMock(return_value=-1)
        mock_redis.expire = AsyncMock()

        budget = LLMBudget(
            redis=mock_redis,
            daily_usd_limit=1.0,
            monthly_usd_limit=10.0,
            critical_paths=set(),
        )

        # Initially not exhausted
        assert await budget.is_exhausted("test") is False

        # Consume half the daily budget
        await budget.consume(0.5)
        assert await budget.is_exhausted("test") is False

        # Consume the rest of the daily budget
        await budget.consume(0.5)
        assert await budget.is_exhausted("test") is True

    @pytest.mark.asyncio
    async def test_critical_path_works_even_when_exhausted(
        self, mock_redis: AsyncMock
    ) -> None:
        """Critical paths should work even when budget is fully exhausted."""
        mock_redis.get.return_value = "999.0"

        budget = LLMBudget(
            redis=mock_redis,
            daily_usd_limit=1.0,
            monthly_usd_limit=10.0,
            critical_paths={"critical_task"},
        )

        # Non-critical should be blocked
        assert await budget.is_exhausted("normal_task") is True

        # Critical should pass through
        assert await budget.is_exhausted("critical_task") is False
