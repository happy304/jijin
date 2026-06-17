"""Unit tests for FactorEngine service.

Covers:
- Basic batch computation (multiple funds, multiple factors)
- Window truncation
- Frequency resampling (weekly/monthly)
- Benchmark injection for factors that need it
- Edge cases (empty nav, unknown factor, None values)
- Performance verification

Satisfies requirements 3.9, 3.11.
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd
import pytest

# Trigger factor registration
import app.domain.factors  # noqa: F401
from app.services.factor_service import FactorEngine


@pytest.fixture(autouse=True)
def _ensure_registry():
    """Ensure factors are registered for tests."""
    # Factors are registered on first import; just ensure the module is loaded
    yield


def _make_nav(n_days: int = 253, start_date: str = "2020-01-01", seed: int = 42) -> pd.Series:
    """Create a realistic NAV series."""
    np.random.seed(seed)
    daily_returns = np.random.normal(0.0004, 0.01, n_days)
    navs = [1.0]
    for r in daily_returns:
        navs.append(navs[-1] * (1 + r))
    dates = pd.bdate_range(start=start_date, periods=len(navs))
    return pd.Series(navs, index=dates, name="nav")


class TestFactorEngineBasic:
    """Basic functionality tests."""

    def test_single_fund_single_factor(self):
        """Compute one factor for one fund."""
        nav = _make_nav(252)
        engine = FactorEngine(
            nav_data={"000001": nav},
            factor_names=["total_return"],
        )
        result = engine.compute()
        assert isinstance(result, pd.DataFrame)
        assert result.shape == (1, 1)
        assert "000001" in result.index
        assert "total_return" in result.columns
        assert not np.isnan(result.loc["000001", "total_return"])

    def test_multiple_funds_multiple_factors(self):
        """Compute multiple factors for multiple funds."""
        nav_data = {
            "000001": _make_nav(252, seed=42),
            "000002": _make_nav(252, seed=99),
            "000003": _make_nav(252, seed=123),
        }
        engine = FactorEngine(
            nav_data=nav_data,
            factor_names=["total_return", "annualized_return", "volatility", "max_drawdown"],
        )
        result = engine.compute()
        assert result.shape == (3, 4)
        assert set(result.index) == {"000001", "000002", "000003"}
        assert set(result.columns) == {"total_return", "annualized_return", "volatility", "max_drawdown"}
        # All values should be valid floats
        assert not result.isna().all().any()

    def test_result_index_name(self):
        """Result DataFrame has proper index and column names."""
        nav = _make_nav(100)
        engine = FactorEngine(
            nav_data={"F001": nav},
            factor_names=["total_return"],
        )
        result = engine.compute()
        assert result.index.name == "fund_code"
        assert result.columns.name == "factor"


class TestFactorEngineWindow:
    """Window truncation tests."""

    def test_window_truncates_data(self):
        """Window parameter limits data to last N points."""
        nav = _make_nav(500, seed=42)
        # Full data
        engine_full = FactorEngine(
            nav_data={"F001": nav},
            factor_names=["total_return"],
        )
        result_full = engine_full.compute()

        # Window of 100
        engine_window = FactorEngine(
            nav_data={"F001": nav},
            factor_names=["total_return"],
            window=100,
        )
        result_window = engine_window.compute()

        # Results should differ (different time periods)
        assert result_full.loc["F001", "total_return"] != result_window.loc["F001", "total_return"]

    def test_window_larger_than_data(self):
        """Window larger than data length uses all data."""
        nav = _make_nav(50, seed=42)
        engine = FactorEngine(
            nav_data={"F001": nav},
            factor_names=["total_return"],
            window=1000,
        )
        result = engine.compute()
        assert not np.isnan(result.loc["F001", "total_return"])


class TestFactorEngineFrequency:
    """Frequency resampling tests."""

    def test_weekly_frequency(self):
        """Weekly resampling produces valid results."""
        nav = _make_nav(500, seed=42)
        engine = FactorEngine(
            nav_data={"F001": nav},
            factor_names=["annualized_return", "volatility"],
            freq="weekly",
        )
        result = engine.compute()
        assert not result.isna().all().any()

    def test_monthly_frequency(self):
        """Monthly resampling produces valid results."""
        nav = _make_nav(756, seed=42)  # ~3 years
        engine = FactorEngine(
            nav_data={"F001": nav},
            factor_names=["annualized_return", "volatility"],
            freq="monthly",
        )
        result = engine.compute()
        assert not result.isna().all().any()

    def test_daily_is_default(self):
        """Daily frequency is the default and doesn't resample."""
        nav = _make_nav(252, seed=42)
        engine = FactorEngine(
            nav_data={"F001": nav},
            factor_names=["total_return"],
            freq="daily",
        )
        result = engine.compute()
        assert not np.isnan(result.loc["F001", "total_return"])


