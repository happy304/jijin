"""Convex portfolio optimization with cvxpy (soft dependency).

Provides a richer set of constraints than the SLSQP-based MV / risk
parity already in this project:

- Industry / type exposure caps:    A_eq @ w == b,  A_ub @ w <= b
- Turnover (L1) constraint:         ||w - w_prev||_1 <= τ
- Cardinality-like sparsity:        L1 norm <= K (relaxation of cardinality)
- Mean-CVaR optimization:           minimize CVaR_α(w'r) - λ × μ'w
- Long-only / market-neutral / leverage constraints

Design notes:
- cvxpy is an *optional* dependency. The installer must install it
  separately (``pip install cvxpy``). At import time we set
  ``CVXPY_AVAILABLE`` to True/False; calling any optimizer when cvxpy
  is missing raises ``CvxpyNotInstalledError`` with installation guidance.
- Falls back gracefully when the user passes inputs that don't exercise
  cvxpy-only features.
- All inputs are numpy arrays for transparency. Convenience wrappers can
  build inputs from pandas / FundMeta tables.

References:
    - Boyd & Vandenberghe, "Convex Optimization" (Cambridge 2004)
    - Rockafellar & Uryasev (2000), "Optimization of Conditional VaR"
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Soft dependency on cvxpy
# ---------------------------------------------------------------------------

try:
    import cvxpy as cp  # type: ignore[import-not-found]

    CVXPY_AVAILABLE = True
except ImportError:  # pragma: no cover - covered by env-specific tests
    cp = None  # type: ignore[assignment]
    CVXPY_AVAILABLE = False


class CvxpyNotInstalledError(RuntimeError):
    """Raised when an optimizer requires cvxpy but it isn't installed."""

    def __init__(self) -> None:
        super().__init__(
            "cvxpy is required for this optimizer but isn't installed. "
            "Install it with `pip install cvxpy` (also installs an open-source "
            "solver suite). For commercial-grade speed consider `cvxpy[CLARABEL]` "
            "or `cvxpy[MOSEK]`."
        )


def _require_cvxpy() -> None:
    if not CVXPY_AVAILABLE:
        raise CvxpyNotInstalledError()


# ---------------------------------------------------------------------------
# Constraints / config dataclasses
# ---------------------------------------------------------------------------


@dataclass
class PortfolioConstraints:
    """Optional constraints applied on top of the basic
    ``sum(w) == 1, w >= 0`` budget+long-only baseline.

    Attributes:
        long_only: If False, allow short positions (default True).
        max_weight: Per-asset upper bound (e.g. 0.10 = 10%).
        min_weight: Per-asset lower bound (default 0 when long_only).
        leverage: Maximum L1 norm of weights. Useful for short-allowed
            portfolios. None = no constraint.
        prev_weights: Previous portfolio weights for turnover constraint.
            Shape (N,).
        turnover_limit: Maximum L1 turnover ||w - w_prev||_1.
        sector_groups: Maps sector name -> list of asset indices.
        sector_caps: Maps sector name -> upper bound on Σ w in that sector.
        sector_floors: Maps sector name -> lower bound on Σ w.
        beta_target: Optional target portfolio beta.
        asset_betas: (N,) array of per-asset betas.
        beta_tolerance: ±tolerance around beta_target.
    """

    long_only: bool = True
    max_weight: float | None = None
    min_weight: float | None = None
    leverage: float | None = None
    prev_weights: np.ndarray | None = None
    turnover_limit: float | None = None
    sector_groups: dict[str, list[int]] = field(default_factory=dict)
    sector_caps: dict[str, float] = field(default_factory=dict)
    sector_floors: dict[str, float] = field(default_factory=dict)
    beta_target: float | None = None
    asset_betas: np.ndarray | None = None
    beta_tolerance: float = 0.05


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_constraints(
    w: Any,  # cp.Variable, untyped to avoid hard import
    n: int,
    constraints: PortfolioConstraints,
) -> list:
    """Translate PortfolioConstraints into a list of cvxpy constraints."""
    _require_cvxpy()

    cons: list = [cp.sum(w) == 1.0]

    if constraints.long_only:
        floor = constraints.min_weight if constraints.min_weight is not None else 0.0
        cons.append(w >= floor)
    else:
        if constraints.min_weight is not None:
            cons.append(w >= constraints.min_weight)

    if constraints.max_weight is not None:
        cons.append(w <= constraints.max_weight)

    if constraints.leverage is not None:
        cons.append(cp.norm(w, 1) <= constraints.leverage)

    # Turnover: ||w - w_prev||_1 <= τ
    if constraints.prev_weights is not None and constraints.turnover_limit is not None:
        if len(constraints.prev_weights) != n:
            raise ValueError(
                f"prev_weights length {len(constraints.prev_weights)} != n {n}"
            )
        cons.append(cp.norm(w - constraints.prev_weights, 1) <= constraints.turnover_limit)

    # Sector exposure
    for sector, members in constraints.sector_groups.items():
        if not members:
            continue
        sec_weight = cp.sum(w[members])
        if sector in constraints.sector_caps:
            cons.append(sec_weight <= constraints.sector_caps[sector])
        if sector in constraints.sector_floors:
            cons.append(sec_weight >= constraints.sector_floors[sector])

    # Beta neutrality / targeting
    if constraints.beta_target is not None and constraints.asset_betas is not None:
        if len(constraints.asset_betas) != n:
            raise ValueError(
                f"asset_betas length {len(constraints.asset_betas)} != n {n}"
            )
        port_beta = constraints.asset_betas @ w
        tol = max(constraints.beta_tolerance, 1e-6)
        cons.append(port_beta >= constraints.beta_target - tol)
        cons.append(port_beta <= constraints.beta_target + tol)

    return cons


