"""Risk contribution analysis: MRC, CRC, and risk budget diagnostics.

Given a portfolio with weights ``w`` and a covariance matrix ``Σ``:

- Portfolio variance: σ_p² = w' Σ w
- Portfolio volatility: σ_p = √(σ_p²)
- **Marginal Risk Contribution (MRC)**: ∂σ_p / ∂w_i = (Σ w)_i / σ_p
- **Component Risk Contribution (CRC)**: w_i × MRC_i
- **Percentage Risk Contribution**: CRC_i / σ_p (sums to 1)

Properties:
    - Σ CRC_i = σ_p (Euler's theorem on homogeneous functions)
    - Equal CRC across assets → risk parity
    - The percentage contribution measures concentration of risk

These diagnostics are essential for:
    - Validating that a "risk parity" portfolio actually achieves equal contribution
    - Identifying assets that dominate portfolio risk
    - Comparing target risk budget with realized contributions
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass(frozen=True)
class RiskContributionResult:
    """Risk contribution analysis result for a portfolio.

    Attributes:
        weights: Asset weights (sums to 1).
        portfolio_volatility: σ_p = √(w' Σ w).
        portfolio_variance: σ_p².
        marginal_risk: MRC_i for each asset.
        component_risk: CRC_i = w_i × MRC_i for each asset.
        pct_risk_contribution: CRC_i / σ_p, sums to 1.
        risk_concentration_hhi: HHI of percentage risk contributions.
            Range [1/N, 1]. Lower = more diversified risk.
        diversification_ratio: (Σ w_i × σ_i) / σ_p. Choueifaty & Coignard (2008).
            Higher = more diversification benefit. Equal to 1 if assets are
            perfectly correlated.
        asset_names: Optional asset identifiers (defaults to ['A0', 'A1', ...]).
    """

    weights: list[float]
    portfolio_volatility: float
    portfolio_variance: float
    marginal_risk: list[float]
    component_risk: list[float]
    pct_risk_contribution: list[float]
    risk_concentration_hhi: float
    diversification_ratio: float
    asset_names: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-friendly dict."""
        names = self.asset_names or [f"A{i}" for i in range(len(self.weights))]
        return {
            "portfolio_volatility": float(self.portfolio_volatility),
            "portfolio_variance": float(self.portfolio_variance),
            "risk_concentration_hhi": float(self.risk_concentration_hhi),
            "diversification_ratio": float(self.diversification_ratio),
            "per_asset": [
                {
                    "asset": names[i],
                    "weight": float(self.weights[i]),
                    "marginal_risk": float(self.marginal_risk[i]),
                    "component_risk": float(self.component_risk[i]),
                    "pct_contribution": float(self.pct_risk_contribution[i]),
                }
                for i in range(len(self.weights))
            ],
        }


def compute_risk_contributions(
    weights: np.ndarray | list[float],
    cov_matrix: np.ndarray | list[list[float]],
    asset_names: list[str] | None = None,
) -> RiskContributionResult | None:
    """Decompose portfolio risk into per-asset contributions.

    Parameters:
        weights: Asset weights, shape (N,). Should sum to ~1.
        cov_matrix: Covariance matrix, shape (N, N). Same period as weights
            (e.g. annualized covariance for annualized risk decomposition).
        asset_names: Optional asset identifiers.

    Returns:
        RiskContributionResult, or None if inputs are invalid.

    Examples::

        # Equal-weight 3-asset portfolio
        weights = [1/3, 1/3, 1/3]
        cov = [[0.04, 0.01, 0.005],
               [0.01, 0.09, 0.02],
               [0.005, 0.02, 0.16]]
        result = compute_risk_contributions(weights, cov)
        print(result.pct_risk_contribution)  # high-vol asset dominates
    """
    w = np.asarray(weights, dtype=np.float64)
    Sigma = np.asarray(cov_matrix, dtype=np.float64)

    if w.ndim != 1:
        return None
    n = len(w)
    if n == 0:
        return None
    if Sigma.shape != (n, n):
        return None

    # Portfolio variance and volatility
    port_var = float(w @ Sigma @ w)
    if port_var <= 0:
        return None
    port_vol = float(np.sqrt(port_var))

    # Marginal Risk Contribution: MRC_i = (Σ w)_i / σ_p
    sigma_w = Sigma @ w
    mrc = sigma_w / port_vol

    # Component Risk Contribution: CRC_i = w_i × MRC_i
    crc = w * mrc

    # Percentage contribution: CRC_i / σ_p (sums to 1)
    # Note: Σ CRC_i = σ_p, so pct = CRC / σ_p
    pct = crc / port_vol

    # Risk concentration HHI
    hhi = float(np.sum(pct**2))

    # Diversification ratio
    asset_vols = np.sqrt(np.diag(Sigma))
    weighted_vol = float(w @ asset_vols)
    div_ratio = weighted_vol / port_vol if port_vol > 0 else 1.0

    return RiskContributionResult(
        weights=w.tolist(),
        portfolio_volatility=port_vol,
        portfolio_variance=port_var,
        marginal_risk=mrc.tolist(),
        component_risk=crc.tolist(),
        pct_risk_contribution=pct.tolist(),
        risk_concentration_hhi=hhi,
        diversification_ratio=div_ratio,
        asset_names=asset_names or [],
    )


def risk_budget_deviation(
    weights: np.ndarray | list[float],
    cov_matrix: np.ndarray | list[list[float]],
    target_budget: np.ndarray | list[float],
) -> float:
    """Distance between realized risk contribution and target risk budget.

    Returns the L2 distance between realized percentage risk contributions
    and the target budget. Useful for validating risk parity or risk
    budgeting optimizers.

    Parameters:
        weights: Asset weights.
        cov_matrix: Covariance matrix.
        target_budget: Target risk budget (should sum to 1). For risk parity,
            pass [1/N] * N.

    Returns:
        L2 norm ||pct_realized - target_budget||_2. 0 = perfect match.
    """
    result = compute_risk_contributions(weights, cov_matrix)
    if result is None:
        return float("nan")

    realized = np.array(result.pct_risk_contribution)
    target = np.asarray(target_budget, dtype=np.float64)

    if len(realized) != len(target):
        return float("nan")

    return float(np.linalg.norm(realized - target))


__all__ = [
    "RiskContributionResult",
    "compute_risk_contributions",
    "risk_budget_deviation",
]
