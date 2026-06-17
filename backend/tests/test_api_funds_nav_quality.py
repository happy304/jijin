"""Tests for fund NAV quality API."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings, get_settings
from app.data.models import Base
from app.data.models.fund_nav import FundNav
from app.data.models.funds import Fund
from app.data.session import get_session
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
async def app(test_settings: Settings, session_factory) -> AsyncIterator[FastAPI]:
    """Create app with DB override."""
    application = create_app(test_settings)
    application.dependency_overrides[get_settings] = lambda: test_settings

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    application.dependency_overrides[get_session] = override_get_session
    try:
        yield application
    finally:
        application.dependency_overrides.clear()
        get_settings.cache_clear()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Async HTTP client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_nav_quality_reports_bond_spike_and_adj_nav_fallback(
    client: AsyncClient,
    session_factory,
) -> None:
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        session.add(
            Fund(
                code="BOND01",
                name="测试债基",
                fund_type="bond",
                status="active",
                updated_at=now,
            )
        )
        session.add_all([
            FundNav(
                fund_code="BOND01",
                trade_date=date(2024, 1, 2),
                unit_nav=Decimal("1.000000"),
                adj_nav=Decimal("1.000000"),
                created_at=now,
            ),
            FundNav(
                fund_code="BOND01",
                trade_date=date(2024, 1, 3),
                unit_nav=Decimal("1.080000"),
                adj_nav=None,
                created_at=now,
            ),
            FundNav(
                fund_code="BOND01",
                trade_date=date(2024, 1, 20),
                unit_nav=Decimal("1.081000"),
                adj_nav=Decimal("1.081000"),
                created_at=now,
            ),
        ])
        await session.commit()

    resp = await client.get(
        "/api/v1/funds/BOND01/nav-quality",
        params={"start_date": "2024-01-01", "end_date": "2024-01-31"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["fund_code"] == "BOND01"
    assert body["fund_type"] == "bond"
    assert body["spike_threshold"] == "0.05"
    assert body["spike_count"] == 1
    assert body["unit_nav_fallback_points"] == 1
    assert body["adj_nav_points"] == 2
    assert body["adj_nav_coverage_ratio"] == pytest.approx(2 / 3)
    assert body["max_gap_days"] == 16
    assert body["status"] == "poor"
    issue_types = {issue["issue_type"] for issue in body["issues"]}
    assert {"spike", "adj_nav_missing", "missing_gap"}.issubset(issue_types)


@pytest.mark.asyncio
async def test_nav_quality_reports_missing_all_as_poor(
    client: AsyncClient,
    session_factory,
) -> None:
    async with session_factory() as session:
        session.add(
            Fund(
                code="EMPTY1",
                name="无净值基金",
                fund_type="stock",
                status="active",
                updated_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()

    resp = await client.get(
        "/api/v1/funds/EMPTY1/nav-quality",
        params={"start_date": "2024-01-01", "end_date": "2024-01-31"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_nav_points"] == 0
    assert body["coverage_ratio"] == 0
    assert body["status"] == "poor"
    assert body["issues"][0]["issue_type"] == "missing_all"


@pytest.mark.asyncio
async def test_nav_quality_overview_lists_and_filters_status(
    client: AsyncClient,
    session_factory,
) -> None:
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        session.add_all([
            Fund(
                code="GOOD01",
                name="质量良好基金",
                fund_type="stock",
                status="active",
                updated_at=now,
            ),
            Fund(
                code="BAD001",
                name="缺失基金",
                fund_type="bond",
                status="active",
                updated_at=now,
            ),
        ])
        session.add_all([
            FundNav(
                fund_code="GOOD01",
                trade_date=date(2024, 1, 1),
                unit_nav=Decimal("1.000000"),
                adj_nav=Decimal("1.000000"),
                created_at=now,
            ),
            FundNav(
                fund_code="GOOD01",
                trade_date=date(2024, 1, 2),
                unit_nav=Decimal("1.010000"),
                adj_nav=Decimal("1.010000"),
                created_at=now,
            ),
            FundNav(
                fund_code="GOOD01",
                trade_date=date(2024, 1, 3),
                unit_nav=Decimal("1.020000"),
                adj_nav=Decimal("1.020000"),
                created_at=now,
            ),
        ])
        await session.commit()

    resp = await client.get(
        "/api/v1/funds/nav-quality-overview",
        params={"start_date": "2024-01-01", "end_date": "2024-01-03"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    assert body["status_counts"]["good"] == 1
    assert body["status_counts"]["poor"] == 1
    rows = {item["fund_code"]: item for item in body["items"]}
    assert rows["GOOD01"]["fund_name"] == "质量良好基金"
    assert rows["GOOD01"]["status"] == "good"
    assert rows["BAD001"]["status"] == "poor"

    filtered = await client.get(
        "/api/v1/funds/nav-quality-overview",
        params={"start_date": "2024-01-01", "end_date": "2024-01-03", "status": "poor"},
    )
    assert filtered.status_code == 200, filtered.text
    filtered_body = filtered.json()
    assert filtered_body["total"] == 1
    assert filtered_body["items"][0]["fund_code"] == "BAD001"
    assert filtered_body["status_counts"]["good"] == 1
    assert filtered_body["status_counts"]["poor"] == 1


@pytest.mark.asyncio
async def test_nav_quality_returns_404_for_unknown_fund(client: AsyncClient) -> None:
    resp = await client.get(
        "/api/v1/funds/NOFUND/nav-quality",
        params={"start_date": "2024-01-01", "end_date": "2024-01-31"},
    )

    assert resp.status_code == 404