# ---------------------------------------------------------------------------
# Mean-Variance with arbitrary constraints
# ---------------------------------------------------------------------------


def mean_variance_optimize(
    expected_returns: np.ndarray,
    cov_matrix: np.ndarray,
    risk_aversion: float = 1.0,
    constraints: PortfolioConstraints | None = None,
    solver: str | None = None,
) -> np.ndarray | None:
    """Mean-variance optimization with arbitrary linear constraints.

    Maximizes  μ' w - (λ/2) × w' Σ w
    subject to budget, sector, turnover, beta, and weight-bound constraints.

    Parameters:
        expected_returns: (N,) annualized expected returns.
        cov_matrix: (N, N) annualized covariance matrix.
        risk_aversion: λ. Higher = lower vol portfolio. Typical range 1-10.
        constraints: PortfolioConstraints object.
        solver: cvxpy solver name (e.g. 'ECOS', 'SCS', 'CLARABEL'). None = auto.

    Returns:
        Optimal weights array (N,), or None if optimization fails.

    Raises:
        CvxpyNotInstalledError: If cvxpy isn't installed.
    """
    _require_cvxpy()
    n = len(expected_returns)
    if cov_matrix.shape != (n, n):
        raise ValueError(
            f"cov_matrix shape {cov_matrix.shape} doesn't match returns length {n}"
        )

    constraints = constraints or PortfolioConstraints()

    w = cp.Variable(n)
    risk = cp.quad_form(w, cp.psd_wrap(cov_matrix))
    ret = expected_returns @ w
    objective = cp.Maximize(ret - 0.5 * risk_aversion * risk)
    cons = _build_constraints(w, n, constraints)

    prob = cp.Problem(objective, cons)
    try:
        prob.solve(solver=solver, verbose=False)
    except (cp.error.SolverError, cp.error.DCPError) as exc:
        logger.warning("MV optimization failed: %s", exc)
        return None

    if w.value is None or prob.status not in ("optimal", "optimal_inaccurate"):
        logger.warning("MV optimization status=%s", prob.status)
        return None

    return _cleanup_weights(w.value, constraints)


# ---------------------------------------------------------------------------
# Mean-CVaR optimization
# ---------------------------------------------------------------------------


