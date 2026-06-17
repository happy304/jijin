"""Integration tests for the funds API endpoints.

Tests cover:
- GET /api/v1/funds (pagination + filtering)
- GET /api/v1/funds/{code} (detail with cache)
- GET /api/v1/funds/{code}/nav (NAV time series with cache)

Requirements: 7.1, 7.2, 2.9
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings, get_settings
from app.data.models import Base
from app.data.models.fund_nav import FundNav
from app.data.models.funds import Fund
from app.data.session import get_session
from app.main import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def test_settings() -> Settings:
    """Test settings with SQLite in-memory database."""
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
async def db_engine(test_settings: Settings):
    """Create an in-memory SQLite engine with tables."""
    engine = create_async_engine(
        test_settings.database_url,
        echo=False,
    )

    # Enable WAL mode and foreign keys for SQLite
    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    await engine.dispose()


@pytest.fixture
async def db_session(db_engine) -> AsyncIterator[AsyncSession]:
    """Provide a test database session."""
    session_factory = async_sessionmaker(
        bind=db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_factory() as session:
        yield session


@pytest.fixture
async def seeded_session(db_session: AsyncSession) -> AsyncSession:
    """Seed the database with test data."""
    # Insert test funds
    funds = [
        Fund(
            code="000001",
            name="华夏成长混合",
            fund_type="mixed",
            company_id="huaxia",
            inception_date=date(2001, 12, 18),
            management_fee=Decimal("0.0150"),
            custodian_fee=Decimal("0.0025"),
            status="active",
            is_purchasable=True,
            source="eastmoney",
            updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        ),
        Fund(
            code="000002",
            name="华夏回报混合A",
            fund_type="mixed",
            company_id="huaxia",
            inception_date=date(2003, 9, 5),
            management_fee=Decimal("0.0150"),
            status="active",
            source="eastmoney",
            updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        ),
        Fund(
            code="110011",
            name="易方达中小盘混合",
            fund_type="stock",
            company_id="efund",
            inception_date=date(2008, 6, 19),
            management_fee=Decimal("0.0150"),
            status="active",
            source="eastmoney",
            updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        ),
        Fund(
            code="519697",
            name="交银优势行业混合",
            fund_type="mixed",
            company_id="bocom",
            inception_date=date(2009, 6, 11),
            management_fee=Decimal("0.0120"),
            status="suspended",
            source="akshare",
            updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        ),
    ]
    db_session.add_all(funds)

    # Insert NAV records for fund 000001
    today = date.today()
    nav_records = []
    for i in range(10):
        d = today - timedelta(days=10 - i)
        nav_records.append(
            FundNav(
                fund_code="000001",
                trade_date=d,
                unit_nav=Decimal("1.5000") + Decimal("0.01") * i,
                accum_nav=Decimal("3.2000") + Decimal("0.01") * i,
                adj_nav=Decimal("2.8000") + Decimal("0.01") * i,
                daily_return=Decimal("0.0050"),
                status="normal",
                source="eastmoney",
                created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            )
        )
    db_session.add_all(nav_records)
    await db_session.commit()
    return db_session


@pytest.fixture
def app_with_db(test_settings: Settings, db_engine) -> FastAPI:
    """Create a FastAPI app with the test database session override."""
    application = create_app(test_settings)
    application.dependency_overrides[get_settings] = lambda: test_settings

    # Override get_session to use our test engine
    session_factory = async_sessionmaker(
        bind=db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    application.dependency_overrides[get_session] = override_get_session
    return application


@pytest.fixture
async def client(app_with_db: FastAPI, seeded_session) -> AsyncIterator[AsyncClient]:
    """Async HTTP client for testing."""
    transport = ASGITransport(app=app_with_db)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Tests: GET /api/v1/funds
# ---------------------------------------------------------------------------


class TestListFunds:
    """Tests for the fund list/search endpoint."""

    async def test_list_funds_default_pagination(self, client: AsyncClient):
        """Should return all funds with default pagination."""
        resp = await client.get("/api/v1/funds")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 4
        assert data["page"] == 1
        assert data["page_size"] == 20
        assert data["pages"] == 1
        assert len(data["items"]) == 4

    async def test_list_funds_pagination(self, client: AsyncClient):
        """Should respect page and page_size parameters."""
        resp = await client.get("/api/v1/funds", params={"page": 1, "page_size": 2})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 4
        assert data["page"] == 1
        assert data["page_size"] == 2
        assert data["pages"] == 2
        assert len(data["items"]) == 2

    async def test_list_funds_page_2(self, client: AsyncClient):
        """Should return second page correctly."""
        resp = await client.get("/api/v1/funds", params={"page": 2, "page_size": 2})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 2
        assert data["page"] == 2

    async def test_list_funds_filter_by_type(self, client: AsyncClient):
        """Should filter by fund_type."""
        resp = await client.get("/api/v1/funds", params={"fund_type": "stock"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["code"] == "110011"

    async def test_list_funds_filter_by_keyword(self, client: AsyncClient):
        """Should filter by keyword matching code or name."""
        resp = await client.get("/api/v1/funds", params={"keyword": "华夏"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        # Both 000001 and 000002 have "华夏" in name
        codes = {item["code"] for item in data["items"]}
        assert codes == {"000001", "000002"}

    async def test_list_funds_filter_by_code_keyword(self, client: AsyncClient):
        """Should filter by code as keyword."""
        resp = await client.get("/api/v1/funds", params={"keyword": "110011"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["code"] == "110011"

    async def test_list_funds_filter_by_company(self, client: AsyncClient):
        """Should filter by company_id."""
        resp = await client.get("/api/v1/funds", params={"company_id": "huaxia"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2

    async def test_list_funds_filter_by_status(self, client: AsyncClient):
        """Should filter by status."""
        resp = await client.get("/api/v1/funds", params={"status": "suspended"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["code"] == "519697"

    async def test_list_funds_combined_filters(self, client: AsyncClient):
        """Should combine multiple filters."""
        resp = await client.get(
            "/api/v1/funds",
            params={"fund_type": "mixed", "company_id": "huaxia"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2

    async def test_list_funds_empty_result(self, client: AsyncClient):
        """Should return empty list when no funds match."""
        resp = await client.get("/api/v1/funds", params={"fund_type": "money"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []
        assert data["pages"] == 0

    async def test_list_funds_invalid_page_size(self, client: AsyncClient):
        """Should reject page_size > 100."""
        resp = await client.get("/api/v1/funds", params={"page_size": 200})
        assert resp.status_code == 422


class TestFundOptions:
    """Tests for the fund options endpoint."""

    async def test_list_fund_options_success(self, client: AsyncClient):
        """Should return all local non-deleted funds for selectors."""
        resp = await client.get("/api/v1/funds/options")
        assert resp.status_code == 200
        data = resp.json()

        assert "items" in data
        assert len(data["items"]) == 4
        assert [item["code"] for item in data["items"]] == [
            "000001",
            "000002",
            "110011",
            "519697",
        ]

    async def test_list_fund_options_response_fields(self, client: AsyncClient):
        """Should only expose lightweight selector fields."""
        resp = await client.get("/api/v1/funds/options")
        assert resp.status_code == 200
        item = resp.json()["items"][0]

        assert set(item.keys()) == {
            "code",
            "name",
            "fund_type",
            "status",
            "inception_date",
        }
        assert item["code"] == "000001"
        assert item["name"] == "华夏成长混合"
        assert item["inception_date"] == "2001-12-18"


# ---------------------------------------------------------------------------
# Tests: GET /api/v1/funds/{code}
# ---------------------------------------------------------------------------


class TestGetFund:
    """Tests for the fund detail endpoint."""

    @patch("app.api.v1.funds.cache.get_fund_meta", new_callable=AsyncMock)
    @patch("app.api.v1.funds.cache.set_fund_meta", new_callable=AsyncMock)
    async def test_get_fund_success(
        self, mock_set_cache, mock_get_cache, client: AsyncClient
    ):
        """Should return fund detail for a valid code."""
        mock_get_cache.return_value = None  # Cache miss
        resp = await client.get("/api/v1/funds/000001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == "000001"
        assert data["name"] == "华夏成长混合"
        assert data["fund_type"] == "mixed"
        assert data["company_id"] == "huaxia"
        assert data["is_purchasable"] is True
        # Verify cache was written
        mock_set_cache.assert_called_once()

    @patch("app.api.v1.funds.cache.get_fund_meta", new_callable=AsyncMock)
    async def test_get_fund_from_cache(self, mock_get_cache, client: AsyncClient):
        """Should return fund detail from cache when available."""
        mock_get_cache.return_value = {
            "code": "000001",
            "name": "华夏成长混合",
            "fund_type": "mixed",
            "sub_type": None,
            "company_id": "huaxia",
            "inception_date": "2001-12-18",
            "benchmark": None,
            "management_fee": "0.0150",
            "custodian_fee": "0.0025",
            "currency": "CNY",
            "status": "active",
            "is_purchasable": True,
            "purchase_limit": None,
            "source": "eastmoney",
        }
        resp = await client.get("/api/v1/funds/000001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == "000001"
        assert data["name"] == "华夏成长混合"

    @patch("app.api.v1.funds.cache.get_fund_meta", new_callable=AsyncMock)
    async def test_get_fund_not_found(self, mock_get_cache, client: AsyncClient):
        """Should return 404 for non-existent fund."""
        mock_get_cache.return_value = None
        resp = await client.get("/api/v1/funds/999999")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: GET /api/v1/funds/{code}/nav
# ---------------------------------------------------------------------------


class TestGetFundNav:
    """Tests for the fund NAV time series endpoint."""

    @patch("app.api.v1.funds.cache.get_nav_records", new_callable=AsyncMock)
    @patch("app.api.v1.funds.cache.set_nav_records", new_callable=AsyncMock)
    async def test_get_nav_default_range(
        self, mock_set_cache, mock_get_cache, client: AsyncClient
    ):
        """Should return NAV records with default 30-day range."""
        mock_get_cache.return_value = None  # Cache miss
        resp = await client.get("/api/v1/funds/000001/nav")
        assert resp.status_code == 200
        data = resp.json()
        assert data["fund_code"] == "000001"
        assert data["count"] == 10  # We seeded 10 records within last 30 days
        assert len(data["records"]) == 10
        # Verify cache was written
        mock_set_cache.assert_called_once()

    @patch("app.api.v1.funds.cache.get_nav_records", new_callable=AsyncMock)
    @patch("app.api.v1.funds.cache.set_nav_records", new_callable=AsyncMock)
    async def test_get_nav_custom_range(
        self, mock_set_cache, mock_get_cache, client: AsyncClient
    ):
        """Should respect custom date range parameters."""
        mock_get_cache.return_value = None
        today = date.today()
        start = today - timedelta(days=5)
        resp = await client.get(
            "/api/v1/funds/000001/nav",
            params={
                "start_date": start.isoformat(),
                "end_date": today.isoformat(),
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["fund_code"] == "000001"
        assert data["start_date"] == start.isoformat()
        assert data["end_date"] == today.isoformat()
        # Should have records within the 5-day window
        assert data["count"] <= 10

    @patch("app.api.v1.funds.cache.get_nav_records", new_callable=AsyncMock)
    async def test_get_nav_from_cache(self, mock_get_cache, client: AsyncClient):
        """Should return NAV records from cache when available."""
        today = date.today()
        start = today - timedelta(days=30)
        mock_get_cache.return_value = [
            {
                "trade_date": (today - timedelta(days=1)).isoformat(),
                "unit_nav": "1.5500",
                "accum_nav": "3.2500",
                "adj_nav": "2.8500",
                "daily_return": "0.0050",
            }
        ]
        resp = await client.get("/api/v1/funds/000001/nav")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert len(data["records"]) == 1

    @patch("app.api.v1.funds.cache.get_nav_records", new_callable=AsyncMock)
    async def test_get_nav_fund_not_found(self, mock_get_cache, client: AsyncClient):
        """Should return 404 for non-existent fund."""
        mock_get_cache.return_value = None
        resp = await client.get("/api/v1/funds/999999/nav")
        assert resp.status_code == 404

    @patch("app.api.v1.funds.cache.get_nav_records", new_callable=AsyncMock)
    @patch("app.api.v1.funds.cache.set_nav_records", new_callable=AsyncMock)
    async def test_get_nav_empty_range(
        self, mock_set_cache, mock_get_cache, client: AsyncClient
    ):
        """Should return empty records for a date range with no data."""
        mock_get_cache.return_value = None
        resp = await client.get(
            "/api/v1/funds/000001/nav",
            params={
                "start_date": "2020-01-01",
                "end_date": "2020-01-31",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["records"] == []

    @patch("app.api.v1.funds.cache.get_nav_records", new_callable=AsyncMock)
    @patch("app.api.v1.funds.cache.set_nav_records", new_callable=AsyncMock)
    async def test_nav_records_ordered_by_date(
        self, mock_set_cache, mock_get_cache, client: AsyncClient
    ):
        """NAV records should be ordered by trade_date ascending."""
        mock_get_cache.return_value = None
        resp = await client.get("/api/v1/funds/000001/nav")
        assert resp.status_code == 200
        data = resp.json()
        dates = [r["trade_date"] for r in data["records"]]
        assert dates == sorted(dates)
