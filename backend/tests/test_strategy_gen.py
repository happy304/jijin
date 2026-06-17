"""Tests for the natural language strategy generation use case.

Tests cover:
- StrategyGenerator with mock LLMService
- Parameter range validation (validate_param_ranges)
- API endpoint POST /ai/strategy-gen
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ai.use_cases.strategy_gen import (
    VALID_STRATEGY_TYPES,
    StrategyGenResult,
    StrategyGenerator,
    validate_param_ranges,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@dataclass
class FakeLLMResult:
    """Minimal LLMResult stand-in for testing."""

    content: str | dict[str, Any]
    cached: bool = False
    provider: str = "test"
    model: str = "test-model"
    cost_usd: float = 0.001
    latency_ms: int = 100


def make_mock_llm_service(response_content: dict[str, Any]) -> AsyncMock:
    """Create a mock LLMService that returns the given content."""
    mock_service = AsyncMock()
    mock_service.call = AsyncMock(
        return_value=FakeLLMResult(content=response_content)
    )
    return mock_service


# ---------------------------------------------------------------------------
# Tests: validate_param_ranges
# ---------------------------------------------------------------------------


class TestValidateParamRanges:
    """Tests for the validate_param_ranges function."""

    def test_valid_dca_params(self):
        errors = validate_param_ranges("dca", {
            "amount": 1000,
            "frequency": "monthly",
        })
        assert errors == []

    def test_valid_momentum_params(self):
        errors = validate_param_ranges("momentum", {
            "lookback_months": 6,
            "top_n": 3,
            "rebalance_freq": "monthly",
            "score_factor": "sharpe",
        })
        assert errors == []

    def test_valid_risk_parity_params(self):
        errors = validate_param_ranges("risk_parity", {
            "rebalance_freq": "monthly",
            "cov_method": "shrinkage",
            "lookback_days": 60,
        })
        assert errors == []

    def test_valid_mean_variance_params(self):
        errors = validate_param_ranges("mean_variance", {
            "rebalance_freq": "quarterly",
            "risk_free_rate": 0.03,
            "max_weight": 0.4,
        })
        assert errors == []

    def test_valid_timing_params(self):
        errors = validate_param_ranges("timing", {
            "method": "dual_ma",
            "fast_window": 5,
            "slow_window": 20,
        })
        assert errors == []

    def test_valid_fof_params(self):
        errors = validate_param_ranges("fof", {
            "factor_weights": {"sharpe": 0.4, "return": 0.3, "volatility": 0.3},
            "top_n": 5,
            "rebalance_freq": "monthly",
            "optimization": "risk_parity",
        })
        assert errors == []

    def test_invalid_strategy_type(self):
        errors = validate_param_ranges("unknown_type", {"foo": "bar"})
        assert len(errors) == 1
        assert "无效的策略类型" in errors[0]

    def test_missing_required_params(self):
        errors = validate_param_ranges("momentum", {
            "lookback_months": 6,
            # missing top_n and rebalance_freq
        })
        assert any("top_n" in e for e in errors)
        assert any("rebalance_freq" in e for e in errors)

    def test_param_below_min(self):
        errors = validate_param_ranges("momentum", {
            "lookback_months": 0,  # min is 1
            "top_n": 3,
            "rebalance_freq": "monthly",
        })
        assert any("小于最小值" in e for e in errors)

    def test_param_above_max(self):
        errors = validate_param_ranges("momentum", {
            "lookback_months": 100,  # max is 36
            "top_n": 3,
            "rebalance_freq": "monthly",
        })
        assert any("大于最大值" in e for e in errors)

    def test_invalid_enum_value(self):
        errors = validate_param_ranges("momentum", {
            "lookback_months": 6,
            "top_n": 3,
            "rebalance_freq": "daily",  # not in enum
        })
        assert any("不在允许范围内" in e for e in errors)

    def test_timing_fast_window_must_be_less_than_slow(self):
        errors = validate_param_ranges("timing", {
            "method": "dual_ma",
            "fast_window": 20,
            "slow_window": 5,  # fast >= slow
        })
        assert any("fast_window" in e and "slow_window" in e for e in errors)

    def test_fof_weights_sum_too_large(self):
        errors = validate_param_ranges("fof", {
            "factor_weights": {"a": 1.5, "b": 1.0},  # sum = 2.5 > 2.0
            "top_n": 5,
            "rebalance_freq": "monthly",
        })
        assert any("权重总和" in e for e in errors)

    def test_wrong_type_integer_expected(self):
        errors = validate_param_ranges("momentum", {
            "lookback_months": 6.5,  # should be integer
            "top_n": 3,
            "rebalance_freq": "monthly",
        })
        assert any("整数类型" in e for e in errors)

    def test_dca_amount_too_small(self):
        errors = validate_param_ranges("dca", {
            "amount": 10,  # min is 100
            "frequency": "monthly",
        })
        assert any("小于最小值" in e for e in errors)


# ---------------------------------------------------------------------------
# Tests: StrategyGenerator
# ---------------------------------------------------------------------------


class TestStrategyGenerator:
    """Tests for the StrategyGenerator use case class."""

    @pytest.mark.asyncio
    async def test_generate_valid_momentum_strategy(self):
        """LLM returns a valid momentum strategy config."""
        llm_response = {
            "strategy_type": "momentum",
            "name": "动量轮动Top3",
            "params": {
                "lookback_months": 6,
                "top_n": 3,
                "rebalance_freq": "monthly",
                "score_factor": "sharpe",
            },
            "universe": {
                "fund_codes": ["000001", "000002", "000003", "000004", "000005"],
            },
            "reasoning": "用户希望选择近期表现好的基金，动量策略最合适。",
        }
        mock_llm = make_mock_llm_service(llm_response)
        generator = StrategyGenerator(llm_service=mock_llm)

        result = await generator.generate("帮我做一个动量轮动策略，选最近半年表现最好的3只基金")

        assert result.is_valid is True
        assert result.strategy_type == "momentum"
        assert result.params["lookback_months"] == 6
        assert result.params["top_n"] == 3
        assert result.validation_errors == []
        mock_llm.call.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_valid_dca_strategy(self):
        """LLM returns a valid DCA strategy config."""
        llm_response = {
            "strategy_type": "dca",
            "name": "每月定投1000元",
            "params": {
                "amount": 1000,
                "frequency": "monthly",
                "dca_type": "fixed",
            },
            "universe": {
                "fund_codes": ["110011"],
            },
            "reasoning": "用户想要简单的定期定额投资。",
        }
        mock_llm = make_mock_llm_service(llm_response)
        generator = StrategyGenerator(llm_service=mock_llm)

        result = await generator.generate("每月定投1000元到易方达中小盘")

        assert result.is_valid is True
        assert result.strategy_type == "dca"
        assert result.params["amount"] == 1000
        assert result.name == "每月定投1000元"

    @pytest.mark.asyncio
    async def test_generate_invalid_params_detected(self):
        """LLM returns params that fail range validation."""
        llm_response = {
            "strategy_type": "momentum",
            "name": "无效策略",
            "params": {
                "lookback_months": 0,  # invalid: min is 1
                "top_n": 3,
                "rebalance_freq": "monthly",
            },
            "universe": {"fund_codes": []},
            "reasoning": "测试",
        }
        mock_llm = make_mock_llm_service(llm_response)
        generator = StrategyGenerator(llm_service=mock_llm)

        result = await generator.generate("做一个动量策略")

        assert result.is_valid is False
        assert len(result.validation_errors) > 0
        assert any("小于最小值" in e for e in result.validation_errors)

    @pytest.mark.asyncio
    async def test_generate_invalid_strategy_type(self):
        """LLM returns an invalid strategy type."""
        llm_response = {
            "strategy_type": "invalid_type",
            "name": "无效",
            "params": {},
            "universe": {},
            "reasoning": "测试",
        }
        mock_llm = make_mock_llm_service(llm_response)
        generator = StrategyGenerator(llm_service=mock_llm)

        result = await generator.generate("做一个策略")

        assert result.is_valid is False
        assert any("无效的策略类型" in e for e in result.validation_errors)

    @pytest.mark.asyncio
    async def test_generate_empty_description(self):
        """Empty description returns validation error without calling LLM."""
        mock_llm = make_mock_llm_service({})
        generator = StrategyGenerator(llm_service=mock_llm)

        result = await generator.generate("")

        assert result.is_valid is False
        assert any("不能为空" in e for e in result.validation_errors)
        mock_llm.call.assert_not_called()

    @pytest.mark.asyncio
    async def test_generate_non_dict_response(self):
        """LLM returns non-dict content (unexpected)."""
        mock_llm = AsyncMock()
        mock_llm.call = AsyncMock(
            return_value=FakeLLMResult(content="not a dict")
        )
        generator = StrategyGenerator(llm_service=mock_llm)

        result = await generator.generate("做一个策略")

        assert result.is_valid is False
        assert any("非结构化" in e for e in result.validation_errors)

    @pytest.mark.asyncio
    async def test_generate_risk_parity_strategy(self):
        """LLM returns a valid risk parity strategy."""
        llm_response = {
            "strategy_type": "risk_parity",
            "name": "风险平价组合",
            "params": {
                "rebalance_freq": "monthly",
                "cov_method": "shrinkage",
                "lookback_days": 60,
            },
            "universe": {
                "fund_codes": ["000001", "000002", "000003"],
                "description": "股债混合基金池",
            },
            "reasoning": "用户希望均衡风险配置。",
        }
        mock_llm = make_mock_llm_service(llm_response)
        generator = StrategyGenerator(llm_service=mock_llm)

        result = await generator.generate("帮我做一个风险平价策略，每月调仓")

        assert result.is_valid is True
        assert result.strategy_type == "risk_parity"
        assert result.universe.get("description") == "股债混合基金池"

    @pytest.mark.asyncio
    async def test_generate_timing_strategy(self):
        """LLM returns a valid timing strategy."""
        llm_response = {
            "strategy_type": "timing",
            "name": "双均线择时",
            "params": {
                "method": "dual_ma",
                "fast_window": 5,
                "slow_window": 20,
            },
            "universe": {"fund_codes": ["510300"]},
            "reasoning": "用户想用均线交叉做择时。",
        }
        mock_llm = make_mock_llm_service(llm_response)
        generator = StrategyGenerator(llm_service=mock_llm)

        result = await generator.generate("用5日和20日均线做择时")

        assert result.is_valid is True
        assert result.strategy_type == "timing"
        assert result.params["fast_window"] == 5
        assert result.params["slow_window"] == 20


# ---------------------------------------------------------------------------
# Tests: API endpoint
# ---------------------------------------------------------------------------


class TestStrategyGenAPI:
    """Tests for the POST /ai/strategy-gen API endpoint."""

    @pytest.fixture
    def client(self):
        """Create a test client with mocked strategy generator."""
        from fastapi.testclient import TestClient

        from app.core.config import Settings, get_settings
        from app.main import create_app

        settings = Settings(
            APP_ENV="test",
            DEBUG="true",
            LOG_LEVEL="WARNING",
            API_PREFIX="/api/v1",
            API_CORS_ORIGINS="http://localhost",
            DB_AUTO_MIGRATE="false",
            PROMETHEUS_ENABLED="false",
        )
        get_settings.cache_clear()
        app = create_app(settings)
        app.dependency_overrides[get_settings] = lambda: settings

        with TestClient(app) as tc:
            yield tc

        app.dependency_overrides.clear()
        get_settings.cache_clear()

    def test_strategy_gen_endpoint_success(self, client):
        """POST /ai/strategy-gen returns generated strategy config."""
        mock_result = StrategyGenResult(
            strategy_type="momentum",
            name="动量轮动",
            params={"lookback_months": 6, "top_n": 3, "rebalance_freq": "monthly"},
            universe={"fund_codes": ["000001", "000002"]},
            reasoning="动量策略适合选择近期表现好的基金",
            is_valid=True,
            validation_errors=[],
        )

        with patch(
            "app.api.v1.ai._get_strategy_generator"
        ) as mock_get_gen:
            mock_generator = AsyncMock()
            mock_generator.generate = AsyncMock(return_value=mock_result)
            mock_get_gen.return_value = mock_generator

            response = client.post(
                "/api/v1/ai/strategy-gen",
                json={"description": "做一个动量轮动策略"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["strategy_type"] == "momentum"
        assert data["is_valid"] is True
        assert data["params"]["lookback_months"] == 6

    def test_strategy_gen_endpoint_empty_description(self, client):
        """POST /ai/strategy-gen rejects empty description."""
        response = client.post(
            "/api/v1/ai/strategy-gen",
            json={"description": ""},
        )
        # Pydantic validation should reject min_length=1
        assert response.status_code == 422

    def test_strategy_gen_endpoint_budget_exhausted(self, client):
        """POST /ai/strategy-gen returns 429 when budget is exhausted."""
        from app.ai.service import BudgetExhaustedError

        with patch(
            "app.api.v1.ai._get_strategy_generator"
        ) as mock_get_gen:
            mock_generator = AsyncMock()
            mock_generator.generate = AsyncMock(
                side_effect=BudgetExhaustedError("strategy_gen")
            )
            mock_get_gen.return_value = mock_generator

            response = client.post(
                "/api/v1/ai/strategy-gen",
                json={"description": "做一个策略"},
            )

        assert response.status_code == 429

    def test_strategy_gen_endpoint_all_providers_failed(self, client):
        """POST /ai/strategy-gen returns 503 when all providers fail."""
        from app.ai.service import AllProvidersFailedError

        with patch(
            "app.api.v1.ai._get_strategy_generator"
        ) as mock_get_gen:
            mock_generator = AsyncMock()
            mock_generator.generate = AsyncMock(
                side_effect=AllProvidersFailedError(
                    "strategy_gen", ["provider1: timeout"]
                )
            )
            mock_get_gen.return_value = mock_generator

            response = client.post(
                "/api/v1/ai/strategy-gen",
                json={"description": "做一个策略"},
            )

        assert response.status_code == 503

    def test_strategy_gen_endpoint_validation_errors_returned(self, client):
        """POST /ai/strategy-gen returns validation errors in response."""
        mock_result = StrategyGenResult(
            strategy_type="momentum",
            name="无效策略",
            params={"lookback_months": 0, "top_n": 3, "rebalance_freq": "monthly"},
            universe={},
            reasoning="测试",
            is_valid=False,
            validation_errors=["参数 lookback_months 的值 0 小于最小值 1"],
        )

        with patch(
            "app.api.v1.ai._get_strategy_generator"
        ) as mock_get_gen:
            mock_generator = AsyncMock()
            mock_generator.generate = AsyncMock(return_value=mock_result)
            mock_get_gen.return_value = mock_generator

            response = client.post(
                "/api/v1/ai/strategy-gen",
                json={"description": "做一个动量策略"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["is_valid"] is False
        assert len(data["validation_errors"]) > 0
