"""Tests for Newey-West HAC standard errors in Fama-French regression."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.domain.performance.fama_french import (
    fama_french_3factor,
    fama_french_5factor,
)


@pytest.fixture
def synthetic_factor_data():
    """Synthetic FF3 factor returns + a fund return generated from them
    plus AR(1) residuals (the case where NW matters)."""
    rng = np.random.default_rng(42)
    n = 500
    dates = pd.date_range("2022-01-01", periods=n, freq="B")

    factors = pd.DataFrame(
        {
            "MKT": rng.normal(0.0005, 0.01, n),
            "SMB": rng.normal(0.0001, 0.005, n),
            "HML": rng.normal(0.0001, 0.005, n),
        },
        index=dates,
    )

    # Generate AR(1) residuals (positive autocorrelation typical of fund data)
    residuals = np.zeros(n)
    residuals[0] = rng.normal(0, 0.005)
    for i in range(1, n):
        residuals[i] = 0.3 * residuals[i - 1] + rng.normal(0, 0.005)

    fund_returns = (
        0.0001  # alpha
        + 1.0 * factors["MKT"].values
        + 0.3 * factors["SMB"].values
        + 0.2 * factors["HML"].values
        + residuals
    )
    return pd.Series(fund_returns, index=dates), factors


class TestFamaFrench3FactorNW:
    def test_classical_vs_nw_t_stats_differ_with_autocorrelation(
        self, synthetic_factor_data
    ):
        """With AR(1) residuals, NW t-stats should differ from iid t-stats."""
        fund, factors = synthetic_factor_data
        result_iid = fama_french_3factor(fund, factors, nw_lags=None)
        result_nw = fama_french_3factor(fund, factors, nw_lags=5)

        assert result_iid is not None
        assert result_nw is not None

        # The coefficients themselves are unchanged (point estimates)
        assert result_iid.alpha == pytest.approx(result_nw.alpha, rel=1e-9)
        for col in ("MKT", "SMB", "HML"):
            assert result_iid.betas[col] == pytest.approx(
                result_nw.betas[col], rel=1e-9
            )

        # But t-stats should differ for at least one coefficient
        differs = False
        for col in ("alpha", "MKT", "SMB", "HML"):
            if abs(result_iid.t_stats[col] - result_nw.t_stats[col]) > 1e-3:
                differs = True
                break
        assert differs, "Newey-West did not change t-stats — implementation bug"

    def test_nw_with_zero_lags_falls_back_to_iid(self, synthetic_factor_data):
        """nw_lags=0 should fall back to the classical OLS path."""
        fund, factors = synthetic_factor_data
        result_iid = fama_french_3factor(fund, factors, nw_lags=None)
        result_nw0 = fama_french_3factor(fund, factors, nw_lags=0)

        assert result_iid is not None
        assert result_nw0 is not None
        # Path control: lags=0 should match the classical iid path
        for col in ("alpha", "MKT", "SMB", "HML"):
            assert result_iid.t_stats[col] == pytest.approx(
                result_nw0.t_stats[col], rel=1e-6
            )

    def test_nw_preserves_betas_and_alpha(self, synthetic_factor_data):
        fund, factors = synthetic_factor_data
        result = fama_french_3factor(fund, factors, nw_lags=5)
        assert result is not None
        # Mock-true MKT beta is 1.0, allow generous tolerance for noise
        assert 0.7 < result.betas["MKT"] < 1.3
        # Sample size should match
        assert result.n_obs == len(fund)


class TestFamaFrench5FactorNW:
    def test_5factor_accepts_nw_lags(self):
        rng = np.random.default_rng(7)
        n = 300
        dates = pd.date_range("2022-01-01", periods=n, freq="B")
        factors = pd.DataFrame(
            {
                "MKT": rng.normal(0.0005, 0.01, n),
                "SMB": rng.normal(0.0001, 0.005, n),
                "HML": rng.normal(0.0001, 0.005, n),
                "RMW": rng.normal(0.0001, 0.004, n),
                "CMA": rng.normal(0.0001, 0.004, n),
            },
            index=dates,
        )
        fund_returns = pd.Series(
            0.8 * factors["MKT"].values + rng.normal(0, 0.005, n),
            index=dates,
        )

        result_iid = fama_french_5factor(fund_returns, factors, nw_lags=None)
        result_nw = fama_french_5factor(fund_returns, factors, nw_lags=5)

        assert result_iid is not None
        assert result_nw is not None
        assert result_iid.alpha == pytest.approx(result_nw.alpha, rel=1e-9)
        # NW path should produce finite t-stats
        for col, t in result_nw.t_stats.items():
            assert np.isfinite(t)
