"""Tests for administrative maintenance API endpoints."""

from __future__ import annotations

from typing import AsyncIterator
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings, get_settings
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
        ADMIN_API_ENABLED="true",
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


async def _client_for_settings(settings: Settings) -> AsyncIterator[AsyncClient]:
    application = create_app(settings)
    application.dependency_overrides[get_settings] = lambda: settings
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    application.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_trigger_recalculate_adj_nav_single_fund(client: AsyncClient) -> None:
    async_result = MagicMock(id="task-123")

    with patch("app.tasks.ingest.recalculate_adj_nav_history.delay", return_value=async_result) as mock_delay:
        resp = await client.post(
            "/api/v1/admin/nav/recalculate-adj-nav",
            json={
                "fund_code": "000001",
                "invalidate_cache": True,
                "mark_stale_results": True,
            },
        )

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "submitted"
    assert body["task_id"] == "task-123"
    assert body["task_name"] == "app.tasks.ingest.recalculate_adj_nav_history"
    assert body["params"] == {
        "fund_code": "000001",
        "fund_codes": None,
        "invalidate_cache": True,
        "mark_stale_results": True,
    }
    mock_delay.assert_called_once_with(
        fund_code="000001",
        fund_codes=None,
        invalidate_cache=True,
        mark_stale_results=True,
    )


@pytest.mark.asyncio
async def test_trigger_recalculate_adj_nav_batch_without_stale_marking(client: AsyncClient) -> None:
    async_result = MagicMock(id="task-456")

    with patch("app.tasks.ingest.recalculate_adj_nav_history.delay", return_value=async_result) as mock_delay:
        resp = await client.post(
            "/api/v1/admin/nav/recalculate-adj-nav",
            json={
                "fund_codes": ["000001", "000002"],
                "invalidate_cache": False,
                "mark_stale_results": False,
            },
        )

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["task_id"] == "task-456"
    assert body["params"] == {
        "fund_code": None,
        "fund_codes": ["000001", "000002"],
        "invalidate_cache": False,
        "mark_stale_results": False,
    }
    mock_delay.assert_called_once_with(
        fund_code=None,
        fund_codes=["000001", "000002"],
        invalidate_cache=False,
        mark_stale_results=False,
    )


@pytest.mark.asyncio
async def test_trigger_recalculate_adj_nav_all_active_funds(client: AsyncClient) -> None:
    async_result = MagicMock(id="task-all")

    with patch("app.tasks.ingest.recalculate_adj_nav_history.delay", return_value=async_result) as mock_delay:
        resp = await client.post("/api/v1/admin/nav/recalculate-adj-nav", json={})

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["params"]["fund_code"] is None
    assert body["params"]["fund_codes"] is None
    mock_delay.assert_called_once_with(
        fund_code=None,
        fund_codes=None,
        invalidate_cache=True,
        mark_stale_results=True,
    )


@pytest.mark.asyncio
async def test_trigger_recalculate_adj_nav_rejects_conflicting_code_inputs(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/admin/nav/recalculate-adj-nav",
        json={"fund_code": "000001", "fund_codes": ["000002"]},
    )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_trigger_recalculate_adj_nav_dispatch_failure_returns_503(client: AsyncClient) -> None:
    with patch("app.tasks.ingest.recalculate_adj_nav_history.delay", side_effect=RuntimeError("broker down")):
        resp = await client.post(
            "/api/v1/admin/nav/recalculate-adj-nav",
            json={"fund_code": "000001"},
        )

    assert resp.status_code == 503
    assert "无法派发复权净值重算任务" in resp.json()["error"]["message"]


@pytest.mark.asyncio
async def test_admin_api_disabled_rejects_request() -> None:
    settings = Settings(
        APP_ENV="test",
        DEBUG="true",
        LOG_LEVEL="WARNING",
        DATABASE_URL="sqlite+aiosqlite:///:memory:",
        DB_AUTO_MIGRATE="false",
        PROMETHEUS_ENABLED="false",
        REDIS_URL="redis://localhost:6379/15",
        ADMIN_API_ENABLED="false",
    )

    async for client in _client_for_settings(settings):
        resp = await client.post(
            "/api/v1/admin/nav/recalculate-adj-nav",
            json={"fund_code": "000001"},
        )

    assert resp.status_code == 403
    assert resp.json()["error"]["message"] == "Admin API is disabled"


@pytest.mark.asyncio
async def test_admin_api_token_required_when_configured() -> None:
    settings = Settings(
        APP_ENV="test",
        DEBUG="true",
        LOG_LEVEL="WARNING",
        DATABASE_URL="sqlite+aiosqlite:///:memory:",
        DB_AUTO_MIGRATE="false",
        PROMETHEUS_ENABLED="false",
        REDIS_URL="redis://localhost:6379/15",
        ADMIN_API_ENABLED="true",
        ADMIN_API_TOKEN="secret-token",
    )

    async for client in _client_for_settings(settings):
        resp = await client.post(
            "/api/v1/admin/nav/recalculate-adj-nav",
            json={"fund_code": "000001"},
        )

    assert resp.status_code == 403
    assert resp.json()["error"]["message"] == "Invalid admin token"


@pytest.mark.asyncio
async def test_admin_api_accepts_valid_token() -> None:
    settings = Settings(
        APP_ENV="test",
        DEBUG="true",
        LOG_LEVEL="WARNING",
        DATABASE_URL="sqlite+aiosqlite:///:memory:",
        DB_AUTO_MIGRATE="false",
        PROMETHEUS_ENABLED="false",
        REDIS_URL="redis://localhost:6379/15",
        ADMIN_API_ENABLED="true",
        ADMIN_API_TOKEN="secret-token",
    )
    async_result = MagicMock(id="task-token")

    async for client in _client_for_settings(settings):
        with patch("app.tasks.ingest.recalculate_adj_nav_history.delay", return_value=async_result) as mock_delay:
            resp = await client.post(
                "/api/v1/admin/nav/recalculate-adj-nav",
                headers={"X-Admin-Token": "secret-token"},
                json={"fund_code": "000001"},
            )

    assert resp.status_code == 202, resp.text
    assert resp.json()["task_id"] == "task-token"
    mock_delay.assert_called_once()
