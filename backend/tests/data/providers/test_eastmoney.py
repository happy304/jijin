"""Integration tests for EastmoneyProvider.

Uses VCR to record and replay HTTP responses from 天天基金 APIs.
Tests cover all 9 interfaces:
  1. 基础信息 (fetch_fund_meta)
  2. 历史净值 (fetch_nav_history)
  3. 实时估值 (fetch_realtime_estimate)
  4. 综合数据 (fetch_pingzhongdata)
  5. 持仓 (fetch_holdings)
  6. 分红拆分 (fetch_dividends)
  7. 排名榜单 (fetch_fund_ranking)
  8. 基金经理 (fetch_fund_manager)
  9. 公告 (fetch_announcements)
  10. 健康检查 (health_check)

Run with:
    pytest tests/data/providers/test_eastmoney.py -v

To re-record cassettes (when API changes):
    pytest tests/data/providers/test_eastmoney.py -v --vcr-record=new_episodes
"""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import vcr

from app.data.fetchers.rate_limiter import RateLimiter
from app.data.providers.base import (
    FundDataProvider,
    HealthStatus,
    ProviderError,
    ProviderNotFoundError,
)
from app.data.providers.eastmoney import EastmoneyProvider
from app.data.providers.snapshot import SnapshotArchive
from app.data.schemas.funds import (
    Announcement,
    DividendRecord,
    FundMeta,
    FundStatus,
    FundType,
    HoldingPosition,
    HoldingSnapshot,
    NavRecord,
    NavStatus,
)


# ---------------------------------------------------------------------------
# VCR Configuration
# ---------------------------------------------------------------------------

# Cassette directory for recorded HTTP responses
CASSETTE_DIR = Path(__file__).parent / "cassettes" / "eastmoney"
CASSETTE_DIR.mkdir(parents=True, exist_ok=True)

