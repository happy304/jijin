"""Tests for cross-sectional factor preprocessing utilities."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.domain.factors.cross_section import (
    neutralize,
    preprocess_factor,
    rank_normalize,
    standardize,
    winsorize_mad,
    winsorize_quantile,
    zscore,
)


class TestWinsorizeQuantile:
    def test_clips_to_quantile_bounds(self):
        s = pd.Series([-100, -1, 0, 1, 2, 3, 4, 5, 6, 100])
        result = winsorize_quantile(s, lower_quantile=0.1, upper_quantile=0.9)
        assert result.min() > -100
        assert result.max() < 100
        # Middle values unchanged
        assert result.iloc[5] == 3

    def test_preserves_nan(self):
        s = pd.Series([1.0, np.nan, 3.0, 100.0])
        result = winsorize_quantile(s, 0.05, 0.95)
        assert pd.isna(result.iloc[1])

    def test_invalid_quantiles_raise(self):
        s = pd.Series([1.0, 2.0, 3.0])
        with pytest.raises(ValueError):
            winsorize_quantile(s, lower_quantile=0.5, upper_quantile=0.4)


class TestWinsorizeMad:
    def test_clips_outliers_using_mad(self):
        # Mix of values with non-zero MAD plus an extreme outlier
        s = pd.Series([0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 100.0])
        result = winsorize_mad(s, n_mad=3.0)
        # Outlier 100 should be clipped
        assert result.iloc[-1] < 100
        # Median values unchanged
        assert result.iloc[5] == 5.0

    def test_returns_unchanged_when_mad_zero(self):
        # All identical → MAD = 0 → no clipping
        s = pd.Series([5.0, 5.0, 5.0])
        result = winsorize_mad(s)
        assert (result == 5.0).all()

    def test_preserves_nan(self):
        s = pd.Series([1.0, np.nan, 3.0, 100.0])
        result = winsorize_mad(s)
        assert pd.isna(result.iloc[1])


class TestZScore:
    def test_zero_mean_unit_var(self):
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = zscore(s)
        assert result.mean() == pytest.approx(0.0, abs=1e-9)
        assert result.std(ddof=1) == pytest.approx(1.0, abs=1e-9)

    def test_constant_series_returns_nan(self):
        s = pd.Series([3.0, 3.0, 3.0])
        result = zscore(s)
        assert result.isna().all()

    def test_preserves_nan(self):
        s = pd.Series([1.0, 2.0, np.nan, 4.0])
        result = zscore(s)
        assert pd.isna(result.iloc[2])


class TestRankNormalize:
    def test_range_is_pm_half(self):
        s = pd.Series([10, 20, 30, 40, 50])
        result = rank_normalize(s)
        assert result.min() == pytest.approx(-0.5)
        assert result.max() == pytest.approx(0.5)

    def test_handles_ties(self):
        s = pd.Series([1, 1, 2, 3])
        result = rank_normalize(s)
        # Tied values get average rank
        assert result.iloc[0] == result.iloc[1]


class TestStandardize:
    def test_zscore_dispatch(self):
        s = pd.Series([1.0, 2.0, 3.0, 4.0])
        zs = standardize(s, method="zscore")
        assert zs.mean() == pytest.approx(0.0, abs=1e-9)

    def test_rank_dispatch(self):
        s = pd.Series([1.0, 2.0, 3.0, 4.0])
        rk = standardize(s, method="rank")
        assert rk.min() == pytest.approx(-0.5)

    def test_unknown_method_raises(self):
        s = pd.Series([1.0, 2.0])
        with pytest.raises(ValueError):
            standardize(s, method="unknown")


class TestNeutralize:
    def test_industry_neutralization_removes_mean_per_industry(self):
        # Two industries with shifted factor values
        # Tech mean = 5, Health mean = 1
        factor = pd.Series(
            [5.0, 5.0, 5.0, 1.0, 1.0, 1.0],
            index=["A", "B", "C", "D", "E", "F"],
        )
        industry = pd.Series(
            ["tech", "tech", "tech", "health", "health", "health"],
            index=["A", "B", "C", "D", "E", "F"],
        )
        result = neutralize(factor, industry=industry)
        # After neutralization, residuals within each industry should sum to ~0
        tech_residuals = result.iloc[:3]
        health_residuals = result.iloc[3:]
        assert tech_residuals.sum() == pytest.approx(0.0, abs=1e-9)
        assert health_residuals.sum() == pytest.approx(0.0, abs=1e-9)

    def test_size_neutralization_removes_size_loading(self):
        # Factor that's just a linear function of size + noise
        np.random.seed(0)
        log_size = pd.Series(np.linspace(0, 5, 50))
        factor = 2.0 + 1.5 * log_size + np.random.normal(0, 0.001, 50)
        result = neutralize(factor, log_size=log_size)
        # Residuals should be near-zero (signal was fully explained by size)
        assert result.std() < 0.01

    def test_returns_unchanged_when_no_features(self):
        s = pd.Series([1.0, 2.0, 3.0])
        result = neutralize(s, industry=None, log_size=None)
        pd.testing.assert_series_equal(result, s)

    def test_returns_nan_for_too_few_observations(self):
        s = pd.Series([1.0, 2.0])
        ind = pd.Series(["a", "b"])
        result = neutralize(s, industry=ind)
        assert result.isna().all()


class TestPreprocessFactor:
    def test_full_pipeline(self):
        np.random.seed(42)
        factor = pd.Series(np.random.randn(50))
        # Add an extreme outlier
        factor.iloc[0] = 100.0

        result = preprocess_factor(
            factor,
            winsorize_method="quantile",
            winsorize_kwargs={"lower_quantile": 0.05, "upper_quantile": 0.95},
            standardize_method="zscore",
        )
        # Outlier should be clipped, then z-scored
        assert result.iloc[0] < 5.0
        # Result should have ~0 mean, ~1 std
        assert abs(result.mean()) < 0.5
        assert abs(result.std() - 1.0) < 0.5

    def test_pipeline_with_neutralization(self):
        np.random.seed(0)
        n = 30
        factor = pd.Series(np.random.randn(n))
        industry = pd.Series(np.random.choice(["a", "b", "c"], size=n))
        log_size = pd.Series(np.random.randn(n))

        result = preprocess_factor(
            factor,
            winsorize_method="quantile",
            standardize_method="zscore",
            industry=industry,
            log_size=log_size,
        )
        # Result should be standardized (zero mean)
        assert abs(result.mean()) < 0.5
