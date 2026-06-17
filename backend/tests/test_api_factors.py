"""Integration tests for the factors API endpoints.

Tests cover:
- GET /api/v1/factors (factor metadata list)
- POST /api/v1/factors/compute (batch factor computation)
- GET /api/v1/funds/{code}/factors (single fund factors)

Requirements: 7.2, 7.6
"""

from __future__ import annotations

from typing import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings, get_settings
from app.main import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def test_settings() -> Settings:
    """Test settings — no DB needed for factor API tests."""
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
def app(test_settings: Settings) -> FastAPI:
    """Create a FastAPI app with test settings."""
    application = create_app(test_settings)
    application.dependency_overrides[get_settings] = lambda: test_settings
    return application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Async HTTP client for testing."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Tests: GET /api/v1/factors
# ---------------------------------------------------------------------------


class TestListFactors:
    """Tests for the factor metadata list endpoint."""

    async def test_list_all_factors(self, client: AsyncClient):
        """Should return all registered factors."""
        resp = await client.get("/api/v1/factors")
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
        assert "factors" in data
        assert data["total"] > 0
        assert len(data["factors"]) == data["total"]

    async def test_factor_metadata_fields(self, client: AsyncClient):
        """Each factor should have required metadata fields."""
        resp = await client.get("/api/v1/factors")
        assert resp.status_code == 200
        data = resp.json()
        for factor in data["factors"]:
            assert "name" in factor
            assert "category" in factor
            assert "return_type" in factor
            assert "description" in factor
            # window can be None
            assert "window" in factor

    async def test_filter_by_category(self, client: AsyncClient):
        """Should filter factors by category."""
        resp = await client.get("/api/v1/factors", params={"category": "return"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] > 0
        for factor in data["factors"]:
            assert factor["category"] == "return"

    async def test_filter_by_category_risk(self, client: AsyncClient):
        """Should filter factors by risk category."""
        resp = await client.get("/api/v1/factors", params={"category": "risk"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] > 0
        for factor in data["factors"]:
            assert factor["category"] == "risk"

    async def test_filter_by_nonexistent_category(self, client: AsyncClient):
        """Should return empty list for non-existent category."""
        resp = await client.get("/api/v1/factors", params={"category": "nonexistent"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["factors"] == []


# ---------------------------------------------------------------------------
# Tests: POST /api/v1/factors/compute
# ---------------------------------------------------------------------------


class TestComputeFactors:
    """Tests for the batch factor computation endpoint."""

    async def test_compute_single_factor_single_fund(self, client: AsyncClient):
        """Should compute a single factor for a single fund."""
        resp = await client.post(
            "/api/v1/factors/compute",
            json={
                "fund_codes": ["000001"],
                "factor_names": ["annualized_return"],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["fund_codes"] == ["000001"]
        assert data["factor_names"] == ["annualized_return"]
        assert data["freq"] == "daily"
        assert len(data["results"]) == 1
        result = data["results"][0]
        assert result["fund_code"] == "000001"
        assert result["factor_name"] == "annualized_return"
        # Value should be a number or null
        assert result["value"] is None or isinstance(result["value"], (int, float))

    async def test_compute_multiple_factors_multiple_funds(self, client: AsyncClient):
        """Should compute multiple factors for multiple funds."""
        resp = await client.post(
            "/api/v1/factors/compute",
            json={
                "fund_codes": ["000001", "000002", "110011"],
                "factor_names": ["annualized_return", "volatility", "max_drawdown"],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["fund_codes"] == ["000001", "000002", "110011"]
        assert data["factor_names"] == ["annualized_return", "volatility", "max_drawdown"]
        # 3 funds × 3 factors = 9 results
        assert len(data["results"]) == 9

    async def test_compute_with_window(self, client: AsyncClient):
        """Should respect the window parameter."""
        resp = await client.post(
            "/api/v1/factors/compute",
            json={
                "fund_codes": ["000001"],
                "factor_names": ["annualized_return"],
                "window": 60,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["window"] == 60
        assert len(data["results"]) == 1

    async def test_compute_with_frequency(self, client: AsyncClient):
        """Should respect the freq parameter."""
        resp = await client.post(
            "/api/v1/factors/compute",
            json={
                "fund_codes": ["000001"],
                "factor_names": ["annualized_return"],
                "freq": "monthly",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["freq"] == "monthly"

    async def test_compute_with_benchmark(self, client: AsyncClient):
        """Should accept benchmark_code for benchmark-related factors."""
        resp = await client.post(
            "/api/v1/factors/compute",
            json={
                "fund_codes": ["000001"],
                "factor_names": ["beta"],
                "benchmark_code": "000300",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) == 1

    async def test_compute_invalid_factor_name(self, client: AsyncClient):
        """Should return 422 for unknown factor names."""
        resp = await client.post(
            "/api/v1/factors/compute",
            json={
                "fund_codes": ["000001"],
                "factor_names": ["nonexistent_factor"],
            },
        )
        assert resp.status_code == 422
        data = resp.json()
        # The error may be in "detail" or wrapped in "error.message"
        error_text = data.get("detail", "") or data.get("error", {}).get("message", "")
        assert "nonexistent_factor" in error_text

    async def test_compute_empty_fund_codes(self, client: AsyncClient):
        """Should return 422 for empty fund_codes list."""
        resp = await client.post(
            "/api/v1/factors/compute",
            json={
                "fund_codes": [],
                "factor_names": ["annualized_return"],
            },
        )
        assert resp.status_code == 422

    async def test_compute_empty_factor_names(self, client: AsyncClient):
        """Should return 422 for empty factor_names list."""
        resp = await client.post(
            "/api/v1/factors/compute",
            json={
                "fund_codes": ["000001"],
                "factor_names": [],
            },
        )
        assert resp.status_code == 422

    async def test_compute_invalid_frequency(self, client: AsyncClient):
        """Should return 422 for invalid frequency value."""
        resp = await client.post(
            "/api/v1/factors/compute",
            json={
                "fund_codes": ["000001"],
                "factor_names": ["annualized_return"],
                "freq": "invalid",
            },
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Tests: GET /api/v1/funds/{code}/factors
# ---------------------------------------------------------------------------


class TestGetFundFactors:
    """Tests for the single-fund factor computation endpoint."""

    async def test_get_fund_factors_default(self, client: AsyncClient):
        """Should return all factors for a fund with default params."""
        resp = await client.get("/api/v1/funds/000001/factors")
        assert resp.status_code == 200
        data = resp.json()
        assert data["fund_code"] == "000001"
        assert "factors" in data
        assert len(data["factors"]) > 0
        # Each result should have the correct fund_code
        for item in data["factors"]:
            assert item["fund_code"] == "000001"
            assert "factor_name" in item
            assert "value" in item

    async def test_get_fund_factors_filter_category(self, client: AsyncClient):
        """Should filter factors by category."""
        resp = await client.get(
            "/api/v1/funds/000001/factors", params={"category": "return"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["fund_code"] == "000001"
        # All returned factors should be in the return category
        all_return_factors = await client.get(
            "/api/v1/factors", params={"category": "return"}
        )
        return_factor_names = {
            f["name"] for f in all_return_factors.json()["factors"]
        }
        for item in data["factors"]:
            assert item["factor_name"] in return_factor_names

    async def test_get_fund_factors_with_window(self, client: AsyncClient):
        """Should respect the window parameter."""
        resp = await client.get(
            "/api/v1/funds/000001/factors", params={"window": 60}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["fund_code"] == "000001"
        assert len(data["factors"]) > 0

    async def test_get_fund_factors_with_frequency(self, client: AsyncClient):
        """Should respect the freq parameter."""
        resp = await client.get(
            "/api/v1/funds/000001/factors", params={"freq": "weekly"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["fund_code"] == "000001"
        assert len(data["factors"]) > 0

    async def test_get_fund_factors_nonexistent_category(self, client: AsyncClient):
        """Should return empty factors for non-existent category."""
        resp = await client.get(
            "/api/v1/funds/000001/factors", params={"category": "nonexistent"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["fund_code"] == "000001"
        assert data["factors"] == []


# ---------------------------------------------------------------------------
# Tests: OpenAPI documentation
# ---------------------------------------------------------------------------


class TestOpenAPIDocumentation:
    """Tests verifying OpenAPI documentation completeness."""

    async def test_openapi_schema_available(self, client: AsyncClient):
        """OpenAPI schema should be accessible."""
        resp = await client.get("/openapi.json")
        assert resp.status_code == 200
        schema = resp.json()
        paths = schema["paths"]

        # Verify factor endpoints are documented
        assert "/api/v1/factors" in paths
        assert "/api/v1/factors/compute" in paths
        assert "/api/v1/funds/{code}/factors" in paths

    async def test_openapi_factors_list_documented(self, client: AsyncClient):
        """GET /factors should have proper OpenAPI documentation."""
        resp = await client.get("/openapi.json")
        schema = resp.json()
        factors_path = schema["paths"]["/api/v1/factors"]
        assert "get" in factors_path
        get_op = factors_path["get"]
        assert "summary" in get_op
        assert "description" in get_op

    async def test_openapi_factors_compute_documented(self, client: AsyncClient):
        """POST /factors/compute should have proper OpenAPI documentation."""
        resp = await client.get("/openapi.json")
        schema = resp.json()
        compute_path = schema["paths"]["/api/v1/factors/compute"]
        assert "post" in compute_path
        post_op = compute_path["post"]
        assert "summary" in post_op
        assert "description" in post_op
        assert "requestBody" in post_op

    async def test_openapi_fund_factors_documented(self, client: AsyncClient):
        """GET /funds/{code}/factors should have proper OpenAPI documentation."""
        resp = await client.get("/openapi.json")
        schema = resp.json()
        fund_factors_path = schema["paths"]["/api/v1/funds/{code}/factors"]
        assert "get" in fund_factors_path
        get_op = fund_factors_path["get"]
        assert "summary" in get_op
        assert "description" in get_op
