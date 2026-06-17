"""Unit tests for the GET /ai/usage endpoint (app/api/v1/ai.py).

Tests verify that the usage endpoint correctly aggregates data from
LLMAuditLog and LLMBudget, and handles error cases gracefully.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.ai.feature_gate import require_ai_enabled
from app.api.v1.ai import router


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app() -> FastAPI:
    """Create a minimal FastAPI app with the AI router mounted and gate bypassed."""
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    # Override the AI feature gate so endpoints are accessible in tests
    app.dependency_overrides[require_ai_enabled] = lambda: None
    return app


@pytest.fixture
async def client(app: FastAPI):
    """Create an async test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGetAIUsage:
    """Tests for GET /api/v1/ai/usage endpoint."""

    @pytest.mark.asyncio
    async def test_usage_returns_200_with_stats(self, client: AsyncClient) -> None:
        """Should return 200 with aggregated usage stats."""
        mock_stats = {
            "total_calls": 42,
            "successful_calls": 40,
            "failed_calls": 2,
            "total_prompt_tokens": 10000,
            "total_completion_tokens": 5000,
            "total_tokens": 15000,
            "total_cost_usd": 0.15,
            "avg_latency_ms": 350.5,
            "by_provider": {
                "openai": {
                    "calls": 30,
                    "prompt_tokens": 7000,
                    "completion_tokens": 3500,
                    "cost_usd": 0.10,
                },
                "anthropic": {
                    "calls": 12,
                    "prompt_tokens": 3000,
                    "completion_tokens": 1500,
                    "cost_usd": 0.05,
                },
            },
            "by_use_case": {
                "nl_query": {
                    "calls": 20,
                    "prompt_tokens": 5000,
                    "completion_tokens": 2500,
                    "cost_usd": 0.07,
                },
                "announcement_parse": {
                    "calls": 22,
                    "prompt_tokens": 5000,
                    "completion_tokens": 2500,
                    "cost_usd": 0.08,
                },
            },
            "period_start": "2024-01-01T00:00:00+00:00",
            "period_end": "2024-01-31T00:00:00+00:00",
        }

        mock_budget = {
            "daily_spend_usd": 1.5,
            "daily_limit_usd": 10.0,
            "daily_remaining_usd": 8.5,
            "monthly_spend_usd": 25.0,
            "monthly_limit_usd": 200.0,
            "monthly_remaining_usd": 175.0,
            "date": "2024-01-15",
            "month": "2024-01",
        }

        with (
            patch("app.ai.audit.LLMAuditLog") as MockAudit,
            patch("app.ai.budget.LLMBudget") as MockBudget,
        ):
            mock_audit_instance = AsyncMock()
            mock_audit_instance.get_stats.return_value = mock_stats
            MockAudit.return_value = mock_audit_instance

            mock_budget_instance = AsyncMock()
            mock_budget_instance.get_usage.return_value = mock_budget
            mock_budget_instance.close = AsyncMock()
            MockBudget.return_value = mock_budget_instance

            response = await client.get("/api/v1/ai/usage")

        assert response.status_code == 200
        data = response.json()

        assert data["total_calls"] == 42
        assert data["successful_calls"] == 40
        assert data["failed_calls"] == 2
        assert data["total_prompt_tokens"] == 10000
        assert data["total_completion_tokens"] == 5000
        assert data["total_tokens"] == 15000
        assert data["total_cost_usd"] == 0.15
        assert data["avg_latency_ms"] == 350.5
        assert "openai" in data["by_provider"]
        assert "anthropic" in data["by_provider"]
        assert data["by_provider"]["openai"]["calls"] == 30
        assert "nl_query" in data["by_use_case"]
        assert data["budget"] is not None
        assert data["budget"]["daily_spend_usd"] == 1.5
        assert data["budget"]["monthly_limit_usd"] == 200.0

    @pytest.mark.asyncio
    async def test_usage_with_custom_days_param(self, client: AsyncClient) -> None:
        """Should pass the days parameter to get_stats."""
        mock_stats = {
            "total_calls": 5,
            "successful_calls": 5,
            "failed_calls": 0,
            "total_prompt_tokens": 1000,
            "total_completion_tokens": 500,
            "total_tokens": 1500,
            "total_cost_usd": 0.02,
            "avg_latency_ms": 200.0,
            "by_provider": {},
            "by_use_case": {},
            "period_start": "2024-01-08T00:00:00+00:00",
            "period_end": "2024-01-15T00:00:00+00:00",
        }

        mock_budget = {
            "daily_spend_usd": 0.5,
            "daily_limit_usd": 10.0,
            "daily_remaining_usd": 9.5,
            "monthly_spend_usd": 5.0,
            "monthly_limit_usd": 200.0,
            "monthly_remaining_usd": 195.0,
            "date": "2024-01-15",
            "month": "2024-01",
        }

        with (
            patch("app.ai.audit.LLMAuditLog") as MockAudit,
            patch("app.ai.budget.LLMBudget") as MockBudget,
        ):
            mock_audit_instance = AsyncMock()
            mock_audit_instance.get_stats.return_value = mock_stats
            MockAudit.return_value = mock_audit_instance

            mock_budget_instance = AsyncMock()
            mock_budget_instance.get_usage.return_value = mock_budget
            mock_budget_instance.close = AsyncMock()
            MockBudget.return_value = mock_budget_instance

            response = await client.get("/api/v1/ai/usage", params={"days": 7})

        assert response.status_code == 200
        mock_audit_instance.get_stats.assert_called_once_with(days=7)

    @pytest.mark.asyncio
    async def test_usage_budget_failure_returns_null_budget(
        self, client: AsyncClient
    ) -> None:
        """Should return null budget when Redis is unavailable."""
        mock_stats = {
            "total_calls": 10,
            "successful_calls": 10,
            "failed_calls": 0,
            "total_prompt_tokens": 2000,
            "total_completion_tokens": 1000,
            "total_tokens": 3000,
            "total_cost_usd": 0.05,
            "avg_latency_ms": 300.0,
            "by_provider": {},
            "by_use_case": {},
            "period_start": "2024-01-01T00:00:00+00:00",
            "period_end": "2024-01-31T00:00:00+00:00",
        }

        with (
            patch("app.ai.audit.LLMAuditLog") as MockAudit,
            patch("app.ai.budget.LLMBudget") as MockBudget,
        ):
            mock_audit_instance = AsyncMock()
            mock_audit_instance.get_stats.return_value = mock_stats
            MockAudit.return_value = mock_audit_instance

            mock_budget_instance = AsyncMock()
            mock_budget_instance.get_usage.side_effect = ConnectionError("Redis down")
            mock_budget_instance.close = AsyncMock()
            MockBudget.return_value = mock_budget_instance

            response = await client.get("/api/v1/ai/usage")

        assert response.status_code == 200
        data = response.json()
        assert data["total_calls"] == 10
        assert data["budget"] is None

    @pytest.mark.asyncio
    async def test_usage_invalid_days_param(self, client: AsyncClient) -> None:
        """Should return 422 for invalid days parameter."""
        response = await client.get("/api/v1/ai/usage", params={"days": 0})
        assert response.status_code == 422

        response = await client.get("/api/v1/ai/usage", params={"days": 500})
        assert response.status_code == 422