def mean_cvar_optimize(
    return_scenarios: np.ndarray,
    expected_returns: np.ndarray | None = None,
    alpha: float = 0.95,
    risk_aversion: float = 1.0,
    constraints: PortfolioConstraints | None = None,
    solver: str | None = None,
) -> np.ndarray | None:
    """Mean-CVaR portfolio optimization.

    Maximizes:  μ' w - λ × CVaR_α(-w' r_s)
    where r_s is a scenario × asset return matrix and CVaR is the average
    of the worst (1-α) fraction of scenario portfolio losses.

    Implementation: Rockafellar & Uryasev (2000) LP form.

    Parameters:
        return_scenarios: (S, N) matrix of S return scenarios for N assets.
            Typically historical returns or Monte-Carlo paths.
        expected_returns: (N,) override of expected returns. If None,
            uses sample mean of return_scenarios.
        alpha: VaR confidence level (e.g. 0.95 = expected loss in worst 5%).
        risk_aversion: λ trading off return vs. tail risk.
        constraints: Linear constraints.
        solver: cvxpy solver name.

    Returns:
        Optimal weights array, or None if optimization fails.

    Raises:
        CvxpyNotInstalledError: If cvxpy isn't installed.
    """
    _require_cvxpy()
    s, n = return_scenarios.shape
    if s < 2 or n < 1:
        return None

    if expected_returns is None:
        expected_returns = return_scenarios.mean(axis=0)
    if len(expected_returns) != n:
        raise ValueError(
            f"expected_returns length {len(expected_returns)} != n {n}"
        )

    constraints = constraints or PortfolioConstraints()

    w = cp.Variable(n)
    # Auxiliary variables for CVaR LP
    var_aux = cp.Variable()  # the VaR quantile (positive value = loss)
    z = cp.Variable(s, nonneg=True)  # scenario excess losses

    # Loss in scenario s = -(r_s @ w)
    losses = -return_scenarios @ w
    # Constraint: z_s >= losses_s - var_aux  (excess over VaR)
    cvar_cons = [z >= losses - var_aux]
    # CVaR_α = var_aux + (1/((1-α)*S)) * Σ z_s
    cvar = var_aux + (1.0 / ((1.0 - alpha) * s)) * cp.sum(z)

    ret = expected_returns @ w
    objective = cp.Maximize(ret - risk_aversion * cvar)

    cons = _build_constraints(w, n, constraints) + cvar_cons
    prob = cp.Problem(objective, cons)
    try:
        prob.solve(solver=solver, verbose=False)
    except (cp.error.SolverError, cp.error.DCPError) as exc:
        logger.warning("Mean-CVaR optimization failed: %s", exc)
        return None

    if w.value is None or prob.status not in ("optimal", "optimal_inaccurate"):
        logger.warning("Mean-CVaR optimization status=%s", prob.status)
        return None

    return _cleanup_weights(w.value, constraints)


# ---------------------------------------------------------------------------
# Minimum variance with constraints
# ---------------------------------------------------------------------------


def minimum_variance_optimize(
    cov_matrix: np.ndarray,
    constraints: PortfolioConstraints | None = None,
    solver: str | None = None,
) -> np.ndarray | None:
    """Minimum-variance portfolio with arbitrary linear constraints.

    Parameters:
        cov_matrix: (N, N) covariance matrix.
        constraints: PortfolioConstraints.
        solver: cvxpy solver name.

    Returns:
        Optimal weights, or None on failure.
    """
    _require_cvxpy()
    n = cov_matrix.shape[0]
    constraints = constraints or PortfolioConstraints()

    w = cp.Variable(n)
    risk = cp.quad_form(w, cp.psd_wrap(cov_matrix))
    objective = cp.Minimize(risk)
    cons = _build_constraints(w, n, constraints)

    prob = cp.Problem(objective, cons)
    try:
        prob.solve(solver=solver, verbose=False)
    except (cp.error.SolverError, cp.error.DCPError) as exc:
        logger.warning("Min-variance optimization failed: %s", exc)
        return None

    if w.value is None or prob.status not in ("optimal", "optimal_inaccurate"):
        return None

    return _cleanup_weights(w.value, constraints)


# ---------------------------------------------------------------------------
# Hierarchical Risk Parity (no cvxpy dependency)
# ---------------------------------------------------------------------------


