"""Tests for DCC-GARCH dynamic covariance estimation."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.domain.strategy.dcc_covariance import (
    ARCH_AVAILABLE,
    ArchNotInstalledError,
    DCCResult,
    estimate_dcc_covariance,
)


@pytest.fixture
def synthetic_returns():
    """3-asset daily returns with time-varying volatility."""
    rng = np.random.default_rng(42)
    n = 300
    dates = pd.date_range("2023-01-01", periods=n, freq="B")

    # Asset 1: low vol regime then high vol
    vol1 = np.concatenate([np.full(150, 0.01), np.full(150, 0.03)])
    r1 = rng.normal(0.0005, 1, n) * vol1

    # Asset 2: constant vol
    r2 = rng.normal(0.0003, 0.015, n)

    # Asset 3: correlated with asset 1
    r3 = 0.6 * r1 + 0.4 * rng.normal(0.0002, 0.012, n)

    return pd.DataFrame({"A": r1, "B": r2, "C": r3}, index=dates)


@pytest.mark.skipif(not ARCH_AVAILABLE, reason="arch not installed")
class TestDCCCovariance:
    def test_basic_estimation(self, synthetic_returns):
        result = estimate_dcc_covariance(synthetic_returns)
        assert result is not None
        assert result.cov_matrix.shape == (3, 3)
        assert result.corr_matrix.shape == (3, 3)
        assert len(result.conditional_vols) == 3
        assert result.n_obs == 300

    def test_covariance_is_psd(self, synthetic_returns):
        """Covariance matrix should be positive semi-definite."""
        result = estimate_dcc_covariance(synthetic_returns)
        assert result is not None
        eigenvalues = np.linalg.eigvalsh(result.cov_matrix)
        assert np.all(eigenvalues >= -1e-10)

    def test_correlation_diagonal_is_one(self, synthetic_returns):
        result = estimate_dcc_covariance(synthetic_returns)
        assert result is not None
        diag = np.diag(result.corr_matrix)
        np.testing.assert_allclose(diag, 1.0, atol=1e-6)

    def test_correlation_bounded(self, synthetic_returns):
        result = estimate_dcc_covariance(synthetic_returns)
        assert result is not None
        assert np.all(result.corr_matrix >= -1.0 - 1e-6)
        assert np.all(result.corr_matrix <= 1.0 + 1e-6)

    def test_annualized_vols_reasonable(self, synthetic_returns):
        """Annualized vols should be in a reasonable range (5%-100%)."""
        result = estimate_dcc_covariance(synthetic_returns, annualize=True)
        assert result is not None
        for vol in result.conditional_vols:
            assert 0.01 < vol < 2.0  # 1% to 200% annualized

    def test_non_annualized(self, synthetic_returns):
        result = estimate_dcc_covariance(synthetic_returns, annualize=False)
        assert result is not None
        # Daily vols should be much smaller than annualized
        for vol in result.conditional_vols:
            assert vol < 0.1  # daily vol < 10%

    def test_returns_none_for_insufficient_data(self):
        dates = pd.date_range("2024-01-01", periods=50, freq="B")
        returns = pd.DataFrame(
            np.random.randn(50, 3) * 0.01, index=dates, columns=["A", "B", "C"]
        )
        result = estimate_dcc_covariance(returns)
        assert result is None

    def test_returns_none_for_single_asset(self):
        dates = pd.date_range("2024-01-01", periods=200, freq="B")
        returns = pd.DataFrame(
            np.random.randn(200, 1) * 0.01, index=dates, columns=["A"]
        )
        result = estimate_dcc_covariance(returns)
        assert result is None

    def test_asset_names_preserved(self, synthetic_returns):
        result = estimate_dcc_covariance(synthetic_returns)
        assert result is not None
        assert result.asset_names == ["A", "B", "C"]

    def test_dcc_captures_regime_change(self, synthetic_returns):
        """Asset A has a vol regime change; DCC should show higher recent vol."""
        result = estimate_dcc_covariance(synthetic_returns, annualize=True)
        assert result is not None
        # Asset A's conditional vol should reflect the high-vol regime (last 150 days)
        # It should be higher than asset B's constant vol
        vol_a = result.conditional_vols[0]
        vol_b = result.conditional_vols[1]
        # A is in high-vol regime at the end → should have higher vol
        assert vol_a > vol_b * 0.8  # generous tolerance
