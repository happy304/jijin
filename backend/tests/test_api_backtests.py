"""Integration tests for the backtest API endpoints.

Tests cover:
- POST /api/v1/backtests (submit backtest, returns 202 with run_id)
- GET /api/v1/backtests/{run_id} (get status)
- GET /api/v1/backtests/{run_id}/equity (get equity curve)
- GET /api/v1/backtests/{run_id}/trades (get trade history)
- GET /api/v1/backtests/{run_id}/attribution (get attribution)
- WebSocket /api/v1/backtests/{run_id}/progress (progress subscription)

Requirements: 7.3, 7.4
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import AsyncIterator
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings, get_settings
from app.data.models import Base
from app.data.models.backtests import BacktestEquity, BacktestRun, BacktestTrade
from app.data.models.fund_nav import FundNav
from app.data.models.strategies import Strategy
from app.data.session import get_session
from app.main import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def test_settings() -> Settings:
    """Test settings with in-memory SQLite."""
    get_settings.cache_clear()
    return Settings(
        APP_ENV="test",
        DEBUG="true",
        LOG_LEVEL="WARNING",
        DATABASE_URL="sqlite+aiosqlite:///:memory:",
        DB_AUTO_MIGRATE="false",
        PROMETHEUS_ENABLED="false",
        REDIS_URL="redis://localhost:6379/15",
    )


@pytest.fixture
async def engine(test_settings: Settings):
    """Create an in-memory database engine with all tables."""
    eng = create_async_engine(
        test_settings.database_url,
        echo=False,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest.fixture
async def session_factory(engine):
    """Create a session factory bound to the test engine."""
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


@pytest.fixture
async def app(test_settings: Settings, session_factory) -> FastAPI:
    """Create a FastAPI app with test settings and DB session override."""
    application = create_app(test_settings)
    application.dependency_overrides[get_settings] = lambda: test_settings

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    application.dependency_overrides[get_session] = override_get_session
    yield application
    application.dependency_overrides.clear()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Async HTTP client for testing."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def sample_strategy(session_factory) -> Strategy:
    """Create a sample strategy in the database for testing."""
    async with session_factory() as session:
        strategy = Strategy(
            name="测试动量策略",
            strategy_type="momentum",
            params={
                "lookback_months": 6,
                "top_n": 3,
                "rebalance_freq": "monthly",
            },
            universe={"fund_codes": ["000001", "000002", "000003"]},
            benchmark="000300",
            created_by="test_user",
        )
        session.add(strategy)
        await session.commit()
        await session.refresh(strategy)
        return strategy


@pytest.fixture
async def sample_run(session_factory, sample_strategy: Strategy) -> BacktestRun:
    """Create a sample backtest run in the database."""
    async with session_factory() as session:
        run = BacktestRun(
            strategy_id=sample_strategy.id,
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
            initial_capital=Decimal("100000"),
            status="done",
            progress=Decimal("100"),
            metrics={
                "total_return": 0.15,
                "annualized_return": 0.15,
                "max_drawdown": -0.08,
                "sharpe": 1.5,
                "volatility": 0.12,
            },
            started_at=datetime(2023, 1, 1, 10, 0, 0),
            finished_at=datetime(2023, 1, 1, 10, 5, 0),
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        return run


@pytest.fixture
async def sample_equity(session_factory, sample_run: BacktestRun) -> list[BacktestEquity]:
    """Create sample equity data for the backtest run."""
    async with session_factory() as session:
        rows = []
        for i, d in enumerate([
            date(2023, 1, 3),
            date(2023, 1, 4),
            date(2023, 1, 5),
        ]):
            row = BacktestEquity(
                run_id=sample_run.id,
                trade_date=d,
                equity=Decimal("100000") + Decimal(str(i * 500)),
                cash=Decimal("50000") - Decimal(str(i * 100)),
                position_value=Decimal("50000") + Decimal(str(i * 600)),
                benchmark_value=Decimal("100000") + Decimal(str(i * 300)),
            )
            session.add(row)
            rows.append(row)
        await session.commit()
        return rows


@pytest.fixture
async def sample_trades(session_factory, sample_run: BacktestRun) -> list[BacktestTrade]:
    """Create sample trade data for the backtest run."""
    async with session_factory() as session:
        rows = []
        for i in range(3):
            row = BacktestTrade(
                run_id=sample_run.id,
                trade_id=i + 1,
                order_date=date(2023, 1, 3),
                confirm_date=date(2023, 1, 4),
                fund_code=f"00000{i + 1}",
                direction="subscribe",
                amount=Decimal("10000"),
                shares=Decimal("6543.21"),
                nav=Decimal("1.5280"),
                fee=Decimal("15.00"),
            )
            session.add(row)
            rows.append(row)
        await session.commit()
        return rows


# ---------------------------------------------------------------------------
# Tests: POST /api/v1/backtests
# ---------------------------------------------------------------------------


class TestSubmitBacktest:
    """Tests for backtest submission endpoint."""

    async def test_submit_backtest_success(
        self, client: AsyncClient, sample_strategy: Strategy
    ):
        """Should accept a backtest submission and return 202 with run_id."""
        with patch("app.api.v1.backtests._dispatch_backtest_task") as mock_dispatch:
            mock_dispatch.return_value = None

            resp = await client.post(
                "/api/v1/backtests",
                json={
                    "strategy_id": sample_strategy.id,
                    "start_date": "2023-01-01",
                    "end_date": "2023-12-31",
                    "initial_capital": "100000",
                },
            )

        assert resp.status_code == 202
        data = resp.json()
        assert "run_id" in data
        assert data["status"] == "pending"
        assert isinstance(data["run_id"], int)

    async def test_submit_backtest_invalid_date_range(
        self, client: AsyncClient, sample_strategy: Strategy
    ):
        """Should return 422 when end_date <= start_date."""
        resp = await client.post(
            "/api/v1/backtests",
            json={
                "strategy_id": sample_strategy.id,
                "start_date": "2023-12-31",
                "end_date": "2023-01-01",
                "initial_capital": "100000",
            },
        )
        assert resp.status_code == 422

    async def test_submit_backtest_nonexistent_strategy(self, client: AsyncClient):
        """Should return 404 when strategy doesn't exist."""
        resp = await client.post(
            "/api/v1/backtests",
            json={
                "strategy_id": 99999,
                "start_date": "2023-01-01",
                "end_date": "2023-12-31",
                "initial_capital": "100000",
            },
        )
        assert resp.status_code == 404

    async def test_submit_backtest_marks_failed_when_dispatch_fails(
        self, client: AsyncClient, session_factory, sample_strategy: Strategy
    ):
        """Should not leave a run stuck when Celery dispatch fails."""
        with patch(
            "app.api.v1.backtests._dispatch_backtest_task",
            side_effect=RuntimeError("回测任务派发失败，请确认 Redis/Celery 可用: broker down"),
        ):
            resp = await client.post(
                "/api/v1/backtests",
                json={
                    "strategy_id": sample_strategy.id,
                    "start_date": "2023-02-01",
                    "end_date": "2023-12-31",
                    "initial_capital": "100000",
                },
            )

        assert resp.status_code == 503
        async with session_factory() as session:
            rows = (
                await session.execute(
                    select(BacktestRun).where(
                        BacktestRun.strategy_id == sample_strategy.id,
                        BacktestRun.start_date == date(2023, 2, 1),
                    )
                )
            ).scalars().all()
            assert len(rows) == 1
            assert rows[0].status == "failed"
            assert "broker down" in (rows[0].error_msg or "")

    async def test_submit_backtest_default_capital(
        self, client: AsyncClient, sample_strategy: Strategy
    ):
        """Should use default initial_capital when not provided."""
        with patch("app.tasks.backtest.run_backtest") as mock_task:
            mock_task.delay = MagicMock()

            resp = await client.post(
                "/api/v1/backtests",
                json={
                    "strategy_id": sample_strategy.id,
                    "start_date": "2023-01-01",
                    "end_date": "2023-12-31",
                },
            )

        assert resp.status_code == 202

    async def test_submit_backtest_clears_stale_live_progress_when_reusing_run(
        self, client: AsyncClient, session_factory, sample_strategy: Strategy
    ):
        """Should delete stale Redis progress when reusing an existing run."""
        async with session_factory() as session:
            run = BacktestRun(
                strategy_id=sample_strategy.id,
                start_date=date(2023, 1, 1),
                end_date=date(2023, 12, 31),
                initial_capital=Decimal("100000"),
                status="failed",
                progress=Decimal("100"),
                error_msg="旧失败",
            )
            session.add(run)
            await session.commit()
            await session.refresh(run)

        mock_task = MagicMock()
        mock_task.delay = MagicMock()
        mock_redis = MagicMock()

        with patch("app.tasks.backtest.run_backtest", mock_task), patch(
            "redis.Redis.from_url", return_value=mock_redis
        ):
            resp = await client.post(
                "/api/v1/backtests",
                json={
                    "strategy_id": sample_strategy.id,
                    "start_date": "2023-01-01",
                    "end_date": "2023-12-31",
                    "initial_capital": "100000",
                },
            )

        assert resp.status_code == 202
        data = resp.json()
        assert data["run_id"] == run.id
        mock_redis.delete.assert_called_once_with(f"backtest:progress:{run.id}")


