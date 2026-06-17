"""Unit tests for :mod:`app.tasks.ingest`.

Tests run with Celery in eager mode and mock the CompositeProvider and
database session to avoid external dependencies. Each task is tested
for:
- Task registration on the Celery app
- Correct routing to the ``ingest`` queue
- Single-fund trigger mode
- Batch trigger mode
- Error handling (provider failure)
- Metrics recording

Requirements: 1.1, 1.2, 1.11, 8.1
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.tasks.celery_app import celery_app
from app.tasks.ingest import (
    _determine_quarter,
    _resolve_fund_codes,
    update_announcements,
    recalculate_adj_nav_history,
    update_daily_nav,
    update_dividends,
    update_fund_meta,
    update_holdings,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def eager_celery() -> Iterator[None]:
    """Temporarily switch the shared Celery app into eager mode."""
    previous_always_eager = celery_app.conf.task_always_eager
    previous_eager_propagates = celery_app.conf.task_eager_propagates
    previous_store_eager = celery_app.conf.task_store_eager_result

    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    celery_app.conf.task_store_eager_result = True
    try:
        yield
    finally:
        celery_app.conf.task_always_eager = previous_always_eager
        celery_app.conf.task_eager_propagates = previous_eager_propagates
        celery_app.conf.task_store_eager_result = previous_store_eager


# ---------------------------------------------------------------------------
# Task registration tests
# ---------------------------------------------------------------------------


class TestTaskRegistration:
    """Verify all ingest tasks are registered on the Celery app."""

    def test_update_fund_meta_registered(self) -> None:
        assert "app.tasks.ingest.update_fund_meta" in celery_app.tasks

    def test_update_daily_nav_registered(self) -> None:
        assert "app.tasks.ingest.update_daily_nav" in celery_app.tasks

    def test_update_holdings_registered(self) -> None:
        assert "app.tasks.ingest.update_holdings" in celery_app.tasks

    def test_update_dividends_registered(self) -> None:
        assert "app.tasks.ingest.update_dividends" in celery_app.tasks

    def test_update_announcements_registered(self) -> None:
        assert "app.tasks.ingest.update_announcements" in celery_app.tasks

    def test_recalculate_adj_nav_history_registered(self) -> None:
        assert "app.tasks.ingest.recalculate_adj_nav_history" in celery_app.tasks

    def test_all_tasks_route_to_ingest_queue(self) -> None:
        assert update_fund_meta.queue == "ingest"
        assert update_daily_nav.queue == "ingest"
        assert update_holdings.queue == "ingest"
        assert update_dividends.queue == "ingest"
        assert update_announcements.queue == "ingest"
        assert recalculate_adj_nav_history.queue == "ingest"


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestResolveFoundCodes:
    """Test the _resolve_fund_codes helper."""

    def test_single_fund_code(self) -> None:
        assert _resolve_fund_codes(fund_code="000001") == ["000001"]

    def test_fund_codes_list(self) -> None:
        codes = ["000001", "000002", "000003"]
        assert _resolve_fund_codes(fund_codes=codes) == codes

    def test_neither_returns_empty(self) -> None:
        assert _resolve_fund_codes() == []

    def test_fund_code_takes_precedence(self) -> None:
        result = _resolve_fund_codes(fund_code="000001", fund_codes=["000002"])
        assert result == ["000001"]


class TestDetermineQuarter:
    """Test the _determine_quarter helper."""

    def test_january_returns_previous_q4(self) -> None:
        assert _determine_quarter(date(2024, 1, 15)) == "2023-Q4"

    def test_march_returns_previous_q4(self) -> None:
        assert _determine_quarter(date(2024, 3, 31)) == "2023-Q4"

    def test_april_returns_q1(self) -> None:
        assert _determine_quarter(date(2024, 4, 15)) == "2024-Q1"

    def test_june_returns_q1(self) -> None:
        assert _determine_quarter(date(2024, 6, 30)) == "2024-Q1"

    def test_july_returns_q2(self) -> None:
        assert _determine_quarter(date(2024, 7, 15)) == "2024-Q2"

    def test_september_returns_q2(self) -> None:
        assert _determine_quarter(date(2024, 9, 30)) == "2024-Q2"

    def test_october_returns_q3(self) -> None:
        assert _determine_quarter(date(2024, 10, 15)) == "2024-Q3"

    def test_december_returns_q3(self) -> None:
        assert _determine_quarter(date(2024, 12, 31)) == "2024-Q3"


# ---------------------------------------------------------------------------
# Task execution tests (with mocked dependencies)
# ---------------------------------------------------------------------------


class TestUpdateFundMeta:
    """Test update_fund_meta task execution."""

    @patch("app.tasks.ingest._get_composite_provider")
    @patch("app.data.session.get_sessionmaker")
    def test_single_fund_success(
        self, mock_sessionmaker_fn, mock_provider_factory, eager_celery
    ) -> None:
        """Single fund meta update succeeds."""
        from app.data.schemas.funds import FundMeta, FundStatus, FundType

        mock_meta = FundMeta(
            code="000001",
            name="测试基金",
            fund_type=FundType.STOCK,
            status=FundStatus.ACTIVE,
        )

        # Setup provider mock
        mock_provider = AsyncMock()
        mock_provider.fetch_fund_meta.return_value = (mock_meta, "eastmoney")
        mock_provider_factory.return_value = mock_provider

        # Setup session mock - factory returns a sessionmaker that produces sessions
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        mock_factory = MagicMock()
        mock_factory.return_value = mock_session
        mock_sessionmaker_fn.return_value = mock_factory

        with patch("app.data.repositories.fund_repo.FundRepo.upsert_many", new_callable=AsyncMock) as mock_upsert:
            mock_upsert.return_value = 1
            result = update_fund_meta(fund_code="000001")

        assert result["success"] == 1
        assert result["failed"] == 0

    @patch("app.tasks.ingest._get_composite_provider")
    @patch("app.data.session.get_sessionmaker")
    def test_provider_failure_records_error(
        self, mock_sessionmaker_fn, mock_provider_factory, eager_celery
    ) -> None:
        """Provider failure is caught and counted."""
        from app.data.providers.base import AllProvidersFailedError

        mock_provider = AsyncMock()
        mock_provider.fetch_fund_meta.side_effect = AllProvidersFailedError(
            [("eastmoney", Exception("timeout"))], fund_code="000001"
        )
        mock_provider_factory.return_value = mock_provider

        # Session mock (won't be used due to early failure)
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_factory = MagicMock()
        mock_factory.return_value = mock_session
        mock_sessionmaker_fn.return_value = mock_factory

        result = update_fund_meta(fund_code="000001")

        assert result["success"] == 0
        assert result["failed"] == 1


class TestUpdateDailyNav:
    """Test update_daily_nav task execution."""

    @patch("app.tasks.ingest._get_composite_provider")
    @patch("app.data.session.get_sessionmaker")
    def test_incremental_fetch_from_last_date(
        self, mock_sessionmaker_fn, mock_provider_factory, eager_celery
    ) -> None:
        """NAV fetch starts from the day after last stored date."""
        from app.data.schemas.funds import NavRecord

        mock_nav = NavRecord(
            fund_code="000001",
            trade_date=date(2024, 1, 15),
            unit_nav=Decimal("1.5000"),
            accum_nav=Decimal("2.0000"),
            daily_return=Decimal("0.0100"),
        )

        # Setup provider mock
        mock_provider = AsyncMock()
        mock_provider.fetch_nav_history.return_value = ([mock_nav], "eastmoney")
        mock_provider.fetch_nav_history_all_sources.return_value = ({"eastmoney": [mock_nav]}, {})
        mock_provider_factory.return_value = mock_provider

        # Setup session mock
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_factory = MagicMock()
        mock_factory.return_value = mock_session
        mock_sessionmaker_fn.return_value = mock_factory

        with (
            patch("app.data.repositories.nav_repo.NavRepo.latest_date", new_callable=AsyncMock) as mock_latest,
            patch("app.data.repositories.nav_repo.NavRepo.upsert_many", new_callable=AsyncMock) as mock_upsert,
            patch("app.data.services.adj_nav.recalculate_adj_nav", new_callable=AsyncMock) as mock_adj,
            patch("app.tasks.ingest._backfill_missing_nav", new_callable=AsyncMock) as mock_backfill,
        ):
            mock_latest.return_value = date(2024, 1, 14)
            mock_upsert.return_value = 1
            mock_adj.return_value = 0
            mock_backfill.return_value = 0

            result = update_daily_nav(fund_code="000001")

        assert result["success"] == 1
        assert result["failed"] == 0
        assert result["records_inserted"] == 1

    @patch("app.tasks.ingest._get_composite_provider")
    @patch("app.data.session.get_sessionmaker")
    def test_cross_source_hard_gate_blocks_nav_write(
        self, mock_sessionmaker_fn, mock_provider_factory, eager_celery
    ) -> None:
        """多源 NAV 原始对照失败时应阻断 fund_nav 写入。"""
        from app.data.schemas.funds import NavRecord

        eastmoney_series = [
            NavRecord(
                fund_code="000001",
                trade_date=date(2024, 1, 15 + idx),
                unit_nav=Decimal("1.5000"),
                accum_nav=Decimal("2.0000"),
                daily_return=Decimal("0.0100"),
            )
            for idx in range(3)
        ]
        akshare_series = [
            NavRecord(
                fund_code="000001",
                trade_date=date(2024, 1, 15 + idx),
                unit_nav=Decimal("1.5500"),
                accum_nav=Decimal("2.0000"),
                daily_return=Decimal("0.0100"),
            )
            for idx in range(3)
        ]
        mock_provider = AsyncMock()
        mock_provider.fetch_nav_history.return_value = (eastmoney_series, "eastmoney")
        mock_provider.fetch_nav_history_all_sources.return_value = (
            {"eastmoney": eastmoney_series, "akshare": akshare_series},
            {},
        )
        mock_provider_factory.return_value = mock_provider

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_factory = MagicMock()
        mock_factory.return_value = mock_session
        mock_sessionmaker_fn.return_value = mock_factory

        with (
            patch("app.data.repositories.nav_repo.NavRepo.latest_date", new_callable=AsyncMock) as mock_latest,
            patch("app.data.repositories.nav_repo.NavRepo.upsert_many", new_callable=AsyncMock) as mock_upsert,
            patch("app.tasks.ingest._backfill_missing_nav", new_callable=AsyncMock) as mock_backfill,
        ):
            mock_latest.return_value = date(2024, 1, 14)
            mock_backfill.return_value = 0
            result = update_daily_nav(fund_code="000001")

        assert result["success"] == 0
        assert result["failed"] == 1
        assert result["records_inserted"] == 0
        mock_upsert.assert_not_awaited()


class TestUpdateHoldings:
    """Test update_holdings task execution."""

    @patch("app.tasks.ingest._get_composite_provider")
    @patch("app.data.session.get_sessionmaker")
    def test_holdings_with_explicit_quarter(
        self, mock_sessionmaker_fn, mock_provider_factory, eager_celery
    ) -> None:
        """Holdings fetch with explicit quarter parameter."""
        from app.data.schemas.funds import HoldingPosition, HoldingSnapshot

        mock_snapshot = HoldingSnapshot(
            fund_code="000001",
            report_date=date(2024, 3, 31),
            positions=[
                HoldingPosition(
                    stock_code="600519",
                    stock_name="贵州茅台",
                    weight=Decimal("0.0800"),
                    shares=Decimal("10000"),
                    market_value=Decimal("1500000"),
                    industry="食品饮料",
                ),
            ],
        )

        mock_provider = AsyncMock()
        mock_provider.fetch_holdings.return_value = (mock_snapshot, "eastmoney")
        mock_provider_factory.return_value = mock_provider

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_factory = MagicMock()
        mock_factory.return_value = mock_session
        mock_sessionmaker_fn.return_value = mock_factory

        with patch("app.data.repositories.holding_repo.HoldingRepo.upsert_many", new_callable=AsyncMock) as mock_upsert:
            mock_upsert.return_value = 1
            result = update_holdings(fund_code="000001", quarter="2024-Q1")

        assert result["success"] == 1
        assert result["failed"] == 0
        assert result["quarter"] == "2024-Q1"


class TestUpdateDividends:
    """Test update_dividends task execution."""

    @patch("app.tasks.ingest._get_composite_provider")
    @patch("app.data.session.get_sessionmaker")
    def test_dividends_success(
        self, mock_sessionmaker_fn, mock_provider_factory, eager_celery
    ) -> None:
        """Dividend fetch and upsert succeeds."""
        from app.data.schemas.funds import DividendRecord

        mock_div = DividendRecord(
            fund_code="000001",
            ex_date=date(2024, 1, 10),
            record_date=date(2024, 1, 9),
            pay_date=date(2024, 1, 12),
            dividend_per_share=Decimal("0.5000"),
            split_ratio=Decimal("1"),
        )

        mock_provider = AsyncMock()
        mock_provider.fetch_dividends.return_value = ([mock_div], "eastmoney")
        mock_provider_factory.return_value = mock_provider

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_factory = MagicMock()
        mock_factory.return_value = mock_session
        mock_sessionmaker_fn.return_value = mock_factory

        with patch("app.data.repositories.dividend_repo.DividendRepo.upsert_many", new_callable=AsyncMock) as mock_upsert:
            mock_upsert.return_value = 1
            with patch("app.data.services.adj_nav.recalculate_adj_nav", new_callable=AsyncMock) as mock_adj:
                mock_adj.return_value = 0
                result = update_dividends(fund_code="000001")

        assert result["success"] == 1
        assert result["failed"] == 0
        assert result["records_upserted"] == 1



class TestRecalculateAdjNavHistory:
    """Test historical adjusted NAV recalculation task execution."""

    @patch("app.data.session.get_sessionmaker")
    def test_single_fund_success(self, mock_sessionmaker_fn, eager_celery) -> None:
        """Single fund historical adj_nav recalculation succeeds."""
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_factory = MagicMock()
        mock_factory.return_value = mock_session
        mock_sessionmaker_fn.return_value = mock_factory

        with (
            patch("app.data.services.adj_nav.recalculate_adj_nav", new_callable=AsyncMock) as mock_adj,
            patch("app.tasks.ingest._mark_nav_dependent_results_stale", new_callable=AsyncMock) as mock_stale,
            patch("app.data.cache.invalidate_nav", new_callable=AsyncMock) as mock_invalidate,
        ):
            mock_adj.return_value = 3
            mock_stale.return_value = {
                "advisor_results": 1,
                "advisor_oos_snapshots": 2,
                "backtest_runs": 0,
                "simulation_runs": 0,
            }
            result = recalculate_adj_nav_history(fund_code="000001")

        assert result["success"] == 1
        assert result["failed"] == 0
        assert result["total"] == 1
        assert result["records_updated"] == 3
        assert result["stale_marked"]["advisor_results"] == 1
        assert result["stale_marked"]["advisor_oos_snapshots"] == 2
        mock_adj.assert_awaited_once_with(mock_session, "000001")
        mock_stale.assert_awaited_once_with(
            mock_session,
            ["000001"],
            reason="adj_nav_history_recalculated",
        )
        mock_invalidate.assert_awaited_once_with("000001")
        mock_session.commit.assert_awaited_once()

    @patch("app.data.session.get_sessionmaker")
    def test_batch_fund_codes_without_cache_invalidation(
        self, mock_sessionmaker_fn, eager_celery
    ) -> None:
        """Batch recalculation can skip cache invalidation when requested."""
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_factory = MagicMock()
        mock_factory.return_value = mock_session
        mock_sessionmaker_fn.return_value = mock_factory

        with (
            patch("app.data.services.adj_nav.recalculate_adj_nav", new_callable=AsyncMock) as mock_adj,
            patch("app.tasks.ingest._mark_nav_dependent_results_stale", new_callable=AsyncMock) as mock_stale,
            patch("app.data.cache.invalidate_nav", new_callable=AsyncMock) as mock_invalidate,
        ):
            mock_adj.side_effect = [2, 4]
            mock_stale.side_effect = [
                {
                    "advisor_results": 1,
                    "advisor_oos_snapshots": 0,
                    "backtest_runs": 1,
                    "simulation_runs": 0,
                },
                {
                    "advisor_results": 2,
                    "advisor_oos_snapshots": 1,
                    "backtest_runs": 0,
                    "simulation_runs": 1,
                },
            ]
            result = recalculate_adj_nav_history(
                fund_codes=["000001", "000002"],
                invalidate_cache=False,
            )

        assert result["success"] == 2
        assert result["failed"] == 0
        assert result["total"] == 2
        assert result["records_updated"] == 6
        assert result["stale_marked"] == {
            "advisor_results": 3,
            "advisor_oos_snapshots": 1,
            "backtest_runs": 1,
            "simulation_runs": 1,
        }
        assert mock_adj.await_count == 2
        assert mock_stale.await_count == 2
        mock_invalidate.assert_not_awaited()

    @patch("app.data.session.get_sessionmaker")
    def test_mark_stale_results_can_be_disabled(
        self, mock_sessionmaker_fn, eager_celery
    ) -> None:
        """Stale marking can be disabled for dry maintenance runs."""
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_factory = MagicMock()
        mock_factory.return_value = mock_session
        mock_sessionmaker_fn.return_value = mock_factory

        with (
            patch("app.data.services.adj_nav.recalculate_adj_nav", new_callable=AsyncMock) as mock_adj,
            patch("app.tasks.ingest._mark_nav_dependent_results_stale", new_callable=AsyncMock) as mock_stale,
            patch("app.data.cache.invalidate_nav", new_callable=AsyncMock),
        ):
            mock_adj.return_value = 3
            result = recalculate_adj_nav_history(
                fund_code="000001",
                mark_stale_results=False,
            )

        assert result["success"] == 1
        assert result["records_updated"] == 3
        assert result["stale_marked"] == {
            "advisor_results": 0,
            "advisor_oos_snapshots": 0,
            "backtest_runs": 0,
            "simulation_runs": 0,
        }
        mock_stale.assert_not_awaited()

    @patch("app.data.session.get_sessionmaker")
    def test_zero_updates_do_not_mark_results_stale(
        self, mock_sessionmaker_fn, eager_celery
    ) -> None:
        """No stale marker is written when adj_nav recalculation changes nothing."""
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_factory = MagicMock()
        mock_factory.return_value = mock_session
        mock_sessionmaker_fn.return_value = mock_factory

        with (
            patch("app.data.services.adj_nav.recalculate_adj_nav", new_callable=AsyncMock) as mock_adj,
            patch("app.tasks.ingest._mark_nav_dependent_results_stale", new_callable=AsyncMock) as mock_stale,
            patch("app.data.cache.invalidate_nav", new_callable=AsyncMock),
        ):
            mock_adj.return_value = 0
            result = recalculate_adj_nav_history(fund_code="000001")

        assert result["success"] == 1
        assert result["records_updated"] == 0
        assert result["stale_marked"] == {
            "advisor_results": 0,
            "advisor_oos_snapshots": 0,
            "backtest_runs": 0,
            "simulation_runs": 0,
        }
        mock_stale.assert_not_awaited()

    @patch("app.data.session.get_sessionmaker")
    def test_recalculation_failure_is_counted(
        self, mock_sessionmaker_fn, eager_celery
    ) -> None:
        """A failed fund does not prevent later funds from being recalculated."""
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_factory = MagicMock()
        mock_factory.return_value = mock_session
        mock_sessionmaker_fn.return_value = mock_factory

        with (
            patch("app.data.services.adj_nav.recalculate_adj_nav", new_callable=AsyncMock) as mock_adj,
            patch("app.tasks.ingest._mark_nav_dependent_results_stale", new_callable=AsyncMock) as mock_stale,
            patch("app.data.cache.invalidate_nav", new_callable=AsyncMock),
        ):
            mock_adj.side_effect = [RuntimeError("boom"), 5]
            mock_stale.return_value = {
                "advisor_results": 1,
                "advisor_oos_snapshots": 0,
                "backtest_runs": 0,
                "simulation_runs": 0,
            }
            result = recalculate_adj_nav_history(fund_codes=["000001", "000002"])

        assert result["success"] == 1
        assert result["failed"] == 1
        assert result["total"] == 2
        assert result["records_updated"] == 5
        assert result["stale_marked"]["advisor_results"] == 1
        mock_stale.assert_awaited_once()


class TestUpdateAnnouncements:
    """Test update_announcements task execution."""

    @patch("app.tasks.ingest._get_composite_provider")
    @patch("app.data.session.get_sessionmaker")
    def test_announcements_success(
        self, mock_sessionmaker_fn, mock_provider_factory, eager_celery
    ) -> None:
        """Announcement fetch and insert succeeds."""
        from app.data.schemas.funds import Announcement, AnnouncementCategory

        mock_ann = Announcement(
            fund_code="000001",
            title="关于暂停大额申购的公告",
            category=AnnouncementCategory.LIMIT_PURCHASE,
            publish_date=date(2024, 1, 15),
            content_url="https://example.com/ann/1",
        )

        mock_provider = AsyncMock()
        mock_provider.fetch_announcements.return_value = ([mock_ann], "eastmoney")
        mock_provider_factory.return_value = mock_provider

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()
        mock_factory = MagicMock()
        mock_factory.return_value = mock_session
        mock_sessionmaker_fn.return_value = mock_factory

        result = update_announcements(fund_code="000001")

        assert result["success"] == 1
        assert result["failed"] == 0
        assert result["records_inserted"] == 1


# ---------------------------------------------------------------------------
# Beat schedule tests
# ---------------------------------------------------------------------------


class TestBeatSchedule:
    """Verify Beat schedule entries are correctly configured."""

    def test_daily_nav_schedule_exists(self) -> None:
        from app.tasks.schedule import BEAT_SCHEDULE

        assert "daily-nav-ingest" in BEAT_SCHEDULE
        entry = BEAT_SCHEDULE["daily-nav-ingest"]
        assert entry["task"] == "app.tasks.ingest.update_daily_nav"
        assert entry["options"]["queue"] == "ingest"

    def test_quarterly_holdings_schedule_exists(self) -> None:
        from app.tasks.schedule import BEAT_SCHEDULE

        assert "quarterly-holdings" in BEAT_SCHEDULE
        entry = BEAT_SCHEDULE["quarterly-holdings"]
        assert entry["task"] == "app.tasks.ingest.update_holdings"

    def test_daily_fund_meta_schedule_exists(self) -> None:
        from app.tasks.schedule import BEAT_SCHEDULE

        assert "daily-fund-meta" in BEAT_SCHEDULE

    def test_daily_dividends_schedule_exists(self) -> None:
        from app.tasks.schedule import BEAT_SCHEDULE

        assert "daily-dividends" in BEAT_SCHEDULE

    def test_daily_announcements_schedule_exists(self) -> None:
        from app.tasks.schedule import BEAT_SCHEDULE

        assert "daily-announcements" in BEAT_SCHEDULE