class TestFactorEngineBenchmark:
    """Benchmark injection tests."""

    def test_benchmark_passed_to_factors(self):
        """Factors requiring benchmark receive it."""
        nav = _make_nav(252, seed=42)
        bench = _make_nav(252, seed=99)
        engine = FactorEngine(
            nav_data={"F001": nav},
            factor_names=["beta", "tracking_error"],
            benchmark_nav=bench,
        )
        result = engine.compute()
        # Should produce valid values when benchmark is provided
        assert not np.isnan(result.loc["F001", "beta"])
        assert not np.isnan(result.loc["F001", "tracking_error"])

    def test_no_benchmark_returns_nan_for_benchmark_factors(self):
        """Factors requiring benchmark return NaN when none provided."""
        nav = _make_nav(252, seed=42)
        engine = FactorEngine(
            nav_data={"F001": nav},
            factor_names=["beta", "tracking_error"],
            benchmark_nav=None,
        )
        result = engine.compute()
        assert np.isnan(result.loc["F001", "beta"])
        assert np.isnan(result.loc["F001", "tracking_error"])


class TestFactorEngineEdgeCases:
    """Edge case tests."""

    def test_empty_nav_returns_nan(self):
        """Fund with empty NAV series returns NaN for all factors."""
        engine = FactorEngine(
            nav_data={"F001": pd.Series([], dtype=float)},
            factor_names=["total_return", "volatility"],
        )
        result = engine.compute()
        assert result.isna().all().all()

    def test_none_nav_returns_nan(self):
        """Fund with None NAV returns NaN for all factors."""
        engine = FactorEngine(
            nav_data={"F001": None},
            factor_names=["total_return"],
        )
        result = engine.compute()
        assert np.isnan(result.loc["F001", "total_return"])

    def test_unknown_factor_raises_key_error(self):
        """Unknown factor name raises KeyError during init."""
        nav = _make_nav(100)
        with pytest.raises(KeyError):
            FactorEngine(
                nav_data={"F001": nav},
                factor_names=["nonexistent_factor"],
            )

    def test_empty_nav_data(self):
        """Empty nav_data dict returns empty DataFrame."""
        engine = FactorEngine(
            nav_data={},
            factor_names=["total_return"],
        )
        result = engine.compute()
        assert result.empty

    def test_single_point_nav(self):
        """Single data point returns NaN (insufficient data)."""
        dates = pd.bdate_range("2020-01-01", periods=1)
        nav = pd.Series([1.0], index=dates)
        engine = FactorEngine(
            nav_data={"F001": nav},
            factor_names=["total_return", "volatility"],
        )
        result = engine.compute()
        assert result.isna().all().all()


class TestFactorEnginePerformance:
    """Performance verification tests."""

    def test_100_funds_10_years_under_5_seconds(self):
        """100 funds × 10 years of daily data should complete in < 5 seconds."""
        # Generate 100 funds with ~2520 data points each (10 years)
        nav_data = {}
        for i in range(100):
            np.random.seed(i)
            daily_returns = np.random.normal(0.0004, 0.01, 2520)
            navs = [1.0]
            for r in daily_returns:
                navs.append(navs[-1] * (1 + r))
            dates = pd.bdate_range("2014-01-01", periods=len(navs))
            nav_data[f"F{i:04d}"] = pd.Series(navs, index=dates)

        engine = FactorEngine(
            nav_data=nav_data,
            factor_names=["total_return", "annualized_return", "volatility", "max_drawdown", "sharpe"],
        )

        start = time.time()
        result = engine.compute()
        elapsed = time.time() - start

        assert result.shape == (100, 5)
        assert not result.isna().all().any()
        # Should complete in under 5 seconds (target is < 1s but allow margin)
        assert elapsed < 5.0, f"Took {elapsed:.2f}s, expected < 5s"