def hierarchical_risk_parity(cov_matrix: np.ndarray) -> np.ndarray | None:
    """Hierarchical Risk Parity (Lopez de Prado 2016).

    Pure-numpy implementation; no cvxpy or scipy.cluster dependency at
    runtime (we use scipy.cluster.hierarchy which scipy>=1 provides).

    Algorithm:
        1. Convert covariance to correlation, then to distance matrix.
        2. Single-linkage hierarchical clustering on the distance matrix.
        3. Quasi-diagonalize: reorder assets by their position in the
           dendrogram leaves.
        4. Recursive bisection: at each split, allocate inverse-variance
           weights between the two sub-clusters.

    Parameters:
        cov_matrix: (N, N) covariance matrix. Must be PSD.

    Returns:
        HRP weights (N,) summing to 1, or None on degenerate input.
    """
    n = cov_matrix.shape[0]
    if n == 0:
        return None
    if n == 1:
        return np.array([1.0])

    # Correlation matrix
    diag = np.sqrt(np.diag(cov_matrix))
    if np.any(diag <= 0) or not np.all(np.isfinite(diag)):
        return None
    inv_diag = 1.0 / diag
    corr = cov_matrix * np.outer(inv_diag, inv_diag)
    corr = np.clip(corr, -1.0, 1.0)

    # Distance: d_ij = sqrt((1 - corr_ij) / 2)
    dist = np.sqrt(0.5 * (1.0 - corr))

    try:
        from scipy.cluster.hierarchy import leaves_list, linkage
        from scipy.spatial.distance import squareform
    except ImportError:  # pragma: no cover
        logger.warning("HRP requires scipy.cluster")
        return None

    try:
        # squareform expects a symmetric distance matrix
        condensed = squareform(dist, checks=False)
        link = linkage(condensed, method="single")
        order = list(leaves_list(link))
    except Exception as exc:
        logger.warning("HRP clustering failed: %s", exc)
        return None

    # Recursive bisection
    weights = np.ones(n)

    def _cluster_var(items: list[int]) -> float:
        """Inverse-variance weighted variance of a sub-cluster."""
        sub_cov = cov_matrix[np.ix_(items, items)]
        # Inverse-variance weighting within the sub-cluster
        ivp = 1.0 / np.diag(sub_cov)
        ivp /= ivp.sum()
        return float(ivp @ sub_cov @ ivp)

    # Recursive bisection on the sorted order list
    clusters: list[list[int]] = [order]
    while clusters:
        new_clusters: list[list[int]] = []
        for cluster in clusters:
            if len(cluster) <= 1:
                continue
            mid = len(cluster) // 2
            left = cluster[:mid]
            right = cluster[mid:]

            var_left = _cluster_var(left)
            var_right = _cluster_var(right)
            total = var_left + var_right
            if total <= 0:
                alpha_l = 0.5
            else:
                # Allocate more to the lower-variance cluster
                alpha_l = 1.0 - var_left / total

            for i in left:
                weights[i] *= alpha_l
            for j in right:
                weights[j] *= 1.0 - alpha_l

            new_clusters.append(left)
            new_clusters.append(right)
        clusters = new_clusters

    weights = np.maximum(weights, 0.0)
    s = weights.sum()
    if s <= 0:
        return None
    return weights / s


# ---------------------------------------------------------------------------
# Cleanup helper
# ---------------------------------------------------------------------------


def _cleanup_weights(
    raw: np.ndarray,
    constraints: PortfolioConstraints,
) -> np.ndarray:
    """Tidy up raw solver output.

    - Clip negatives if long_only.
    - Renormalize to sum to 1 (handles slight solver tolerance).
    - Zero out tiny weights (< 1e-6).
    """
    w = np.asarray(raw, dtype=np.float64)
    if constraints.long_only:
        w = np.maximum(w, 0.0)
    # Drop tiny weights
    w = np.where(np.abs(w) < 1e-6, 0.0, w)
    s = np.abs(w).sum()
    if s > 0:
        if constraints.long_only:
            w = w / w.sum()
        else:
            # Preserve sign structure when shorting allowed; just normalize total long+short
            if w.sum() != 0:
                w = w / abs(w.sum())
    return w


__all__ = [
    "CVXPY_AVAILABLE",
    "CvxpyNotInstalledError",
    "PortfolioConstraints",
    "hierarchical_risk_parity",
    "mean_cvar_optimize",
    "mean_variance_optimize",
    "minimum_variance_optimize",
]
