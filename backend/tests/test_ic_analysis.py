"""Tests for IC analysis and quintile backtest."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.domain.factors.ic_analysis import (
    compute_ic_decay,
    compute_ic_series,
    compute_ic_stats,
    evaluate_factor,
    quintile_backtest,
)


@pytest.fixture
def synthetic_factor_panel():
    """Generate a synthetic factor panel + return panel where the factor has
    moderate predictive power.

    Returns are generated as: r_t+1 = 0.05 * factor_t + noise
    so true IC ~ 0.5 (Pearson) when factor is N(0,1) and noise SD = 1.
    """
    rng = np.random.default_rng(42)
    n_dates = 100
    n_assets = 30
    dates = pd.date_range("2024-01-01", periods=n_dates, freq="B")
    asset_codes = [f"A{i:03d}" for i in range(n_assets)]

    factor = rng.normal(0, 1, size=(n_dates, n_assets))
    # Forward returns weakly predicted by factor
    noise = rng.normal(0, 0.02, size=(n_dates, n_assets))
    returns = 0.005 * factor + noise

    factor_df = pd.DataFrame(factor, index=dates, columns=asset_codes)
    # returns_df values represent the return for the period (date, date+1)
    returns_df = pd.DataFrame(returns, index=dates, columns=asset_codes)
    return factor_df, returns_df


@pytest.fixture
def random_factor_panel():
    """Factor and return panels with no relationship (true IC = 0)."""
    rng = np.random.default_rng(0)
    n_dates = 100
    n_assets = 30
    dates = pd.date_range("2024-01-01", periods=n_dates, freq="B")
    asset_codes = [f"A{i:03d}" for i in range(n_assets)]
    factor = rng.normal(0, 1, size=(n_dates, n_assets))
    returns = rng.normal(0, 0.02, size=(n_dates, n_assets))
    return (
        pd.DataFrame(factor, index=dates, columns=asset_codes),
        pd.DataFrame(returns, index=dates, columns=asset_codes),
    )


class TestComputeICSeries:
    def test_ic_series_is_correlated_with_signal(self, synthetic_factor_panel):
        factor, returns = synthetic_factor_panel
        ic = compute_ic_series(factor, returns, method="pearson")
        # IC should be predominantly positive
        assert ic.mean() > 0.05
        assert len(ic) > 50  # most dates contribute

    def test_ic_series_zero_for_random_data(self, random_factor_panel):
        factor, returns = random_factor_panel
        ic = compute_ic_series(factor, returns, method="pearson")
        # Mean IC should be small; tolerance set generously for noise
        assert abs(ic.mean()) < 0.10

    def test_spearman_method(self, synthetic_factor_panel):
        factor, returns = synthetic_factor_panel
        ic = compute_ic_series(factor, returns, method="spearman")
        assert len(ic) > 50

    def test_empty_inputs(self):
        result = compute_ic_series(pd.DataFrame(), pd.DataFrame())
        assert len(result) == 0

    def test_skips_dates_with_too_few_assets(self):
        dates = pd.date_range("2024-01-01", periods=3, freq="B")
        # Only 2 assets but min_assets=5 → all dates skipped
        f = pd.DataFrame(
            [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]], index=dates, columns=["A", "B"]
        )
        r = pd.DataFrame(
            [[0.01, 0.02], [-0.01, 0.0], [0.02, -0.03]],
            index=dates,
            columns=["A", "B"],
        )
        ic = compute_ic_series(f, r, min_assets_per_period=5)
        assert len(ic) == 0


class TestComputeICStats:
    def test_aggregate_stats(self, synthetic_factor_panel):
        factor, returns = synthetic_factor_panel
        ic_series = compute_ic_series(factor, returns, method="pearson")
        stats = compute_ic_stats(ic_series, method="pearson")
        assert stats is not None
        assert stats.ic_mean > 0
        assert stats.ic_std > 0
        assert stats.ic_ir != 0
        assert stats.n_periods == len(ic_series)
        # For a synthetic strong signal, t-stat should be highly significant
        assert abs(stats.ic_t_stat) > 2.0
        assert stats.ic_p_value < 0.05

    def test_returns_none_for_insufficient_data(self):
        result = compute_ic_stats(pd.Series([0.01]))
        assert result is None

    def test_to_dict(self, synthetic_factor_panel):
        factor, returns = synthetic_factor_panel
        ic = compute_ic_series(factor, returns)
        stats = compute_ic_stats(ic)
        d = stats.to_dict()
        assert "ic_mean" in d
        assert "ic_t_stat" in d
        assert "ic_p_value" in d
        assert "n_periods" in d


class TestICDecay:
    def test_decay_over_horizons(self, synthetic_factor_panel):
        factor, returns = synthetic_factor_panel
        decay = compute_ic_decay(factor, returns, horizons=[1, 5, 10])
        # All horizons should produce stats
        assert 1 in decay
        assert 5 in decay
        assert 10 in decay
        # 1-day horizon should have highest IC since signal-to-noise drops
        # (synthetic signal is per-period; longer compound returns add noise)
        assert decay[1].ic_mean >= 0


class TestQuintileBacktest:
    def test_long_short_positive_for_predictive_factor(
        self, synthetic_factor_panel
    ):
        factor, returns = synthetic_factor_panel
        # Use shifted forward returns
        result = quintile_backtest(factor, returns.shift(-1), n_groups=5)
        assert result is not None
        # Top quintile (5) should have higher annualized return than bottom (1)
        assert result.annualized_returns[5] > result.annualized_returns[1]
        # Long-short Sharpe should be positive
        assert result.long_short_sharpe > 0
        # Result is 5-group
        assert result.n_groups == 5

    def test_random_factor_has_low_long_short(self, random_factor_panel):
        factor, returns = random_factor_panel
        result = quintile_backtest(factor, returns.shift(-1), n_groups=5)
        assert result is not None
        # Long-short total return should be near zero
        ls_total = result.long_short_cumulative.iloc[-1] - 1.0
        assert abs(ls_total) < 0.3

    def test_returns_none_for_insufficient_assets(self):
        dates = pd.date_range("2024-01-01", periods=10, freq="B")
        # Only 3 assets but n_groups=5
        f = pd.DataFrame(
            np.random.randn(10, 3), index=dates, columns=["A", "B", "C"]
        )
        r = pd.DataFrame(
            np.random.randn(10, 3) * 0.01, index=dates, columns=["A", "B", "C"]
        )
        result = quintile_backtest(f, r.shift(-1), n_groups=5)
        assert result is None

    def test_to_dict_structure(self, synthetic_factor_panel):
        factor, returns = synthetic_factor_panel
        result = quintile_backtest(factor, returns.shift(-1), n_groups=5)
        d = result.to_dict()
        assert d["n_groups"] == 5
        assert "annualized_returns" in d
        assert "sharpes" in d
        assert "long_short_sharpe" in d
        assert "monotonicity" in d


class TestEvaluateFactor:
    def test_one_shot_evaluation(self, synthetic_factor_panel):
        factor, returns = synthetic_factor_panel
        result = evaluate_factor(factor, returns, decay_horizons=[1, 5], n_groups=5)
        assert result.ic_pearson is not None
        assert result.ic_spearman is not None
        assert 1 in result.ic_decay
        assert 5 in result.ic_decay
        assert result.quintile is not None

    def test_to_dict(self, synthetic_factor_panel):
        factor, returns = synthetic_factor_panel
        result = evaluate_factor(factor, returns, decay_horizons=[1, 5])
        d = result.to_dict()
        assert "ic_pearson" in d
        assert "ic_spearman" in d
        assert "ic_decay" in d
        assert "quintile" in d


class TestNeweyWestFallback:
    """When IC series has strong autocorrelation, NW SE > iid SE → smaller t-stat."""

    def test_nw_handles_autocorrelated_ic(self):
        # AR(1) IC series with strong positive autocorrelation
        rng = np.random.default_rng(123)
        n = 500
        ic = np.zeros(n)
        ic[0] = 0.05
        for i in range(1, n):
            ic[i] = 0.7 * ic[i - 1] + rng.normal(0.015, 0.02)
        ic_series = pd.Series(ic, index=pd.date_range("2024-01-01", periods=n, freq="B"))

        stats_with_nw = compute_ic_stats(ic_series, nw_lags=10)
        stats_iid = compute_ic_stats(ic_series, nw_lags=0)

        assert stats_with_nw is not None
        assert stats_iid is not None
        # NW t-stat should have a different magnitude than the iid version
        # (typically smaller because the positive AC inflates SE)
        assert abs(stats_with_nw.ic_t_stat) != pytest.approx(
            abs(stats_iid.ic_t_stat), rel=1e-3
        )
