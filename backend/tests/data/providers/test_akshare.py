"""Unit tests for AkshareProvider.

Tests cover all FundDataProvider Protocol methods:
  1. 基础信息 (fetch_fund_meta)
  2. 历史净值 (fetch_nav_history)
  3. 持仓 (fetch_holdings)
  4. 分红拆分 (fetch_dividends)
  5. 公告 (fetch_announcements)
  6. 健康检查 (health_check)

Run with:
    pytest tests/data/providers/test_akshare.py -v

Note: These tests use mocking to avoid actual network calls to AkShare.
For integration tests with real data, use --run-integration flag.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.data.fetchers.rate_limiter import RateLimiter
from app.data.providers.base import (
    FundDataProvider,
    HealthStatus,
    ProviderError,
    ProviderNotFoundError,
    ProviderTimeoutError,
)
from app.data.providers.akshare import AkshareProvider, _HAS_AKSHARE
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
# Skip if akshare not installed
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.skipif(
    not _HAS_AKSHARE,
    reason="akshare not installed",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def rate_limiter() -> RateLimiter:
    """Create a rate limiter for testing (high rate to avoid delays)."""
    limiter = RateLimiter(default_rate=100.0)
    limiter.configure("akshare", rate=100.0)
    return limiter


@pytest.fixture
def snapshot_archive(tmp_path: Path) -> SnapshotArchive:
    """Create a snapshot archive in a temp directory."""
    return SnapshotArchive(base_dir=tmp_path / "snapshots")


@pytest.fixture
def provider(
    rate_limiter: RateLimiter,
    snapshot_archive: SnapshotArchive,
) -> AkshareProvider:
    """Create an AkshareProvider instance for testing."""
    return AkshareProvider(
        rate_limiter=rate_limiter,
        snapshot_archive=snapshot_archive,
        timeout=30.0,
    )


@pytest.fixture
def mock_pandas():
    """Create a mock pandas module."""
    import pandas as pd
    return pd


# ---------------------------------------------------------------------------
# Protocol Compliance Tests
# ---------------------------------------------------------------------------


class TestProtocolCompliance:
    """Verify AkshareProvider satisfies FundDataProvider Protocol."""

    def test_isinstance_check(self, provider: AkshareProvider) -> None:
        """Provider should satisfy the FundDataProvider Protocol."""
        assert isinstance(provider, FundDataProvider)

    def test_name_attribute(self, provider: AkshareProvider) -> None:
        """Provider should have name='akshare'."""
        assert provider.name == "akshare"

    def test_priority_attribute(self, provider: AkshareProvider) -> None:
        """Provider should have priority=2 (backup source)."""
        assert provider.priority == 2


# ---------------------------------------------------------------------------
# Unit Tests - Helper Methods
# ---------------------------------------------------------------------------


class TestHelperMethods:
    """Unit tests for internal helper methods."""

    def test_parse_quarter_valid(self, provider: AkshareProvider) -> None:
        """Test quarter parsing with valid inputs."""
        assert provider._parse_quarter("2024-Q1") == (2024, 3)
        assert provider._parse_quarter("2024-Q2") == (2024, 6)
        assert provider._parse_quarter("2024-Q3") == (2024, 9)
        assert provider._parse_quarter("2024-Q4") == (2024, 12)
        assert provider._parse_quarter("2023-q1") == (2023, 3)  # lowercase

    def test_parse_quarter_invalid(self, provider: AkshareProvider) -> None:
        """Test quarter parsing with invalid inputs."""
        with pytest.raises(ValueError, match="无效的季度格式"):
            provider._parse_quarter("2024-Q5")
        with pytest.raises(ValueError, match="无效的季度格式"):
            provider._parse_quarter("2024Q1")
        with pytest.raises(ValueError, match="无效的季度格式"):
            provider._parse_quarter("invalid")

    def test_quarter_to_report_date(self, provider: AkshareProvider) -> None:
        """Test quarter to report date conversion."""
        assert provider._quarter_to_report_date("2024-Q1") == date(2024, 3, 31)
        assert provider._quarter_to_report_date("2024-Q2") == date(2024, 6, 30)
        assert provider._quarter_to_report_date("2024-Q3") == date(2024, 9, 30)
        assert provider._quarter_to_report_date("2024-Q4") == date(2024, 12, 31)

    def test_match_quarter_chinese_format(self, provider: AkshareProvider) -> None:
        """Test quarter matching with Chinese format."""
        assert provider._match_quarter("2024年1季度", 2024, 3) is True
        assert provider._match_quarter("2024年2季度", 2024, 6) is True
        assert provider._match_quarter("2024年3季度", 2024, 9) is True
        assert provider._match_quarter("2024年4季度", 2024, 12) is True
        assert provider._match_quarter("2024年1季度", 2024, 6) is False
        assert provider._match_quarter("2023年1季度", 2024, 3) is False

    def test_match_quarter_iso_format(self, provider: AkshareProvider) -> None:
        """Test quarter matching with ISO format."""
        assert provider._match_quarter("2024-Q1", 2024, 3) is True
        assert provider._match_quarter("2024Q2", 2024, 6) is True
        assert provider._match_quarter("2024-Q1", 2024, 6) is False

    def test_match_quarter_empty(self, provider: AkshareProvider) -> None:
        """Test quarter matching with empty input."""
        assert provider._match_quarter("", 2024, 3) is False
        assert provider._match_quarter(None, 2024, 3) is False  # type: ignore


# ---------------------------------------------------------------------------
# Unit Tests - fetch_fund_meta
# ---------------------------------------------------------------------------


class TestFetchFundMeta:
    """Unit tests for fetch_fund_meta."""

    @pytest.mark.asyncio
    async def test_fetch_fund_meta_success(
        self, provider: AkshareProvider, mock_pandas
    ) -> None:
        """Test fetching fund metadata successfully."""
        # Mock akshare functions
        mock_nav_df = mock_pandas.DataFrame({
            "净值日期": ["2024-01-15"],
            "单位净值": [1.5],
            "累计净值": [2.0],
        })
        
        mock_info_df = mock_pandas.DataFrame([
            ["基金全称", "测试基金"],
            ["基金类型", "股票型"],
            ["成立日期", "2020-01-01"],
            ["管理费率", "1.50%"],
            ["托管费率", "0.25%"],
        ])

        with patch("app.data.providers.akshare.ak") as mock_ak:
            mock_ak.fund_open_fund_info_em.return_value = mock_nav_df
            mock_ak.fund_individual_basic_info_xq.return_value = mock_info_df
            
            meta = await provider.fetch_fund_meta("000001")

        assert isinstance(meta, FundMeta)
        assert meta.code == "000001"
        assert meta.name == "测试基金"
        assert meta.fund_type == FundType.STOCK
        assert meta.inception_date == date(2020, 1, 1)
        assert meta.management_fee == Decimal("0.015")
        assert meta.custodian_fee == Decimal("0.0025")
        assert meta.source == "akshare"

    @pytest.mark.asyncio
    async def test_fetch_fund_meta_not_found(
        self, provider: AkshareProvider
    ) -> None:
        """Test fetching fund metadata for non-existent fund."""
        with patch("app.data.providers.akshare.ak") as mock_ak:
            mock_ak.fund_open_fund_info_em.side_effect = Exception("基金不存在")
            
            with pytest.raises(ProviderNotFoundError):
                await provider.fetch_fund_meta("999999")

    @pytest.mark.asyncio
    async def test_fetch_fund_meta_timeout(
        self, provider: AkshareProvider
    ) -> None:
        """Test fetching fund metadata with timeout."""
        import asyncio
        
        with patch("app.data.providers.akshare.ak") as mock_ak:
            mock_ak.fund_open_fund_info_em.side_effect = asyncio.TimeoutError()
            
            with pytest.raises(ProviderTimeoutError):
                await provider.fetch_fund_meta("000001")


# ---------------------------------------------------------------------------
# Unit Tests - fetch_nav_history
# ---------------------------------------------------------------------------


class TestFetchNavHistory:
    """Unit tests for fetch_nav_history."""

    @pytest.mark.asyncio
    async def test_fetch_nav_history_success(
        self, provider: AkshareProvider, mock_pandas
    ) -> None:
        """Test fetching NAV history successfully."""
        mock_df = mock_pandas.DataFrame({
            "净值日期": ["2024-01-15", "2024-01-16", "2024-01-17"],
            "单位净值": [1.5, 1.52, 1.48],
            "累计净值": [2.0, 2.02, 1.98],
            "日增长率": [0.5, 1.33, -2.63],
        })

        with patch("app.data.providers.akshare.ak") as mock_ak:
            mock_ak.fund_open_fund_info_em.return_value = mock_df
            
            records = await provider.fetch_nav_history(
                "000001",
                date(2024, 1, 1),
                date(2024, 1, 31),
            )

        assert isinstance(records, list)
        assert len(records) == 3
        
        for record in records:
            assert isinstance(record, NavRecord)
            assert record.fund_code == "000001"
            assert record.source == "akshare"

        # Check first record
        assert records[0].trade_date == date(2024, 1, 15)
        assert records[0].unit_nav == Decimal("1.5")
        assert records[0].accum_nav == Decimal("2.0")
        assert records[0].daily_return == Decimal("0.005")  # 0.5% → 0.005

    @pytest.mark.asyncio
    async def test_fetch_nav_history_date_filter(
        self, provider: AkshareProvider, mock_pandas
    ) -> None:
        """Test that NAV history is filtered by date range."""
        mock_df = mock_pandas.DataFrame({
            "净值日期": ["2024-01-10", "2024-01-15", "2024-01-20"],
            "单位净值": [1.5, 1.52, 1.48],
            "累计净值": [2.0, 2.02, 1.98],
            "日增长率": [0.5, 1.33, -2.63],
        })

        with patch("app.data.providers.akshare.ak") as mock_ak:
            mock_ak.fund_open_fund_info_em.return_value = mock_df
            
            records = await provider.fetch_nav_history(
                "000001",
                date(2024, 1, 14),
                date(2024, 1, 16),
            )

        # Only the record on 2024-01-15 should be included
        assert len(records) == 1
        assert records[0].trade_date == date(2024, 1, 15)

    @pytest.mark.asyncio
    async def test_fetch_nav_history_sorted(
        self, provider: AkshareProvider, mock_pandas
    ) -> None:
        """Test that NAV history is sorted by date ascending."""
        # Return data in reverse order
        mock_df = mock_pandas.DataFrame({
            "净值日期": ["2024-01-17", "2024-01-15", "2024-01-16"],
            "单位净值": [1.48, 1.5, 1.52],
            "累计净值": [1.98, 2.0, 2.02],
            "日增长率": [-2.63, 0.5, 1.33],
        })

        with patch("app.data.providers.akshare.ak") as mock_ak:
            mock_ak.fund_open_fund_info_em.return_value = mock_df
            
            records = await provider.fetch_nav_history(
                "000001",
                date(2024, 1, 1),
                date(2024, 1, 31),
            )

        # Should be sorted by date ascending
        dates = [r.trade_date for r in records]
        assert dates == sorted(dates)

    @pytest.mark.asyncio
    async def test_fetch_nav_history_empty(
        self, provider: AkshareProvider, mock_pandas
    ) -> None:
        """Test fetching NAV history with no data."""
        mock_df = mock_pandas.DataFrame()

        with patch("app.data.providers.akshare.ak") as mock_ak:
            mock_ak.fund_open_fund_info_em.return_value = mock_df
            
            records = await provider.fetch_nav_history(
                "000001",
                date(2024, 1, 1),
                date(2024, 1, 31),
            )

        assert records == []


# ---------------------------------------------------------------------------
# Unit Tests - fetch_holdings
# ---------------------------------------------------------------------------


class TestFetchHoldings:
    """Unit tests for fetch_holdings."""

    @pytest.mark.asyncio
    async def test_fetch_holdings_success(
        self, provider: AkshareProvider, mock_pandas
    ) -> None:
        """Test fetching holdings successfully."""
        mock_df = mock_pandas.DataFrame({
            "季度": ["2024年1季度", "2024年1季度"],
            "股票代码": ["600519", "000858"],
            "股票名称": ["贵州茅台", "五粮液"],
            "占净值比例": [9.5, 8.2],
            "持股数": [100, 200],
            "持仓市值": [1500, 1200],
        })

        with patch("app.data.providers.akshare.ak") as mock_ak:
            mock_ak.fund_portfolio_hold_em.return_value = mock_df
            
            snapshot = await provider.fetch_holdings("000001", "2024-Q1")

        assert isinstance(snapshot, HoldingSnapshot)
        assert snapshot.fund_code == "000001"
        assert snapshot.report_date == date(2024, 3, 31)
        assert len(snapshot.positions) == 2

        # Check first position
        pos = snapshot.positions[0]
        assert pos.stock_code == "600519"
        assert pos.stock_name == "贵州茅台"
        assert pos.weight == Decimal("0.095")  # 9.5% → 0.095
        assert pos.shares == Decimal("1000000")  # 100万股 → 1000000股
        assert pos.market_value == Decimal("15000000")  # 1500万元 → 15000000元

    @pytest.mark.asyncio
    async def test_fetch_holdings_quarter_filter(
        self, provider: AkshareProvider, mock_pandas
    ) -> None:
        """Test that holdings are filtered by quarter."""
        mock_df = mock_pandas.DataFrame({
            "季度": ["2024年1季度", "2024年2季度"],
            "股票代码": ["600519", "000858"],
            "股票名称": ["贵州茅台", "五粮液"],
            "占净值比例": [9.5, 8.2],
            "持股数": [100, 200],
            "持仓市值": [1500, 1200],
        })

        with patch("app.data.providers.akshare.ak") as mock_ak:
            mock_ak.fund_portfolio_hold_em.return_value = mock_df
            
            snapshot = await provider.fetch_holdings("000001", "2024-Q1")

        # Only Q1 data should be included
        assert len(snapshot.positions) == 1
        assert snapshot.positions[0].stock_code == "600519"

    @pytest.mark.asyncio
    async def test_fetch_holdings_invalid_quarter(
        self, provider: AkshareProvider
    ) -> None:
        """Test fetching holdings with invalid quarter format."""
        with pytest.raises(ValueError, match="无效的季度格式"):
            await provider.fetch_holdings("000001", "2024-Q5")

    @pytest.mark.asyncio
    async def test_fetch_holdings_empty(
        self, provider: AkshareProvider, mock_pandas
    ) -> None:
        """Test fetching holdings with no data."""
        mock_df = mock_pandas.DataFrame()

        with patch("app.data.providers.akshare.ak") as mock_ak:
            mock_ak.fund_portfolio_hold_em.return_value = mock_df
            
            snapshot = await provider.fetch_holdings("000001", "2024-Q1")

        assert snapshot.positions == []


# ---------------------------------------------------------------------------
# Unit Tests - fetch_dividends
# ---------------------------------------------------------------------------


class TestFetchDividends:
    """Unit tests for fetch_dividends."""

    @pytest.mark.asyncio
    async def test_fetch_dividends_success(
        self, provider: AkshareProvider, mock_pandas
    ) -> None:
        """Test fetching dividends successfully."""
        mock_df = mock_pandas.DataFrame({
            "除息日": ["2024-01-15", "2024-06-15"],
            "权益登记日": ["2024-01-14", "2024-06-14"],
            "派息日": ["2024-01-20", "2024-06-20"],
            "每份分红": [0.05, 0.08],
        })

        with patch("app.data.providers.akshare.ak") as mock_ak:
            mock_ak.fund_fh_em.return_value = mock_df
            
            records = await provider.fetch_dividends("000001")

        assert isinstance(records, list)
        assert len(records) == 2

        for record in records:
            assert isinstance(record, DividendRecord)
            assert record.fund_code == "000001"
            assert record.split_ratio == Decimal("1")

        # Check first record
        assert records[0].ex_date == date(2024, 1, 15)
        assert records[0].record_date == date(2024, 1, 14)
        assert records[0].pay_date == date(2024, 1, 20)
        assert records[0].dividend_per_share == Decimal("0.05")

    @pytest.mark.asyncio
    async def test_fetch_dividends_sorted(
        self, provider: AkshareProvider, mock_pandas
    ) -> None:
        """Test that dividends are sorted by ex_date ascending."""
        # Return data in reverse order
        mock_df = mock_pandas.DataFrame({
            "除息日": ["2024-06-15", "2024-01-15"],
            "权益登记日": ["2024-06-14", "2024-01-14"],
            "派息日": ["2024-06-20", "2024-01-20"],
            "每份分红": [0.08, 0.05],
        })

        with patch("app.data.providers.akshare.ak") as mock_ak:
            mock_ak.fund_fh_em.return_value = mock_df
            
            records = await provider.fetch_dividends("000001")

        # Should be sorted by ex_date ascending
        dates = [r.ex_date for r in records]
        assert dates == sorted(dates)

    @pytest.mark.asyncio
    async def test_fetch_dividends_empty(
        self, provider: AkshareProvider, mock_pandas
    ) -> None:
        """Test fetching dividends with no data."""
        mock_df = mock_pandas.DataFrame()

        with patch("app.data.providers.akshare.ak") as mock_ak:
            mock_ak.fund_fh_em.return_value = mock_df
            
            records = await provider.fetch_dividends("000001")

        assert records == []


# ---------------------------------------------------------------------------
# Unit Tests - fetch_announcements
# ---------------------------------------------------------------------------


class TestFetchAnnouncements:
    """Unit tests for fetch_announcements."""

    @pytest.mark.asyncio
    async def test_fetch_announcements_returns_empty(
        self, provider: AkshareProvider
    ) -> None:
        """Test that fetch_announcements returns empty list (not supported)."""
        announcements = await provider.fetch_announcements(
            "000001",
            date(2024, 1, 1),
        )

        assert announcements == []


# ---------------------------------------------------------------------------
# Unit Tests - health_check
# ---------------------------------------------------------------------------


class TestHealthCheck:
    """Unit tests for health_check."""

    @pytest.mark.asyncio
    async def test_health_check_success(
        self, provider: AkshareProvider, mock_pandas
    ) -> None:
        """Test health check returns healthy status."""
        mock_df = mock_pandas.DataFrame({
            "基金代码": ["000001", "000002"],
            "基金简称": ["基金A", "基金B"],
        })

        with patch("app.data.providers.akshare.ak") as mock_ak:
            mock_ak.fund_name_em.return_value = mock_df
            
            status = await provider.health_check()

        assert isinstance(status, HealthStatus)
        assert status.healthy is True
        assert "正常" in status.message
        assert status.latency_ms is not None
        assert status.latency_ms >= 0

    @pytest.mark.asyncio
    async def test_health_check_empty_response(
        self, provider: AkshareProvider, mock_pandas
    ) -> None:
        """Test health check with empty response."""
        mock_df = mock_pandas.DataFrame()

        with patch("app.data.providers.akshare.ak") as mock_ak:
            mock_ak.fund_name_em.return_value = mock_df
            
            status = await provider.health_check()

        assert isinstance(status, HealthStatus)
        assert status.healthy is False
        assert "空数据" in status.message

    @pytest.mark.asyncio
    async def test_health_check_timeout(
        self, provider: AkshareProvider
    ) -> None:
        """Test health check with timeout."""
        import asyncio

        with patch("app.data.providers.akshare.ak") as mock_ak:
            mock_ak.fund_name_em.side_effect = asyncio.TimeoutError()
            
            status = await provider.health_check()

        assert isinstance(status, HealthStatus)
        assert status.healthy is False
        assert "超时" in status.message

    @pytest.mark.asyncio
    async def test_health_check_error(
        self, provider: AkshareProvider
    ) -> None:
        """Test health check with error."""
        with patch("app.data.providers.akshare.ak") as mock_ak:
            mock_ak.fund_name_em.side_effect = Exception("Network error")
            
            status = await provider.health_check()

        assert isinstance(status, HealthStatus)
        assert status.healthy is False
        assert "异常" in status.message


# ---------------------------------------------------------------------------
# Snapshot Tests
# ---------------------------------------------------------------------------


class TestSnapshotSaving:
    """Tests for snapshot saving functionality."""

    @pytest.mark.asyncio
    async def test_snapshot_saved_on_fetch_nav(
        self,
        provider: AkshareProvider,
        snapshot_archive: SnapshotArchive,
        mock_pandas,
    ) -> None:
        """Test that snapshots are saved when fetching NAV data."""
        mock_df = mock_pandas.DataFrame({
            "净值日期": ["2024-01-15"],
            "单位净值": [1.5],
            "累计净值": [2.0],
            "日增长率": [0.5],
        })

        with patch("app.data.providers.akshare.ak") as mock_ak:
            mock_ak.fund_open_fund_info_em.return_value = mock_df
            
            await provider.fetch_nav_history(
                "000001",
                date(2024, 1, 1),
                date(2024, 1, 31),
            )

        # Check that snapshot was saved
        snapshots = snapshot_archive.list_snapshots(
            provider="akshare",
            fund_code="000001",
        )
        assert len(snapshots) > 0


# ---------------------------------------------------------------------------
# Error Handling Tests
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Tests for error handling."""

    @pytest.mark.asyncio
    async def test_provider_error_on_general_exception(
        self, provider: AkshareProvider
    ) -> None:
        """Test that general exceptions are wrapped in ProviderError."""
        with patch("app.data.providers.akshare.ak") as mock_ak:
            mock_ak.fund_open_fund_info_em.side_effect = Exception("Unknown error")
            
            with pytest.raises(ProviderError) as exc_info:
                await provider.fetch_fund_meta("000001")
            
            assert exc_info.value.provider_name == "akshare"
            assert exc_info.value.fund_code == "000001"

    @pytest.mark.asyncio
    async def test_not_found_error_detection(
        self, provider: AkshareProvider
    ) -> None:
        """Test that 'not found' errors are properly detected."""
        with patch("app.data.providers.akshare.ak") as mock_ak:
            mock_ak.fund_open_fund_info_em.side_effect = Exception("基金代码不存在")
            
            with pytest.raises(ProviderNotFoundError):
                await provider.fetch_fund_meta("999999")
