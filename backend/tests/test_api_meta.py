"""Tests for meta API endpoints."""

from __future__ import annotations

from typing import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings, get_settings
from app.domain.performance.metrics import METRIC_VERSION
from app.main import create_app


@pytest.fixture
def test_settings() -> Settings:
    """Test settings for meta API."""
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
async def app(test_settings: Settings) -> AsyncIterator[FastAPI]:
    application = create_app(test_settings)
    application.dependency_overrides[get_settings] = lambda: test_settings
    try:
        yield application
    finally:
        application.dependency_overrides.clear()
        get_settings.cache_clear()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_metric_definitions_endpoint(client: AsyncClient) -> None:
    """Metric definitions should expose formulas and口径 metadata."""
    resp = await client.get("/api/v1/meta/metric-definitions")

    assert resp.status_code == 200
    body = resp.json()
    assert body["metric_version"] == METRIC_VERSION
    assert body["frequency"] == 252

    definitions = {item["key"]: item for item in body["definitions"]}
    assert "annualized_return" in definitions
    assert "sortino" in definitions
    assert "var_95" in definitions
    assert "win_rate" in definitions
    assert "profit_factor" in definitions
    assert "trade_win_rate" in definitions
    assert "trade_profit_factor" in definitions
    assert "n_points - 1" in definitions["annualized_return"]["formula"]
    assert "全样本分母" in definitions["sortino"]["annualization"]
    assert "正数损失" in definitions["var_95"]["sign"]
    assert "正数损失" in definitions["cvar_95"]["sign"]
    assert "负收益分位数" not in definitions["var_95"]["sign"]
    assert "负尾部收益" not in definitions["cvar_95"]["sign"]
    assert "风险因子少于 2 个收益点" in definitions["var_95"]["insufficient_data"]
    assert "日收益为正" in definitions["win_rate"]["usage"]
    assert "非严格逐笔配对" in definitions["trade_win_rate"]["usage"]
    assert "不能等同于严格逐笔交易盈亏比" in definitions["trade_profit_factor"]["usage"]
