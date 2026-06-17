"""Tests for marginal/component risk contribution analysis."""

from __future__ import annotations

import numpy as np
import pytest

from app.domain.performance.risk_contribution import (
    compute_risk_contributions,
    risk_budget_deviation,
)


@pytest.fixture
def two_asset_cov():
    """Simple 2-asset covariance matrix."""
    return np.array([[0.04, 0.01], [0.01, 0.09]])


@pytest.fixture
def three_asset_cov():
    """3-asset covariance matrix used by the risk parity sample test."""
    return np.array([
        [0.04, 0.01, 0.005],
        [0.01, 0.09, 0.02],
        [0.005, 0.02, 0.16],
    ])


class TestRiskContribution:
    """Core MRC/CRC properties."""

    def test_pct_contributions_sum_to_one(self, three_asset_cov):
        """Σ pct_risk_contribution = 1 always."""
        weights = [0.4, 0.3, 0.3]
        result = compute_risk_contributions(weights, three_asset_cov)
        assert result is not None
        assert sum(result.pct_risk_contribution) == pytest.approx(1.0, abs=1e-9)

    def test_crc_sums_to_volatility(self, three_asset_cov):
        """Euler's identity: Σ CRC_i = σ_p."""
        weights = [0.5, 0.3, 0.2]
        result = compute_risk_contributions(weights, three_asset_cov)
        assert result is not None
        assert sum(result.component_risk) == pytest.approx(
            result.portfolio_volatility, abs=1e-9
        )

    def test_equal_weight_high_vol_dominates(self, three_asset_cov):
        """Equal-weight portfolio: highest-vol asset contributes most risk."""
        weights = [1 / 3] * 3
        result = compute_risk_contributions(weights, three_asset_cov)
        assert result is not None
        # asset 2 has highest variance (0.16), should dominate risk
        assert (
            result.pct_risk_contribution[2]
            > result.pct_risk_contribution[1]
            > result.pct_risk_contribution[0]
        )

    def test_single_asset(self):
        """Single-asset portfolio: 100% risk contribution to that asset."""
        result = compute_risk_contributions([1.0], np.array([[0.04]]))
        assert result is not None
        assert result.pct_risk_contribution[0] == pytest.approx(1.0)
        assert result.portfolio_volatility == pytest.approx(0.2)

    def test_returns_none_for_invalid_dims(self):
        """Mismatched dimensions return None."""
        result = compute_risk_contributions(
            [0.5, 0.5], np.array([[0.04, 0.01], [0.01, 0.09], [0, 0]])
        )
        assert result is None

    def test_returns_none_for_zero_variance(self):
        """All-zero covariance → port_var <= 0 → None."""
        result = compute_risk_contributions([0.5, 0.5], np.zeros((2, 2)))
        assert result is None

    def test_diversification_ratio_at_least_one(self, three_asset_cov):
        """DR ≥ 1 (= 1 if perfectly correlated, > 1 otherwise)."""
        weights = [1 / 3] * 3
        result = compute_risk_contributions(weights, three_asset_cov)
        assert result is not None
        assert result.diversification_ratio >= 1.0 - 1e-9

    def test_to_dict_structure(self, two_asset_cov):
        """to_dict produces expected schema."""
        result = compute_risk_contributions([0.5, 0.5], two_asset_cov, asset_names=["A", "B"])
        d = result.to_dict()
        assert "portfolio_volatility" in d
        assert "per_asset" in d
        assert len(d["per_asset"]) == 2
        assert d["per_asset"][0]["asset"] == "A"
        assert "marginal_risk" in d["per_asset"][0]
        assert "component_risk" in d["per_asset"][0]
        assert "pct_contribution" in d["per_asset"][0]


class TestRiskParityCheck:
    """Use risk_budget_deviation to validate a risk-parity portfolio."""

    def test_inverse_vol_weights_close_to_risk_parity_for_diagonal_cov(self):
        """For a diagonal covariance, inverse-vol weights = risk parity exactly."""
        # Diagonal cov → no correlation → inverse-vol gives equal CRC
        cov = np.diag([0.04, 0.09, 0.16])
        vols = np.sqrt(np.diag(cov))
        inv_vol = 1.0 / vols
        weights = inv_vol / inv_vol.sum()

        result = compute_risk_contributions(weights, cov)
        assert result is not None

        # All pct contributions should be equal (risk parity)
        target = np.array([1 / 3] * 3)
        deviation = risk_budget_deviation(weights, cov, target_budget=target)
        assert deviation < 1e-6

    def test_equal_weight_far_from_risk_parity_when_vols_differ(self, three_asset_cov):
        """Equal-weight is NOT risk parity when vols differ."""
        weights = [1 / 3] * 3
        target = [1 / 3] * 3
        deviation = risk_budget_deviation(weights, three_asset_cov, target_budget=target)
        # Equal-weight in unequal-vol assets → high concentration
        assert deviation > 0.1
