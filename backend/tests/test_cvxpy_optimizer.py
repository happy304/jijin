"""Tests for the cvxpy-based portfolio optimizer.

When cvxpy isn't installed, only the HRP test runs (HRP doesn't need cvxpy).
The other tests are skipped via pytest.importorskip.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.domain.strategy.cvxpy_optimizer import (
    CVXPY_AVAILABLE,
    CvxpyNotInstalledError,
    PortfolioConstraints,
    hierarchical_risk_parity,
    mean_cvar_optimize,
    mean_variance_optimize,
    minimum_variance_optimize,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def three_asset_problem():
    """3-asset MV problem used across multiple tests."""
    expected_returns = np.array([0.10, 0.08, 0.04])
    cov_matrix = np.array(
        [
            [0.04, 0.005, 0.002],
            [0.005, 0.09, 0.01],
            [0.002, 0.01, 0.16],
        ]
    )
    return expected_returns, cov_matrix


# ---------------------------------------------------------------------------
# Soft-dependency behaviour
# ---------------------------------------------------------------------------


class TestSoftDependency:
    def test_cvxpy_not_installed_raises_clean_error(self, three_asset_problem):
        """When cvxpy missing, optimizers raise CvxpyNotInstalledError."""
        if CVXPY_AVAILABLE:
            pytest.skip("cvxpy is installed; cannot test the missing-dep path")

        mu, cov = three_asset_problem
        with pytest.raises(CvxpyNotInstalledError):
            mean_variance_optimize(mu, cov)


# ---------------------------------------------------------------------------
# Mean-Variance optimization (cvxpy required)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not CVXPY_AVAILABLE, reason="cvxpy not installed")
class TestMeanVariance:
    def test_basic_mv_solve(self, three_asset_problem):
        mu, cov = three_asset_problem
        w = mean_variance_optimize(mu, cov, risk_aversion=2.0)
        assert w is not None
        assert w.shape == (3,)
        # Long only by default
        assert (w >= -1e-6).all()
        assert w.sum() == pytest.approx(1.0, abs=1e-3)

    def test_max_weight_constraint(self, three_asset_problem):
        mu, cov = three_asset_problem
        cons = PortfolioConstraints(max_weight=0.4)
        w = mean_variance_optimize(mu, cov, constraints=cons)
        assert w is not None
        # Constraint enforced (allow tiny tolerance)
        assert w.max() <= 0.4 + 1e-6

    def test_turnover_constraint_limits_change(self, three_asset_problem):
        mu, cov = three_asset_problem
        prev = np.array([1 / 3, 1 / 3, 1 / 3])
        cons = PortfolioConstraints(
            prev_weights=prev,
            turnover_limit=0.1,  # very tight
        )
        w = mean_variance_optimize(mu, cov, constraints=cons)
        assert w is not None
        l1 = float(np.abs(w - prev).sum())
        assert l1 <= 0.1 + 1e-6

    def test_sector_cap_constraint(self, three_asset_problem):
        mu, cov = three_asset_problem
        # Force assets 0 and 1 into the same "sector" with cap 0.4
        cons = PortfolioConstraints(
            sector_groups={"tech": [0, 1]},
            sector_caps={"tech": 0.4},
        )
        w = mean_variance_optimize(mu, cov, constraints=cons)
        assert w is not None
        sec_weight = w[0] + w[1]
        assert sec_weight <= 0.4 + 1e-6


@pytest.mark.skipif(not CVXPY_AVAILABLE, reason="cvxpy not installed")
class TestMinimumVariance:
    def test_min_variance_finds_lower_vol_than_equal_weight(self, three_asset_problem):
        _, cov = three_asset_problem
        w_min = minimum_variance_optimize(cov)
        assert w_min is not None
        ew = np.full(3, 1 / 3)

        var_min = float(w_min @ cov @ w_min)
        var_ew = float(ew @ cov @ ew)
        assert var_min <= var_ew + 1e-6


@pytest.mark.skipif(not CVXPY_AVAILABLE, reason="cvxpy not installed")
class TestMeanCVaR:
    def test_basic_cvar_solve(self):
        rng = np.random.default_rng(42)
        # 3 assets, 200 scenarios
        scenarios = rng.normal(0.001, 0.02, size=(200, 3))
        # Add a fat tail to one asset
        scenarios[:, 2] *= 3.0

        w = mean_cvar_optimize(
            return_scenarios=scenarios,
            alpha=0.95,
            risk_aversion=1.0,
        )
        assert w is not None
        assert w.shape == (3,)
        # Long only by default → all non-negative
        assert (w >= -1e-6).all()
        assert w.sum() == pytest.approx(1.0, abs=1e-3)
        # The fat-tail asset should get less weight than equal weight
        assert w[2] < 1 / 3 + 1e-2


# ---------------------------------------------------------------------------
# HRP (no cvxpy needed)
# ---------------------------------------------------------------------------


class TestHRP:
    def test_basic_hrp_weights(self, three_asset_problem):
        _, cov = three_asset_problem
        w = hierarchical_risk_parity(cov)
        assert w is not None
        assert w.shape == (3,)
        assert (w >= 0).all()
        assert w.sum() == pytest.approx(1.0, abs=1e-9)

    def test_single_asset_returns_unit_weight(self):
        cov = np.array([[0.04]])
        w = hierarchical_risk_parity(cov)
        assert w is not None
        assert w[0] == pytest.approx(1.0)

    def test_returns_none_for_degenerate_cov(self):
        # Zero diagonal → degenerate
        cov = np.zeros((3, 3))
        w = hierarchical_risk_parity(cov)
        assert w is None

    def test_hrp_avoids_concentration_in_high_vol(self):
        """HRP should give the high-vol asset less weight."""
        cov = np.array(
            [
                [0.04, 0.01, 0.005],
                [0.01, 0.09, 0.02],
                [0.005, 0.02, 0.16],  # highest variance
            ]
        )
        w = hierarchical_risk_parity(cov)
        assert w is not None
        # The highest-vol asset (index 2) should get the smallest weight
        assert w[2] < w[0]
