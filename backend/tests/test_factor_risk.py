"""Unit tests for risk-category factors.

Covers:
- volatility: annualized vol, rolling mode, edge cases
- downside_deviation: semi-deviation below target, rolling mode
- max_drawdown: peak-to-trough, monotonic series, rolling mode
- calmar: ratio correctness, zero drawdown edge case
- var: historical VaR at 95%/99%, rolling mode
- cvar: expected shortfall, rolling mode

Tests include extreme values and boundary conditions.

Satisfies requirement 3.2.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.domain.factors.registry import _restore_registry, _snapshot_registry

import app.domain.factors.risk as risk_mod  # noqa: F401
from app.domain.factors.risk import (
    calmar,
    cvar,
    downside_deviation,
    max_drawdown,
    var,
    volatility,
)
from app.domain.performance.metrics import (
    historical_cvar,
    historical_var,
    returns_from_nav,
    rolling_max_drawdown_from_nav,
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
# volatility
# ===========================================================================


class TestVolatility:
    """Tests for the volatility factor."""

    def test_basic_volatility(self):
        """Known constant daily returns should give predictable vol."""
        # 253 points = 252 intervals, constant 1% daily return → vol = 0
        # Actually constant returns → std = 0 → vol = 0
        navs = [1.0 * (1.01**i) for i in range(253)]
        nav = _make_nav(navs)
        result = volatility(nav, freq=252)
        assert result == pytest.approx(0.0, abs=1e-10)

    def test_known_volatility(self):
        """Generate returns with known std, verify annualized vol."""
        np.random.seed(42)
        daily_std = 0.01  # 1% daily std
        daily_returns = np.random.normal(0.0005, daily_std, 500)
        nav = _make_nav_from_returns(daily_returns.tolist())
        result = volatility(nav, freq=252)
        expected_annual_vol = daily_std * np.sqrt(252)
        # Allow some sampling error
        assert result == pytest.approx(expected_annual_vol, rel=0.1)

    def test_empty_series_returns_nan(self):
        nav = pd.Series([], dtype=float)
        assert np.isnan(volatility(nav))

    def test_single_point_returns_nan(self):
        nav = _make_nav([1.0])
        assert np.isnan(volatility(nav))

    def test_none_returns_nan(self):
        assert np.isnan(volatility(None))

    def test_two_points(self):
        """Two points give one return — std of single value is NaN with ddof=1."""
        nav = _make_nav([1.0, 1.05])
        result = volatility(nav)
        # std of a single value with ddof=1 is NaN
        assert np.isnan(result)

    def test_three_points(self):
        """Three points give two returns — should compute valid vol."""
        nav = _make_nav([1.0, 1.01, 1.02])
        result = volatility(nav)
        assert not np.isnan(result)
        assert result >= 0

    def test_rolling_mode(self):
        """Rolling volatility returns a Series."""
        np.random.seed(42)
        daily_returns = np.random.normal(0.0005, 0.01, 100)
        nav = _make_nav_from_returns(daily_returns.tolist())
        result = volatility(nav, freq=252, window=20)
        assert isinstance(result, pd.Series)
        # First 19 values should be NaN (window=20 needs 20 returns)
        assert result.iloc[:19].isna().all()
        # Values after window should be valid
        assert not result.iloc[19:].isna().any()

    def test_rolling_mode_empty(self):
        """Rolling mode with empty series returns empty Series."""
        nav = pd.Series([], dtype=float)
        result = volatility(nav, window=20)
        assert isinstance(result, pd.Series)
        assert len(result) == 0

    def test_all_nan_returns_nan(self):
        nav = _make_nav([np.nan, np.nan, np.nan])
        assert np.isnan(volatility(nav))

    def test_weekly_frequency(self):
        """Verify annualization with weekly frequency."""
        np.random.seed(42)
        weekly_std = 0.02
        weekly_returns = np.random.normal(0.001, weekly_std, 104)
        nav = _make_nav_from_returns(weekly_returns.tolist())
        result = volatility(nav, freq=52)
        expected = weekly_std * np.sqrt(52)
        assert result == pytest.approx(expected, rel=0.15)


# ===========================================================================
# downside_deviation
# ===========================================================================


class TestDownsideDeviation:
    """Tests for the downside_deviation factor."""

    def test_all_positive_returns(self):
        """If all returns are above target, downside deviation is 0."""
        navs = [1.0 * (1.01**i) for i in range(100)]
        nav = _make_nav(navs)
        result = downside_deviation(nav, target=0.0)
        assert result == pytest.approx(0.0, abs=1e-10)

    def test_all_negative_returns(self):
        """All returns below target → downside deviation > 0."""
        navs = [1.0 * (0.99**i) for i in range(100)]
        nav = _make_nav(navs)
        result = downside_deviation(nav, target=0.0)
        assert result > 0

    def test_symmetric_returns(self):
        """Symmetric returns: downside deviation < full volatility."""
        np.random.seed(42)
        daily_returns = np.random.normal(0.0, 0.01, 500)
        nav = _make_nav_from_returns(daily_returns.tolist())
        vol = volatility(nav, freq=252)
        dd = downside_deviation(nav, target=0.0, freq=252)
        # Downside deviation should be less than full volatility
        # for symmetric distribution (roughly vol / sqrt(2))
        assert dd < vol
        assert dd > 0

    def test_custom_target(self):
        """Non-zero target changes the threshold."""
        np.random.seed(42)
        daily_returns = np.random.normal(0.001, 0.01, 500)
        nav = _make_nav_from_returns(daily_returns.tolist())
        dd_zero = downside_deviation(nav, target=0.0, freq=252)
        dd_positive = downside_deviation(nav, target=0.001, freq=252)
        # Higher target → more returns fall below → higher downside deviation
        assert dd_positive > dd_zero

    def test_empty_returns_nan(self):
        nav = pd.Series([], dtype=float)
        assert np.isnan(downside_deviation(nav))

    def test_none_returns_nan(self):
        assert np.isnan(downside_deviation(None))

    def test_single_point_returns_nan(self):
        nav = _make_nav([1.0])
        assert np.isnan(downside_deviation(nav))

    def test_rolling_mode(self):
        """Rolling downside deviation returns a Series."""
        np.random.seed(42)
        daily_returns = np.random.normal(0.0, 0.01, 100)
        nav = _make_nav_from_returns(daily_returns.tolist())
        result = downside_deviation(nav, freq=252, window=20)
        assert isinstance(result, pd.Series)
        # First values should be NaN
        assert result.iloc[:19].isna().all()


# ===========================================================================
# max_drawdown
# ===========================================================================


class TestMaxDrawdown:
    """Tests for the max_drawdown factor."""

    def test_basic_drawdown(self):
        """Simple peak-to-trough scenario."""
        nav = _make_nav([1.0, 1.2, 1.1, 0.9, 1.0, 1.1])
        result = max_drawdown(nav)
        # Peak at 1.2, trough at 0.9 → drawdown = (0.9 - 1.2) / 1.2 = -0.25
        assert result == pytest.approx(-0.25, abs=1e-10)

    def test_monotonically_increasing(self):
        """No drawdown if NAV only goes up."""
        nav = _make_nav([1.0, 1.1, 1.2, 1.3, 1.4])
        result = max_drawdown(nav)
        assert result == pytest.approx(0.0, abs=1e-10)

    def test_monotonically_decreasing(self):
        """Entire series is a drawdown from the first point."""
        nav = _make_nav([1.0, 0.9, 0.8, 0.7, 0.6])
        result = max_drawdown(nav)
        # Peak at 1.0, trough at 0.6 → drawdown = -0.4
        assert result == pytest.approx(-0.4, abs=1e-10)

    def test_total_loss(self):
        """NAV drops to near zero."""
        nav = _make_nav([1.0, 0.5, 0.1, 0.01])
        result = max_drawdown(nav)
        # Peak at 1.0, trough at 0.01 → drawdown = -0.99
        assert result == pytest.approx(-0.99, abs=1e-10)

    def test_empty_returns_nan(self):
        nav = pd.Series([], dtype=float)
        assert np.isnan(max_drawdown(nav))

    def test_single_point_returns_nan(self):
        nav = _make_nav([1.0])
        assert np.isnan(max_drawdown(nav))

    def test_none_returns_nan(self):
        assert np.isnan(max_drawdown(None))

    def test_multiple_drawdowns_picks_worst(self):
        """Multiple drawdowns — should return the worst one."""
        nav = _make_nav([1.0, 1.2, 1.0, 1.3, 0.9, 1.1])
        result = max_drawdown(nav)
        # First drawdown: 1.2 → 1.0 = -16.67%
        # Second drawdown: 1.3 → 0.9 = -30.77%
        expected = (0.9 - 1.3) / 1.3
        assert result == pytest.approx(expected, abs=1e-10)

    def test_rolling_mode(self):
        """Rolling max drawdown returns a Series."""
        nav = _make_nav([1.0, 1.1, 1.2, 1.0, 0.9, 1.1, 1.2, 1.3, 1.1, 1.0])
        result = max_drawdown(nav, window=5)
        assert isinstance(result, pd.Series)
        # First 4 values should be NaN (window=5)
        assert result.iloc[:4].isna().all()

    def test_rolling_mode_matches_shared_metric_helper(self):
        """滚动最大回撤因子应复用统一指标工具口径。"""
        nav = _make_nav([1.0, 1.1, 1.2, 1.0, 0.9, 1.1, 1.2, 1.3, 1.1, 1.0])

        result = max_drawdown(nav, window=5)
        expected = rolling_max_drawdown_from_nav(nav, window=5)

        pd.testing.assert_series_equal(result, expected)

    def test_nan_values_handled(self):
        """NaN values in NAV are dropped before computation."""
        nav = _make_nav([1.0, np.nan, 1.2, 1.1, np.nan, 0.9, 1.0])
        result = max_drawdown(nav)
        # After dropping NaN: [1.0, 1.2, 1.1, 0.9, 1.0]
        # Peak at 1.2, trough at 0.9 → -0.25
        assert result == pytest.approx(-0.25, abs=1e-10)


# ===========================================================================
# calmar
# ===========================================================================


class TestCalmar:
    """Tests for the calmar factor."""

    def test_basic_calmar(self):
        """Known annualized return and max drawdown."""
        # Create a series with known properties:
        # 1 year of data, 20% total return, 10% max drawdown
        n = 253  # 252 intervals = 1 year
        # Start at 1.0, go up to 1.2, with a 10% dip in the middle
        navs = list(np.linspace(1.0, 1.1, n // 2))
        # Add a 10% drawdown from peak
        peak = navs[-1]
        trough = peak * 0.9
        navs.extend(np.linspace(peak, trough, 10).tolist()[1:])
        # Recover and go up to 1.2
        remaining = n - len(navs)
        navs.extend(np.linspace(trough, 1.2, remaining).tolist()[1:])
        nav = _make_nav(navs)

        result = calmar(nav, freq=252)
        # Should be positive (positive return, negative drawdown)
        assert result > 0

    def test_zero_drawdown_returns_nan(self):
        """If max drawdown is 0 (monotonically increasing), return NaN."""
        nav = _make_nav([1.0, 1.1, 1.2, 1.3, 1.4])
        result = calmar(nav)
        assert np.isnan(result)

    def test_negative_return_negative_calmar(self):
        """Negative annualized return with drawdown → negative Calmar."""
        nav = _make_nav([1.0, 0.95, 0.90, 0.85, 0.80])
        result = calmar(nav)
        # Negative return / positive |drawdown| → negative
        assert result < 0

    def test_empty_returns_nan(self):
        nav = pd.Series([], dtype=float)
        assert np.isnan(calmar(nav))

    def test_none_returns_nan(self):
        assert np.isnan(calmar(None))

    def test_single_point_returns_nan(self):
        nav = _make_nav([1.0])
        assert np.isnan(calmar(nav))

    def test_rolling_mode(self):
        """Rolling Calmar returns a Series."""
        np.random.seed(42)
        daily_returns = np.random.normal(0.001, 0.02, 100)
        nav = _make_nav_from_returns(daily_returns.tolist())
        result = calmar(nav, freq=252, window=50)
        assert isinstance(result, pd.Series)


# ===========================================================================
# var (Value at Risk)
# ===========================================================================


class TestVaR:
    """Tests for the var factor."""

    def test_basic_var_95(self):
        """VaR at 95% confidence on known distribution."""
        np.random.seed(42)
        # Large sample from normal distribution
        daily_returns = np.random.normal(0.0, 0.01, 10000)
        nav = _make_nav_from_returns(daily_returns.tolist())
        result = var(nav, confidence=0.95)
        # For N(0, 0.01), 5th percentile ≈ -1.645 * 0.01，正损失 VaR ≈ 0.01645
        assert result > 0
        assert result == pytest.approx(0.01645, abs=0.002)

    def test_basic_var_99(self):
        """VaR at 99% confidence."""
        np.random.seed(42)
        daily_returns = np.random.normal(0.0, 0.01, 10000)
        nav = _make_nav_from_returns(daily_returns.tolist())
        result = var(nav, confidence=0.99)
        # For N(0, 0.01), 1st percentile ≈ -2.326 * 0.01，正损失 VaR ≈ 0.02326
        assert result > 0
        assert result == pytest.approx(0.02326, abs=0.002)

    def test_var_99_more_extreme_than_95(self):
        """99% VaR should be a larger positive loss than 95% VaR."""
        np.random.seed(42)
        daily_returns = np.random.normal(0.0, 0.01, 1000)
        nav = _make_nav_from_returns(daily_returns.tolist())
        var_95 = var(nav, confidence=0.95)
        var_99 = var(nav, confidence=0.99)
        assert var_99 > var_95

    def test_all_positive_returns(self):
        """If all returns are positive, VaR is still the lowest quantile."""
        navs = [1.0 * (1.01**i) for i in range(100)]
        nav = _make_nav(navs)
        result = var(nav, confidence=0.95)
        # All returns are ~1%，没有预期损失，正损失口径下 VaR 为 0。
        assert result == pytest.approx(0.0, abs=1e-10)

    def test_var_matches_shared_positive_loss_metric(self):
        """VaR 因子应与统一指标工具的正损失口径一致。"""
        daily_returns = [-0.05, -0.04, -0.03, -0.02, -0.01, 0, 0.01, 0.02, 0.03, 0.04]
        nav = _make_nav_from_returns(daily_returns)

        assert var(nav, confidence=0.90) == pytest.approx(
            historical_var(returns_from_nav(nav), confidence=0.90)
        )

    def test_empty_returns_nan(self):
        nav = pd.Series([], dtype=float)
        assert np.isnan(var(nav))

    def test_none_returns_nan(self):
        assert np.isnan(var(None))

    def test_single_point_returns_nan(self):
        nav = _make_nav([1.0])
        assert np.isnan(var(nav))

    def test_rolling_mode(self):
        """Rolling VaR returns a Series."""
        np.random.seed(42)
        daily_returns = np.random.normal(0.0, 0.01, 100)
        nav = _make_nav_from_returns(daily_returns.tolist())
        result = var(nav, confidence=0.95, window=50)
        assert isinstance(result, pd.Series)
        # First 49 values should be NaN
        assert result.iloc[:49].isna().all()


# ===========================================================================
# cvar (Conditional VaR / Expected Shortfall)
# ===========================================================================


class TestCVaR:
    """Tests for the cvar factor."""

    def test_cvar_more_extreme_than_var(self):
        """CVaR should be at least as large as VaR under positive-loss口径."""
        np.random.seed(42)
        daily_returns = np.random.normal(0.0, 0.01, 1000)
        nav = _make_nav_from_returns(daily_returns.tolist())
        var_95 = var(nav, confidence=0.95)
        cvar_95 = cvar(nav, confidence=0.95)
        assert cvar_95 >= var_95

    def test_basic_cvar_95(self):
        """CVaR at 95% on known distribution."""
        np.random.seed(42)
        daily_returns = np.random.normal(0.0, 0.01, 10000)
        nav = _make_nav_from_returns(daily_returns.tolist())
        result = cvar(nav, confidence=0.95)
        # CVaR should be a positive loss for symmetric distribution centered at 0
        assert result > 0

    def test_cvar_99_more_extreme_than_95(self):
        """99% CVaR should be a larger positive loss than 95% CVaR."""
        np.random.seed(42)
        daily_returns = np.random.normal(0.0, 0.01, 1000)
        nav = _make_nav_from_returns(daily_returns.tolist())
        cvar_95 = cvar(nav, confidence=0.95)
        cvar_99 = cvar(nav, confidence=0.99)
        assert cvar_99 > cvar_95

    def test_cvar_matches_shared_positive_loss_metric(self):
        """CVaR 因子应与统一指标工具的正损失口径一致。"""
        daily_returns = [-0.05, -0.04, -0.03, -0.02, -0.01, 0, 0.01, 0.02, 0.03, 0.04]
        nav = _make_nav_from_returns(daily_returns)

        assert cvar(nav, confidence=0.90) == pytest.approx(
            historical_cvar(returns_from_nav(nav), confidence=0.90)
        )

    def test_empty_returns_nan(self):
        nav = pd.Series([], dtype=float)
        assert np.isnan(cvar(nav))

    def test_none_returns_nan(self):
        assert np.isnan(cvar(None))

    def test_single_point_returns_nan(self):
        nav = _make_nav([1.0])
        assert np.isnan(cvar(nav))

    def test_rolling_mode(self):
        """Rolling CVaR returns a Series."""
        np.random.seed(42)
        daily_returns = np.random.normal(0.0, 0.01, 100)
        nav = _make_nav_from_returns(daily_returns.tolist())
        result = cvar(nav, confidence=0.95, window=50)
        assert isinstance(result, pd.Series)
        # First 49 values should be NaN
        assert result.iloc[:49].isna().all()

    def test_all_same_returns(self):
        """If all returns are the same, CVaR equals that return."""
        # Constant 1% daily return
        navs = [1.0 * (1.01**i) for i in range(100)]
        nav = _make_nav(navs)
        result = cvar(nav, confidence=0.95)
        # All returns are ~0.01，没有尾部损失，CVaR = VaR = 0。
        var_val = var(nav, confidence=0.95)
        assert result == pytest.approx(var_val, abs=1e-10)
        assert result == pytest.approx(0.0, abs=1e-10)


# ===========================================================================
# Determinism tests
# ===========================================================================


class TestDeterminism:
    """Verify that all risk factors produce deterministic output (req 3.12)."""

    def test_all_factors_deterministic(self):
        np.random.seed(42)
        daily_returns = np.random.normal(0.0005, 0.01, 252)
        nav = _make_nav_from_returns(daily_returns.tolist())

        # Run each factor twice and verify identical results
        assert volatility(nav) == volatility(nav)
        assert downside_deviation(nav) == downside_deviation(nav)
        assert max_drawdown(nav) == max_drawdown(nav)
        assert calmar(nav) == calmar(nav)
        assert var(nav) == var(nav)
        assert cvar(nav) == cvar(nav)


# ===========================================================================
# Extreme values and boundary tests
# ===========================================================================


class TestExtremeValues:
    """Tests for extreme and boundary conditions."""

    def test_very_large_nav_values(self):
        """NAV with very large values should still compute correctly."""
        nav = _make_nav([1e6, 1.1e6, 1.2e6, 1.0e6, 1.15e6])
        result = max_drawdown(nav)
        # Peak at 1.2e6, trough at 1.0e6 → -1/6 ≈ -0.1667
        expected = (1.0e6 - 1.2e6) / 1.2e6
        assert result == pytest.approx(expected, abs=1e-10)

    def test_very_small_nav_values(self):
        """NAV with very small values should still compute correctly."""
        nav = _make_nav([0.001, 0.0012, 0.0011, 0.0009, 0.0010])
        result = max_drawdown(nav)
        # Peak at 0.0012, trough at 0.0009 → -0.25
        assert result == pytest.approx(-0.25, abs=1e-10)

    def test_high_volatility_series(self):
        """Extremely volatile series should not crash."""
        np.random.seed(42)
        daily_returns = np.random.normal(0.0, 0.10, 100)  # 10% daily std!
        nav = _make_nav_from_returns(daily_returns.tolist())
        # Should not raise
        vol = volatility(nav)
        dd = downside_deviation(nav)
        mdd = max_drawdown(nav)
        v = var(nav)
        cv = cvar(nav)
        assert not np.isnan(vol)
        assert not np.isnan(dd)
        assert not np.isnan(mdd)
        assert not np.isnan(v)
        assert not np.isnan(cv)

    def test_two_points_max_drawdown(self):
        """Two points: if second < first, drawdown = (second - first) / first."""
        nav = _make_nav([1.0, 0.8])
        result = max_drawdown(nav)
        assert result == pytest.approx(-0.2, abs=1e-10)

    def test_two_points_no_drawdown(self):
        """Two points: if second > first, no drawdown."""
        nav = _make_nav([1.0, 1.2])
        result = max_drawdown(nav)
        assert result == pytest.approx(0.0, abs=1e-10)

    def test_var_with_few_data_points(self):
        """VaR with very few data points should still work."""
        nav = _make_nav([1.0, 1.01, 0.99, 1.02])
        result = var(nav, confidence=0.95)
        # 3 returns, 5th percentile is the minimum
        assert not np.isnan(result)

    def test_cvar_with_few_data_points(self):
        """CVaR with very few data points should still work."""
        nav = _make_nav([1.0, 1.01, 0.99, 1.02])
        result = cvar(nav, confidence=0.95)
        assert not np.isnan(result)