# ---------------------------------------------------------------------------
# Tests: GET /api/v1/backtests/{run_id}
# ---------------------------------------------------------------------------


class TestRerunBacktest:
    """Tests for backtest rerun endpoint."""

    async def test_rerun_backtest_nonexistent(self, client: AsyncClient):
        """Should return 404 when rerun target does not exist."""
        resp = await client.post("/api/v1/backtests/99999/rerun")
        assert resp.status_code == 404

    async def test_rerun_backtest_rejects_running_status(
        self, client: AsyncClient, session_factory, sample_strategy: Strategy
    ):
        """Should return 409 when run is already pending/running."""
        async with session_factory() as session:
            run = BacktestRun(
                strategy_id=sample_strategy.id,
                start_date=date(2023, 1, 1),
                end_date=date(2023, 12, 31),
                initial_capital=Decimal("100000"),
                status="running",
                progress=Decimal("37.5"),
            )
            session.add(run)
            await session.commit()
            await session.refresh(run)

        resp = await client.post(f"/api/v1/backtests/{run.id}/rerun")
        assert resp.status_code == 409

    async def test_rerun_backtest_success_resets_and_clears_old_results(
        self,
        client: AsyncClient,
        session_factory,
        sample_run: BacktestRun,
        sample_equity,
        sample_trades,
    ):
        """Should clear old data, reset run fields, and enqueue rerun."""
        mock_task = MagicMock()
        mock_task.delay = MagicMock()
        mock_redis = MagicMock()

        async with session_factory() as session:
            run = await session.get(BacktestRun, sample_run.id)
            assert run is not None
            run.error_msg = "旧错误"
            run.metrics = {"total_return": 0.15, "sharpe": 1.5}
            await session.commit()

        with patch("app.tasks.backtest.run_backtest", mock_task), patch(
            "redis.Redis.from_url", return_value=mock_redis
        ):
            resp = await client.post(f"/api/v1/backtests/{sample_run.id}/rerun")

        assert resp.status_code == 202
        data = resp.json()
        assert data == {
            "run_id": sample_run.id,
            "status": "pending",
            "message": "回测任务已重新提交，正在异步执行",
        }
        mock_task.delay.assert_called_once_with(sample_run.id)
        mock_redis.delete.assert_called_once_with(f"backtest:progress:{sample_run.id}")

        async with session_factory() as session:
            run = await session.get(BacktestRun, sample_run.id)
            assert run is not None
            assert run.status == "pending"
            assert run.progress == Decimal("0")
            assert run.metrics is None
            assert run.error_msg is None
            assert run.started_at is None
            assert run.finished_at is None

            equity_count = await session.scalar(
                select(func.count())
                .select_from(BacktestEquity)
                .where(BacktestEquity.run_id == sample_run.id)
            )
            trade_count = await session.scalar(
                select(func.count())
                .select_from(BacktestTrade)
                .where(BacktestTrade.run_id == sample_run.id)
            )
            assert equity_count == 0
            assert trade_count == 0


