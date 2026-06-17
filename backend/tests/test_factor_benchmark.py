"""Unit tests for benchmark-related factors.

Covers:
- beta: Beta coefficient, rolling mode, edge cases
- tracking_error: Tracking error, rolling mode, edge cases
- r_squared: R-squared, rolling mode, edge cases
- up_capture: Up capture ratio, rolling mode, edge cases
- down_capture: Down capture ratio, rolling mode, edge cases

Tests include edge cases, determinism, and boundary conditions.

Satisfies requirement 3.4.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.domain.factors.registry import _restore_registry, _snapshot_registry

import app.domain.factors.benchmark as bench_mod  # noqa: F401
from app.domain.factors.benchmark import (
    beta,
    down_capture,
    r_squared,
    tracking_error,
    up_capture,
)


@pytest.fixture(autouse=True)
def _ensure_registry_clean():
    """Snapshot registry before test, restore after to avoid cross-pollution."""
    snapshot = _snapshot_registry()
    yield
    _restore_registry(snapshot)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_nav(values: list[float], start_date: str = "2020-01-01") -> pd.Series:
    """Create a NAV series with business-day frequency."""
    dates = pd.bdate_range(start=start_date, periods=len(values))
    return pd.Series(values, index=dates, name="nav")


def _make_nav_from_returns(
    daily_returns: list[float], start_nav: float = 1.0, start_date: str = "2020-01-01"
) -> pd.Series:
    """Create a NAV series from a list of daily returns."""
    navs = [start_nav]
    for r in daily_returns:
        navs.append(navs[-1] * (1 + r))
    dates = pd.bdate_range(start=start_date, periods=len(navs))
    return pd.Series(navs, index=dates, name="nav")


# ===========================================================================
# beta
# ===========================================================================


class TestBeta:
    """Tests for the beta factor."""

    def test_beta_close_to_one_for_correlated(self):
        """Fund that tracks benchmark closely → beta ≈ 1."""
        np.random.seed(42)
        bench_returns = np.random.normal(0.0005, 0.01, 500)
        # Fund = benchmark + small noise
        noise = np.random.normal(0, 0.002, 500)
        fund_returns = bench_returns + noise
        fund_nav = _make_nav_from_returns(fund_returns.tolist())
        bench_nav = _make_nav_from_returns(bench_returns.tolist())
        result = beta(fund_nav, benchmark_nav=bench_nav)
        assert result == pytest.approx(1.0, abs=0.1)

    def test_beta_greater_than_one(self):
        """Fund with higher systematic risk → beta > 1."""
        np.random.seed(42)
        bench_returns = np.random.normal(0.0005, 0.01, 500)
        beta_true = 1.5
        noise = np.random.normal(0, 0.003, 500)
        fund_returns = beta_true * bench_returns + noise
        fund_nav = _make_nav_from_returns(fund_returns.tolist())
        bench_nav = _make_nav_from_returns(bench_returns.tolist())
        result = beta(fund_nav, benchmark_nav=bench_nav)
        assert result == pytest.approx(beta_true, abs=0.15)

    def test_beta_less_than_one(self):
        """Defensive fund → beta < 1."""
        np.random.seed(42)
        bench_returns = np.random.normal(0.0005, 0.01, 500)
        beta_true = 0.5
        noise = np.random.normal(0, 0.003, 500)
        fund_returns = beta_true * bench_returns + noise
        fund_nav = _make_nav_from_returns(fund_returns.tolist())
        bench_nav = _make_nav_from_returns(bench_returns.tolist())
        result = beta(fund_nav, benchmark_nav=bench_nav)
        assert result == pytest.approx(beta_true, abs=0.15)

    def test_no_benchmark_returns_nan(self):
        """Without benchmark, returns NaN."""
        np.random.seed(42)
        daily_returns = np.random.normal(0.0005, 0.01, 100)
        nav = _make_nav_from_returns(daily_returns.tolist())
        result = beta(nav, benchmark_nav=None)
        assert np.isnan(result)

    def test_insufficient_data_returns_nan(self):
        """Fewer than 10 common data points → NaN."""
        nav = _make_nav([1.0, 1.01, 1.02, 1.03, 1.04])
        bench = _make_nav([1.0, 1.005, 1.01, 1.015, 1.02])
        result = beta(nav, benchmark_nav=bench)
        assert np.isnan(result)

    def test_empty_returns_nan(self):
        nav = pd.Series([], dtype=float)
        assert np.isnan(beta(nav))

    def test_none_returns_nan(self):
        assert np.isnan(beta(None))

    def test_single_point_returns_nan(self):
        nav = _make_nav([1.0])
        bench = _make_nav([1.0])
        assert np.isnan(beta(nav, benchmark_nav=bench))

    def test_constant_benchmark_returns_nan(self):
        """Constant benchmark (zero variance) → NaN."""
        np.random.seed(42)
        fund_returns = np.random.normal(0.001, 0.01, 100)
        fund_nav = _make_nav_from_returns(fund_returns.tolist())
        bench_nav = _make_nav([1.0] * 101)
        result = beta(fund_nav, benchmark_nav=bench_nav)
        assert np.isnan(result)

    def test_rolling_mode(self):
        """Rolling beta returns a Series."""
        np.random.seed(42)
        bench_returns = np.random.normal(0.0005, 0.01, 100)
        fund_returns = 1.2 * bench_returns + np.random.normal(0, 0.003, 100)
        fund_nav = _make_nav_from_returns(fund_returns.tolist())
        bench_nav = _make_nav_from_returns(bench_returns.tolist())
        result = beta(fund_nav, benchmark_nav=bench_nav, window=30)
        assert isinstance(result, pd.Series)
        # First 29 values should be NaN (window=30)
        assert result.iloc[:29].isna().all()
        # Values after window should be valid
        valid_values = result.iloc[30:]
        assert not valid_values.isna().all()

    def test_rolling_mode_empty(self):
        """Rolling mode with empty series returns empty Series."""
        nav = pd.Series([], dtype=float)
        result = beta(nav, window=20)
        assert isinstance(result, pd.Series)
        assert len(result) == 0


# ===========================================================================
# tracking_error
# ===========================================================================


class TestTrackingError:
    """Tests for the tracking_error factor."""

    def test_positive_tracking_error(self):
        """Fund with excess return volatility → positive tracking error."""
        np.random.seed(42)
        bench_returns = np.random.normal(0.0005, 0.01, 500)
        fund_returns = bench_returns + np.random.normal(0, 0.005, 500)
        fund_nav = _make_nav_from_returns(fund_returns.tolist())
        bench_nav = _make_nav_from_returns(bench_returns.tolist())
        result = tracking_error(fund_nav, benchmark_nav=bench_nav, freq=252)
        assert result > 0
        # Annualized TE should be roughly 0.005 * sqrt(252) ≈ 7.9%
        assert result == pytest.approx(0.079, abs=0.02)

    def test_identical_nav_zero_te(self):
        """If fund = benchmark, tracking error = 0."""
        np.random.seed(42)
        daily_returns = np.random.normal(0.0005, 0.01, 100)
        nav = _make_nav_from_returns(daily_returns.tolist())
        result = tracking_error(nav, benchmark_nav=nav, freq=252)
        assert result == pytest.approx(0.0, abs=1e-10)

    def test_no_benchmark_returns_nan(self):
        """Without benchmark, returns NaN."""
        np.random.seed(42)
        daily_returns = np.random.normal(0.0005, 0.01, 100)
        nav = _make_nav_from_returns(daily_returns.tolist())
        result = tracking_error(nav, benchmark_nav=None)
        assert np.isnan(result)

    def test_empty_returns_nan(self):
        nav = pd.Series([], dtype=float)
        assert np.isnan(tracking_error(nav))

    def test_none_returns_nan(self):
        assert np.isnan(tracking_error(None))

    def test_single_point_returns_nan(self):
        nav = _make_nav([1.0])
        bench = _make_nav([1.0])
        assert np.isnan(tracking_error(nav, benchmark_nav=bench))

    def test_rolling_mode(self):
        """Rolling tracking error returns a Series."""
        np.random.seed(42)
        bench_returns = np.random.normal(0.0005, 0.01, 100)
        fund_returns = bench_returns + np.random.normal(0, 0.005, 100)
        fund_nav = _make_nav_from_returns(fund_returns.tolist())
        bench_nav = _make_nav_from_returns(bench_returns.tolist())
        result = tracking_error(fund_nav, benchmark_nav=bench_nav, freq=252, window=20)
        assert isinstance(result, pd.Series)
        # First 19 values should be NaN
        assert result.iloc[:19].isna().all()
        # Values after window should be positive
        valid_values = result.dropna()
        assert (valid_values > 0).all()

    def test_different_length_series_aligned(self):
        """Fund and benchmark with different lengths are aligned on common dates."""
        np.random.seed(42)
        fund_returns = np.random.normal(0.001, 0.01, 100)
        bench_returns = np.random.normal(0.0005, 0.01, 80)
        fund_nav = _make_nav_from_returns(fund_returns.tolist(), start_date="2020-01-01")
        bench_nav = _make_nav_from_returns(
            bench_returns.tolist(), start_date="2020-01-15"
        )
        result = tracking_error(fund_nav, benchmark_nav=bench_nav, freq=252)
        assert not np.isnan(result)
        assert result > 0


# ===========================================================================
# r_squared
# ===========================================================================


class TestRSquared:
    """Tests for the r_squared factor."""

    def test_high_r_squared_for_correlated(self):
        """Fund highly correlated with benchmark → R² close to 1."""
        np.random.seed(42)
        bench_returns = np.random.normal(0.0005, 0.01, 500)
        # Fund = alpha + beta * benchmark + tiny noise
        fund_returns = 0.0001 + 1.1 * bench_returns + np.random.normal(0, 0.001, 500)
        fund_nav = _make_nav_from_returns(fund_returns.tolist())
        bench_nav = _make_nav_from_returns(bench_returns.tolist())
        result = r_squared(fund_nav, benchmark_nav=bench_nav)
        assert result > 0.9

    def test_low_r_squared_for_uncorrelated(self):
        """Uncorrelated fund and benchmark → low R²."""
        np.random.seed(42)
        fund_returns = np.random.normal(0.001, 0.01, 500)
        np.random.seed(123)
        bench_returns = np.random.normal(0.0005, 0.01, 500)
        fund_nav = _make_nav_from_returns(fund_returns.tolist())
        bench_nav = _make_nav_from_returns(bench_returns.tolist())
        result = r_squared(fund_nav, benchmark_nav=bench_nav)
        assert result < 0.1

    def test_r_squared_between_zero_and_one(self):
        """R² should be in [0, 1] for typical data."""
        np.random.seed(42)
        bench_returns = np.random.normal(0.0005, 0.01, 500)
        fund_returns = 0.5 * bench_returns + np.random.normal(0, 0.008, 500)
        fund_nav = _make_nav_from_returns(fund_returns.tolist())
        bench_nav = _make_nav_from_returns(bench_returns.tolist())
        result = r_squared(fund_nav, benchmark_nav=bench_nav)
        assert 0 <= result <= 1

    def test_no_benchmark_returns_nan(self):
        """Without benchmark, returns NaN."""
        np.random.seed(42)
        daily_returns = np.random.normal(0.0005, 0.01, 100)
        nav = _make_nav_from_returns(daily_returns.tolist())
        result = r_squared(nav, benchmark_nav=None)
        assert np.isnan(result)

    def test_insufficient_data_returns_nan(self):
        """Fewer than 10 common data points → NaN."""
        nav = _make_nav([1.0, 1.01, 1.02, 1.03, 1.04])
        bench = _make_nav([1.0, 1.005, 1.01, 1.015, 1.02])
        result = r_squared(nav, benchmark_nav=bench)
        assert np.isnan(result)

    def test_empty_returns_nan(self):
        nav = pd.Series([], dtype=float)
        assert np.isnan(r_squared(nav))

    def test_none_returns_nan(self):
        assert np.isnan(r_squared(None))

    def test_constant_benchmark_returns_nan(self):
        """Constant benchmark (zero variance) → NaN."""
        np.random.seed(42)
        fund_returns = np.random.normal(0.001, 0.01, 100)
        fund_nav = _make_nav_from_returns(fund_returns.tolist())
        bench_nav = _make_nav([1.0] * 101)
        result = r_squared(fund_nav, benchmark_nav=bench_nav)
        assert np.isnan(result)

    def test_rolling_mode(self):
        """Rolling R² returns a Series."""
        np.random.seed(42)
        bench_returns = np.random.normal(0.0005, 0.01, 100)
        fund_returns = 1.2 * bench_returns + np.random.normal(0, 0.003, 100)
        fund_nav = _make_nav_from_returns(fund_returns.tolist())
        bench_nav = _make_nav_from_returns(bench_returns.tolist())
        result = r_squared(fund_nav, benchmark_nav=bench_nav, window=30)
        assert isinstance(result, pd.Series)
        # First 29 values should be NaN
        assert result.iloc[:29].isna().all()
        # Valid values should be in [0, 1]
        valid_values = result.dropna()
        assert (valid_values >= 0).all()
        assert (valid_values <= 1).all()


# ===========================================================================
# up_capture
# ===========================================================================


class TestUpCapture:
    """Tests for the up_capture factor."""

    def test_up_capture_around_100_for_tracking(self):
        """Fund that tracks benchmark → up capture ≈ 100."""
        np.random.seed(42)
        bench_returns = np.random.normal(0.0005, 0.01, 500)
        # Fund = benchmark + tiny noise
        fund_returns = bench_returns + np.random.normal(0, 0.001, 500)
        fund_nav = _make_nav_from_returns(fund_returns.tolist())
        bench_nav = _make_nav_from_returns(bench_returns.tolist())
        result = up_capture(fund_nav, benchmark_nav=bench_nav)
        assert result == pytest.approx(100, abs=10)

    def test_up_capture_greater_than_100_for_aggressive(self):
        """Aggressive fund (beta > 1) → up capture > 100."""
        np.random.seed(42)
        bench_returns = np.random.normal(0.001, 0.01, 500)
        fund_returns = 1.5 * bench_returns + np.random.normal(0, 0.002, 500)
        fund_nav = _make_nav_from_returns(fund_returns.tolist())
        bench_nav = _make_nav_from_returns(bench_returns.tolist())
        result = up_capture(fund_nav, benchmark_nav=bench_nav)
        assert result > 100

    def test_up_capture_less_than_100_for_defensive(self):
        """Defensive fund (beta < 1) → up capture < 100."""
        np.random.seed(42)
        bench_returns = np.random.normal(0.001, 0.01, 500)
        fund_returns = 0.5 * bench_returns + np.random.normal(0, 0.002, 500)
        fund_nav = _make_nav_from_returns(fund_returns.tolist())
        bench_nav = _make_nav_from_returns(bench_returns.tolist())
        result = up_capture(fund_nav, benchmark_nav=bench_nav)
        assert result < 100

    def test_no_benchmark_returns_nan(self):
        """Without benchmark, returns NaN."""
        np.random.seed(42)
        daily_returns = np.random.normal(0.0005, 0.01, 100)
        nav = _make_nav_from_returns(daily_returns.tolist())
        result = up_capture(nav, benchmark_nav=None)
        assert np.isnan(result)

    def test_no_up_periods_returns_nan(self):
        """If benchmark never goes up, returns NaN."""
        # All negative benchmark returns
        bench_returns = [-0.01] * 50
        fund_returns = [-0.005] * 50
        fund_nav = _make_nav_from_returns(fund_returns)
        bench_nav = _make_nav_from_returns(bench_returns)
        result = up_capture(fund_nav, benchmark_nav=bench_nav)
        assert np.isnan(result)

    def test_empty_returns_nan(self):
        nav = pd.Series([], dtype=float)
        assert np.isnan(up_capture(nav))

    def test_none_returns_nan(self):
        assert np.isnan(up_capture(None))

    def test_rolling_mode(self):
        """Rolling up capture returns a Series."""
        np.random.seed(42)
        bench_returns = np.random.normal(0.001, 0.01, 100)
        fund_returns = 1.2 * bench_returns + np.random.normal(0, 0.003, 100)
        fund_nav = _make_nav_from_returns(fund_returns.tolist())
        bench_nav = _make_nav_from_returns(bench_returns.tolist())
        result = up_capture(fund_nav, benchmark_nav=bench_nav, window=30)
        assert isinstance(result, pd.Series)
        # First 29 values should be NaN
        assert result.iloc[:29].isna().all()


# ===========================================================================
# down_capture
# ===========================================================================


class TestDownCapture:
    """Tests for the down_capture factor."""

    def test_down_capture_around_100_for_tracking(self):
        """Fund that tracks benchmark → down capture ≈ 100."""
        np.random.seed(42)
        bench_returns = np.random.normal(-0.0005, 0.01, 500)
        # Fund = benchmark + tiny noise
        fund_returns = bench_returns + np.random.normal(0, 0.001, 500)
        fund_nav = _make_nav_from_returns(fund_returns.tolist())
        bench_nav = _make_nav_from_returns(bench_returns.tolist())
        result = down_capture(fund_nav, benchmark_nav=bench_nav)
        assert result == pytest.approx(100, abs=15)

    def test_down_capture_less_than_100_for_defensive(self):
        """Defensive fund loses less in down markets → down capture < 100."""
        np.random.seed(42)
        bench_returns = np.random.normal(0.0, 0.01, 500)
        # Fund has lower beta in down markets
        fund_returns = 0.5 * bench_returns + np.random.normal(0, 0.002, 500)
        fund_nav = _make_nav_from_returns(fund_returns.tolist())
        bench_nav = _make_nav_from_returns(bench_returns.tolist())
        result = down_capture(fund_nav, benchmark_nav=bench_nav)
        assert result < 100

    def test_down_capture_greater_than_100_for_aggressive(self):
        """Aggressive fund loses more in down markets → down capture > 100."""
        np.random.seed(42)
        bench_returns = np.random.normal(0.0, 0.01, 500)
        fund_returns = 1.5 * bench_returns + np.random.normal(0, 0.002, 500)
        fund_nav = _make_nav_from_returns(fund_returns.tolist())
        bench_nav = _make_nav_from_returns(bench_returns.tolist())
        result = down_capture(fund_nav, benchmark_nav=bench_nav)
        assert result > 100

    def test_no_benchmark_returns_nan(self):
        """Without benchmark, returns NaN."""
        np.random.seed(42)
        daily_returns = np.random.normal(0.0005, 0.01, 100)
        nav = _make_nav_from_returns(daily_returns.tolist())
        result = down_capture(nav, benchmark_nav=None)
        assert np.isnan(result)

    def test_no_down_periods_returns_nan(self):
        """If benchmark never goes down, returns NaN."""
        # All positive benchmark returns
        bench_returns = [0.01] * 50
        fund_returns = [0.015] * 50
        fund_nav = _make_nav_from_returns(fund_returns)
        bench_nav = _make_nav_from_returns(bench_returns)
        result = down_capture(fund_nav, benchmark_nav=bench_nav)
        assert np.isnan(result)

    def test_empty_returns_nan(self):
        nav = pd.Series([], dtype=float)
        assert np.isnan(down_capture(nav))

    def test_none_returns_nan(self):
        assert np.isnan(down_capture(None))

    def test_rolling_mode(self):
        """Rolling down capture returns a Series."""
        np.random.seed(42)
        bench_returns = np.random.normal(0.0, 0.01, 100)
        fund_returns = 0.8 * bench_returns + np.random.normal(0, 0.003, 100)
        fund_nav = _make_nav_from_returns(fund_returns.tolist())
        bench_nav = _make_nav_from_returns(bench_returns.tolist())
        result = down_capture(fund_nav, benchmark_nav=bench_nav, window=30)
        assert isinstance(result, pd.Series)
        # First 29 values should be NaN
        assert result.iloc[:29].isna().all()


# ===========================================================================
# Determinism tests
# ===========================================================================


class TestDeterminism:
    """Verify that all benchmark factors produce deterministic output (req 3.12)."""

    def test_all_factors_deterministic(self):
        np.random.seed(42)
        bench_returns = np.random.normal(0.0005, 0.01, 252)
        fund_returns = 1.1 * bench_returns + np.random.normal(0, 0.003, 252)
        fund_nav = _make_nav_from_returns(fund_returns.tolist())
        bench_nav = _make_nav_from_returns(bench_returns.tolist())

        # Run each factor twice and verify identical results
        assert beta(fund_nav, benchmark_nav=bench_nav) == beta(
            fund_nav, benchmark_nav=bench_nav
        )
        assert tracking_error(fund_nav, benchmark_nav=bench_nav) == tracking_error(
            fund_nav, benchmark_nav=bench_nav
        )
        assert r_squared(fund_nav, benchmark_nav=bench_nav) == r_squared(
            fund_nav, benchmark_nav=bench_nav
        )
        assert up_capture(fund_nav, benchmark_nav=bench_nav) == up_capture(
            fund_nav, benchmark_nav=bench_nav
        )
        assert down_capture(fund_nav, benchmark_nav=bench_nav) == down_capture(
            fund_nav, benchmark_nav=bench_nav
        )


# ===========================================================================
# Registration tests
# ===========================================================================


class TestRegistration:
    """Verify factors are properly registered in the factor registry."""

    def _reload_registry(self):
        """Re-import the module to trigger registration after cleanup."""
        import importlib

        from app.domain.factors.registry import _clear_registry

        _clear_registry()

        import app.domain.factors.benchmark as mod

        importlib.reload(mod)

    def test_beta_registered(self):
        from app.domain.factors.registry import get_factor

        self._reload_registry()
        f = get_factor("beta")
        assert f.name == "beta"
        assert f.category == "benchmark"

    def test_tracking_error_registered(self):
        from app.domain.factors.registry import get_factor

        self._reload_registry()
        f = get_factor("tracking_error")
        assert f.name == "tracking_error"
        assert f.category == "benchmark"

    def test_r_squared_registered(self):
        from app.domain.factors.registry import get_factor

        self._reload_registry()
        f = get_factor("r_squared")
        assert f.name == "r_squared"
        assert f.category == "benchmark"

    def test_up_capture_registered(self):
        from app.domain.factors.registry import get_factor

        self._reload_registry()
        f = get_factor("up_capture")
        assert f.name == "up_capture"
        assert f.category == "benchmark"

    def test_down_capture_registered(self):
        from app.domain.factors.registry import get_factor

        self._reload_registry()
        f = get_factor("down_capture")
        assert f.name == "down_capture"
        assert f.category == "benchmark"

    def test_list_benchmark_factors(self):
        from app.domain.factors.registry import list_factors

        self._reload_registry()
        factors = list_factors(category="benchmark")
        names = {f.name for f in factors}
        assert "beta" in names
        assert "tracking_error" in names
        assert "r_squared" in names
        assert "up_capture" in names
        assert "down_capture" in names
