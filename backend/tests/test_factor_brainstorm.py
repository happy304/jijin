"""Unit tests for the factor brainstorm use case.

Tests cover:
- DSL parser validation (valid/invalid expressions)
- FactorBrainstormer pipeline with mocked LLMService
- IC/IR validation flow (significant and insignificant factors)
- Factor registration for significant factors
- API endpoint response structure

Requirements: 11.20, 11.21, 11.22, 11.23
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ai.use_cases.factor_brainstorm import (
    AVAILABLE_FIELDS,
    WHITELISTED_FUNCTIONS,
    BrainstormResult,
    CandidateFactor,
    FactorBrainstormer,
    FactorSignificance,
    ICIRResult,
    ICIRValidator,
    is_valid_dsl,
    validate_dsl_expression,
)


# ---------------------------------------------------------------------------
# DSL Parser Tests
# ---------------------------------------------------------------------------


class TestDSLValidation:
    """Tests for the DSL expression validator."""

    def test_valid_simple_field(self):
        """A bare field reference should be valid."""
        errors = validate_dsl_expression("daily_return")
        assert errors == []

    def test_valid_arithmetic(self):
        """Basic arithmetic with fields should be valid."""
        errors = validate_dsl_expression("adj_nav + daily_return * 2")
        assert errors == []

    def test_valid_function_call(self):
        """Whitelisted function calls should be valid."""
        errors = validate_dsl_expression("rolling_mean(daily_return, 20)")
        assert errors == []

    def test_valid_nested_functions(self):
        """Nested whitelisted function calls should be valid."""
        expr = "rolling_mean(daily_return, 20) / rolling_std(daily_return, 20)"
        errors = validate_dsl_expression(expr)
        assert errors == []

    def test_valid_complex_expression(self):
        """Complex but valid DSL expression."""
        expr = "zscore(fund_size) * rank(daily_return)"
        errors = validate_dsl_expression(expr)
        assert errors == []

    def test_valid_diff_lag(self):
        """diff and lag functions should be valid."""
        expr = "diff(adj_nav, 5) / lag(adj_nav, 5)"
        errors = validate_dsl_expression(expr)
        assert errors == []

    def test_valid_abs_log_sqrt(self):
        """abs, log, sqrt should be valid."""
        expr = "abs(log(adj_nav)) + sqrt(fund_size)"
        errors = validate_dsl_expression(expr)
        assert errors == []

    def test_valid_max_min(self):
        """max and min functions should be valid."""
        expr = "max(daily_return, 0) - min(daily_return, 0)"
        errors = validate_dsl_expression(expr)
        assert errors == []

    def test_invalid_unknown_field(self):
        """Unknown field names should be rejected."""
        errors = validate_dsl_expression("unknown_field + daily_return")
        assert len(errors) > 0
        assert "unknown_field" in errors[0].lower() or "Unknown" in errors[0]

    def test_invalid_unknown_function(self):
        """Non-whitelisted functions should be rejected."""
        errors = validate_dsl_expression("pandas_rolling(daily_return, 20)")
        assert len(errors) > 0
        assert "pandas_rolling" in errors[0] or "not whitelisted" in errors[0]

    def test_invalid_import(self):
        """Import statements should be rejected (via syntax error in eval mode)."""
        errors = validate_dsl_expression("import os")
        assert len(errors) > 0

    def test_invalid_attribute_access(self):
        """Attribute access should be rejected."""
        errors = validate_dsl_expression("daily_return.mean()")
        assert len(errors) > 0

    def test_invalid_subscript(self):
        """Subscript/indexing should be rejected."""
        errors = validate_dsl_expression("daily_return[0]")
        assert len(errors) > 0

    def test_invalid_lambda(self):
        """Lambda expressions should be rejected."""
        errors = validate_dsl_expression("lambda x: x + 1")
        assert len(errors) > 0

    def test_invalid_empty_expression(self):
        """Empty expressions should be rejected."""
        errors = validate_dsl_expression("")
        assert len(errors) > 0

    def test_invalid_too_long(self):
        """Expressions exceeding 500 chars should be rejected."""
        expr = "daily_return + " * 100
        errors = validate_dsl_expression(expr)
        assert len(errors) > 0
        assert "too long" in errors[0].lower()

    def test_invalid_syntax_error(self):
        """Syntax errors should be caught."""
        errors = validate_dsl_expression("daily_return +")
        assert len(errors) > 0
        assert "syntax" in errors[0].lower()

    def test_is_valid_dsl_true(self):
        """is_valid_dsl returns True for valid expressions."""
        assert is_valid_dsl("rolling_mean(daily_return, 20)") is True

    def test_is_valid_dsl_false(self):
        """is_valid_dsl returns False for invalid expressions."""
        assert is_valid_dsl("import os") is False

    def test_all_whitelisted_functions_accepted(self):
        """Every whitelisted function should be accepted when called."""
        for func in WHITELISTED_FUNCTIONS:
            if func in ("abs", "log", "sqrt"):
                expr = f"{func}(daily_return)"
            elif func in ("max", "min"):
                expr = f"{func}(daily_return, 0)"
            elif func in ("rolling_mean", "rolling_std"):
                expr = f"{func}(daily_return, 20)"
            elif func in ("lag", "diff"):
                expr = f"{func}(daily_return, 5)"
            elif func in ("rank", "zscore"):
                expr = f"{func}(daily_return)"
            else:
                expr = f"{func}(daily_return)"
            errors = validate_dsl_expression(expr)
            assert errors == [], f"Function '{func}' should be valid but got: {errors}"

    def test_all_available_fields_accepted(self):
        """Every available field should be accepted as a bare reference."""
        for field_name in AVAILABLE_FIELDS:
            errors = validate_dsl_expression(field_name)
            assert errors == [], f"Field '{field_name}' should be valid but got: {errors}"


# ---------------------------------------------------------------------------
# ICIRValidator Tests
# ---------------------------------------------------------------------------


class TestICIRValidator:
    """Tests for the IC/IR significance assessment."""

    def test_significant_factor(self):
        """Factor with high IC and IR should be significant."""
        validator = ICIRValidator(ic_threshold=0.03, ir_threshold=0.5)
        result = validator.assess_significance(ic_mean=0.05, ir=0.8)
        assert result == FactorSignificance.SIGNIFICANT

    def test_insignificant_low_ic(self):
        """Factor with low IC should be insignificant."""
        validator = ICIRValidator(ic_threshold=0.03, ir_threshold=0.5)
        result = validator.assess_significance(ic_mean=0.01, ir=0.8)
        assert result == FactorSignificance.INSIGNIFICANT

    def test_insignificant_low_ir(self):
        """Factor with low IR should be insignificant."""
        validator = ICIRValidator(ic_threshold=0.03, ir_threshold=0.5)
        result = validator.assess_significance(ic_mean=0.05, ir=0.3)
        assert result == FactorSignificance.INSIGNIFICANT

    def test_negative_ic_significant(self):
        """Negative IC with high absolute value should be significant."""
        validator = ICIRValidator(ic_threshold=0.03, ir_threshold=0.5)
        result = validator.assess_significance(ic_mean=-0.05, ir=-0.8)
        assert result == FactorSignificance.SIGNIFICANT


# ---------------------------------------------------------------------------
# FactorBrainstormer Tests (with mocked LLMService)
# ---------------------------------------------------------------------------


class MockICIRValidator(ICIRValidator):
    """Mock IC/IR validator that returns configurable results."""

    def __init__(self, results: dict[str, ICIRResult] | None = None, **kwargs):
        super().__init__(**kwargs)
        self._results = results or {}

    async def validate(self, formula: str, name: str) -> ICIRResult:
        if name in self._results:
            return self._results[name]
        # Default: insignificant
        return ICIRResult(
            ic_mean=0.01,
            ic_std=0.05,
            ir=0.2,
            significance=FactorSignificance.INSIGNIFICANT,
            p_value=0.3,
        )


def _make_mock_llm_service(factors: list[dict[str, str]]) -> AsyncMock:
    """Create a mock LLMService that returns the given factors."""
    mock_service = AsyncMock()

    @dataclass
    class FakeLLMResult:
        content: dict[str, Any]
        cached: bool = False

    mock_service.call = AsyncMock(
        return_value=FakeLLMResult(content={"factors": factors})
    )
    return mock_service


@pytest.mark.asyncio
async def test_brainstorm_basic_flow():
    """Test the basic brainstorm pipeline with valid factors."""
    factors = [
        {
            "name": "momentum_5d",
            "formula": "diff(adj_nav, 5) / lag(adj_nav, 5)",
            "rationale": "5-day momentum captures short-term trends",
        },
        {
            "name": "rolling_sharpe",
            "formula": "rolling_mean(daily_return, 20) / rolling_std(daily_return, 20)",
            "rationale": "Rolling Sharpe ratio as quality signal",
        },
    ]
    mock_llm = _make_mock_llm_service(factors)
    brainstormer = FactorBrainstormer(mock_llm, auto_register=False)

    result = await brainstormer.brainstorm("动量因子在小盘基金中的表现")

    assert result.hypothesis == "动量因子在小盘基金中的表现"
    assert len(result.candidates) == 2
    assert result.valid_count == 2
    assert all(c.is_valid_dsl for c in result.candidates)
    assert all(c.dsl_errors == [] for c in result.candidates)


@pytest.mark.asyncio
async def test_brainstorm_with_invalid_dsl():
    """Test that invalid DSL expressions are flagged."""
    factors = [
        {
            "name": "valid_factor",
            "formula": "rolling_mean(daily_return, 20)",
            "rationale": "Valid factor",
        },
        {
            "name": "invalid_factor",
            "formula": "pandas.rolling_mean(daily_return, 20)",
            "rationale": "Uses disallowed attribute access",
        },
    ]
    mock_llm = _make_mock_llm_service(factors)
    brainstormer = FactorBrainstormer(mock_llm, auto_register=False)

    result = await brainstormer.brainstorm("测试无效DSL")

    assert len(result.candidates) == 2
    assert result.valid_count == 1
    assert result.candidates[0].is_valid_dsl is True
    assert result.candidates[1].is_valid_dsl is False
    assert len(result.candidates[1].dsl_errors) > 0


@pytest.mark.asyncio
async def test_brainstorm_with_ic_ir_validation():
    """Test IC/IR validation flow with significant and insignificant factors."""
    factors = [
        {
            "name": "good_factor",
            "formula": "zscore(fund_size) * rank(daily_return)",
            "rationale": "Size-momentum cross factor",
        },
        {
            "name": "weak_factor",
            "formula": "rolling_mean(daily_return, 60)",
            "rationale": "Simple moving average",
        },
    ]
    mock_llm = _make_mock_llm_service(factors)

    ic_ir_results = {
        "good_factor": ICIRResult(
            ic_mean=0.05,
            ic_std=0.03,
            ir=1.67,
            significance=FactorSignificance.SIGNIFICANT,
            p_value=0.001,
        ),
        "weak_factor": ICIRResult(
            ic_mean=0.01,
            ic_std=0.05,
            ir=0.2,
            significance=FactorSignificance.INSIGNIFICANT,
            p_value=0.3,
        ),
    }
    validator = MockICIRValidator(results=ic_ir_results)
    brainstormer = FactorBrainstormer(
        mock_llm, ic_ir_validator=validator, auto_register=False
    )

    result = await brainstormer.brainstorm("规模动量交叉因子")

    assert result.significant_count == 1
    assert result.candidates[0].ic_ir_result is not None
    assert result.candidates[0].ic_ir_result.significance == FactorSignificance.SIGNIFICANT
    assert result.candidates[1].ic_ir_result is not None
    assert result.candidates[1].ic_ir_result.significance == FactorSignificance.INSIGNIFICANT
    # Insignificant factor should be in experiment log
    assert len(result.experiment_log) == 1
    assert result.experiment_log[0].name == "weak_factor"


@pytest.mark.asyncio
async def test_brainstorm_auto_register():
    """Test that significant factors are registered when auto_register=True."""
    factors = [
        {
            "name": "alpha_factor",
            "formula": "diff(adj_nav, 20) / lag(adj_nav, 20)",
            "rationale": "20-day momentum",
        },
    ]
    mock_llm = _make_mock_llm_service(factors)

    ic_ir_results = {
        "alpha_factor": ICIRResult(
            ic_mean=0.06,
            ic_std=0.03,
            ir=2.0,
            significance=FactorSignificance.SIGNIFICANT,
            p_value=0.0001,
        ),
    }
    validator = MockICIRValidator(results=ic_ir_results)

    # Patch the registry dict inside the domain module that _register_factor imports
    with patch(
        "app.domain.factors.registry._FACTOR_REGISTRY", {}
    ) as mock_registry:
        brainstormer = FactorBrainstormer(
            mock_llm, ic_ir_validator=validator, auto_register=True
        )
        result = await brainstormer.brainstorm("动量因子")

        assert result.registered_count == 1
        assert result.candidates[0].registered is True
        # Verify the factor was added to the registry
        assert len(mock_registry) == 1
        registered_name = list(mock_registry.keys())[0]
        assert "alpha_factor" in registered_name


@pytest.mark.asyncio
async def test_brainstorm_empty_hypothesis_raises():
    """Test that empty hypothesis raises ValueError."""
    mock_llm = _make_mock_llm_service([])
    brainstormer = FactorBrainstormer(mock_llm)

    with pytest.raises(ValueError, match="cannot be empty"):
        await brainstormer.brainstorm("")


@pytest.mark.asyncio
async def test_brainstorm_llm_returns_non_dict():
    """Test graceful handling when LLM returns unexpected content type."""
    mock_service = AsyncMock()

    @dataclass
    class FakeLLMResult:
        content: str
        cached: bool = False

    mock_service.call = AsyncMock(
        return_value=FakeLLMResult(content="not a dict")
    )
    brainstormer = FactorBrainstormer(mock_service, auto_register=False)

    result = await brainstormer.brainstorm("测试异常返回")

    assert result.hypothesis == "测试异常返回"
    assert len(result.candidates) == 0


@pytest.mark.asyncio
async def test_brainstorm_ic_ir_error_handling():
    """Test that IC/IR validation errors are handled gracefully."""
    factors = [
        {
            "name": "error_factor",
            "formula": "rolling_mean(daily_return, 20)",
            "rationale": "Test error handling",
        },
    ]
    mock_llm = _make_mock_llm_service(factors)

    # Validator that raises an exception
    class ErrorValidator(ICIRValidator):
        async def validate(self, formula: str, name: str) -> ICIRResult:
            raise RuntimeError("Database connection failed")

    validator = ErrorValidator()
    brainstormer = FactorBrainstormer(
        mock_llm, ic_ir_validator=validator, auto_register=False
    )

    result = await brainstormer.brainstorm("测试错误处理")

    assert len(result.candidates) == 1
    assert result.candidates[0].ic_ir_result is not None
    assert result.candidates[0].ic_ir_result.significance == FactorSignificance.ERROR
    assert result.significant_count == 0


@pytest.mark.asyncio
async def test_brainstorm_no_ic_ir_validator():
    """Test pipeline without IC/IR validator (validation skipped)."""
    factors = [
        {
            "name": "test_factor",
            "formula": "rank(daily_return)",
            "rationale": "Simple rank factor",
        },
    ]
    mock_llm = _make_mock_llm_service(factors)
    brainstormer = FactorBrainstormer(mock_llm, ic_ir_validator=None, auto_register=False)

    result = await brainstormer.brainstorm("简单排名因子")

    assert len(result.candidates) == 1
    assert result.candidates[0].ic_ir_result is None
    assert result.significant_count == 0


# ---------------------------------------------------------------------------
# API Endpoint Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_factor_brainstorm_endpoint_exists():
    """Test that the factor-brainstorm endpoint is registered."""
    from httpx import ASGITransport, AsyncClient

    from app.main import create_app
    from app.core.config import Settings

    settings = Settings(
        app_env="test",
        database_url="sqlite+aiosqlite:///",
        redis_url="redis://localhost:6379/0",
        db_auto_migrate=False,
    )
    app = create_app(settings)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # The endpoint should exist (returns 501 without LLM config)
        response = await client.post(
            "/api/v1/ai/factor-brainstorm",
            json={"hypothesis": "动量因子在小盘基金中的表现"},
        )
        # 501 is expected since LLMService is not configured in test
        assert response.status_code == 501


@pytest.mark.asyncio
async def test_api_factor_brainstorm_validation():
    """Test request validation on the endpoint."""
    from httpx import ASGITransport, AsyncClient

    from app.main import create_app
    from app.core.config import Settings

    settings = Settings(
        app_env="test",
        database_url="sqlite+aiosqlite:///",
        redis_url="redis://localhost:6379/0",
        db_auto_migrate=False,
    )
    app = create_app(settings)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Too short hypothesis should fail validation
        response = await client.post(
            "/api/v1/ai/factor-brainstorm",
            json={"hypothesis": "ab"},
        )
        assert response.status_code == 422
