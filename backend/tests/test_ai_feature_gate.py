"""Integration tests for AI feature gate and data masking.

Tests verify:
1. AI_ENABLED=False → all AI endpoints return 501
2. AI_ENABLED=False → core endpoints (health, version, funds) still work
3. Data masking aggregates holdings by industry correctly

Requirements: 11.24, 11.25, 11.26
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ai.data_masking import (
    MaskedHolding,
    format_masked_holdings_for_llm,
    mask_holdings_by_industry,
)
from app.core.config import Settings, get_settings
from app.main import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ai_disabled_settings() -> Settings:
    """Settings with AI explicitly disabled."""
    get_settings.cache_clear()
    return Settings(
        APP_ENV="test",
        DEBUG="true",
        LOG_LEVEL="WARNING",
        API_PREFIX="/api/v1",
        AI_ENABLED="false",
        DB_AUTO_MIGRATE="false",
        PROMETHEUS_ENABLED="false",
    )


@pytest.fixture
def ai_enabled_settings() -> Settings:
    """Settings with AI explicitly enabled."""
    get_settings.cache_clear()
    return Settings(
        APP_ENV="test",
        DEBUG="true",
        LOG_LEVEL="WARNING",
        API_PREFIX="/api/v1",
        AI_ENABLED="true",
        DB_AUTO_MIGRATE="false",
        PROMETHEUS_ENABLED="false",
    )


@pytest.fixture
def ai_disabled_app(ai_disabled_settings: Settings) -> Iterator[FastAPI]:
    """App with AI disabled."""
    application = create_app(ai_disabled_settings)
    application.dependency_overrides[get_settings] = lambda: ai_disabled_settings
    try:
        yield application
    finally:
        application.dependency_overrides.clear()
        get_settings.cache_clear()


@pytest.fixture
def ai_enabled_app(ai_enabled_settings: Settings) -> Iterator[FastAPI]:
    """App with AI enabled."""
    application = create_app(ai_enabled_settings)
    application.dependency_overrides[get_settings] = lambda: ai_enabled_settings
    try:
        yield application
    finally:
        application.dependency_overrides.clear()
        get_settings.cache_clear()


@pytest.fixture
def ai_disabled_client(ai_disabled_app: FastAPI) -> Iterator[TestClient]:
    """Test client with AI disabled."""
    with TestClient(ai_disabled_app) as tc:
        yield tc


@pytest.fixture
def ai_enabled_client(ai_enabled_app: FastAPI) -> Iterator[TestClient]:
    """Test client with AI enabled."""
    with TestClient(ai_enabled_app) as tc:
        yield tc


# ---------------------------------------------------------------------------
# Tests: AI disabled → 501 on all AI endpoints
# ---------------------------------------------------------------------------


class TestAIDisabledReturns501:
    """When AI_ENABLED=False, all /ai/ endpoints return 501."""

    def test_ai_query_returns_501(self, ai_disabled_client: TestClient) -> None:
        resp = ai_disabled_client.post(
            "/api/v1/ai/query",
            json={"query": "查找夏普比率最高的基金"},
        )
        assert resp.status_code == 501
        body = resp.json()
        assert "AI 功能已关闭" in body["error"]["message"]

    def test_ai_strategy_gen_returns_501(self, ai_disabled_client: TestClient) -> None:
        resp = ai_disabled_client.post(
            "/api/v1/ai/strategy-gen",
            json={"description": "动量轮动策略"},
        )
        assert resp.status_code == 501
        assert "AI 功能已关闭" in resp.json()["error"]["message"]

    def test_ai_factor_brainstorm_returns_501(
        self, ai_disabled_client: TestClient
    ) -> None:
        resp = ai_disabled_client.post(
            "/api/v1/ai/factor-brainstorm",
            json={"hypothesis": "动量因子在小盘基金中可能有更强的预测力"},
        )
        assert resp.status_code == 501
        assert "AI 功能已关闭" in resp.json()["error"]["message"]

    def test_ai_usage_returns_501(self, ai_disabled_client: TestClient) -> None:
        resp = ai_disabled_client.get("/api/v1/ai/usage")
        assert resp.status_code == 501
        assert "AI 功能已关闭" in resp.json()["error"]["message"]


# ---------------------------------------------------------------------------
# Tests: AI disabled → core endpoints still work
# ---------------------------------------------------------------------------


class TestCoreEndpointsWorkWithAIDisabled:
    """When AI_ENABLED=False, core platform endpoints remain functional.

    Note: endpoints that require a database connection will return 500
    (no DB available in test), but crucially NOT 501 (AI disabled).
    The point is that the AI feature gate does not interfere with
    non-AI routes.
    """

    def test_health_endpoint_works(self, ai_disabled_client: TestClient) -> None:
        resp = ai_disabled_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_version_endpoint_works(self, ai_disabled_client: TestClient) -> None:
        resp = ai_disabled_client.get("/api/v1/version")
        assert resp.status_code == 200
        data = resp.json()
        assert "version" in data

    def test_funds_endpoint_not_blocked_by_ai_gate(
        self, ai_disabled_client: TestClient
    ) -> None:
        """Funds search endpoint is not blocked by AI gate.

        The endpoint requires a DB connection which isn't available in
        this test environment. A ConnectionRefusedError or 500 response
        proves the route was reached (not blocked by the AI gate).
        If it were blocked, we'd get a clean 501 response.
        """
        try:
            resp = ai_disabled_client.get("/api/v1/funds/NONEXIST")
            # If we get a response, it should not be 501
            assert resp.status_code != 501
        except ConnectionRefusedError:
            # ConnectionRefusedError means the route was reached but
            # the DB is unavailable — this is fine, it proves the AI
            # gate did not block the request.
            pass

    def test_factors_endpoint_not_blocked_by_ai_gate(
        self, ai_disabled_client: TestClient
    ) -> None:
        resp = ai_disabled_client.get("/api/v1/factors")
        assert resp.status_code != 501
        # Factors list is in-memory, should return 200
        assert resp.status_code == 200

    def test_strategies_endpoint_not_blocked_by_ai_gate(
        self, ai_disabled_client: TestClient
    ) -> None:
        """Strategies endpoint route exists and is not gated by AI_ENABLED.

        We POST an invalid strategy to get a validation error (422)
        which proves the route is reachable without hitting the DB.
        """
        resp = ai_disabled_client.post(
            "/api/v1/strategies",
            json={},  # Invalid payload → 422 validation error
        )
        # 422 means the route is reachable, just invalid payload
        assert resp.status_code != 501


# ---------------------------------------------------------------------------
# Tests: AI enabled → AI endpoints do NOT return 501
# ---------------------------------------------------------------------------


class TestAIEnabledDoesNotBlock:
    """When AI_ENABLED=True, AI endpoints are not blocked by the gate."""

    def test_ai_query_not_blocked(self, ai_enabled_client: TestClient) -> None:
        resp = ai_enabled_client.post(
            "/api/v1/ai/query",
            json={"query": "查找夏普比率最高的基金"},
        )
        # Should not be 501 — may be 503 (no LLM configured) or other
        assert resp.status_code != 501

    def test_ai_strategy_gen_not_blocked(self, ai_enabled_client: TestClient) -> None:
        resp = ai_enabled_client.post(
            "/api/v1/ai/strategy-gen",
            json={"description": "动量轮动策略"},
        )
        assert resp.status_code != 501

    def test_ai_usage_not_blocked(self, ai_enabled_client: TestClient) -> None:
        resp = ai_enabled_client.get("/api/v1/ai/usage")
        assert resp.status_code != 501


# ---------------------------------------------------------------------------
# Tests: Data masking utility
# ---------------------------------------------------------------------------


class TestDataMasking:
    """Test holdings data masking by industry aggregation."""

    def test_basic_aggregation(self) -> None:
        holdings = [
            {"stock_code": "600519", "stock_name": "贵州茅台", "industry": "食品饮料", "weight": 8.5},
            {"stock_code": "000858", "stock_name": "五粮液", "industry": "食品饮料", "weight": 5.2},
            {"stock_code": "601318", "stock_name": "中国平安", "industry": "金融", "weight": 6.0},
            {"stock_code": "000001", "stock_name": "平安银行", "industry": "金融", "weight": 3.5},
            {"stock_code": "300750", "stock_name": "宁德时代", "industry": "新能源", "weight": 7.0},
        ]

        result = mask_holdings_by_industry(holdings)

        assert len(result) == 3
        # Sorted by weight descending
        assert result[0].industry == "食品饮料"
        assert result[0].total_weight == pytest.approx(13.7, abs=0.01)
        assert result[0].stock_count == 2

        assert result[1].industry == "金融"
        assert result[1].total_weight == pytest.approx(9.5, abs=0.01)
        assert result[1].stock_count == 2

        assert result[2].industry == "新能源"
        assert result[2].total_weight == pytest.approx(7.0, abs=0.01)
        assert result[2].stock_count == 1

    def test_no_stock_codes_in_output(self) -> None:
        """Masked output must not contain individual stock identifiers."""
        holdings = [
            {"stock_code": "600519", "stock_name": "贵州茅台", "industry": "食品饮料", "weight": 8.5},
        ]

        result = mask_holdings_by_industry(holdings)

        # The MaskedHolding dataclass has no stock_code or stock_name fields
        for item in result:
            assert not hasattr(item, "stock_code")
            assert not hasattr(item, "stock_name")

    def test_missing_industry_grouped_as_uncategorized(self) -> None:
        holdings = [
            {"stock_code": "600519", "weight": 5.0},
            {"stock_code": "000858", "industry": "", "weight": 3.0},
            {"stock_code": "601318", "industry": "金融", "weight": 4.0},
        ]

        result = mask_holdings_by_industry(holdings)

        industries = {item.industry for item in result}
        assert "未分类" in industries
        assert "金融" in industries

        uncategorized = next(r for r in result if r.industry == "未分类")
        assert uncategorized.stock_count == 2
        assert uncategorized.total_weight == pytest.approx(8.0, abs=0.01)

    def test_decimal_weights_handled(self) -> None:
        holdings = [
            {"stock_code": "600519", "industry": "食品饮料", "weight": Decimal("8.5000")},
            {"stock_code": "000858", "industry": "食品饮料", "weight": Decimal("5.2000")},
        ]

        result = mask_holdings_by_industry(holdings)

        assert len(result) == 1
        assert result[0].total_weight == pytest.approx(13.7, abs=0.01)

    def test_empty_holdings(self) -> None:
        result = mask_holdings_by_industry([])
        assert result == []

    def test_format_for_llm(self) -> None:
        masked = [
            MaskedHolding(industry="食品饮料", total_weight=13.7, stock_count=2),
            MaskedHolding(industry="金融", total_weight=9.5, stock_count=2),
        ]

        text = format_masked_holdings_for_llm(masked)

        assert "行业配置分布" in text
        assert "食品饮料" in text
        assert "13.70%" in text
        assert "2 只" in text
        assert "金融" in text
        # Should NOT contain any stock codes
        assert "600519" not in text
        assert "贵州茅台" not in text

    def test_format_empty_holdings(self) -> None:
        text = format_masked_holdings_for_llm([])
        assert text == "无持仓数据"
