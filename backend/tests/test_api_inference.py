"""Tests for the new ``GET /api/v1/backtests/{id}/inference`` endpoint.

Reuses the in-memory SQLite app fixture from ``test_api_backtests``
via importing its session_factory pattern.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings, get_settings
from app.data.models import Base
from app.data.models.backtests import BacktestEquity, BacktestRun
from app.data.models.strategies import Strategy
from app.data.session import get_session
from app.main import create_app


@pytest.fixture
def test_settings() -> Settings:
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
    eng = create_async_engine(test_settings.database_url, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest.fixture
async def session_factory(engine):
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


@pytest.fixture
async def app(test_settings: Settings, session_factory) -> FastAPI:
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
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Test data builders
# ---------------------------------------------------------------------------


async def _build_done_run_with_equity(
    session_factory,
    n_days: int = 200,
    drift: float = 0.001,
    vol: float = 0.012,
) -> int:
    """Insert a done backtest run and ``n_days`` synthetic equity points.

    Returns the run_id.
    """
    import numpy as np

    async with session_factory() as session:
        strategy = Strategy(
            name="inference test strat",
            strategy_type="momentum",
            params={},
            universe={"fund_codes": ["000001"]},
            benchmark="000300",
            created_by="test",
        )
        session.add(strategy)
        await session.commit()
        await session.refresh(strategy)

        run = BacktestRun(
            strategy_id=strategy.id,
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
            initial_capital=Decimal("100000"),
            status="done",
            progress=Decimal("100"),
            metrics={"sharpe": 1.5, "total_return": 0.15},
            started_at=datetime(2023, 1, 1, 10, 0, 0),
            finished_at=datetime(2023, 1, 1, 10, 5, 0),
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)

        rng = np.random.default_rng(42)
        returns = rng.normal(drift, vol, size=n_days)
        equity = 100000.0
        rows = []
        for i, r in enumerate(returns):
            equity *= 1.0 + r
            rows.append(
                BacktestEquity(
                    run_id=run.id,
                    trade_date=date(2023, 1, 1).fromordinal(date(2023, 1, 1).toordinal() + i),
                    equity=Decimal(str(round(equity, 2))),
                    cash=Decimal(str(round(equity / 2, 2))),
                    position_value=Decimal(str(round(equity / 2, 2))),
                )
            )
        session.add_all(rows)
        await session.commit()
        return run.id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inference_returns_finite_metrics(client: AsyncClient, session_factory):
    """For a healthy backtest with 200 days of returns, inference returns finite numbers."""
    run_id = await _build_done_run_with_equity(session_factory, n_days=200, drift=0.0008)

    resp = await client.get(f"/api/v1/backtests/{run_id}/inference")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["run_id"] == run_id
    assert data["n_observations"] >= 100
    # Sharpe should be finite
    assert data["sharpe_observed"] is not None
    assert data["sharpe_annualized"] is not None
    assert data["psr"] is not None
    assert 0 <= data["psr"] <= 1
    assert data["dsr"] is not None
    assert 0 <= data["dsr"] <= 1


@pytest.mark.asyncio
async def test_inference_dsr_lower_with_many_trials(client: AsyncClient, session_factory):
    """DSR should decrease (or equal) when n_trials grows."""
    run_id = await _build_done_run_with_equity(session_factory, n_days=300, drift=0.001)

    resp1 = await client.get(f"/api/v1/backtests/{run_id}/inference?n_trials=1")
    resp100 = await client.get(f"/api/v1/backtests/{run_id}/inference?n_trials=100")
    assert resp1.status_code == 200
    assert resp100.status_code == 200
    d1 = resp1.json()
    d100 = resp100.json()
    # PSR is the same regardless of n_trials
    assert d1["psr"] == pytest.approx(d100["psr"], rel=1e-9)
    # DSR should be smaller or equal with more trials
    assert d100["dsr"] <= d1["dsr"] + 1e-9


@pytest.mark.asyncio
async def test_inference_404_when_run_missing(client: AsyncClient):
    resp = await client.get("/api/v1/backtests/99999/inference")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_inference_409_when_not_done(client: AsyncClient, session_factory):
    """Pending runs cannot have inference computed yet."""
    async with session_factory() as session:
        strat = Strategy(
            name="pending",
            strategy_type="momentum",
            params={},
            universe={"fund_codes": ["000001"]},
            created_by="test",
        )
        session.add(strat)
        await session.commit()
        await session.refresh(strat)

        run = BacktestRun(
            strategy_id=strat.id,
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
            initial_capital=Decimal("100000"),
            status="running",
            progress=Decimal("50"),
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        run_id = run.id

    resp = await client.get(f"/api/v1/backtests/{run_id}/inference")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_inference_handles_short_equity(client: AsyncClient, session_factory):
    """A run with too few equity points returns 200 with note."""
    async with session_factory() as session:
        strat = Strategy(
            name="short",
            strategy_type="momentum",
            params={},
            universe={"fund_codes": ["000001"]},
            created_by="test",
        )
        session.add(strat)
        await session.commit()
        await session.refresh(strat)

        run = BacktestRun(
            strategy_id=strat.id,
            start_date=date(2023, 1, 1),
            end_date=date(2023, 1, 10),
            initial_capital=Decimal("100000"),
            status="done",
            progress=Decimal("100"),
            metrics={},
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)

        # Insert just 5 equity points (< 30 threshold)
        for i in range(5):
            session.add(
                BacktestEquity(
                    run_id=run.id,
                    trade_date=date(2023, 1, i + 1),
                    equity=Decimal("100000"),
                    cash=Decimal("100000"),
                    position_value=Decimal("0"),
                )
            )
        await session.commit()
        run_id = run.id

    resp = await client.get(f"/api/v1/backtests/{run_id}/inference")
    assert resp.status_code == 200
    data = resp.json()
    # Should return a note about insufficient samples
    assert data["note"] is not None
    assert "sample" in data["note"].lower() or "样本" in data["note"]
