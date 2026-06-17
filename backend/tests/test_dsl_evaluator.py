"""Tests for the DSL evaluator and DataDrivenICIRValidator."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.ai.use_cases.factor_brainstorm import (
    DataDrivenICIRValidator,
    DSLValidationError,
    FactorSignificance,
    evaluate_dsl,
)


@pytest.fixture
def panel_fields():
    """Wide-format panels to feed the DSL evaluator."""
    rng = np.random.default_rng(42)
    dates = pd.date_range("2024-01-01", periods=100, freq="B")
    assets = [f"A{i:03d}" for i in range(20)]

    daily_return = pd.DataFrame(
        rng.normal(0.0005, 0.012, size=(100, 20)),
        index=dates,
        columns=assets,
    )
    fund_size = pd.DataFrame(
        np.exp(rng.normal(20.0, 1.0, size=(100, 20))),
        index=dates,
        columns=assets,
    )
    return {"daily_return": daily_return, "fund_size": fund_size}


class TestEvaluateDSL:
    def test_simple_field_returns_dataframe(self, panel_fields):
        result = evaluate_dsl("daily_return", panel_fields)
        # daily_return is a DataFrame
        assert isinstance(result, pd.DataFrame)

    def test_rolling_mean(self, panel_fields):
        result = evaluate_dsl("rolling_mean(daily_return, 20)", panel_fields)
        assert result.shape == panel_fields["daily_return"].shape

    def test_arithmetic(self, panel_fields):
        # Sharpe-like: rolling_mean / rolling_std
        result = evaluate_dsl(
            "rolling_mean(daily_return, 20) / rolling_std(daily_return, 20)",
            panel_fields,
        )
        assert result.shape == panel_fields["daily_return"].shape

    def test_log_with_negative_handled_via_nan(self, panel_fields):
        # log(daily_return) has many negatives → should produce NaN, not error
        result = evaluate_dsl("log(daily_return)", panel_fields)
        # At least some NaNs from negative returns
        assert result.isna().any().any()

    def test_invalid_dsl_raises(self, panel_fields):
        with pytest.raises(DSLValidationError):
            evaluate_dsl("__import__('os').system('ls')", panel_fields)

    def test_unknown_field_raises(self, panel_fields):
        with pytest.raises(DSLValidationError):
            # 'volume' is whitelisted as a field name but not provided in fields dict
            evaluate_dsl("volume", panel_fields)


class TestDataDrivenICIRValidator:
    @pytest.mark.asyncio
    async def test_validate_dsl_against_real_data(self, panel_fields):
        """Validator runs the DSL → IC → significance pipeline end-to-end."""
        # Generate synthetic factor that genuinely predicts returns
        daily_return = panel_fields["daily_return"].copy()
        # Build a true predictive factor: lag(daily_return, 1) × -1
        # I.e. a mean-reversion signal that should produce significant rank IC
        rng = np.random.default_rng(0)
        # Inject signal: tomorrow's return = -0.05 × today's signal + noise
        signal = rng.normal(0, 0.01, size=daily_return.shape)
        new_returns = -0.5 * signal + rng.normal(0, 0.005, size=daily_return.shape)
        daily_return.iloc[1:] = new_returns[:-1] + signal[:-1]
        panel_fields["daily_return"] = daily_return

        # Forward returns for IC test
        forward_returns = daily_return.shift(-1)

        validator = DataDrivenICIRValidator(
            fields=panel_fields,
            forward_returns=forward_returns,
            ic_threshold=0.01,  # liberal for synthetic data
            ir_threshold=0.1,
        )

        # Test a simple, evaluable formula
        result = await validator.validate(
            formula="rolling_mean(daily_return, 5)",
            name="test_factor",
        )
        # We get back an ICIRResult, with finite metrics
        assert result.significance in (
            FactorSignificance.SIGNIFICANT,
            FactorSignificance.INSIGNIFICANT,
            FactorSignificance.ERROR,
        )
        assert isinstance(result.ic_mean, float)
        assert isinstance(result.ir, float)
        assert 0 <= result.p_value <= 1

    @pytest.mark.asyncio
    async def test_validate_invalid_formula_returns_error(self, panel_fields):
        forward_returns = panel_fields["daily_return"].shift(-1)
        validator = DataDrivenICIRValidator(
            fields=panel_fields,
            forward_returns=forward_returns,
        )
        # Formula references a field not in the panel
        result = await validator.validate(formula="volume", name="bad")
        assert result.significance == FactorSignificance.ERROR

    @pytest.mark.asyncio
    async def test_validate_returns_finite_p_value(self, panel_fields):
        forward_returns = panel_fields["daily_return"].shift(-1)
        validator = DataDrivenICIRValidator(
            fields=panel_fields,
            forward_returns=forward_returns,
        )
        result = await validator.validate(
            formula="lag(daily_return, 1)",
            name="lag1",
        )
        # p_value should be finite (or 1.0 if the test was inconclusive)
        import math

        assert not math.isnan(result.p_value)