class TestGetBacktestStatus:
    """Tests for backtest status endpoint."""

    async def test_get_status_done(
        self, client: AsyncClient, sample_run: BacktestRun
    ):
        """Should return status and metrics for a completed run."""
        resp = await client.get(f"/api/v1/backtests/{sample_run.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == sample_run.id
        assert data["status"] == "done"
        assert data["progress"] == 100.0
        assert data["metrics"]["total_return"] == 0.15
        assert data["metrics"]["sharpe"] == 1.5
        assert data["nav_data_stale"] is None
        assert data["quality"]["cash_arrival_delay_modelled"] is True
        assert data["quality"]["decision_grade"] == "research_approximation"

    async def test_get_status_exposes_nav_data_stale(
        self, client: AsyncClient, session_factory, sample_run: BacktestRun
    ):
        """Should expose NAV stale marker at the top level."""
        marker = {
            "stale": True,
            "reason": "adj_nav_history_recalculated",
            "fund_codes": ["000001"],
        }
        async with session_factory() as session:
            run = await session.get(BacktestRun, sample_run.id)
            assert run is not None
            run.metrics = {**(run.metrics or {}), "nav_data_stale": marker}
            await session.commit()

        resp = await client.get(f"/api/v1/backtests/{sample_run.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["nav_data_stale"] == marker

    async def test_get_status_exposes_nav_quality_warning(
        self, client: AsyncClient, session_factory, sample_run: BacktestRun
    ):
        """Should expose NAV quality warning at the top level."""
        warning = {
            "has_unit_nav_fallback": True,
            "funds": {
                "000001": {
                    "total_points": 10,
                    "adj_nav_points": 8,
                    "unit_nav_fallback_points": 2,
                }
            },
        }
        async with session_factory() as session:
            run = await session.get(BacktestRun, sample_run.id)
            assert run is not None
            run.metrics = {**(run.metrics or {}), "nav_quality_warning": warning}
            await session.commit()

        resp = await client.get(f"/api/v1/backtests/{sample_run.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["nav_quality_warning"] == warning

    async def test_get_status_includes_data_quality_report_in_metrics(
        self, client: AsyncClient, session_factory, sample_run: BacktestRun
    ):
        """Should keep the data quality JSON audit snapshot in metrics."""
        report = {
            "overall_status": "warning",
            "can_proceed": True,
            "warnings": ["000001: 净值跳变 1 次（阈值 ±0.05）"],
            "funds": [
                {
                    "fund_code": "000001",
                    "coverage_ratio": 1.0,
                    "total_trading_days": 2,
                    "available_days": 2,
                    "max_gap_days": 0,
                    "spike_count": 1,
                    "spike_threshold": "0.05",
                    "spike_dates": ["2024-01-03"],
                    "first_data_date": "2024-01-02",
                    "last_data_date": "2024-01-03",
                    "status": "warning",
                }
            ],
        }
        async with session_factory() as session:
            run = await session.get(BacktestRun, sample_run.id)
            assert run is not None
            run.metrics = {**(run.metrics or {}), "data_quality_report": report}
            await session.commit()

        resp = await client.get(f"/api/v1/backtests/{sample_run.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["metrics"]["data_quality_report"] == report

    async def test_get_status_nonexistent(self, client: AsyncClient):
        """Should return 404 for non-existent run."""
        resp = await client.get("/api/v1/backtests/99999")
        assert resp.status_code == 404

    async def test_get_status_pending(
        self, client: AsyncClient, session_factory, sample_strategy: Strategy
    ):
        """Should return pending status for a new run."""
        async with session_factory() as session:
            run = BacktestRun(
                strategy_id=sample_strategy.id,
                start_date=date(2023, 1, 1),
                end_date=date(2023, 12, 31),
                initial_capital=Decimal("100000"),
                status="pending",
                progress=Decimal("0"),
            )
            session.add(run)
            await session.commit()
            await session.refresh(run)

        resp = await client.get(f"/api/v1/backtests/{run.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "pending"
        assert data["progress"] == 0.0

    async def test_get_status_uses_live_redis_progress(
        self, client: AsyncClient, session_factory, sample_strategy: Strategy
    ):
        """Should prefer live Redis progress for pending/running runs."""
        async with session_factory() as session:
            run = BacktestRun(
                strategy_id=sample_strategy.id,
                start_date=date(2023, 1, 1),
                end_date=date(2023, 12, 31),
                initial_capital=Decimal("100000"),
                status="running",
                progress=Decimal("0"),
            )
            session.add(run)
            await session.commit()
            await session.refresh(run)

        payload = (
            '{'
            f'"run_id": {run.id}, '
            '"progress": 42.5, '
            '"message": "回测进行中 (10/20)", '
            '"status": "running"'
            '}'
        )
        mock_redis = MagicMock()
        mock_redis.get.return_value = payload

        with patch("redis.Redis.from_url", return_value=mock_redis):
            resp = await client.get(f"/api/v1/backtests/{run.id}")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "running"
        assert data["progress"] == 42.5
        assert data["progress_message"] == "回测进行中 (10/20)"


class TestDataQuality:
    """Tests for backtest data quality endpoint."""

    async def test_check_quality_prefers_adjusted_nav(
        self, client: AsyncClient, session_factory, sample_strategy: Strategy
    ):
        """Should use adj_nav when available instead of raw unit_nav."""
        async with session_factory() as session:
            session.add_all([
                FundNav(
                    fund_code="000001",
                    trade_date=date(2023, 1, 3),
                    unit_nav=Decimal("1.00"),
                    adj_nav=Decimal("1.10"),
                    created_at=datetime.now(timezone.utc),
                ),
                FundNav(
                    fund_code="000001",
                    trade_date=date(2023, 1, 4),
                    unit_nav=Decimal("0.90"),
                    adj_nav=Decimal("1.11"),
                    created_at=datetime.now(timezone.utc),
                ),
                FundNav(
                    fund_code="000002",
                    trade_date=date(2023, 1, 3),
                    unit_nav=Decimal("1.00"),
                    adj_nav=Decimal("1.00"),
                    created_at=datetime.now(timezone.utc),
                ),
                FundNav(
                    fund_code="000002",
                    trade_date=date(2023, 1, 4),
                    unit_nav=Decimal("1.01"),
                    adj_nav=Decimal("1.01"),
                    created_at=datetime.now(timezone.utc),
                ),
                FundNav(
                    fund_code="000003",
                    trade_date=date(2023, 1, 3),
                    unit_nav=Decimal("1.00"),
                    adj_nav=Decimal("1.00"),
                    created_at=datetime.now(timezone.utc),
                ),
                FundNav(
                    fund_code="000003",
                    trade_date=date(2023, 1, 4),
                    unit_nav=Decimal("1.02"),
                    adj_nav=Decimal("1.02"),
                    created_at=datetime.now(timezone.utc),
                ),
            ])
            await session.commit()

        resp = await client.post(
            "/api/v1/backtests/check-quality",
            json={
                "strategy_id": sample_strategy.id,
                "start_date": "2023-01-03",
                "end_date": "2023-01-04",
                "initial_capital": "100000",
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        fund_item = next(item for item in data["funds"] if item["fund_code"] == "000001")
        # 如果错误使用 unit_nav，会产生一次明显 spike；使用 adj_nav 则应为 0。
        assert fund_item["spike_count"] == 0
        assert data["can_proceed"] is True


class TestListBacktests:
    """Tests for backtest list endpoint."""

    async def test_list_backtests_uses_live_redis_progress(
        self, client: AsyncClient, session_factory, sample_strategy: Strategy
    ):
        """Should merge live Redis progress into the list response."""
        async with session_factory() as session:
            run = BacktestRun(
                strategy_id=sample_strategy.id,
                start_date=date(2023, 1, 1),
                end_date=date(2023, 12, 31),
                initial_capital=Decimal("100000"),
                status="running",
                progress=Decimal("0"),
            )
            session.add(run)
            await session.commit()
            await session.refresh(run)

        payload = (
            '{'
            f'"run_id": {run.id}, '
            '"progress": 55, '
            '"message": "保存回测结果", '
            '"status": "running"'
            '}'
        )
        mock_redis = MagicMock()
        mock_redis.get.return_value = payload

        with patch("redis.Redis.from_url", return_value=mock_redis):
            resp = await client.get("/api/v1/backtests")

        assert resp.status_code == 200
        data = resp.json()
        target = next(item for item in data if item["id"] == run.id)
        assert target["status"] == "running"
        assert target["progress"] == 55
        assert target["progress_message"] == "保存回测结果"


class TestWalkForwardParamSpace:
    """Tests for explicit walk-forward parameter space construction."""

    def test_build_param_space_for_momentum_uses_explicit_dimensions(self):
        from app.api.v1.backtests import _build_param_space

        space = _build_param_space(
            "momentum",
            {"lookback_days": 120, "top_n": 3, "rebalance_freq": "monthly", "score_method": "return"},
        )
        names = {dim.name for dim in space.dimensions}
        assert {"lookback_days", "top_n", "rebalance_freq", "score_method"}.issubset(names)

    def test_build_param_space_for_timing_dual_ma_uses_explicit_windows(self):
        from app.api.v1.backtests import _build_param_space

        space = _build_param_space(
            "timing",
            {"method": "dual_ma", "short_window": 5, "long_window": 20},
        )
        names = {dim.name for dim in space.dimensions}
        assert "method" in names
        assert "short_window" in names
        assert "long_window" in names

    def test_build_param_space_for_unknown_type_is_empty(self):
        from app.api.v1.backtests import _build_param_space

        space = _build_param_space("unknown", {"foo": 1})
        assert space.dimensions == []


class TestCPCVEndpoint:
    """Tests for CPCV/PBO endpoints."""

    async def test_get_cpcv_uses_true_runner(
        self, client: AsyncClient, sample_run: BacktestRun
    ):
        """Should delegate CPCV/PBO diagnostics to the true helper."""
        from app.api.v1.backtests import CPCVPathResponse, CPCVResponse

        fake_response = CPCVResponse(
            run_id=sample_run.id,
            pbo=0.28,
            avg_oos_sharpe=0.63,
            std_oos_sharpe=0.22,
            avg_is_sharpe=0.91,
            n_paths=10,
            is_overfit=False,
            n_splits=6,
            n_test_splits=2,
            note="PBO 未显示明显过拟合",
            paths=[
                CPCVPathResponse(
                    test_groups=[1, 3],
                    train_groups=[0, 2, 4, 5],
                    is_sharpe=1.02,
                    oos_sharpe=0.58,
                    is_return=0.14,
                    oos_return=0.07,
                )
            ],
        )

        with patch("app.api.v1.backtests._run_true_cpcv", new=AsyncMock(return_value=fake_response)) as mock_runner:
            resp = await client.get(f"/api/v1/backtests/{sample_run.id}/cpcv")

        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == sample_run.id
        assert data["pbo"] == 0.28
        assert data["paths"][0]["test_groups"] == [1, 3]
        mock_runner.assert_awaited_once()


class TestWalkForwardEndpoint:
    """Tests for walk-forward endpoints."""

    async def test_post_walk_forward_uses_true_runner(
        self, client: AsyncClient, sample_run: BacktestRun
    ):
        """Should delegate walk-forward validation to the true rerun helper."""
        from app.api.v1.backtests import WalkForwardResponse, WalkForwardWindowResponse

        fake_response = WalkForwardResponse(
            run_id=sample_run.id,
            wfe=0.62,
            avg_oos_sharpe=0.88,
            avg_is_sharpe=1.11,
            avg_oos_return=0.12,
            oos_win_rate=0.67,
            total_oos_return=0.21,
            is_robust=True,
            windows=[
                WalkForwardWindowResponse(
                    window_id=1,
                    train_start=date(2023, 1, 1),
                    train_end=date(2023, 6, 30),
                    test_start=date(2023, 7, 1),
                    test_end=date(2023, 9, 30),
                    is_sharpe=1.1,
                    oos_sharpe=0.8,
                    is_return=0.15,
                    oos_return=0.09,
                    is_max_drawdown=0.05,
                    oos_max_drawdown=0.06,
                )
            ],
            note="true walk-forward",
        )

        with patch("app.api.v1.backtests._run_true_walk_forward", new=AsyncMock(return_value=fake_response)) as mock_runner:
            resp = await client.post(f"/api/v1/backtests/{sample_run.id}/walk-forward")

        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == sample_run.id
        assert data["wfe"] == 0.62
        assert data["windows"][0]["window_id"] == 1
        mock_runner.assert_awaited_once()

    async def test_get_walk_forward_is_lightweight_hint(
        self, client: AsyncClient, sample_run: BacktestRun
    ):
        """GET must not trigger heavy Walk-Forward computation."""
        with patch("app.api.v1.backtests._run_true_walk_forward", new=AsyncMock()) as mock_runner:
            resp = await client.get(f"/api/v1/backtests/{sample_run.id}/walk-forward?max_trials=1")

        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == sample_run.id
        assert data["windows"] == []
        assert data["note"].startswith("GET 仅返回轻量提示")
        mock_runner.assert_not_awaited()


# ---------------------------------------------------------------------------
# Tests: GET /api/v1/backtests/{run_id}/equity
# ---------------------------------------------------------------------------


class TestGetBacktestEquity:
    """Tests for equity curve endpoint."""

    async def test_get_equity_curve(
        self, client: AsyncClient, sample_run: BacktestRun, sample_equity
    ):
        """Should return equity curve data."""
        resp = await client.get(f"/api/v1/backtests/{sample_run.id}/equity")
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == sample_run.id
        assert len(data["records"]) == 3
        assert data["records"][0]["trade_date"] == "2023-01-03"
        assert data["records"][0]["equity"] == 100000.00

    async def test_get_equity_empty(
        self, client: AsyncClient, sample_run: BacktestRun
    ):
        """Should return empty data when no equity records exist."""
        resp = await client.get(f"/api/v1/backtests/{sample_run.id}/equity")
        assert resp.status_code == 200
        data = resp.json()
        assert data["records"] == []

    async def test_get_equity_nonexistent_run(self, client: AsyncClient):
        """Should return 404 for non-existent run."""
        resp = await client.get("/api/v1/backtests/99999/equity")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: GET /api/v1/backtests/{run_id}/trades
# ---------------------------------------------------------------------------


class TestGetBacktestTrades:
    """Tests for trades endpoint."""

    async def test_get_trades(
        self, client: AsyncClient, sample_run: BacktestRun, sample_trades
    ):
        """Should return trade records."""
        resp = await client.get(f"/api/v1/backtests/{sample_run.id}/trades")
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == sample_run.id
        assert data["total"] == 3
        assert len(data["items"]) == 3
        assert data["items"][0]["fund_code"] == "000001"
        assert data["items"][0]["direction"] == "subscribe"

    async def test_get_trades_pagination(
        self, client: AsyncClient, sample_run: BacktestRun, sample_trades
    ):
        """Should paginate trade records."""
        resp = await client.get(
            f"/api/v1/backtests/{sample_run.id}/trades",
            params={"page": 1, "page_size": 2},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert len(data["items"]) == 2

    async def test_get_trades_empty(
        self, client: AsyncClient, sample_run: BacktestRun
    ):
        """Should return empty data when no trades exist."""
        resp = await client.get(f"/api/v1/backtests/{sample_run.id}/trades")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []

    async def test_get_trades_nonexistent_run(self, client: AsyncClient):
        """Should return 404 for non-existent run."""
        resp = await client.get("/api/v1/backtests/99999/trades")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: GET /api/v1/backtests/{run_id}/attribution
# ---------------------------------------------------------------------------


class TestGetBacktestAttribution:
    """Tests for attribution endpoint."""

    async def test_get_attribution_done(
        self, client: AsyncClient, sample_run: BacktestRun
    ):
        """Should return attribution data for a completed run."""
        resp = await client.get(f"/api/v1/backtests/{sample_run.id}/attribution")
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == sample_run.id

    async def test_get_attribution_not_done(
        self, client: AsyncClient, session_factory, sample_strategy: Strategy
    ):
        """Should return 400 when run is not completed."""
        async with session_factory() as session:
            run = BacktestRun(
                strategy_id=sample_strategy.id,
                start_date=date(2023, 1, 1),
                end_date=date(2023, 12, 31),
                initial_capital=Decimal("100000"),
                status="running",
                progress=Decimal("50"),
            )
            session.add(run)
            await session.commit()
            await session.refresh(run)

        resp = await client.get(f"/api/v1/backtests/{run.id}/attribution")
        assert resp.status_code == 400

    async def test_get_attribution_nonexistent_run(self, client: AsyncClient):
        """Should return 404 for non-existent run."""
        resp = await client.get("/api/v1/backtests/99999/attribution")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: WebSocket /api/v1/backtests/{run_id}/progress
# ---------------------------------------------------------------------------


class TestBacktestProgressWebSocket:
    """Tests for WebSocket progress endpoint."""

    async def test_websocket_connect(self, app: FastAPI):
        """Should accept WebSocket connection and receive progress."""
        import json
        from unittest.mock import AsyncMock

        # Mock the Redis async client
        completed_payload = json.dumps({
            "run_id": 1,
            "progress": 100,
            "message": "回测完成",
            "status": "done",
        })

        mock_redis_instance = AsyncMock()
        mock_redis_instance.get = AsyncMock(return_value=completed_payload)
        mock_redis_instance.close = AsyncMock()

        with patch("redis.asyncio.from_url", return_value=mock_redis_instance):
            from starlette.testclient import TestClient

            with TestClient(app) as tc:
                with tc.websocket_connect("/api/v1/backtests/1/progress") as ws:
                    # Should receive the current state (completed)
                    msg = ws.receive_text()
                    data = json.loads(msg)
                    assert data["run_id"] == 1
                    assert data["status"] == "done"
                    assert data["progress"] == 100
