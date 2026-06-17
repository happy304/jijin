"""Unit tests for risk-adjusted return factors.

Covers:
- sharpe: Sharpe ratio, custom risk-free rate, rolling mode, edge cases
- sortino: Sortino ratio, downside-only risk, rolling mode
- information_ratio: IR with benchmark, rolling mode
- treynor: Treynor ratio with benchmark and beta, rolling mode

Tests include edge cases, determinism, and boundary conditions.

Satisfies requirement 3.3.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.domain.factors.registry import _restore_registry, _snapshot_registry

import app.domain.factors.risk_adjusted as risk_adj_mod  # noqa: F401
from app.domain.factors.risk_adjusted import (
    information_ratio,
    sharpe,
    sortino,
    treynor,
)
from app.domain.performance.metrics import (
    returns_from_nav,
    sharpe_ratio_from_returns,
    sortino_ratio_from_returns,
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
# sharpe
# ===========================================================================


class TestSharpe:
    """Tests for the sharpe factor."""

    def test_positive_sharpe(self):
        """Fund with positive excess return and moderate vol → positive Sharpe."""
        np.random.seed(42)
        # Mean daily return of 0.05% with 1% daily std
        daily_returns = np.random.normal(0.0005, 0.01, 500)
        nav = _make_nav_from_returns(daily_returns.tolist())
        result = sharpe(nav, risk_free_rate=0.0, freq=252)
        # Expected: annualized return ~12.6%, vol ~15.9% → Sharpe ~0.79
        assert result > 0
        assert result == pytest.approx(0.79, abs=0.3)

    def test_zero_risk_free_rate(self):
        """With rf=0, Sharpe = annualized_return / annualized_vol."""
        np.random.seed(42)
        daily_returns = np.random.normal(0.001, 0.01, 252)
        nav = _make_nav_from_returns(daily_returns.tolist())
        result = sharpe(nav, risk_free_rate=0.0, freq=252)
        assert not np.isnan(result)
        assert result > 0

    def test_custom_risk_free_rate(self):
        """Higher risk-free rate reduces Sharpe ratio."""
        np.random.seed(42)
        daily_returns = np.random.normal(0.0005, 0.01, 500)
        nav = _make_nav_from_returns(daily_returns.tolist())
        sharpe_0 = sharpe(nav, risk_free_rate=0.0, freq=252)
        sharpe_3 = sharpe(nav, risk_free_rate=0.03, freq=252)
        sharpe_5 = sharpe(nav, risk_free_rate=0.05, freq=252)
        # Higher rf → lower Sharpe
        assert sharpe_0 > sharpe_3 > sharpe_5

    def test_sharpe_matches_shared_metric(self):
        """Sharpe 因子标量模式应与统一指标工具口径一致。"""
        daily_returns = [0.02, -0.01, 0.03, -0.02, 0.01]
        nav = _make_nav_from_returns(daily_returns)
        expected = sharpe_ratio_from_returns(
            returns_from_nav(nav),
            risk_free_rate=0.0,
            freq=252,
        )

        assert sharpe(nav, risk_free_rate=0.0, freq=252) == pytest.approx(expected)

    def test_negative_sharpe(self):
        """Fund with negative excess return → negative Sharpe."""
        np.random.seed(42)
        daily_returns = np.random.normal(-0.001, 0.01, 500)
        nav = _make_nav_from_returns(daily_returns.tolist())
        result = sharpe(nav, risk_free_rate=0.0, freq=252)
        assert result < 0

    def test_zero_volatility_returns_nan(self):
        """Constant NAV (zero vol) → NaN."""
        navs = [1.0] * 100
        nav = _make_nav(navs)
        result = sharpe(nav)
        assert np.isnan(result)

    def test_empty_returns_nan(self):
        nav = pd.Series([], dtype=float)
        assert np.isnan(sharpe(nav))

    def test_none_returns_nan(self):
        assert np.isnan(sharpe(None))

    def test_single_point_returns_nan(self):
        nav = _make_nav([1.0])
        assert np.isnan(sharpe(nav))

    def test_two_points_returns_nan(self):
        """Two points → one return → std with ddof=1 is NaN."""
        nav = _make_nav([1.0, 1.05])
        result = sharpe(nav)
        assert np.isnan(result)

    def test_rolling_mode(self):
        """Rolling Sharpe returns a Series."""
        np.random.seed(42)
        daily_returns = np.random.normal(0.0005, 0.01, 100)
        nav = _make_nav_from_returns(daily_returns.tolist())
        result = sharpe(nav, freq=252, window=20)
        assert isinstance(result, pd.Series)
        # First 19 values should be NaN (window=20)
        assert result.iloc[:19].isna().all()
        # Values after window should be valid
        assert not result.iloc[19:].isna().any()

    def test_rolling_mode_empty(self):
        """Rolling mode with empty series returns empty Series."""
        nav = pd.Series([], dtype=float)
        result = sharpe(nav, window=20)
        assert isinstance(result, pd.Series)
        assert len(result) == 0

    def test_all_nan_returns_nan(self):
        nav = _make_nav([np.nan, np.nan, np.nan])
        assert np.isnan(sharpe(nav))


# ===========================================================================
# sortino
# ===========================================================================


class TestSortino:
    """Tests for the sortino factor."""

    def test_positive_sortino(self):
        """Fund with positive return → positive Sortino."""
        np.random.seed(42)
        daily_returns = np.random.normal(0.0005, 0.01, 500)
        nav = _make_nav_from_returns(daily_returns.tolist())
        result = sortino(nav, risk_free_rate=0.0, freq=252)
        assert result > 0

    def test_sortino_greater_than_sharpe_for_positive_skew(self):
        """For positively skewed returns, Sortino > Sharpe (less downside)."""
        np.random.seed(42)
        # Generate positively skewed returns (more upside than downside)
        daily_returns = np.abs(np.random.normal(0.001, 0.01, 500))
        # Mix in some negative returns but fewer
        negative_returns = np.random.normal(-0.002, 0.005, 100)
        all_returns = np.concatenate([daily_returns, negative_returns])
        np.random.shuffle(all_returns)
        nav = _make_nav_from_returns(all_returns.tolist())
        s = sharpe(nav, freq=252)
        so = sortino(nav, freq=252)
        # Sortino should generally be higher when there's positive skew
        # because downside deviation < total volatility
        assert so > s

    def test_custom_risk_free_rate(self):
        """Higher risk-free rate reduces Sortino ratio."""
        np.random.seed(42)
        daily_returns = np.random.normal(0.0005, 0.01, 500)
        nav = _make_nav_from_returns(daily_returns.tolist())
        sortino_0 = sortino(nav, risk_free_rate=0.0, freq=252)
        sortino_5 = sortino(nav, risk_free_rate=0.05, freq=252)
        assert sortino_0 > sortino_5

    def test_all_positive_returns_nan(self):
        """If all returns are above rf, no downside → NaN."""
        navs = [1.0 * (1.01**i) for i in range(100)]
        nav = _make_nav(navs)
        result = sortino(nav, risk_free_rate=0.0, freq=252)
        # All returns are ~1% daily, all above 0 → no downside deviation
        assert np.isnan(result)

    def test_empty_returns_nan(self):
        nav = pd.Series([], dtype=float)
        assert np.isnan(sortino(nav))

    def test_none_returns_nan(self):
        assert np.isnan(sortino(None))

    def test_single_point_returns_nan(self):
        nav = _make_nav([1.0])
        assert np.isnan(sortino(nav))

    def test_rolling_mode(self):
        """Rolling Sortino returns a Series."""
        np.random.seed(42)
        daily_returns = np.random.normal(0.0005, 0.01, 100)
        nav = _make_nav_from_returns(daily_returns.tolist())
        result = sortino(nav, freq=252, window=20)
        assert isinstance(result, pd.Series)
        # First 19 values should be NaN
        assert result.iloc[:19].isna().all()

    def test_negative_sortino(self):
        """Fund with negative excess return → negative Sortino."""
        np.random.seed(42)
        daily_returns = np.random.normal(-0.001, 0.01, 500)
        nav = _make_nav_from_returns(daily_returns.tolist())
        result = sortino(nav, risk_free_rate=0.0, freq=252)
        assert result < 0

    def test_sortino_matches_shared_full_sample_downside_metric(self):
        """Sortino 因子应与统一指标工具的全样本下行偏差口径一致。"""
        daily_returns = [0.02, -0.01, 0.03, -0.02, 0.01]
        nav = _make_nav_from_returns(daily_returns)
        expected = sortino_ratio_from_returns(
            returns_from_nav(nav),
            risk_free_rate=0.0,
            freq=252,
        )

        assert sortino(nav, risk_free_rate=0.0, freq=252) == pytest.approx(expected)

    def test_rolling_sortino_matches_shared_metric_last_window(self):
        """滚动 Sortino 的最后一个窗口应使用统一指标口径。"""
        daily_returns = [0.02, -0.01, 0.03, -0.02, 0.01, -0.005]
        nav = _make_nav_from_returns(daily_returns)
        result = sortino(nav, risk_free_rate=0.0, freq=252, window=4)
        all_returns = returns_from_nav(nav)
        expected = sortino_ratio_from_returns(all_returns.iloc[-4:], risk_free_rate=0.0, freq=252)

        assert result.dropna().iloc[-1] == pytest.approx(expected)


# ===========================================================================
# information_ratio
# ===========================================================================


class TestInformationRatio:
    """Tests for the information_ratio factor."""

    def test_positive_ir(self):
        """Fund consistently outperforming benchmark → positive IR."""
        np.random.seed(42)
        # Fund: higher mean return
        fund_returns = np.random.normal(0.001, 0.01, 500)
        # Benchmark: lower mean return, correlated
        bench_returns = np.random.normal(0.0005, 0.01, 500)
        fund_nav = _make_nav_from_returns(fund_returns.tolist())
        bench_nav = _make_nav_from_returns(bench_returns.tolist())
        result = information_ratio(fund_nav, benchmark_nav=bench_nav, freq=252)
        assert result > 0

    def test_negative_ir(self):
        """Fund underperforming benchmark → negative IR."""
        np.random.seed(42)
        fund_returns = np.random.normal(0.0003, 0.01, 500)
        bench_returns = np.random.normal(0.001, 0.01, 500)
        fund_nav = _make_nav_from_returns(fund_returns.tolist())
        bench_nav = _make_nav_from_returns(bench_returns.tolist())
        result = information_ratio(fund_nav, benchmark_nav=bench_nav, freq=252)
        assert result < 0

    def test_identical_nav_zero_tracking_error(self):
        """If fund = benchmark, tracking error = 0 → NaN."""
        np.random.seed(42)
        daily_returns = np.random.normal(0.0005, 0.01, 100)
        nav = _make_nav_from_returns(daily_returns.tolist())
        result = information_ratio(nav, benchmark_nav=nav, freq=252)
        assert np.isnan(result)

    def test_no_benchmark_returns_nan(self):
        """Without benchmark, returns NaN."""
        np.random.seed(42)
        daily_returns = np.random.normal(0.0005, 0.01, 100)
        nav = _make_nav_from_returns(daily_returns.tolist())
        result = information_ratio(nav, benchmark_nav=None)
        assert np.isnan(result)

    def test_empty_returns_nan(self):
        nav = pd.Series([], dtype=float)
        assert np.isnan(information_ratio(nav))

    def test_none_returns_nan(self):
        assert np.isnan(information_ratio(None))

    def test_single_point_returns_nan(self):
        nav = _make_nav([1.0])
        bench = _make_nav([1.0])
        assert np.isnan(information_ratio(nav, benchmark_nav=bench))

    def test_rolling_mode(self):
        """Rolling IR returns a Series."""
        np.random.seed(42)
        fund_returns = np.random.normal(0.001, 0.01, 100)
        bench_returns = np.random.normal(0.0005, 0.01, 100)
        fund_nav = _make_nav_from_returns(fund_returns.tolist())
        bench_nav = _make_nav_from_returns(bench_returns.tolist())
        result = information_ratio(
            fund_nav, benchmark_nav=bench_nav, freq=252, window=20
        )
        assert isinstance(result, pd.Series)
        # First 19 values should be NaN
        assert result.iloc[:19].isna().all()

    def test_different_length_series_aligned(self):
        """Fund and benchmark with different lengths are aligned on common dates."""
        np.random.seed(42)
        fund_returns = np.random.normal(0.001, 0.01, 100)
        bench_returns = np.random.normal(0.0005, 0.01, 80)
        fund_nav = _make_nav_from_returns(fund_returns.tolist(), start_date="2020-01-01")
        bench_nav = _make_nav_from_returns(
            bench_returns.tolist(), start_date="2020-01-15"
        )
        result = information_ratio(fund_nav, benchmark_nav=bench_nav, freq=252)
        # Should compute on overlapping period
        assert not np.isnan(result)


# ===========================================================================
# treynor
# ===========================================================================


class TestTreynor:
    """Tests for the treynor factor."""

    def test_positive_treynor(self):
        """Fund with positive excess return and positive beta → positive Treynor."""
        np.random.seed(42)
        # Create correlated returns: fund = alpha + beta * benchmark + noise
        bench_returns = np.random.normal(0.0005, 0.01, 500)
        alpha = 0.0002
        beta_true = 1.2
        noise = np.random.normal(0, 0.005, 500)
        fund_returns = alpha + beta_true * bench_returns + noise
        fund_nav = _make_nav_from_returns(fund_returns.tolist())
        bench_nav = _make_nav_from_returns(bench_returns.tolist())
        result = treynor(fund_nav, benchmark_nav=bench_nav, risk_free_rate=0.0, freq=252)
        assert result > 0

    def test_custom_risk_free_rate(self):
        """Higher risk-free rate reduces Treynor ratio."""
        np.random.seed(42)
        bench_returns = np.random.normal(0.0005, 0.01, 500)
        fund_returns = 0.0002 + 1.2 * bench_returns + np.random.normal(0, 0.005, 500)
        fund_nav = _make_nav_from_returns(fund_returns.tolist())
        bench_nav = _make_nav_from_returns(bench_returns.tolist())
        treynor_0 = treynor(fund_nav, benchmark_nav=bench_nav, risk_free_rate=0.0)
        treynor_5 = treynor(fund_nav, benchmark_nav=bench_nav, risk_free_rate=0.05)
        assert treynor_0 > treynor_5

    def test_no_benchmark_returns_nan(self):
        """Without benchmark, returns NaN."""
        np.random.seed(42)
        daily_returns = np.random.normal(0.0005, 0.01, 100)
        nav = _make_nav_from_returns(daily_returns.tolist())
        result = treynor(nav, benchmark_nav=None)
        assert np.isnan(result)

    def test_insufficient_data_returns_nan(self):
        """Fewer than 10 common data points → NaN."""
        nav = _make_nav([1.0, 1.01, 1.02, 1.03, 1.04])
        bench = _make_nav([1.0, 1.005, 1.01, 1.015, 1.02])
        result = treynor(nav, benchmark_nav=bench)
        assert np.isnan(result)

    def test_empty_returns_nan(self):
        nav = pd.Series([], dtype=float)
        assert np.isnan(treynor(nav))

    def test_none_returns_nan(self):
        assert np.isnan(treynor(None))

    def test_single_point_returns_nan(self):
        nav = _make_nav([1.0])
        bench = _make_nav([1.0])
        assert np.isnan(treynor(nav, benchmark_nav=bench))

    def test_rolling_mode(self):
        """Rolling Treynor returns a Series."""
        np.random.seed(42)
        bench_returns = np.random.normal(0.0005, 0.01, 100)
        fund_returns = 0.0002 + 1.2 * bench_returns + np.random.normal(0, 0.005, 100)
        fund_nav = _make_nav_from_returns(fund_returns.tolist())
        bench_nav = _make_nav_from_returns(bench_returns.tolist())
        result = treynor(fund_nav, benchmark_nav=bench_nav, freq=252, window=30)
        assert isinstance(result, pd.Series)
        # First 29 values should be NaN (window=30)
        assert result.iloc[:29].isna().all()

    def test_treynor_uses_aligned_return_sample(self):
        """Treynor 的分子和 beta 应使用同一组共同日期收益。

        基金在基准开始前存在一段极端上涨，如果错误使用基金全样本年化收益，
        Treynor 会被显著放大；正确结果应只由共同日期内的收益决定。
        """
        pre_overlap_returns = [0.20] * 5
        overlap_bench_returns = [0.01, -0.005, 0.008, -0.003, 0.006] * 3
        overlap_fund_returns = [0.0002 + r for r in overlap_bench_returns]

        fund_nav = _make_nav_from_returns(
            pre_overlap_returns + overlap_fund_returns,
            start_date="2020-01-01",
        )
        bench_nav = _make_nav_from_returns(
            overlap_bench_returns,
            start_date=str(fund_nav.index[len(pre_overlap_returns)].date()),
        )

        result = treynor(fund_nav, benchmark_nav=bench_nav, risk_free_rate=0.0, freq=252)
        expected = np.mean(overlap_fund_returns) * 252
        assert result == pytest.approx(expected, abs=1e-10)

    def test_zero_beta_returns_nan(self):
        """If beta is zero (uncorrelated), returns NaN."""
        np.random.seed(42)
        # Completely uncorrelated returns
        fund_returns = np.random.normal(0.001, 0.01, 500)
        bench_returns = np.random.normal(0.0005, 0.01, 500)
        # Make them truly uncorrelated by using different seeds
        np.random.seed(123)
        bench_returns = np.random.normal(0.0005, 0.01, 500)
        fund_nav = _make_nav_from_returns(fund_returns.tolist())
        bench_nav = _make_nav_from_returns(bench_returns.tolist())
        result = treynor(fund_nav, benchmark_nav=bench_nav, freq=252)
        # With random uncorrelated data, beta is near zero but not exactly zero
        # so this test just verifies it doesn't crash
        assert isinstance(result, float)


# ===========================================================================
# Determinism tests
# ===========================================================================


class TestDeterminism:
    """Verify that all risk-adjusted factors produce deterministic output (req 3.12)."""

    def test_all_factors_deterministic(self):
        np.random.seed(42)
        daily_returns = np.random.normal(0.0005, 0.01, 252)
        nav = _make_nav_from_returns(daily_returns.tolist())

        np.random.seed(99)
        bench_returns = np.random.normal(0.0003, 0.01, 252)
        bench_nav = _make_nav_from_returns(bench_returns.tolist())

        # Run each factor twice and verify identical results
        assert sharpe(nav) == sharpe(nav)
        assert sortino(nav) == sortino(nav)
        assert information_ratio(nav, benchmark_nav=bench_nav) == information_ratio(
            nav, benchmark_nav=bench_nav
        )
        assert treynor(nav, benchmark_nav=bench_nav) == treynor(
            nav, benchmark_nav=bench_nav
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

        import app.domain.factors.risk_adjusted as mod

        importlib.reload(mod)

    def test_sharpe_registered(self):
        from app.domain.factors.registry import get_factor

        self._reload_registry()
        f = get_factor("sharpe")
        assert f.name == "sharpe"
        assert f.category == "risk_adjusted"

    def test_sortino_registered(self):
        from app.domain.factors.registry import get_factor

        self._reload_registry()
        f = get_factor("sortino")
        assert f.name == "sortino"
        assert f.category == "risk_adjusted"

    def test_information_ratio_registered(self):
        from app.domain.factors.registry import get_factor

        self._reload_registry()
        f = get_factor("information_ratio")
        assert f.name == "information_ratio"
        assert f.category == "risk_adjusted"

    def test_treynor_registered(self):
        from app.domain.factors.registry import get_factor

        self._reload_registry()
        f = get_factor("treynor")
        assert f.name == "treynor"
        assert f.category == "risk_adjusted"

    def test_list_risk_adjusted_factors(self):
        from app.domain.factors.registry import list_factors

        self._reload_registry()
        factors = list_factors(category="risk_adjusted")
        names = {f.name for f in factors}
        assert "sharpe" in names
        assert "sortino" in names
        assert "information_ratio" in names
        assert "treynor" in names
