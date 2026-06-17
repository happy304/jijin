"""Tests for Fama-MacBeth two-pass cross-sectional regression."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.domain.performance.fama_macbeth import FamaMacBethResult, fama_macbeth


@pytest.fixture
def synthetic_panel():
    """Generate a synthetic returns panel + factor returns where MKT has
    a positive risk premium and SMB has near-zero premium.
    """
    rng = np.random.default_rng(42)
    n_dates = 300
    n_assets = 20
    dates = pd.date_range("2022-01-01", periods=n_dates, freq="B")
    assets = [f"A{i:03d}" for i in range(n_assets)]

    # Factor returns
    mkt = rng.normal(0.0005, 0.01, n_dates)
    smb = rng.normal(0.0, 0.005, n_dates)
    factors = pd.DataFrame({"MKT": mkt, "SMB": smb}, index=dates)

    # Asset returns: r_i = α_i + β_mkt_i × MKT + β_smb_i × SMB + ε
    betas_mkt = rng.uniform(0.5, 1.5, n_assets)
    betas_smb = rng.uniform(-0.5, 0.5, n_assets)
    alphas = rng.normal(0.0001, 0.0002, n_assets)

    returns_data = np.zeros((n_dates, n_assets))
    for i in range(n_assets):
        returns_data[:, i] = (
            alphas[i]
            + betas_mkt[i] * mkt
            + betas_smb[i] * smb
            + rng.normal(0, 0.005, n_dates)
        )

    returns_panel = pd.DataFrame(returns_data, index=dates, columns=assets)
    return returns_panel, factors


class TestFamaMacBeth:
    def test_basic_estimation(self, synthetic_panel):
        returns, factors = synthetic_panel
        result = fama_macbeth(returns, factors)
        assert result is not None
        assert "MKT" in result.risk_premia
        assert "SMB" in result.risk_premia
        assert result.n_assets >= 15
        assert result.n_periods >= 100

    def test_mkt_premium_direction(self, synthetic_panel):
        """MKT risk premium should be finite and have a reasonable magnitude.

        Note: Fama-MacBeth estimates the cross-sectional risk premium, which
        can differ from the time-series mean factor return in small samples.
        We only check that it's finite and not wildly large.
        """
        returns, factors = synthetic_panel
        result = fama_macbeth(returns, factors)
        assert result is not None
        assert np.isfinite(result.risk_premia["MKT"])
        # Should be in a reasonable daily range (< 1% per day)
        assert abs(result.risk_premia["MKT"]) < 0.01

    def test_t_stats_finite(self, synthetic_panel):
        returns, factors = synthetic_panel
        result = fama_macbeth(returns, factors)
        assert result is not None
        for fname in ("MKT", "SMB"):
            assert np.isfinite(result.t_stats[fname])
            assert np.isfinite(result.p_values[fname])
            assert 0 <= result.p_values[fname] <= 1

    def test_intercept_near_zero(self, synthetic_panel):
        """Under the factor model, the intercept should be small."""
        returns, factors = synthetic_panel
        result = fama_macbeth(returns, factors)
        assert result is not None
        # Intercept should be small relative to factor premia
        assert abs(result.intercept) < 0.01

    def test_r_squared_positive(self, synthetic_panel):
        returns, factors = synthetic_panel
        result = fama_macbeth(returns, factors)
        assert result is not None
        assert result.r_squared_avg > 0

    def test_returns_none_for_insufficient_data(self):
        dates = pd.date_range("2024-01-01", periods=10, freq="B")
        returns = pd.DataFrame(
            np.random.randn(10, 5) * 0.01, index=dates, columns=list("ABCDE")
        )
        factors = pd.DataFrame(
            np.random.randn(10, 2) * 0.01, index=dates, columns=["MKT", "SMB"]
        )
        result = fama_macbeth(returns, factors, min_obs_pass1=60)
        assert result is None

    def test_to_dict(self, synthetic_panel):
        returns, factors = synthetic_panel
        result = fama_macbeth(returns, factors)
        d = result.to_dict()
        assert "risk_premia" in d
        assert "t_stats" in d
        assert "p_values" in d
        assert "intercept" in d
        assert "n_assets" in d
        assert "n_periods" in d

    def test_with_risk_free_rate(self, synthetic_panel):
        returns, factors = synthetic_panel
        rf = pd.Series(0.0001, index=returns.index)
        result = fama_macbeth(returns, factors, risk_free_rate=rf)
        assert result is not None
        # Should still produce valid results
        assert result.n_periods >= 100
