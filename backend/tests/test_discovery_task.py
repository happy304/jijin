"""Unit tests for :mod:`app.tasks.discovery`.

Tests run with Celery in eager mode and mock the EastmoneyProvider and
database session to avoid external dependencies.

Tests cover:
- Task registration on the Celery app
- Correct routing to the ``ingest`` queue
- Ranking data fetching and storage
- New fund identification and creation
- Observation period filtering
- Watchlist size limit enforcement
- Backfill task triggering
- Cleanup of stale rankings
- Beat schedule entries
- API endpoints

Requirements: auto-discovery feature
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.tasks.celery_app import celery_app
from app.tasks.discovery import (
    COOLDOWN_DAYS,
    FUND_TYPE_FILTERS,
    MAX_WATCHLIST_SIZE,
    OBSERVATION_DAYS,
    RANKING_DIMENSIONS,
    TOP_N,
    cleanup_stale_rankings,
    discover_funds,
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


def _make_ranking_item(code: str, name: str, half_year: float = 0.25) -> dict:
    """Helper to create a mock ranking item."""
    return {
        "code": code,
        "name": name,
        "unit_nav": Decimal("1.5000"),
        "accum_nav": Decimal("2.0000"),
        "daily_return": Decimal("0.0100"),
        "weekly_return": Decimal("0.0300"),
        "monthly_return": Decimal("0.0800"),
        "quarterly_return": Decimal("0.1500"),
        "half_year_return": Decimal(str(half_year)),
        "yearly_return": Decimal("0.4000"),
    }


# ---------------------------------------------------------------------------
# Task registration tests
# ---------------------------------------------------------------------------


class TestTaskRegistration:
    """Verify discovery tasks are registered on the Celery app."""

    def test_discover_funds_registered(self) -> None:
        assert "app.tasks.discovery.discover_funds" in celery_app.tasks

    def test_cleanup_stale_rankings_registered(self) -> None:
        assert "app.tasks.discovery.cleanup_stale_rankings" in celery_app.tasks

    def test_discover_funds_routes_to_ingest_queue(self) -> None:
        assert discover_funds.queue == "ingest"

    def test_cleanup_routes_to_ingest_queue(self) -> None:
        assert cleanup_stale_rankings.queue == "ingest"


# ---------------------------------------------------------------------------
# Configuration tests
# ---------------------------------------------------------------------------


class TestConfiguration:
    """Verify discovery configuration constants are reasonable."""

    def test_ranking_dimensions_not_empty(self) -> None:
        assert len(RANKING_DIMENSIONS) > 0

    def test_fund_type_filters_not_empty(self) -> None:
        assert len(FUND_TYPE_FILTERS) > 0

    def test_top_n_positive(self) -> None:
        assert TOP_N > 0

    def test_observation_days_positive(self) -> None:
        assert OBSERVATION_DAYS >= 1

    def test_cooldown_days_positive(self) -> None:
        assert COOLDOWN_DAYS >= 1

    def test_max_watchlist_size_reasonable(self) -> None:
        assert 50 <= MAX_WATCHLIST_SIZE <= 1000

    def test_dimensions_include_6month(self) -> None:
        metrics = [dim[0] for dim in RANKING_DIMENSIONS]
        assert "6yzf" in metrics

    def test_fund_types_include_stock(self) -> None:
        types = [ft[0] for ft in FUND_TYPE_FILTERS]
        assert "stock" in types


# ---------------------------------------------------------------------------
# Task execution tests (with mocked dependencies)
# ---------------------------------------------------------------------------


class TestDiscoverFunds:
    """Test discover_funds task execution."""

    @patch("app.tasks.discovery._trigger_backfill")
    @patch("app.tasks.discovery._create_new_funds", new_callable=AsyncMock)
    @patch("app.tasks.discovery._filter_by_observation", new_callable=AsyncMock)
    @patch("app.tasks.discovery._get_existing_fund_codes", new_callable=AsyncMock)
    @patch("app.tasks.discovery._store_rankings", new_callable=AsyncMock)
    @patch("app.data.session.get_sessionmaker")
    @patch("app.data.providers.eastmoney.EastmoneyProvider.fetch_fund_ranking", new_callable=AsyncMock)
    def test_full_discovery_flow(
        self,
        mock_fetch_ranking,
        mock_sessionmaker,
        mock_store_rankings,
        mock_get_existing,
        mock_filter_observation,
        mock_create_funds,
        mock_trigger_backfill,
        eager_celery,
    ) -> None:
        """Full discovery flow: fetch → store → identify → create → backfill."""
        # Mock ranking data
        mock_fetch_ranking.return_value = [
            _make_ranking_item("000001", "基金A"),
            _make_ranking_item("000002", "基金B"),
            _make_ranking_item("999999", "新基金C"),
        ]

        # Mock session factory
        mock_factory = MagicMock()
        mock_sessionmaker.return_value = mock_factory

        # Mock store rankings
        mock_store_rankings.return_value = 27  # 9 dimensions × 3 items

        # Mock existing codes (000001 and 000002 already exist)
        mock_get_existing.return_value = {"000001", "000002"}

        # Mock observation filter (999999 passes)
        mock_filter_observation.return_value = {"999999"}

        # Mock fund creation
        mock_create_funds.return_value = 1

        # Mock backfill trigger
        mock_trigger_backfill.return_value = 1

        result = discover_funds()

        assert result["status"] == "success"
        assert result["unique_codes_discovered"] == 3
        assert result["new_codes_found"] == 1
        assert result["qualified_after_observation"] == 1
        assert result["funds_created"] == 1
        assert result["backfill_triggered"] == 1

    @patch("app.tasks.discovery._trigger_backfill")
    @patch("app.tasks.discovery._create_new_funds", new_callable=AsyncMock)
    @patch("app.tasks.discovery._filter_by_observation", new_callable=AsyncMock)
    @patch("app.tasks.discovery._get_existing_fund_codes", new_callable=AsyncMock)
    @patch("app.tasks.discovery._store_rankings", new_callable=AsyncMock)
    @patch("app.data.session.get_sessionmaker")
    @patch("app.data.providers.eastmoney.EastmoneyProvider.fetch_fund_ranking", new_callable=AsyncMock)
    def test_no_new_funds(
        self,
        mock_fetch_ranking,
        mock_sessionmaker,
        mock_store_rankings,
        mock_get_existing,
        mock_filter_observation,
        mock_create_funds,
        mock_trigger_backfill,
        eager_celery,
    ) -> None:
        """When all discovered funds already exist, no creation happens."""
        mock_fetch_ranking.return_value = [
            _make_ranking_item("000001", "基金A"),
        ]

        mock_factory = MagicMock()
        mock_sessionmaker.return_value = mock_factory
        mock_store_rankings.return_value = 9
        mock_get_existing.return_value = {"000001"}
        mock_filter_observation.return_value = set()
        mock_create_funds.return_value = 0
        mock_trigger_backfill.return_value = 0

        result = discover_funds()

        assert result["status"] == "success"
        assert result["new_codes_found"] == 0
        assert result["funds_created"] == 0
        assert result["backfill_triggered"] == 0
        mock_create_funds.assert_not_called()
        mock_trigger_backfill.assert_not_called()

    @patch("app.tasks.discovery._trigger_backfill")
    @patch("app.tasks.discovery._create_new_funds", new_callable=AsyncMock)
    @patch("app.tasks.discovery._filter_by_observation", new_callable=AsyncMock)
    @patch("app.tasks.discovery._get_existing_fund_codes", new_callable=AsyncMock)
    @patch("app.tasks.discovery._store_rankings", new_callable=AsyncMock)
    @patch("app.data.session.get_sessionmaker")
    @patch("app.data.providers.eastmoney.EastmoneyProvider.fetch_fund_ranking", new_callable=AsyncMock)
    def test_fetch_error_handled_gracefully(
        self,
        mock_fetch_ranking,
        mock_sessionmaker,
        mock_store_rankings,
        mock_get_existing,
        mock_filter_observation,
        mock_create_funds,
        mock_trigger_backfill,
        eager_celery,
    ) -> None:
        """Provider errors are caught and counted, not propagated."""
        mock_fetch_ranking.side_effect = Exception("network timeout")

        mock_factory = MagicMock()
        mock_sessionmaker.return_value = mock_factory
        mock_store_rankings.return_value = 0
        mock_get_existing.return_value = set()
        mock_filter_observation.return_value = set()
        mock_create_funds.return_value = 0
        mock_trigger_backfill.return_value = 0

        result = discover_funds()

        assert result["status"] == "success"
        # All 9 dimension×type combos should fail
        expected_errors = len(RANKING_DIMENSIONS) * len(FUND_TYPE_FILTERS)
        assert result["fetch_errors"] == expected_errors
        assert result["unique_codes_discovered"] == 0

    @patch("app.tasks.discovery._trigger_backfill")
    @patch("app.tasks.discovery._create_new_funds", new_callable=AsyncMock)
    @patch("app.tasks.discovery._filter_by_observation", new_callable=AsyncMock)
    @patch("app.tasks.discovery._get_existing_fund_codes", new_callable=AsyncMock)
    @patch("app.tasks.discovery._store_rankings", new_callable=AsyncMock)
    @patch("app.data.session.get_sessionmaker")
    @patch("app.data.providers.eastmoney.EastmoneyProvider.fetch_fund_ranking", new_callable=AsyncMock)
    def test_watchlist_limit_enforced(
        self,
        mock_fetch_ranking,
        mock_sessionmaker,
        mock_store_rankings,
        mock_get_existing,
        mock_filter_observation,
        mock_create_funds,
        mock_trigger_backfill,
        eager_celery,
    ) -> None:
        """When watchlist is at capacity, no new funds are added."""
        mock_fetch_ranking.return_value = [
            _make_ranking_item("999999", "新基金"),
        ]

        mock_factory = MagicMock()
        mock_sessionmaker.return_value = mock_factory
        mock_store_rankings.return_value = 9

        # Simulate watchlist at capacity
        mock_get_existing.return_value = {f"{i:06d}" for i in range(MAX_WATCHLIST_SIZE)}
        mock_filter_observation.return_value = {"999999"}
        mock_create_funds.return_value = 0
        mock_trigger_backfill.return_value = 0

        result = discover_funds()

        assert result["status"] == "success"
        # Should not create any funds since we're at capacity
        assert result["funds_created"] == 0


class TestCleanupStaleRankings:
    """Test cleanup_stale_rankings task execution."""

    @patch("app.data.session.get_sessionmaker")
    def test_cleanup_success(self, mock_sessionmaker, eager_celery) -> None:
        """Cleanup task executes and returns summary."""
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        mock_result = MagicMock()
        mock_result.rowcount = 150
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        mock_factory = MagicMock()
        mock_factory.return_value = mock_session
        mock_sessionmaker.return_value = mock_factory

        result = cleanup_stale_rankings(retention_days=30)

        assert result["status"] == "success"
        assert result["records_deleted"] == 150

    @patch("app.data.session.get_sessionmaker")
    def test_cleanup_error_handled(self, mock_sessionmaker, eager_celery) -> None:
        """Cleanup handles database errors gracefully."""
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session.execute = AsyncMock(side_effect=Exception("db error"))
        mock_session.rollback = AsyncMock()

        mock_factory = MagicMock()
        mock_factory.return_value = mock_session
        mock_sessionmaker.return_value = mock_factory

        result = cleanup_stale_rankings(retention_days=30)

        assert result["status"] == "error"
        assert "db error" in result["error"]


# ---------------------------------------------------------------------------
# Beat schedule tests
# ---------------------------------------------------------------------------


class TestBeatSchedule:
    """Verify Beat schedule entries for discovery tasks."""

    def test_daily_discovery_schedule_exists(self) -> None:
        from app.tasks.schedule import BEAT_SCHEDULE

        assert "daily-fund-discovery" in BEAT_SCHEDULE
        entry = BEAT_SCHEDULE["daily-fund-discovery"]
        assert entry["task"] == "app.tasks.discovery.discover_funds"
        assert entry["options"]["queue"] == "ingest"

    def test_weekly_cleanup_schedule_exists(self) -> None:
        from app.tasks.schedule import BEAT_SCHEDULE

        assert "weekly-ranking-cleanup" in BEAT_SCHEDULE
        entry = BEAT_SCHEDULE["weekly-ranking-cleanup"]
        assert entry["task"] == "app.tasks.discovery.cleanup_stale_rankings"
        assert entry["options"]["queue"] == "ingest"

    def test_discovery_runs_before_ingestion(self) -> None:
        """Discovery (20:30) should run before data ingestion (21:00)."""
        from app.tasks.schedule import BEAT_SCHEDULE

        discovery_entry = BEAT_SCHEDULE["daily-fund-discovery"]
        nav_entry = BEAT_SCHEDULE["daily-nav-ingest"]

        # Extract hour from crontab schedule (crontab.hour is a set-like)
        discovery_hour = min(discovery_entry["schedule"].hour)
        nav_hour = min(nav_entry["schedule"].hour)

        # Discovery at 20, NAV at 21
        assert discovery_hour < nav_hour


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------


class TestDiscoveryAPI:
    """Test discovery API endpoints."""

    def test_trigger_endpoint_sync_mode(self, client) -> None:
        """POST /discovery/trigger (default sync) executes and returns result."""
        mock_result = {
            "status": "success",
            "unique_codes_discovered": 15,
            "funds_created": 3,
        }
        with patch("app.tasks.discovery._discover_funds_async", new_callable=AsyncMock) as mock_fn:
            mock_fn.return_value = mock_result
            response = client.post("/api/v1/discovery/trigger")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert "15" in data["message"]
        assert "3" in data["message"]

    def test_trigger_endpoint_async_mode(self, client) -> None:
        """POST /discovery/trigger?async=true submits to Celery queue."""
        with patch("app.tasks.discovery.discover_funds.apply_async") as mock_apply:
            mock_task_result = MagicMock()
            mock_task_result.id = "test-task-id-456"
            mock_apply.return_value = mock_task_result

            response = client.post("/api/v1/discovery/trigger?async=true")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "triggered"
        assert data["task_id"] == "test-task-id-456"


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestFundRankingModel:
    """Test FundRanking ORM model."""

    def test_model_tablename(self) -> None:
        from app.data.models.fund_ranking import FundRanking

        assert FundRanking.__tablename__ == "fund_rankings"

    def test_model_primary_key_columns(self) -> None:
        from app.data.models.fund_ranking import FundRanking

        pk_cols = [c.name for c in FundRanking.__table__.primary_key.columns]
        assert "fund_code" in pk_cols
        assert "snapshot_date" in pk_cols
        assert "sort_metric" in pk_cols

    def test_model_has_performance_columns(self) -> None:
        from app.data.models.fund_ranking import FundRanking

        col_names = [c.name for c in FundRanking.__table__.columns]
        assert "half_year_return" in col_names
        assert "yearly_return" in col_names
        assert "quarterly_return" in col_names
        assert "rank_position" in col_names

    def test_model_registered_in_base(self) -> None:
        from app.data.models import Base

        assert "fund_rankings" in Base.metadata.tables
