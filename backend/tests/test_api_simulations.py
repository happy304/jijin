"""Integration tests for the simulation API endpoints."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import AsyncIterator
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings, get_settings
from app.data.models import Base
from app.data.models.simulations import SimulationRun
from app.data.models.strategies import Strategy
from app.data.session import get_session
from app.domain.simulation.monte_carlo import MonteCarloEngine, SimulationConfig
from app.domain.simulation.risk_metrics import compute_extended_metrics
from app.domain.simulation.strategy_simulation import StrategySimConfig, StrategySimulationEngine
from app.main import create_app


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
    eng = create_async_engine(test_settings.database_url, echo=False)
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
            name="测试模拟策略",
            strategy_type="momentum",
            params={"lookback_months": 6, "top_n": 3},
            universe={"fund_codes": ["000001", "000002", "000003"]},
            benchmark="000300",
            created_by="test_user",
        )
        session.add(strategy)
        await session.commit()
        await session.refresh(strategy)
        return strategy


@pytest.fixture
async def sample_simulation_run(session_factory, sample_strategy: Strategy) -> SimulationRun:
    """Create a sample simulation run in the database."""
    async with session_factory() as session:
        run = SimulationRun(
            strategy_id=sample_strategy.id,
            horizon_days=252,
            num_simulations=10000,
            method="gbm",
            initial_capital=Decimal("100000"),
            confidence_levels=[0.95, 0.99],
            lookback_days=504,
            strategy_snapshot={
                "id": sample_strategy.id,
                "name": sample_strategy.name,
                "strategy_type": sample_strategy.strategy_type,
                "params": sample_strategy.params,
                "universe": sample_strategy.universe,
                "benchmark": sample_strategy.benchmark,
            },
            status="done",
            progress=Decimal("100"),
            metrics={"expected_return": 0.12},
            started_at=datetime(2024, 1, 1, 10, 0, 0),
            finished_at=datetime(2024, 1, 1, 10, 5, 0),
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        return run


class TestSimulationStatus:
    """Tests for simulation status endpoints."""

    async def test_get_simulation_done(
        self, client: AsyncClient, sample_simulation_run: SimulationRun
    ):
        """Should return persisted status for a completed run."""
        resp = await client.get(f"/api/v1/simulations/{sample_simulation_run.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == sample_simulation_run.id
        assert data["status"] == "done"
        assert data["progress"] == 100.0
        assert data["strategy_name"] == "测试模拟策略"
        assert data["nav_data_stale"] is None

    async def test_get_simulation_exposes_nav_data_stale(
        self, client: AsyncClient, session_factory, sample_simulation_run: SimulationRun
    ):
        """Should expose NAV stale marker at the top level."""
        marker = {
            "stale": True,
            "reason": "adj_nav_history_recalculated",
            "fund_codes": ["000001"],
        }
        async with session_factory() as session:
            run = await session.get(SimulationRun, sample_simulation_run.id)
            assert run is not None
            run.metrics = {**(run.metrics or {}), "nav_data_stale": marker}
            await session.commit()

        resp = await client.get(f"/api/v1/simulations/{sample_simulation_run.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["nav_data_stale"] == marker

    async def test_get_simulation_exposes_nav_quality_warning(
        self, client: AsyncClient, session_factory, sample_simulation_run: SimulationRun
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
            run = await session.get(SimulationRun, sample_simulation_run.id)
            assert run is not None
            run.metrics = {**(run.metrics or {}), "nav_quality_warning": warning}
            await session.commit()

        resp = await client.get(f"/api/v1/simulations/{sample_simulation_run.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["nav_quality_warning"] == warning

    async def test_get_simulation_uses_live_redis_progress(
        self,
        client: AsyncClient,
        session_factory,
        sample_strategy: Strategy,
    ):
        """Should prefer live Redis progress and message for active runs."""
        async with session_factory() as session:
            run = SimulationRun(
                strategy_id=sample_strategy.id,
                horizon_days=252,
                num_simulations=10000,
                method="gbm",
                initial_capital=Decimal("100000"),
                confidence_levels=[0.95, 0.99],
                lookback_days=504,
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
            '"message": "收益率计算完成", '
            '"status": "running"'
            '}'
        )
        mock_redis = MagicMock()
        mock_redis.get.return_value = payload

        with patch("redis.Redis.from_url", return_value=mock_redis):
            resp = await client.get(f"/api/v1/simulations/{run.id}")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "running"
        assert data["progress"] == 42.5
        assert data["progress_message"] == "收益率计算完成"

    async def test_list_simulations_uses_live_redis_progress(
        self,
        client: AsyncClient,
        session_factory,
        sample_strategy: Strategy,
    ):
        """Should merge Redis live progress into the list response."""
        async with session_factory() as session:
            run = SimulationRun(
                strategy_id=sample_strategy.id,
                horizon_days=252,
                num_simulations=10000,
                method="gbm",
                initial_capital=Decimal("100000"),
                confidence_levels=[0.95, 0.99],
                lookback_days=504,
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
            '"message": "初始化 Monte Carlo 引擎", '
            '"status": "running"'
            '}'
        )
        mock_redis = MagicMock()
        mock_redis.get.return_value = payload

        with patch("redis.Redis.from_url", return_value=mock_redis):
            resp = await client.get("/api/v1/simulations")

        assert resp.status_code == 200
        data = resp.json()
        target = next(item for item in data if item["id"] == run.id)
        assert target["status"] == "running"
        assert target["progress"] == 55
        assert target["progress_message"] == "初始化 Monte Carlo 引擎"


class TestSimulationStrategySnapshots:
    """Tests for persisted strategy snapshot behavior."""

    async def test_get_simulation_prefers_snapshot_name_when_strategy_deleted(
        self,
        client: AsyncClient,
        session_factory,
        sample_strategy: Strategy,
    ):
        """Should keep displaying the submission-time strategy name after deletion."""
        async with session_factory() as session:
            run = SimulationRun(
                strategy_id=sample_strategy.id,
                horizon_days=252,
                num_simulations=10000,
                method="gbm",
                initial_capital=Decimal("100000"),
                confidence_levels=[0.95, 0.99],
                lookback_days=504,
                strategy_snapshot={
                    "id": sample_strategy.id,
                    "name": "历史策略名称",
                    "strategy_type": sample_strategy.strategy_type,
                    "params": sample_strategy.params,
                    "universe": sample_strategy.universe,
                    "benchmark": sample_strategy.benchmark,
                },
                status="done",
                progress=Decimal("100"),
            )
            session.add(run)
            await session.commit()
            await session.refresh(run)

            db_strategy = await session.get(Strategy, sample_strategy.id)
            await session.delete(db_strategy)
            await session.commit()

        resp = await client.get(f"/api/v1/simulations/{run.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["strategy_name"] == "历史策略名称"

    async def test_list_simulations_falls_back_to_deleted_strategy_placeholder(
        self,
        client: AsyncClient,
        session_factory,
    ):
        """Should expose a clear placeholder when old data has no snapshot and no live strategy."""
        async with session_factory() as session:
            run = SimulationRun(
                strategy_id=999,
                horizon_days=252,
                num_simulations=10000,
                method="gbm",
                initial_capital=Decimal("100000"),
                confidence_levels=[0.95, 0.99],
                lookback_days=504,
                status="done",
                progress=Decimal("100"),
            )
            session.add(run)
            await session.commit()
            await session.refresh(run)

        resp = await client.get("/api/v1/simulations")
        assert resp.status_code == 200
        data = resp.json()
        target = next(item for item in data if item["id"] == run.id)
        assert target["strategy_name"] == "已删除策略 #999"


def test_simulation_data_quality_report_uses_lookback_nav_snapshot():
    """模拟任务应能为本次参数估计窗口生成可审计的数据质量快照。"""
    from app.tasks.simulation import _build_simulation_data_quality_report

    report = _build_simulation_data_quality_report(
        {
            "BOND001": {
                date(2024, 1, 2): Decimal("1.0000"),
                date(2024, 1, 3): Decimal("1.0800"),
            }
        },
        date(2024, 1, 2),
        date(2024, 1, 3),
        {"BOND001": "bond"},
    )

    payload = report.to_dict()
    assert payload["overall_status"] == "warning"
    assert payload["can_proceed"] is True
    assert payload["funds"][0]["fund_code"] == "BOND001"
    assert payload["funds"][0]["spike_count"] == 1
    assert payload["funds"][0]["spike_threshold"] == "0.05"


def test_compute_extended_metrics_handles_zero_vol_paths_without_warning():
    """Zero-volatility paths should not trigger divide-by-zero runtime warnings."""
    import warnings
    import numpy as np

    paths = np.full((4, 6), 100000.0, dtype=np.float64)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("error", RuntimeWarning)
        extended = compute_extended_metrics(paths)

    assert caught == []
    assert extended.predicted_sharpe == 0.0
    assert extended.predicted_sortino == 0.0
    assert extended.predicted_calmar == 0.0


def test_strategy_simulation_extended_metrics_use_same_paths():
    """Strategy-aware simulation should compute extended metrics from the exact same paths."""
    historical_returns = __import__("pandas").DataFrame(
        {
            "000001": [0.001 + i * 0.00001 for i in range(80)],
            "000002": [0.0015 + i * 0.00001 for i in range(80)],
            "000003": [0.0008 + i * 0.00001 for i in range(80)],
        }
    )
    config = SimulationConfig(
        horizon_days=30,
        num_simulations=200,
        method="bootstrap",
        confidence_levels=[0.95],
        initial_capital=100000,
        random_seed=42,
    )
    engine = StrategySimulationEngine(
        config,
        StrategySimConfig(
            strategy_type="momentum",
            params={"top_n": 2},
            universe_codes=["000001", "000002", "000003"],
        ),
    )

    paths = engine.simulate_paths(historical_returns)
    result = engine._compute_results(paths)
    extended = compute_extended_metrics(paths)

    assert paths.shape == (200, 31)
    assert result.percentile_paths["p50"][0] == 100000.0
    assert isinstance(extended.predicted_calmar, float)
    assert 0.0 <= extended.prob_positive_return <= 1.0


class TestSubmitSimulation:
    """Tests for simulation submission endpoint."""

    async def test_submit_simulation_clears_stale_live_progress_when_reusing_run(
        self,
        client: AsyncClient,
        session_factory,
        sample_strategy: Strategy,
    ):
        """Should delete stale Redis progress when reusing an existing run."""
        async with session_factory() as session:
            run = SimulationRun(
                strategy_id=sample_strategy.id,
                horizon_days=252,
                num_simulations=10000,
                method="gbm",
                initial_capital=Decimal("100000"),
                confidence_levels=[0.95, 0.99],
                lookback_days=504,
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

        with patch("app.tasks.simulation.run_simulation", mock_task), patch(
            "redis.Redis.from_url", return_value=mock_redis
        ):
            resp = await client.post(
                "/api/v1/simulations",
                json={
                    "strategy_id": sample_strategy.id,
                    "horizon_days": 252,
                    "num_simulations": 10000,
                    "method": "gbm",
                    "initial_capital": "100000",
                    "confidence_levels": [0.95, 0.99],
                    "lookback_days": 504,
                },
            )

        assert resp.status_code == 202
        data = resp.json()
        assert data["run_id"] == run.id
        mock_redis.delete.assert_called_once_with(f"simulation:progress:{run.id}")

        async with session_factory() as session:
            refreshed = await session.get(SimulationRun, run.id)
            assert refreshed is not None
            assert refreshed.strategy_snapshot is not None
            assert refreshed.strategy_snapshot["name"] == sample_strategy.name
            assert refreshed.strategy_snapshot["strategy_type"] == sample_strategy.strategy_type

    async def test_submit_simulation_persists_strategy_snapshot_for_new_run(
        self,
        client: AsyncClient,
        session_factory,
        sample_strategy: Strategy,
    ):
        """Should store a strategy snapshot on newly created simulation runs."""
        mock_task = MagicMock()
        mock_task.delay = MagicMock()

        with patch("app.tasks.simulation.run_simulation", mock_task):
            resp = await client.post(
                "/api/v1/simulations",
                json={
                    "strategy_id": sample_strategy.id,
                    "horizon_days": 300,
                    "num_simulations": 5000,
                    "method": "bootstrap",
                    "initial_capital": "120000",
                    "confidence_levels": [0.95, 0.99],
                    "lookback_days": 700,
                },
            )

        assert resp.status_code == 202
        run_id = resp.json()["run_id"]

        async with session_factory() as session:
            run = await session.get(SimulationRun, run_id)
            assert run is not None
            assert run.strategy_snapshot == {
                "id": sample_strategy.id,
                "name": sample_strategy.name,
                "strategy_type": sample_strategy.strategy_type,
                "params": sample_strategy.params,
                "universe": sample_strategy.universe,
                "benchmark": sample_strategy.benchmark,
            }


class TestRerunSimulation:
    """Tests for simulation rerun endpoint."""

    async def test_rerun_simulation_nonexistent(self, client: AsyncClient):
        """Should return 404 when rerun target does not exist."""
        resp = await client.post("/api/v1/simulations/99999/rerun")
        assert resp.status_code == 404

    async def test_rerun_simulation_rejects_running_status(
        self,
        client: AsyncClient,
        session_factory,
        sample_strategy: Strategy,
    ):
        """Should return 409 when run is already pending/running."""
        async with session_factory() as session:
            run = SimulationRun(
                strategy_id=sample_strategy.id,
                horizon_days=252,
                num_simulations=10000,
                method="gbm",
                initial_capital=Decimal("100000"),
                confidence_levels=[0.95, 0.99],
                lookback_days=504,
                status="running",
                progress=Decimal("37.5"),
            )
            session.add(run)
            await session.commit()
            await session.refresh(run)

        resp = await client.post(f"/api/v1/simulations/{run.id}/rerun")
        assert resp.status_code == 409

    async def test_rerun_simulation_success_resets_and_enqueues(
        self,
        client: AsyncClient,
        session_factory,
        sample_simulation_run: SimulationRun,
    ):
        """Should reset result fields, clear live progress, and enqueue rerun."""
        mock_task = MagicMock()
        mock_task.delay = MagicMock()
        mock_redis = MagicMock()

        async with session_factory() as session:
            run = await session.get(SimulationRun, sample_simulation_run.id)
            assert run is not None
            run.status = "failed"
            run.progress = Decimal("100")
            run.metrics = {"expected_return": 0.12}
            run.percentile_paths = {"p50": [100000, 101000]}
            run.error_msg = "旧错误"
            await session.commit()

        with patch("app.tasks.simulation.run_simulation", mock_task), patch(
            "redis.Redis.from_url", return_value=mock_redis
        ):
            resp = await client.post(
                f"/api/v1/simulations/{sample_simulation_run.id}/rerun"
            )

        assert resp.status_code == 202
        data = resp.json()
        assert data == {
            "run_id": sample_simulation_run.id,
            "status": "pending",
            "message": "模拟任务已重新提交，正在异步执行",
        }
        mock_task.delay.assert_called_once_with(sample_simulation_run.id)
        mock_redis.delete.assert_called_once_with(
            f"simulation:progress:{sample_simulation_run.id}"
        )

        async with session_factory() as session:
            run = await session.get(SimulationRun, sample_simulation_run.id)
            assert run is not None
            assert run.status == "pending"
            assert run.progress == Decimal("0")
            assert run.metrics is None
            assert run.percentile_paths is None
            assert run.error_msg is None
            assert run.started_at is None
            assert run.finished_at is None
