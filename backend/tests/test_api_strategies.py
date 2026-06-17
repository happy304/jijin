"""Integration tests for the strategies CRUD API endpoints.

Tests cover:
- POST /api/v1/strategies (create)
- GET /api/v1/strategies (list with pagination)
- GET /api/v1/strategies/{id} (detail)
- PUT /api/v1/strategies/{id} (update)
- DELETE /api/v1/strategies/{id} (delete)
- Pydantic JSON Schema validation for strategy params

Requirements: 7.5
"""

from __future__ import annotations

from typing import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings, get_settings
from app.data.models import Base
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
async def db_session(test_settings: Settings) -> AsyncIterator[AsyncSession]:
    """Create an in-memory database with the strategies table."""
    engine = create_async_engine(
        test_settings.database_url,
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    async with session_factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def app(test_settings: Settings, db_session: AsyncSession) -> FastAPI:
    """Create a FastAPI app with test settings and DB session override."""
    application = create_app(test_settings)
    application.dependency_overrides[get_settings] = lambda: test_settings

    # Override the DB session dependency to use our test session
    engine = create_async_engine(
        test_settings.database_url,
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    application.dependency_overrides[get_session] = override_get_session
    yield application
    application.dependency_overrides.clear()
    await engine.dispose()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Async HTTP client for testing."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def _create_strategy(client: AsyncClient, **overrides) -> dict:
    """Helper to create a strategy and return the response JSON."""
    payload = {
        "name": "测试动量策略",
        "strategy_type": "momentum",
        "params": {
            "lookback_months": 6,
            "top_n": 3,
            "rebalance_freq": "monthly",
            "score_factor": "sharpe",
        },
        "universe": ["000001", "000002", "110011"],
        "benchmark": "000300",
        "created_by": "test_user",
    }
    payload.update(overrides)
    resp = await client.post("/api/v1/strategies", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Tests: POST /api/v1/strategies
# ---------------------------------------------------------------------------


class TestCreateStrategy:
    """Tests for strategy creation endpoint."""

    async def test_create_momentum_strategy(self, client: AsyncClient):
        """Should create a momentum strategy with valid params."""
        data = await _create_strategy(client)
        assert data["name"] == "测试动量策略"
        assert data["strategy_type"] == "momentum"
        assert data["params"]["lookback_months"] == 6
        assert data["params"]["top_n"] == 3
        assert data["universe"] == {"fund_codes": ["000001", "000002", "110011"]}
        assert data["benchmark"] == "000300"
        assert data["created_by"] == "test_user"
        assert data["id"] is not None
        assert data["created_at"] is not None

    async def test_create_dca_strategy(self, client: AsyncClient):
        """Should create a DCA strategy."""
        data = await _create_strategy(
            client,
            name="定投策略",
            strategy_type="dca",
            params={"amount": 1000, "frequency": "monthly"},
        )
        assert data["name"] == "定投策略"
        assert data["strategy_type"] == "dca"
        assert data["params"]["amount"] == 1000

    async def test_create_risk_parity_strategy(self, client: AsyncClient):
        """Should create a risk parity strategy."""
        data = await _create_strategy(
            client,
            name="风险平价",
            strategy_type="risk_parity",
            params={"rebalance_freq": "monthly", "cov_method": "ewm", "lookback_days": 60},
        )
        assert data["strategy_type"] == "risk_parity"
        assert data["params"]["cov_method"] == "ewm"

    async def test_create_strategy_with_dict_universe(self, client: AsyncClient):
        """Should accept universe as a dict."""
        data = await _create_strategy(
            client,
            universe={"fund_codes": ["000001"], "filters": {"fund_type": "stock"}},
        )
        assert data["universe"]["fund_codes"] == ["000001"]
        assert data["universe"]["filters"]["fund_type"] == "stock"

    async def test_create_strategy_without_type(self, client: AsyncClient):
        """Should allow creating a strategy without strategy_type."""
        data = await _create_strategy(
            client,
            name="自定义策略",
            strategy_type=None,
            params={"custom_key": "custom_value"},
        )
        assert data["strategy_type"] is None
        assert data["params"]["custom_key"] == "custom_value"

    async def test_create_strategy_missing_required_params(self, client: AsyncClient):
        """Should return 422 when required params are missing for the type."""
        resp = await client.post(
            "/api/v1/strategies",
            json={
                "name": "缺少参数",
                "strategy_type": "momentum",
                "params": {"lookback_months": 6},  # missing top_n and rebalance_freq
                "universe": ["000001"],
            },
        )
        assert resp.status_code == 422

    async def test_create_strategy_invalid_param_type(self, client: AsyncClient):
        """Should return 422 when param type is wrong."""
        resp = await client.post(
            "/api/v1/strategies",
            json={
                "name": "类型错误",
                "strategy_type": "momentum",
                "params": {
                    "lookback_months": "not_a_number",  # should be int
                    "top_n": 3,
                    "rebalance_freq": "monthly",
                },
                "universe": ["000001"],
            },
        )
        assert resp.status_code == 422

    async def test_create_strategy_invalid_enum_value(self, client: AsyncClient):
        """Should return 422 when enum value is invalid."""
        resp = await client.post(
            "/api/v1/strategies",
            json={
                "name": "枚举错误",
                "strategy_type": "momentum",
                "params": {
                    "lookback_months": 6,
                    "top_n": 3,
                    "rebalance_freq": "daily",  # not in enum
                },
                "universe": ["000001"],
            },
        )
        assert resp.status_code == 422

    async def test_create_strategy_invalid_strategy_type(self, client: AsyncClient):
        """Should return 422 for unknown strategy_type."""
        resp = await client.post(
            "/api/v1/strategies",
            json={
                "name": "未知类型",
                "strategy_type": "unknown_type",
                "params": {"key": "value"},
                "universe": ["000001"],
            },
        )
        assert resp.status_code == 422

    async def test_create_strategy_empty_name(self, client: AsyncClient):
        """Should return 422 for empty name."""
        resp = await client.post(
            "/api/v1/strategies",
            json={
                "name": "",
                "strategy_type": "dca",
                "params": {"amount": 1000, "frequency": "monthly"},
                "universe": ["000001"],
            },
        )
        assert resp.status_code == 422

    async def test_create_strategy_dca_invalid_amount(self, client: AsyncClient):
        """Should return 422 when DCA amount is not positive."""
        resp = await client.post(
            "/api/v1/strategies",
            json={
                "name": "金额错误",
                "strategy_type": "dca",
                "params": {"amount": 0, "frequency": "monthly"},
                "universe": ["000001"],
            },
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Tests: GET /api/v1/strategies
# ---------------------------------------------------------------------------


class TestListStrategies:
    """Tests for strategy list endpoint."""

    async def test_list_empty(self, client: AsyncClient):
        """Should return empty list when no strategies exist."""
        resp = await client.get("/api/v1/strategies")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0
        assert data["page"] == 1
        assert data["pages"] == 0

    async def test_list_with_strategies(self, client: AsyncClient):
        """Should return created strategies."""
        await _create_strategy(client, name="策略1")
        await _create_strategy(client, name="策略2")

        resp = await client.get("/api/v1/strategies")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["items"]) == 2

    async def test_list_pagination(self, client: AsyncClient):
        """Should paginate results correctly."""
        for i in range(5):
            await _create_strategy(client, name=f"策略{i}")

        resp = await client.get("/api/v1/strategies", params={"page": 1, "page_size": 2})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 5
        assert len(data["items"]) == 2
        assert data["page"] == 1
        assert data["page_size"] == 2
        assert data["pages"] == 3

    async def test_list_filter_by_type(self, client: AsyncClient):
        """Should filter by strategy_type."""
        await _create_strategy(client, name="动量1", strategy_type="momentum")
        await _create_strategy(
            client,
            name="定投1",
            strategy_type="dca",
            params={"amount": 1000, "frequency": "monthly"},
        )

        resp = await client.get("/api/v1/strategies", params={"strategy_type": "momentum"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["name"] == "动量1"

    async def test_list_filter_by_created_by(self, client: AsyncClient):
        """Should filter by created_by."""
        await _create_strategy(client, name="用户A策略", created_by="user_a")
        await _create_strategy(client, name="用户B策略", created_by="user_b")

        resp = await client.get("/api/v1/strategies", params={"created_by": "user_a"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["created_by"] == "user_a"


# ---------------------------------------------------------------------------
# Tests: GET /api/v1/strategies/{id}
# ---------------------------------------------------------------------------


class TestGetStrategy:
    """Tests for strategy detail endpoint."""

    async def test_get_existing_strategy(self, client: AsyncClient):
        """Should return strategy detail by ID."""
        created = await _create_strategy(client)
        strategy_id = created["id"]

        resp = await client.get(f"/api/v1/strategies/{strategy_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == strategy_id
        assert data["name"] == created["name"]
        assert data["params"] == created["params"]

    async def test_get_nonexistent_strategy(self, client: AsyncClient):
        """Should return 404 for non-existent strategy."""
        resp = await client.get("/api/v1/strategies/99999")
        assert resp.status_code == 404
        data = resp.json()
        assert "不存在" in data.get("detail", "") or "不存在" in data.get("error", {}).get("message", "")


# ---------------------------------------------------------------------------
# Tests: PUT /api/v1/strategies/{id}
# ---------------------------------------------------------------------------


class TestUpdateStrategy:
    """Tests for strategy update endpoint."""

    async def test_update_name(self, client: AsyncClient):
        """Should update strategy name."""
        created = await _create_strategy(client)
        strategy_id = created["id"]

        resp = await client.put(
            f"/api/v1/strategies/{strategy_id}",
            json={"name": "新名称"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "新名称"
        # Other fields unchanged
        assert data["strategy_type"] == created["strategy_type"]
        assert data["params"] == created["params"]

    async def test_update_params(self, client: AsyncClient):
        """Should update strategy params with validation."""
        created = await _create_strategy(client)
        strategy_id = created["id"]

        new_params = {
            "lookback_months": 12,
            "top_n": 5,
            "rebalance_freq": "quarterly",
        }
        resp = await client.put(
            f"/api/v1/strategies/{strategy_id}",
            json={"params": new_params},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["params"]["lookback_months"] == 12
        assert data["params"]["top_n"] == 5

    async def test_update_params_invalid(self, client: AsyncClient):
        """Should return 422 when updated params are invalid for existing type."""
        created = await _create_strategy(client)
        strategy_id = created["id"]

        resp = await client.put(
            f"/api/v1/strategies/{strategy_id}",
            json={"params": {"lookback_months": 6}},  # missing required fields
        )
        assert resp.status_code == 422

    async def test_update_nonexistent_strategy(self, client: AsyncClient):
        """Should return 404 for non-existent strategy."""
        resp = await client.put(
            "/api/v1/strategies/99999",
            json={"name": "不存在"},
        )
        assert resp.status_code == 404

    async def test_update_universe(self, client: AsyncClient):
        """Should update universe field."""
        created = await _create_strategy(client)
        strategy_id = created["id"]

        resp = await client.put(
            f"/api/v1/strategies/{strategy_id}",
            json={"universe": ["000003", "000004"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["universe"] == {"fund_codes": ["000003", "000004"]}


# ---------------------------------------------------------------------------
# Tests: DELETE /api/v1/strategies/{id}
# ---------------------------------------------------------------------------


class TestDeleteStrategy:
    """Tests for strategy delete endpoint."""

    async def test_delete_existing_strategy(self, client: AsyncClient):
        """Should delete a strategy and return 204."""
        created = await _create_strategy(client)
        strategy_id = created["id"]

        resp = await client.delete(f"/api/v1/strategies/{strategy_id}")
        assert resp.status_code == 204

        # Verify it's gone
        resp = await client.get(f"/api/v1/strategies/{strategy_id}")
        assert resp.status_code == 404

    async def test_delete_nonexistent_strategy(self, client: AsyncClient):
        """Should return 404 for non-existent strategy."""
        resp = await client.delete("/api/v1/strategies/99999")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: Pydantic JSON Schema validation
# ---------------------------------------------------------------------------


class TestParamsValidation:
    """Tests for strategy params JSON Schema validation."""

    async def test_dca_valid_params(self, client: AsyncClient):
        """DCA strategy with valid params should succeed."""
        data = await _create_strategy(
            client,
            name="定投",
            strategy_type="dca",
            params={"amount": 500, "frequency": "weekly", "dca_type": "smart", "ma_window": 20},
        )
        assert data["params"]["dca_type"] == "smart"

    async def test_timing_valid_params(self, client: AsyncClient):
        """Timing strategy with valid params should succeed."""
        data = await _create_strategy(
            client,
            name="择时",
            strategy_type="timing",
            params={"method": "dual_ma", "fast_window": 5, "slow_window": 20},
        )
        assert data["params"]["method"] == "dual_ma"

    async def test_mean_variance_valid_params(self, client: AsyncClient):
        """Mean-variance strategy with valid params should succeed."""
        data = await _create_strategy(
            client,
            name="均值方差",
            strategy_type="mean_variance",
            params={
                "rebalance_freq": "monthly",
                "risk_free_rate": 0.03,
                "max_weight": 0.4,
            },
        )
        assert data["params"]["max_weight"] == 0.4

    async def test_mean_variance_max_weight_exceeds_1(self, client: AsyncClient):
        """Should reject max_weight > 1."""
        resp = await client.post(
            "/api/v1/strategies",
            json={
                "name": "权重超限",
                "strategy_type": "mean_variance",
                "params": {
                    "rebalance_freq": "monthly",
                    "max_weight": 1.5,
                },
                "universe": ["000001"],
            },
        )
        assert resp.status_code == 422

    async def test_fof_valid_params(self, client: AsyncClient):
        """FOF strategy with valid params should succeed."""
        data = await _create_strategy(
            client,
            name="FOF策略",
            strategy_type="fof",
            params={
                "factor_weights": {"sharpe": 0.4, "max_drawdown": 0.3, "volatility": 0.3},
                "top_n": 10,
                "rebalance_freq": "quarterly",
                "optimization": "risk_parity",
            },
        )
        assert data["params"]["optimization"] == "risk_parity"
