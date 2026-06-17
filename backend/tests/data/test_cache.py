"""Unit tests for the Redis cache layer (app.data.cache).

Tests cover:
- Key naming conventions (fund:meta:{code}, fund:nav:{code}:{start}:{end})
- Cache read/write operations for fund metadata and NAV records
- Cache invalidation (single key and pattern-based)
- Graceful degradation on Redis errors
- Synchronous wrappers for Celery tasks

Uses fakeredis for isolated, in-memory Redis simulation.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.data.cache import (
    META_KEY_PREFIX,
    META_TTL_SECONDS,
    NAV_KEY_PREFIX,
    NAV_TTL_SECONDS,
    get_fund_meta,
    get_nav_records,
    invalidate_fund,
    invalidate_fund_meta,
    invalidate_nav,
    meta_key,
    nav_key,
    nav_pattern,
    set_fund_meta,
    set_nav_records,
    sync_invalidate_fund_meta,
    sync_invalidate_nav,
)


# ---------------------------------------------------------------------------
# Key naming tests
# ---------------------------------------------------------------------------


class TestKeyNaming:
    """Verify cache key format conventions."""

    def test_meta_key_format(self):
        """fund:meta:{code} format."""
        assert meta_key("000001") == "fund:meta:000001"
        assert meta_key("110011") == "fund:meta:110011"

    def test_nav_key_format(self):
        """fund:nav:{code}:{start}:{end} format with ISO dates."""
        start = date(2024, 1, 1)
        end = date(2024, 6, 30)
        expected = "fund:nav:000001:2024-01-01:2024-06-30"
        assert nav_key("000001", start, end) == expected

    def test_nav_pattern_format(self):
        """Pattern for glob-based invalidation."""
        assert nav_pattern("000001") == "fund:nav:000001:*"

    def test_key_prefixes(self):
        """Verify prefix constants."""
        assert META_KEY_PREFIX == "fund:meta"
        assert NAV_KEY_PREFIX == "fund:nav"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_meta() -> dict:
    """Sample fund metadata dict."""
    return {
        "code": "000001",
        "name": "华夏成长混合",
        "fund_type": "mixed",
        "company_id": "80000222",
        "inception_date": "2001-12-18",
        "management_fee": "0.0150",
        "custodian_fee": "0.0025",
        "currency": "CNY",
        "status": "active",
        "is_purchasable": True,
        "source": "eastmoney",
    }


@pytest.fixture
def sample_nav_records() -> list[dict]:
    """Sample NAV records list."""
    return [
        {
            "fund_code": "000001",
            "trade_date": "2024-01-02",
            "unit_nav": "1.2345",
            "accum_nav": "3.4567",
            "adj_nav": "2.8901",
            "daily_return": "0.0012",
            "status": "normal",
            "source": "eastmoney",
        },
        {
            "fund_code": "000001",
            "trade_date": "2024-01-03",
            "unit_nav": "1.2400",
            "accum_nav": "3.4622",
            "adj_nav": "2.8950",
            "daily_return": "0.0045",
            "status": "normal",
            "source": "eastmoney",
        },
    ]


# ---------------------------------------------------------------------------
# Mock Redis fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_redis():
    """Provide a mock async Redis client for testing cache operations."""
    mock_client = AsyncMock()
    # Storage dict to simulate Redis behavior
    storage: dict[str, str] = {}

    async def mock_get(key):
        return storage.get(key)

    async def mock_set(key, value, ex=None):
        storage[key] = value

    async def mock_delete(*keys):
        for key in keys:
            storage.pop(key, None)
        return len(keys)

    async def mock_scan_iter(match=None, count=100):
        """Simulate SCAN with pattern matching."""
        import fnmatch

        for key in list(storage.keys()):
            if match is None or fnmatch.fnmatch(key, match):
                yield key

    mock_client.get = AsyncMock(side_effect=mock_get)
    mock_client.set = AsyncMock(side_effect=mock_set)
    mock_client.delete = AsyncMock(side_effect=mock_delete)
    mock_client.scan_iter = mock_scan_iter
    mock_client._storage = storage  # expose for test assertions

    with patch("app.data.cache.get_redis", return_value=_async_return(mock_client)):
        # We need to patch get_redis to return the mock
        yield mock_client, storage


def _async_return(value):
    """Create an async function that returns a value."""

    async def _inner():
        return value

    return _inner()


@pytest.fixture
def patched_redis():
    """Patch get_redis to return a mock client with dict-based storage."""
    storage: dict[str, str] = {}
    mock_client = AsyncMock()

    async def mock_get(key):
        return storage.get(key)

    async def mock_set(key, value, ex=None):
        storage[key] = value

    async def mock_delete(*keys):
        count = 0
        for key in keys:
            if key in storage:
                del storage[key]
                count += 1
        return count

    async def mock_scan_iter(match=None, count=100):
        import fnmatch

        for key in list(storage.keys()):
            if match is None or fnmatch.fnmatch(key, match):
                yield key

    mock_client.get = mock_get
    mock_client.set = mock_set
    mock_client.delete = mock_delete
    mock_client.scan_iter = mock_scan_iter

    with patch("app.data.cache.get_redis", new=AsyncMock(return_value=mock_client)):
        yield mock_client, storage


# ---------------------------------------------------------------------------
# Cache read/write tests
# ---------------------------------------------------------------------------


class TestFundMetaCache:
    """Tests for fund metadata cache operations."""

    @pytest.mark.asyncio
    async def test_set_and_get_meta(self, patched_redis, sample_meta):
        """Write then read metadata from cache."""
        _, storage = patched_redis

        await set_fund_meta("000001", sample_meta)

        # Verify key was stored
        key = meta_key("000001")
        assert key in storage

        # Verify data can be read back
        result = await get_fund_meta("000001")
        assert result is not None
        assert result["code"] == "000001"
        assert result["name"] == "华夏成长混合"
        assert result["fund_type"] == "mixed"

    @pytest.mark.asyncio
    async def test_get_meta_cache_miss(self, patched_redis):
        """Return None on cache miss."""
        result = await get_fund_meta("999999")
        assert result is None

    @pytest.mark.asyncio
    async def test_invalidate_meta(self, patched_redis, sample_meta):
        """Invalidation removes the cached entry."""
        _, storage = patched_redis

        await set_fund_meta("000001", sample_meta)
        assert meta_key("000001") in storage

        await invalidate_fund_meta("000001")
        assert meta_key("000001") not in storage

        # Subsequent read returns None
        result = await get_fund_meta("000001")
        assert result is None


class TestNavCache:
    """Tests for NAV data cache operations."""

    @pytest.mark.asyncio
    async def test_set_and_get_nav(self, patched_redis, sample_nav_records):
        """Write then read NAV records from cache."""
        _, storage = patched_redis
        start = date(2024, 1, 1)
        end = date(2024, 1, 31)

        await set_nav_records("000001", start, end, sample_nav_records)

        key = nav_key("000001", start, end)
        assert key in storage

        result = await get_nav_records("000001", start, end)
        assert result is not None
        assert len(result) == 2
        assert result[0]["trade_date"] == "2024-01-02"
        assert result[1]["unit_nav"] == "1.2400"

    @pytest.mark.asyncio
    async def test_get_nav_cache_miss(self, patched_redis):
        """Return None on cache miss."""
        result = await get_nav_records("000001", date(2024, 1, 1), date(2024, 1, 31))
        assert result is None

    @pytest.mark.asyncio
    async def test_invalidate_nav_pattern(self, patched_redis, sample_nav_records):
        """Pattern-based invalidation removes all NAV keys for a fund."""
        _, storage = patched_redis

        # Store multiple NAV ranges for the same fund
        await set_nav_records(
            "000001", date(2024, 1, 1), date(2024, 1, 31), sample_nav_records
        )
        await set_nav_records(
            "000001", date(2024, 2, 1), date(2024, 2, 28), sample_nav_records
        )
        # Store NAV for a different fund (should not be affected)
        await set_nav_records(
            "000002", date(2024, 1, 1), date(2024, 1, 31), sample_nav_records
        )

        assert len(storage) == 3

        # Invalidate all NAV for fund 000001
        await invalidate_nav("000001")

        # Only fund 000002's cache should remain
        assert len(storage) == 1
        assert nav_key("000002", date(2024, 1, 1), date(2024, 1, 31)) in storage


class TestInvalidateFund:
    """Tests for combined fund invalidation."""

    @pytest.mark.asyncio
    async def test_invalidate_fund_removes_all(
        self, patched_redis, sample_meta, sample_nav_records
    ):
        """invalidate_fund removes both meta and NAV cache."""
        _, storage = patched_redis

        await set_fund_meta("000001", sample_meta)
        await set_nav_records(
            "000001", date(2024, 1, 1), date(2024, 1, 31), sample_nav_records
        )

        assert len(storage) == 2

        await invalidate_fund("000001")

        assert len(storage) == 0


# ---------------------------------------------------------------------------
# Graceful degradation tests
# ---------------------------------------------------------------------------


class TestGracefulDegradation:
    """Cache operations must not raise on Redis errors."""

    @pytest.mark.asyncio
    async def test_get_meta_on_redis_error(self):
        """get_fund_meta returns None when Redis raises."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=ConnectionError("Redis down"))

        with patch(
            "app.data.cache.get_redis", new=AsyncMock(return_value=mock_client)
        ):
            result = await get_fund_meta("000001")
            assert result is None

    @pytest.mark.asyncio
    async def test_set_meta_on_redis_error(self):
        """set_fund_meta silently logs warning when Redis raises."""
        mock_client = AsyncMock()
        mock_client.set = AsyncMock(side_effect=ConnectionError("Redis down"))

        with patch(
            "app.data.cache.get_redis", new=AsyncMock(return_value=mock_client)
        ):
            # Should not raise
            await set_fund_meta("000001", {"code": "000001", "name": "Test"})

    @pytest.mark.asyncio
    async def test_get_nav_on_redis_error(self):
        """get_nav_records returns None when Redis raises."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=TimeoutError("Redis timeout"))

        with patch(
            "app.data.cache.get_redis", new=AsyncMock(return_value=mock_client)
        ):
            result = await get_nav_records(
                "000001", date(2024, 1, 1), date(2024, 1, 31)
            )
            assert result is None

    @pytest.mark.asyncio
    async def test_invalidate_on_redis_error(self):
        """invalidate_fund_meta does not raise on Redis error."""
        mock_client = AsyncMock()
        mock_client.delete = AsyncMock(side_effect=ConnectionError("Redis down"))

        with patch(
            "app.data.cache.get_redis", new=AsyncMock(return_value=mock_client)
        ):
            # Should not raise
            await invalidate_fund_meta("000001")


# ---------------------------------------------------------------------------
# Sync wrapper tests
# ---------------------------------------------------------------------------


class TestSyncWrappers:
    """Tests for synchronous wrappers used by Celery tasks."""

    def test_sync_invalidate_fund_meta(self):
        """sync_invalidate_fund_meta calls the async version."""
        with patch(
            "app.data.cache.invalidate_fund_meta", new_callable=AsyncMock
        ) as mock_inv:
            sync_invalidate_fund_meta("000001")
            mock_inv.assert_called_once_with("000001")

    def test_sync_invalidate_nav(self):
        """sync_invalidate_nav calls the async version."""
        with patch(
            "app.data.cache.invalidate_nav", new_callable=AsyncMock
        ) as mock_inv:
            sync_invalidate_nav("000001")
            mock_inv.assert_called_once_with("000001")

    def test_sync_invalidate_meta_handles_error(self):
        """sync_invalidate_fund_meta does not raise on error."""
        with patch(
            "app.data.cache.invalidate_fund_meta",
            new_callable=AsyncMock,
            side_effect=ConnectionError("Redis down"),
        ):
            # Should not raise
            sync_invalidate_fund_meta("000001")

    def test_sync_invalidate_nav_handles_error(self):
        """sync_invalidate_nav does not raise on error."""
        with patch(
            "app.data.cache.invalidate_nav",
            new_callable=AsyncMock,
            side_effect=ConnectionError("Redis down"),
        ):
            # Should not raise
            sync_invalidate_nav("000001")


# ---------------------------------------------------------------------------
# TTL constant tests
# ---------------------------------------------------------------------------


class TestTTLConstants:
    """Verify TTL values are reasonable."""

    def test_meta_ttl(self):
        """Metadata TTL is 1 hour."""
        assert META_TTL_SECONDS == 3600

    def test_nav_ttl(self):
        """NAV TTL is 30 minutes."""
        assert NAV_TTL_SECONDS == 1800
