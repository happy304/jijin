"""Integration tests for the new ``POST /api/v1/factors/evaluate`` endpoint.

Loads fund NAV data into an in-memory SQLite, then exercises the IC +
quintile evaluation endpoint end-to-end.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import AsyncIterator

import numpy as np
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings, get_settings
from app.data.models import Base
from app.data.models.fund_nav import FundNav
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
# Helpers
# ---------------------------------------------------------------------------


async def _seed_nav_panel(
    session_factory,
    n_funds: int = 8,
    n_days: int = 300,
    seed: int = 42,
) -> list[str]:
    """Insert ``n_funds`` × ``n_days`` synthetic NAV rows.

    Returns the list of fund codes inserted.
    """
    from datetime import datetime, timezone

    rng = np.random.default_rng(seed)
    fund_codes = [f"00000{i + 1}" for i in range(n_funds)]

    # Explicit created_at because the SQLite test backend stores the
    # "NOW()" server_default literally as a string and round-trips break.
    seed_ts = datetime(2024, 1, 1, tzinfo=timezone.utc)

    start = date(2023, 1, 1)
    async with session_factory() as session:
        rows: list[FundNav] = []
        # Each fund gets its own random walk
        for code in fund_codes:
            nav = 1.0
            for i in range(n_days):
                d = start + timedelta(days=i)
                ret = rng.normal(0.0005, 0.012)
                nav *= 1.0 + ret
                rows.append(
                    FundNav(
                        fund_code=code,
                        trade_date=d,
                        unit_nav=Decimal(str(round(nav, 6))),
                        created_at=seed_ts,
                    )
                )
        # Bulk add
        session.add_all(rows)
        await session.commit()
    return fund_codes


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_returns_ic_and_quintile(client: AsyncClient, session_factory):
    """Healthy NAV panel + a registered factor → full IC/quintile result."""
    fund_codes = await _seed_nav_panel(session_factory, n_funds=8, n_days=300)

    resp = await client.post(
        "/api/v1/factors/evaluate",
        json={
            "fund_codes": fund_codes,
            "factor_name": "volatility",  # registered factor
            "rebalance_freq": "M",
            "decay_horizons": [1, 2, 3],
            "n_groups": 5,
            "method": "spearman",
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["factor_name"] == "volatility"
    assert data["n_assets"] == 8
    assert data["n_dates"] >= 8

    # IC blocks should be filled (sample size is sufficient)
    assert data["ic_pearson"] is not None
    assert data["ic_spearman"] is not None
    assert data["ic_spearman"]["method"] == "spearman"
    assert data["ic_spearman"]["n_periods"] >= 1

    # IC decay returned for each requested horizon
    assert "1" in data["ic_decay"] or len(data["ic_decay"]) >= 1

    # Quintile result exists with 5 groups
    assert data["quintile"] is not None
    assert data["quintile"]["n_groups"] == 5
    assert data["quintile"]["monotonicity"] in (-1, 0, 1)


@pytest.mark.asyncio
async def test_evaluate_404_for_unknown_factor(client: AsyncClient, session_factory):
    fund_codes = await _seed_nav_panel(session_factory, n_funds=6, n_days=200)
    resp = await client.post(
        "/api/v1/factors/evaluate",
        json={
            "fund_codes": fund_codes,
            "factor_name": "nonexistent_factor",
            "rebalance_freq": "M",
        },
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_evaluate_404_for_missing_nav_data(client: AsyncClient):
    """No NAV data in DB → 404."""
    resp = await client.post(
        "/api/v1/factors/evaluate",
        json={
            "fund_codes": ["NONE01", "NONE02", "NONE03", "NONE04", "NONE05"],
            "factor_name": "volatility",
        },
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_evaluate_short_sample_returns_note(
    client: AsyncClient, session_factory
):
    """Very short sample period yields a graceful note instead of failure."""
    fund_codes = await _seed_nav_panel(session_factory, n_funds=6, n_days=15)

    resp = await client.post(
        "/api/v1/factors/evaluate",
        json={
            "fund_codes": fund_codes,
            "factor_name": "volatility",
            "rebalance_freq": "M",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    # 15 days × monthly resample → < 10 periods, expect a note
    assert data["note"] is not None


@pytest.mark.asyncio
async def test_evaluate_invalid_freq_422(client: AsyncClient, session_factory):
    fund_codes = await _seed_nav_panel(session_factory, n_funds=6, n_days=100)
    resp = await client.post(
        "/api/v1/factors/evaluate",
        json={
            "fund_codes": fund_codes,
            "factor_name": "volatility",
            "rebalance_freq": "X",
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_evaluate_invalid_method_422(client: AsyncClient, session_factory):
    fund_codes = await _seed_nav_panel(session_factory, n_funds=6, n_days=100)
    resp = await client.post(
        "/api/v1/factors/evaluate",
        json={
            "fund_codes": fund_codes,
            "factor_name": "volatility",
            "rebalance_freq": "M",
            "method": "kendall",
        },
    )
    assert resp.status_code == 422
