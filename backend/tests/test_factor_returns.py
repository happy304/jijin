"""Unit tests for return-category factors.

Covers:
- total_return: basic computation, edge cases (empty, single point, zero start)
- annualized_return: CAGR correctness, short series
- excess_return: overlapping alignment, missing benchmark
- jensen_alpha: regression-based alpha, insufficient data

Also includes "对拍" (cross-validation) tests against known fund values
to verify accuracy within 0.01% tolerance.

Satisfies requirements 3.1, 3.10, 3.12.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.domain.factors.registry import _restore_registry, _snapshot_registry

# Import the module to trigger registration, then access via registry
import app.domain.factors.returns as returns_mod  # noqa: F401
from app.domain.factors.returns import (
    annualized_return,
    excess_return,
    jensen_alpha,
    total_return,
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
# total_return
# ===========================================================================


class TestTotalReturn:
    """Tests for the total_return factor."""

    def test_basic_positive_return(self):
        nav = _make_nav([1.0, 1.05, 1.10, 1.15])
        result = total_return(nav)
        assert result == pytest.approx(0.15, abs=1e-10)

    def test_basic_negative_return(self):
        nav = _make_nav([1.0, 0.95, 0.90, 0.85])
        result = total_return(nav)
        assert result == pytest.approx(-0.15, abs=1e-10)

    def test_zero_return(self):
        nav = _make_nav([1.0, 1.1, 0.9, 1.0])
        result = total_return(nav)
        assert result == pytest.approx(0.0, abs=1e-10)

    def test_empty_series_returns_nan(self):
        nav = pd.Series([], dtype=float)
        assert np.isnan(total_return(nav))

    def test_single_point_returns_nan(self):
        nav = _make_nav([1.0])
        assert np.isnan(total_return(nav))

    def test_none_returns_nan(self):
        assert np.isnan(total_return(None))

    def test_all_nan_returns_nan(self):
        nav = _make_nav([np.nan, np.nan, np.nan])
        assert np.isnan(total_return(nav))

    def test_zero_start_returns_nan(self):
        nav = _make_nav([0.0, 1.0, 1.1])
        assert np.isnan(total_return(nav))

    def test_nan_values_are_dropped(self):
        """NaN values in the middle should be ignored."""
        nav = _make_nav([1.0, np.nan, 1.1, np.nan, 1.2])
        result = total_return(nav)
        # After dropping NaN: [1.0, 1.1, 1.2] → return = 0.2
        assert result == pytest.approx(0.2, abs=1e-10)

    def test_large_return(self):
        nav = _make_nav([1.0, 5.0])
        result = total_return(nav)
        assert result == pytest.approx(4.0, abs=1e-10)


# ===========================================================================
# annualized_return
# ===========================================================================


class TestAnnualizedReturn:
    """Tests for the annualized_return factor."""

    def test_one_year_daily_data(self):
        """253 data points = 252 intervals (1 year) with 10% total return."""
        total_ret = 0.10
        n_intervals = 252
        daily_ret = (1 + total_ret) ** (1.0 / n_intervals) - 1
        navs = [1.0]
        for _ in range(n_intervals):
            navs.append(navs[-1] * (1 + daily_ret))
        nav = _make_nav(navs)
        result = annualized_return(nav, freq=252)
        assert result == pytest.approx(0.10, abs=1e-4)

    def test_two_years_data(self):
        """505 data points = 504 intervals (2 years) with 21% total return."""
        total_ret = 0.21  # (1.1)^2 - 1 = 0.21
        n_intervals = 504
        daily_ret = (1 + total_ret) ** (1.0 / n_intervals) - 1
        navs = [1.0]
        for _ in range(n_intervals):
            navs.append(navs[-1] * (1 + daily_ret))
        nav = _make_nav(navs)
        result = annualized_return(nav, freq=252)
        assert result == pytest.approx(0.10, abs=1e-4)

    def test_empty_returns_nan(self):
        nav = pd.Series([], dtype=float)
        assert np.isnan(annualized_return(nav))

    def test_single_point_returns_nan(self):
        nav = _make_nav([1.0])
        assert np.isnan(annualized_return(nav))

    def test_none_returns_nan(self):
        assert np.isnan(annualized_return(None))

    def test_zero_start_returns_nan(self):
        nav = _make_nav([0.0, 1.0, 1.1])
        assert np.isnan(annualized_return(nav))

    def test_weekly_frequency(self):
        """53 data points = 52 intervals (1 year) with 10% total return."""
        total_ret = 0.10
        n_intervals = 52
        weekly_ret = (1 + total_ret) ** (1.0 / n_intervals) - 1
        navs = [1.0]
        for _ in range(n_intervals):
            navs.append(navs[-1] * (1 + weekly_ret))
        nav = _make_nav(navs)
        result = annualized_return(nav, freq=52)
        assert result == pytest.approx(0.10, abs=1e-4)

    def test_negative_total_return(self):
        """Fund lost 20% over 1 year (253 points = 252 intervals)."""
        total_ret = -0.20
        n_intervals = 252
        daily_ret = (1 + total_ret) ** (1.0 / n_intervals) - 1
        navs = [1.0]
        for _ in range(n_intervals):
            navs.append(navs[-1] * (1 + daily_ret))
        nav = _make_nav(navs)
        result = annualized_return(nav, freq=252)
        assert result == pytest.approx(-0.20, abs=1e-4)


# ===========================================================================
# excess_return
# ===========================================================================


class TestExcessReturn:
    """Tests for the excess_return factor."""

    def test_basic_excess(self):
        fund_nav = _make_nav([1.0, 1.05, 1.10, 1.15])
        bench_nav = _make_nav([1.0, 1.02, 1.04, 1.06])
        result = excess_return(fund_nav, bench_nav)
        # Fund: 15%, Bench: 6% → Excess: 9%
        assert result == pytest.approx(0.09, abs=1e-10)

    def test_negative_excess(self):
        fund_nav = _make_nav([1.0, 1.01, 1.02, 1.03])
        bench_nav = _make_nav([1.0, 1.05, 1.10, 1.15])
        result = excess_return(fund_nav, bench_nav)
        # Fund: 3%, Bench: 15% → Excess: -12%
        assert result == pytest.approx(-0.12, abs=1e-10)

    def test_no_benchmark_returns_nan(self):
        fund_nav = _make_nav([1.0, 1.1, 1.2])
        assert np.isnan(excess_return(fund_nav, None))

    def test_empty_fund_returns_nan(self):
        bench_nav = _make_nav([1.0, 1.1, 1.2])
        assert np.isnan(excess_return(pd.Series([], dtype=float), bench_nav))

    def test_empty_benchmark_returns_nan(self):
        fund_nav = _make_nav([1.0, 1.1, 1.2])
        assert np.isnan(excess_return(fund_nav, pd.Series([], dtype=float)))

    def test_partial_overlap(self):
        """Fund and benchmark have different date ranges — use exact common dates."""
        fund_dates = pd.bdate_range("2020-01-01", periods=10)
        bench_dates = pd.bdate_range("2020-01-06", periods=10)

        fund_nav = pd.Series(np.linspace(1.0, 1.1, 10), index=fund_dates)
        bench_nav = pd.Series(np.linspace(2.0, 2.1, 10), index=bench_dates)

        result = excess_return(fund_nav, bench_nav)
        common_idx = fund_nav.index.intersection(bench_nav.index)
        expected = (
            fund_nav.loc[common_idx].iloc[-1] / fund_nav.loc[common_idx].iloc[0] - 1
        ) - (
            bench_nav.loc[common_idx].iloc[-1] / bench_nav.loc[common_idx].iloc[0] - 1
        )
        assert result == pytest.approx(expected, abs=1e-12)

    def test_requires_exact_common_dates_not_only_common_range(self):
        """共同日期不足时不应按相近日期强行计算超额收益。"""
        fund_dates = pd.to_datetime(["2020-01-01", "2020-01-03", "2020-01-05"])
        bench_dates = pd.to_datetime(["2020-01-01", "2020-01-02", "2020-01-04"])
        fund_nav = pd.Series([1.0, 1.1, 1.2], index=fund_dates)
        bench_nav = pd.Series([1.0, 1.05, 1.1], index=bench_dates)

        assert np.isnan(excess_return(fund_nav, bench_nav))

    def test_none_fund_returns_nan(self):
        bench_nav = _make_nav([1.0, 1.1, 1.2])
        assert np.isnan(excess_return(None, bench_nav))


# ===========================================================================
# jensen_alpha
# ===========================================================================


class TestJensenAlpha:
    """Tests for the jensen_alpha factor."""

    def test_zero_alpha_when_fund_tracks_benchmark(self):
        """If fund perfectly tracks benchmark, alpha should be ~0."""
        np.random.seed(42)
        bench_returns = np.random.normal(0.0004, 0.01, 252)
        # Fund = benchmark (beta=1, alpha=0)
        fund_nav = _make_nav_from_returns(bench_returns.tolist())
        bench_nav = _make_nav_from_returns(bench_returns.tolist())
        result = jensen_alpha(fund_nav, bench_nav, risk_free_rate=0.0)
        assert result == pytest.approx(0.0, abs=0.01)

    def test_positive_alpha(self):
        """Fund consistently outperforms benchmark → positive alpha."""
        np.random.seed(42)
        bench_returns = np.random.normal(0.0004, 0.01, 252)
        # Fund = benchmark + daily alpha of 0.0002 (~5% annualized)
        fund_returns = bench_returns + 0.0002
        fund_nav = _make_nav_from_returns(fund_returns.tolist())
        bench_nav = _make_nav_from_returns(bench_returns.tolist())
        result = jensen_alpha(fund_nav, bench_nav, risk_free_rate=0.0)
        assert result > 0.03  # Should be around 5%

    def test_insufficient_data_returns_nan(self):
        """Less than 10 overlapping points → NaN."""
        fund_nav = _make_nav([1.0, 1.01, 1.02, 1.03, 1.04])
        bench_nav = _make_nav([1.0, 1.005, 1.01, 1.015, 1.02])
        result = jensen_alpha(fund_nav, bench_nav)
        assert np.isnan(result)

    def test_none_benchmark_returns_nan(self):
        fund_nav = _make_nav([1.0, 1.01, 1.02])
        assert np.isnan(jensen_alpha(fund_nav, None))

    def test_none_fund_returns_nan(self):
        bench_nav = _make_nav([1.0, 1.01, 1.02])
        assert np.isnan(jensen_alpha(None, bench_nav))

    def test_with_risk_free_rate(self):
        """Non-zero risk-free rate should shift alpha."""
        np.random.seed(42)
        bench_returns = np.random.normal(0.0004, 0.01, 252)
        fund_returns = bench_returns + 0.0002
        fund_nav = _make_nav_from_returns(fund_returns.tolist())
        bench_nav = _make_nav_from_returns(bench_returns.tolist())

        alpha_no_rf = jensen_alpha(fund_nav, bench_nav, risk_free_rate=0.0)
        alpha_with_rf = jensen_alpha(fund_nav, bench_nav, risk_free_rate=0.03)
        # Both should be positive; values may differ slightly
        assert alpha_no_rf > 0
        assert alpha_with_rf > 0


# ===========================================================================
# Cross-validation (对拍) tests
# ===========================================================================


class TestCrossValidation:
    """Cross-validation against known fund performance values.

    These tests simulate realistic NAV series and verify that computed
    factors match expected values within 0.01% tolerance.
    """

    def test_total_return_realistic_fund(self):
        """Simulate a fund with known total return.

        Example: Fund starts at NAV 1.0000, ends at 1.5832 after 3 years.
        Total return = 58.32%.
        """
        nav = _make_nav([1.0000, 1.5832])
        result = total_return(nav)
        expected = 0.5832
        assert abs(result - expected) < 0.0001  # < 0.01%

    def test_annualized_return_3_year_fund(self):
        """3 years of daily data, total return 58.32%.

        Annualized = (1.5832)^(1/3) - 1 ≈ 16.55%.
        We simulate 756 intervals (3 × 252) → 757 data points.
        """
        target_total = 0.5832
        n_intervals = 756  # 3 years × 252 trading days
        daily_ret = (1 + target_total) ** (1.0 / n_intervals) - 1
        navs = [1.0]
        for _ in range(n_intervals):
            navs.append(navs[-1] * (1 + daily_ret))
        nav = _make_nav(navs)

        result = annualized_return(nav, freq=252)
        expected = (1 + target_total) ** (1.0 / 3) - 1  # ~0.1655
        assert abs(result - expected) < 0.0001  # < 0.01%

    def test_annualized_return_1_year_fund(self):
        """1 year of daily data, total return 25.67%.

        Annualized should equal total return for exactly 1 year.
        252 intervals → 253 data points.
        """
        target_total = 0.2567
        n_intervals = 252
        daily_ret = (1 + target_total) ** (1.0 / n_intervals) - 1
        navs = [1.0]
        for _ in range(n_intervals):
            navs.append(navs[-1] * (1 + daily_ret))
        nav = _make_nav(navs)

        result = annualized_return(nav, freq=252)
        assert abs(result - target_total) < 0.0001  # < 0.01%

    def test_excess_return_realistic(self):
        """Fund returns 20%, benchmark returns 12% → excess 8%.

        252 intervals → 253 data points.
        """
        n_intervals = 252
        fund_daily = (1.20) ** (1.0 / n_intervals) - 1
        bench_daily = (1.12) ** (1.0 / n_intervals) - 1

        fund_navs = [1.0]
        bench_navs = [1.0]
        for _ in range(n_intervals):
            fund_navs.append(fund_navs[-1] * (1 + fund_daily))
            bench_navs.append(bench_navs[-1] * (1 + bench_daily))

        fund_nav = _make_nav(fund_navs)
        bench_nav = _make_nav(bench_navs)

        result = excess_return(fund_nav, bench_nav)
        expected = 0.20 - 0.12  # 8%
        assert abs(result - expected) < 0.0001  # < 0.01%

    def test_deterministic_output(self):
        """Same input must produce same output (requirement 3.12)."""
        nav = _make_nav([1.0, 1.05, 1.10, 1.08, 1.12, 1.15])
        r1 = total_return(nav)
        r2 = total_return(nav)
        assert r1 == r2

        a1 = annualized_return(nav)
        a2 = annualized_return(nav)
        assert a1 == a2