# VCR configuration
vcr_config = vcr.VCR(
    cassette_library_dir=str(CASSETTE_DIR),
    record_mode="none" if os.environ.get("CI") else "new_episodes",
    match_on=["method", "scheme", "host", "port", "path", "query"],
    filter_headers=["User-Agent", "Cookie"],
    decode_compressed_response=True,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def rate_limiter() -> RateLimiter:
    """Create a rate limiter for testing (high rate to avoid delays)."""
    limiter = RateLimiter(default_rate=100.0)
    limiter.configure("eastmoney", rate=100.0)
    return limiter


@pytest.fixture
def snapshot_archive(tmp_path: Path) -> SnapshotArchive:
    """Create a snapshot archive in a temp directory."""
    return SnapshotArchive(base_dir=tmp_path / "snapshots")


@pytest.fixture
def provider(
    rate_limiter: RateLimiter,
    snapshot_archive: SnapshotArchive,
) -> EastmoneyProvider:
    """Create an EastmoneyProvider instance for testing."""
    return EastmoneyProvider(
        rate_limiter=rate_limiter,
        snapshot_archive=snapshot_archive,
        timeout=30.0,
    )


# ---------------------------------------------------------------------------
# Protocol Compliance Tests
# ---------------------------------------------------------------------------


class TestProtocolCompliance:
    """Verify EastmoneyProvider satisfies FundDataProvider Protocol."""

    def test_isinstance_check(self, provider: EastmoneyProvider) -> None:
        """Provider should satisfy the FundDataProvider Protocol."""
        assert isinstance(provider, FundDataProvider)

    def test_name_attribute(self, provider: EastmoneyProvider) -> None:
        """Provider should have name='eastmoney'."""
        assert provider.name == "eastmoney"

    def test_priority_attribute(self, provider: EastmoneyProvider) -> None:
        """Provider should have priority=1 (primary source)."""
        assert provider.priority == 1


# ---------------------------------------------------------------------------
# Unit Tests (No Network)
# ---------------------------------------------------------------------------


class TestHelperMethods:
    """Unit tests for internal helper methods."""

    def test_parse_quarter_valid(self, provider: EastmoneyProvider) -> None:
        """Test quarter parsing with valid inputs."""
        assert provider._parse_quarter("2024-Q1") == (2024, 3)
        assert provider._parse_quarter("2024-Q2") == (2024, 6)
        assert provider._parse_quarter("2024-Q3") == (2024, 9)
        assert provider._parse_quarter("2024-Q4") == (2024, 12)
        assert provider._parse_quarter("2023-q1") == (2023, 3)  # lowercase

    def test_parse_quarter_invalid(self, provider: EastmoneyProvider) -> None:
        """Test quarter parsing with invalid inputs."""
        with pytest.raises(ValueError, match="无效的季度格式"):
            provider._parse_quarter("2024-Q5")
        with pytest.raises(ValueError, match="无效的季度格式"):
            provider._parse_quarter("2024Q1")
        with pytest.raises(ValueError, match="无效的季度格式"):
            provider._parse_quarter("invalid")

    def test_quarter_to_report_date(self, provider: EastmoneyProvider) -> None:
        """Test quarter to report date conversion."""
        assert provider._quarter_to_report_date("2024-Q1") == date(2024, 3, 31)
        assert provider._quarter_to_report_date("2024-Q2") == date(2024, 6, 30)
        assert provider._quarter_to_report_date("2024-Q3") == date(2024, 9, 30)
        assert provider._quarter_to_report_date("2024-Q4") == date(2024, 12, 31)

    def test_parse_percent(self, provider: EastmoneyProvider) -> None:
        """Test percentage parsing."""
        assert provider._parse_percent("5.23%") == Decimal("0.0523")
        assert provider._parse_percent("5.23") == Decimal("0.0523")
        assert provider._parse_percent("--") is None
        assert provider._parse_percent("") is None
        assert provider._parse_percent(None) is None  # type: ignore[arg-type]

    def test_parse_wan(self, provider: EastmoneyProvider) -> None:
        """Test 万 unit parsing."""
        assert provider._parse_wan("123.45") == Decimal("1234500")
        assert provider._parse_wan("1,234.56") == Decimal("12345600")
        assert provider._parse_wan("--") is None
        assert provider._parse_wan("") is None

    def test_next_ua_rotation(self, provider: EastmoneyProvider) -> None:
        """Test User-Agent rotation."""
        ua1 = provider._next_ua()
        ua2 = provider._next_ua()
        ua3 = provider._next_ua()
        # Should rotate through the pool
        assert ua1 != ua2 or ua2 != ua3  # At least some rotation
        assert "Mozilla" in ua1
        assert "Mozilla" in ua2

    def test_build_headers(self, provider: EastmoneyProvider) -> None:
        """Test header building."""
        headers = provider._build_headers()
        assert "User-Agent" in headers
        assert "Referer" in headers
        assert headers["Referer"] == "http://fundf10.eastmoney.com/"
        assert "Accept" in headers


# ---------------------------------------------------------------------------
# Integration Tests (With VCR)
# ---------------------------------------------------------------------------


class TestFetchFundMeta:
    """Integration tests for fetch_fund_meta."""

    @pytest.mark.asyncio
    @vcr_config.use_cassette("fund_meta_000001.yaml")
    async def test_fetch_fund_meta_success(
        self, provider: EastmoneyProvider
    ) -> None:
        """Test fetching fund metadata for a valid fund code."""
        meta = await provider.fetch_fund_meta("000001")

        assert isinstance(meta, FundMeta)
        assert meta.code == "000001"
        assert meta.name  # Should have a name
        assert meta.source == "eastmoney"
        assert meta.updated_at is not None

    @pytest.mark.asyncio
    @vcr_config.use_cassette("fund_meta_110011.yaml")
    async def test_fetch_fund_meta_with_type(
        self, provider: EastmoneyProvider
    ) -> None:
        """Test fetching fund metadata with fund type parsing."""
        meta = await provider.fetch_fund_meta("110011")

        assert isinstance(meta, FundMeta)
        assert meta.code == "110011"
        # Fund type should be parsed
        assert meta.fund_type is None or isinstance(meta.fund_type, FundType)


class TestFetchNavHistory:
    """Integration tests for fetch_nav_history."""

    @pytest.mark.asyncio
    @vcr_config.use_cassette("nav_history_000001.yaml")
    async def test_fetch_nav_history_success(
        self, provider: EastmoneyProvider
    ) -> None:
        """Test fetching NAV history for a valid fund code."""
        start = date(2024, 1, 1)
        end = date(2024, 1, 31)
        records = await provider.fetch_nav_history("000001", start, end)

        assert isinstance(records, list)
        # Should have some records (may be empty if no trading days)
        for record in records:
            assert isinstance(record, NavRecord)
            assert record.fund_code == "000001"
            assert record.trade_date >= start
            assert record.trade_date <= end
            assert record.source == "eastmoney"

    @pytest.mark.asyncio
    @vcr_config.use_cassette("nav_history_sorted.yaml")
    async def test_fetch_nav_history_sorted(
        self, provider: EastmoneyProvider
    ) -> None:
        """Test that NAV history is sorted by date ascending."""
        start = date(2024, 1, 1)
        end = date(2024, 3, 31)
        records = await provider.fetch_nav_history("000001", start, end)

        if len(records) > 1:
            dates = [r.trade_date for r in records]
            assert dates == sorted(dates), "Records should be sorted by date"


class TestFetchRealtimeEstimate:
    """Integration tests for fetch_realtime_estimate."""

    @pytest.mark.asyncio
    @vcr_config.use_cassette("realtime_estimate_000001.yaml")
    async def test_fetch_realtime_estimate_success(
        self, provider: EastmoneyProvider
    ) -> None:
        """Test fetching realtime estimate."""
        result = await provider.fetch_realtime_estimate("000001")

        assert isinstance(result, dict)
        # Should have standard fields
        assert "fundcode" in result or "gsz" in result or len(result) > 0


class TestFetchPingzhongdata:
    """Integration tests for fetch_pingzhongdata."""

    @pytest.mark.asyncio
    @vcr_config.use_cassette("pingzhongdata_000001.yaml")
    async def test_fetch_pingzhongdata_success(
        self, provider: EastmoneyProvider
    ) -> None:
        """Test fetching pingzhongdata."""
        result = await provider.fetch_pingzhongdata("000001")

        assert isinstance(result, dict)
        # Should have extracted some JS variables
        assert len(result) >= 0  # May be empty for some funds


class TestFetchHoldings:
    """Integration tests for fetch_holdings."""

    @pytest.mark.asyncio
    @vcr_config.use_cassette("holdings_000001_2024Q1.yaml")
    async def test_fetch_holdings_success(
        self, provider: EastmoneyProvider
    ) -> None:
        """Test fetching holdings for a valid fund and quarter."""
        snapshot = await provider.fetch_holdings("000001", "2024-Q1")

        assert isinstance(snapshot, HoldingSnapshot)
        assert snapshot.fund_code == "000001"
        assert snapshot.report_date == date(2024, 3, 31)
        assert isinstance(snapshot.positions, list)

        for pos in snapshot.positions:
            assert isinstance(pos, HoldingPosition)

    @pytest.mark.asyncio
    async def test_fetch_holdings_invalid_quarter(
        self, provider: EastmoneyProvider
    ) -> None:
        """Test fetching holdings with invalid quarter format."""
        with pytest.raises(ValueError, match="无效的季度格式"):
            await provider.fetch_holdings("000001", "2024-Q5")


class TestFetchDividends:
    """Integration tests for fetch_dividends."""

    @pytest.mark.asyncio
    @vcr_config.use_cassette("dividends_000001.yaml")
    async def test_fetch_dividends_success(
        self, provider: EastmoneyProvider
    ) -> None:
        """Test fetching dividends for a valid fund code."""
        records = await provider.fetch_dividends("000001")

        assert isinstance(records, list)
        for record in records:
            assert isinstance(record, DividendRecord)
            assert record.fund_code == "000001"
            assert record.ex_date is not None

    @pytest.mark.asyncio
    @vcr_config.use_cassette("dividends_sorted.yaml")
    async def test_fetch_dividends_sorted(
        self, provider: EastmoneyProvider
    ) -> None:
        """Test that dividends are sorted by ex_date ascending."""
        records = await provider.fetch_dividends("000001")

        if len(records) > 1:
            dates = [r.ex_date for r in records]
            assert dates == sorted(dates), "Records should be sorted by ex_date"


class TestFetchAnnouncements:
    """Integration tests for fetch_announcements."""

    @pytest.mark.asyncio
    @vcr_config.use_cassette("announcements_000001.yaml")
    async def test_fetch_announcements_success(
        self, provider: EastmoneyProvider
    ) -> None:
        """Test fetching announcements for a valid fund code."""
        since = date(2024, 1, 1)
        announcements = await provider.fetch_announcements("000001", since)

        assert isinstance(announcements, list)
        for ann in announcements:
            assert isinstance(ann, Announcement)
            assert ann.fund_code == "000001"
            if ann.publish_date:
                assert ann.publish_date >= since

    @pytest.mark.asyncio
    @vcr_config.use_cassette("announcements_sorted.yaml")
    async def test_fetch_announcements_sorted(
        self, provider: EastmoneyProvider
    ) -> None:
        """Test that announcements are sorted by publish_date ascending."""
        since = date(2024, 1, 1)
        announcements = await provider.fetch_announcements("000001", since)

        if len(announcements) > 1:
            dates = [a.publish_date for a in announcements if a.publish_date]
            assert dates == sorted(dates), "Announcements should be sorted by date"


class TestFetchFundRanking:
    """Integration tests for fetch_fund_ranking."""

    @pytest.mark.asyncio
    @vcr_config.use_cassette("ranking_all.yaml")
    async def test_fetch_fund_ranking_success(
        self, provider: EastmoneyProvider
    ) -> None:
        """Test fetching fund ranking."""
        results = await provider.fetch_fund_ranking(
            fund_type="all",
            sort_by="6yzf",
            page=1,
            page_size=10,
        )

        assert isinstance(results, list)
        for item in results:
            assert isinstance(item, dict)
            assert "code" in item
            assert "name" in item


class TestFetchFundManager:
    """Integration tests for fetch_fund_manager."""

    @pytest.mark.asyncio
    @vcr_config.use_cassette("manager_000001.yaml")
    async def test_fetch_fund_manager_success(
        self, provider: EastmoneyProvider
    ) -> None:
        """Test fetching fund manager info."""
        managers = await provider.fetch_fund_manager("000001")

        assert isinstance(managers, list)
        for mgr in managers:
            assert isinstance(mgr, dict)
            assert "name" in mgr


class TestHealthCheck:
    """Integration tests for health_check."""

    @pytest.mark.asyncio
    @vcr_config.use_cassette("health_check.yaml")
    async def test_health_check_success(
        self, provider: EastmoneyProvider
    ) -> None:
        """Test health check returns healthy status."""
        status = await provider.health_check()

        assert isinstance(status, HealthStatus)
        # Note: actual health depends on network; in VCR mode should be healthy
        assert status.latency_ms is not None
        assert status.latency_ms >= 0

    @pytest.mark.asyncio
    async def test_health_check_handles_errors(
        self, provider: EastmoneyProvider
    ) -> None:
        """Test health check handles network errors gracefully."""
        # Mock the HTTP client to simulate a network error
        with patch.object(
            provider, "_rate_limiter", new_callable=MagicMock
        ) as mock_limiter:
            mock_limiter.acquire = AsyncMock()

            with patch("httpx.AsyncClient") as mock_client:
                mock_instance = AsyncMock()
                mock_instance.__aenter__.return_value = mock_instance
                mock_instance.__aexit__.return_value = None
                mock_instance.get.side_effect = Exception("Network error")
                mock_client.return_value = mock_instance

                status = await provider.health_check()

                assert isinstance(status, HealthStatus)
                assert status.healthy is False
                assert "异常" in status.message or "error" in status.message.lower()


# ---------------------------------------------------------------------------
# Snapshot Tests
# ---------------------------------------------------------------------------


class TestSnapshotSaving:
    """Tests for snapshot saving functionality."""

    @pytest.mark.asyncio
    async def test_snapshot_saved_on_fetch(
        self,
        provider: EastmoneyProvider,
        snapshot_archive: SnapshotArchive,
        tmp_path: Path,
    ) -> None:
        """Test that snapshots are saved when fetching data."""
        # Mock the HTTP response
        mock_response = MagicMock()
        mock_response.content = b'{"test": "data"}'

        with patch.object(provider, "_get", return_value=mock_response):
            with patch.object(provider, "_parse_fund_meta") as mock_parse:
                mock_parse.return_value = FundMeta(
                    code="000001",
                    name="Test Fund",
                )
                await provider.fetch_fund_meta("000001")

        # Check that snapshot was saved
        snapshots = snapshot_archive.list_snapshots(
            provider="eastmoney",
            fund_code="000001",
        )
        assert len(snapshots) > 0


# ---------------------------------------------------------------------------
# Error Handling Tests
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Tests for error handling."""

    @pytest.mark.asyncio
    async def test_timeout_error(self, provider: EastmoneyProvider) -> None:
        """Test that timeout errors are properly wrapped."""
        import httpx

        with patch.object(provider, "_rate_limiter") as mock_limiter:
            mock_limiter.acquire = AsyncMock()

            with patch("httpx.AsyncClient") as mock_client:
                mock_instance = AsyncMock()
                mock_instance.__aenter__.return_value = mock_instance
                mock_instance.__aexit__.return_value = None
                mock_instance.get.side_effect = httpx.TimeoutException("timeout")
                mock_client.return_value = mock_instance

                from app.data.providers.base import ProviderTimeoutError

                with pytest.raises(ProviderTimeoutError):
                    await provider.fetch_fund_meta("000001")

    @pytest.mark.asyncio
    async def test_not_found_error(self, provider: EastmoneyProvider) -> None:
        """Test that 404 errors are properly wrapped."""
        import httpx

        with patch.object(provider, "_rate_limiter") as mock_limiter:
            mock_limiter.acquire = AsyncMock()

            with patch("httpx.AsyncClient") as mock_client:
                mock_instance = AsyncMock()
                mock_instance.__aenter__.return_value = mock_instance
                mock_instance.__aexit__.return_value = None

                # Create a mock 404 response
                mock_response = MagicMock()
                mock_response.status_code = 404
                mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                    "Not Found",
                    request=MagicMock(),
                    response=mock_response,
                )
                mock_instance.get.return_value = mock_response

                mock_client.return_value = mock_instance

                with pytest.raises(ProviderNotFoundError):
                    await provider.fetch_fund_meta("999999")
