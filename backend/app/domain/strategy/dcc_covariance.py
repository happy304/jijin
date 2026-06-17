"""DCC-GARCH dynamic conditional covariance estimation.

Standard sample covariance treats all historical observations equally.
In reality, correlations and volatilities are time-varying. The DCC
(Dynamic Conditional Correlation) model of Engle (2002) captures this:

    1. Fit a univariate GARCH(1,1) to each asset's return series to get
       conditional volatilities σ_i,t.
    2. Standardize returns: ε_i,t = r_i,t / σ_i,t
    3. Estimate the DCC parameters (a, b) that govern how the correlation
       matrix Q_t evolves:
           Q_t = (1 - a - b) × Q̄ + a × ε_{t-1} ε_{t-1}' + b × Q_{t-1}
    4. The conditional covariance matrix is:
           Σ_t = D_t × R_t × D_t
       where D_t = diag(σ_1,t, ..., σ_N,t) and R_t = diag(Q_t)^{-1/2} Q_t diag(Q_t)^{-1/2}

This module wraps the ``arch`` library's DCC implementation and provides
a clean interface for the portfolio optimization layer.

Soft dependency: ``arch`` must be installed. If missing, functions raise
``ArchNotInstalledError`` with installation guidance.

References:
    - Engle, R. (2002): "Dynamic Conditional Correlation: A Simple Class
      of Multivariate Generalized Autoregressive Conditional Heteroskedasticity
      Models." Journal of Business & Economic Statistics, 20(3), 339-350.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Soft dependency
# ---------------------------------------------------------------------------

try:
    from arch import arch_model  # type: ignore[import-not-found]
    from arch.univariate import GARCH, ConstantMean  # type: ignore[import-not-found]

    ARCH_AVAILABLE = True
except ImportError:  # pragma: no cover
    ARCH_AVAILABLE = False


class ArchNotInstalledError(RuntimeError):
    """Raised when arch is required but not installed."""

    def __init__(self) -> None:
        super().__init__(
            "The 'arch' package is required for DCC-GARCH covariance estimation. "
            "Install it with: pip install 'arch>=7.0'"
        )


def _require_arch() -> None:
    if not ARCH_AVAILABLE:
        raise ArchNotInstalledError()


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DCCResult:
    """Result of DCC-GARCH covariance estimation.

    Attributes:
        cov_matrix: The latest (most recent date) conditional covariance
            matrix, shape (N, N). Annualized if ``annualize=True``.
        corr_matrix: The latest conditional correlation matrix, shape (N, N).
        conditional_vols: Latest conditional volatilities per asset, shape (N,).
        asset_names: Asset identifiers.
        n_obs: Number of observations used in estimation.
        dcc_params: DCC model parameters (a, b).
    """

    cov_matrix: np.ndarray
    corr_matrix: np.ndarray
    conditional_vols: np.ndarray
    asset_names: list[str]
    n_obs: int = 0
    dcc_params: dict[str, float] | None = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def estimate_dcc_covariance(
    returns: pd.DataFrame,
    annualize: bool = True,
    freq: int = 252,
) -> DCCResult | None:
    """Estimate the DCC-GARCH conditional covariance matrix.

    Fits univariate GARCH(1,1) to each asset, then estimates DCC parameters
    on the standardized residuals. Returns the **latest** (most recent date)
    conditional covariance matrix.

    Parameters:
        returns: Wide DataFrame of asset returns (index=date, columns=asset_codes).
            Daily frequency recommended. At least 100 observations required.
        annualize: If True, multiply the daily covariance by ``freq`` to get
            annualized covariance (standard for portfolio optimization).
        freq: Annualization factor (252 for daily, 52 for weekly, 12 for monthly).

    Returns:
        DCCResult or None if estimation fails.

    Raises:
        ArchNotInstalledError: If ``arch`` is not installed.
    """
    _require_arch()

    if returns is None or returns.empty:
        return None

    returns = returns.dropna(how="all")
    n_obs, n_assets = returns.shape

    if n_obs < 100 or n_assets < 2:
        logger.warning(
            "DCC requires >= 100 obs and >= 2 assets, got %d obs × %d assets",
            n_obs,
            n_assets,
        )
        return None

    asset_names = list(returns.columns)

    # Step 1: Fit univariate GARCH(1,1) to each asset
    conditional_vols = np.zeros((n_obs, n_assets))
    standardized = np.zeros((n_obs, n_assets))

    for i, code in enumerate(asset_names):
        series = returns[code].dropna()
        if len(series) < 100:
            # Fall back to sample std for this asset
            conditional_vols[:, i] = series.std()
            standardized[:, i] = (returns[code].fillna(0.0) / max(series.std(), 1e-10)).values
            continue

        try:
            # Scale returns to percentage for numerical stability
            am = arch_model(series * 100, mean="Constant", vol="GARCH", p=1, q=1)
            res = am.fit(disp="off", show_warning=False)
            # Conditional volatility (in percentage, convert back)
            cond_vol = res.conditional_volatility / 100.0
            # Align to full index
            cond_vol_aligned = cond_vol.reindex(returns.index).ffill().fillna(series.std())
            conditional_vols[:, i] = cond_vol_aligned.values
            # Standardized residuals
            std_resid = (returns[code].fillna(0.0) / cond_vol_aligned.replace(0, 1e-10)).values
            standardized[:, i] = std_resid
        except Exception as exc:
            logger.warning("GARCH fit failed for %s: %s", code, exc)
            vol = returns[code].std()
            conditional_vols[:, i] = vol if vol > 0 else 1e-10
            standardized[:, i] = (returns[code].fillna(0.0) / max(vol, 1e-10)).values

    # Step 2: Estimate DCC on standardized residuals
    # Use MLE to estimate (a, b) parameters via scipy.optimize
    try:
        from scipy.optimize import minimize as sp_minimize

        def _dcc_log_likelihood(params: np.ndarray) -> float:
            """Negative log-likelihood for DCC(1,1) model."""
            a, b = params
            if a < 0 or b < 0 or a + b >= 1:
                return 1e10

            n_t = n_obs
            Q_bar_local = np.corrcoef(standardized, rowvar=False)
            if not np.all(np.isfinite(Q_bar_local)):
                return 1e10

            Q_prev = Q_bar_local.copy()
            ll = 0.0

            for t in range(1, n_t):
                eps_t = standardized[t - 1].reshape(-1, 1)
                Q_curr = (1 - a - b) * Q_bar_local + a * (eps_t @ eps_t.T) + b * Q_prev

                # Normalize to get R_t
                diag_q = np.sqrt(np.diag(Q_curr))
                diag_q = np.where(diag_q > 1e-10, diag_q, 1e-10)
                R_curr = Q_curr / np.outer(diag_q, diag_q)
                np.fill_diagonal(R_curr, 1.0)

                # Log-likelihood contribution: -0.5 * (log|R_t| + ε_t' R_t^{-1} ε_t - ε_t' ε_t)
                try:
                    sign, logdet = np.linalg.slogdet(R_curr)
                    if sign <= 0:
                        Q_prev = Q_curr
                        continue
                    R_inv = np.linalg.inv(R_curr)
                    eps_vec = standardized[t]
                    quad = eps_vec @ R_inv @ eps_vec
                    quad_id = eps_vec @ eps_vec
                    ll += -0.5 * (logdet + quad - quad_id)
                except np.linalg.LinAlgError:
                    pass

                Q_prev = Q_curr

            return -ll  # minimize negative log-likelihood

        # Optimize with bounds: a ∈ [0.001, 0.3], b ∈ [0.5, 0.998], a+b < 1
        from scipy.optimize import Bounds

        result_opt = sp_minimize(
            _dcc_log_likelihood,
            x0=np.array([0.05, 0.93]),
            method="L-BFGS-B",
            bounds=Bounds([0.001, 0.5], [0.3, 0.998]),
            options={"maxiter": 100, "ftol": 1e-6},
        )

        if result_opt.success:
            a_dcc, b_dcc = float(result_opt.x[0]), float(result_opt.x[1])
            # Ensure a + b < 1
            if a_dcc + b_dcc >= 0.999:
                b_dcc = 0.999 - a_dcc
        else:
            # Fall back to typical values
            a_dcc, b_dcc = 0.05, 0.93
            logger.debug("DCC MLE did not converge, using defaults a=0.05, b=0.93")

        Q_bar = np.corrcoef(standardized, rowvar=False)
        if not np.all(np.isfinite(Q_bar)):
            Q_bar = np.eye(n_assets)

        Q_t = Q_bar.copy()
        R_t = Q_bar.copy()

        # Iterate to get the latest Q_t with estimated parameters
        for t in range(1, n_obs):
            eps_t = standardized[t - 1].reshape(-1, 1)
            Q_t = (1 - a_dcc - b_dcc) * Q_bar + a_dcc * (eps_t @ eps_t.T) + b_dcc * Q_t

        # Normalize Q_t to get R_t (correlation matrix)
        diag_Q = np.sqrt(np.diag(Q_t))
        diag_Q = np.where(diag_Q > 0, diag_Q, 1e-10)
        R_t = Q_t / np.outer(diag_Q, diag_Q)
        # Ensure valid correlation matrix
        R_t = np.clip(R_t, -1.0, 1.0)
        np.fill_diagonal(R_t, 1.0)

    except Exception as exc:
        logger.warning("DCC estimation failed, falling back to sample correlation: %s", exc)
        R_t = np.corrcoef(returns.values, rowvar=False)
        if not np.all(np.isfinite(R_t)):
            R_t = np.eye(n_assets)
        a_dcc = 0.0
        b_dcc = 0.0

    # Step 3: Build conditional covariance matrix
    # Σ_t = D_t × R_t × D_t where D_t = diag(latest conditional vols)
    latest_vols = conditional_vols[-1]
    D_t = np.diag(latest_vols)
    cov_matrix = D_t @ R_t @ D_t

    if annualize:
        cov_matrix = cov_matrix * freq

    return DCCResult(
        cov_matrix=cov_matrix,
        corr_matrix=R_t,
        conditional_vols=latest_vols * (np.sqrt(freq) if annualize else 1.0),
        asset_names=asset_names,
        n_obs=n_obs,
        dcc_params={"a": a_dcc, "b": b_dcc},
    )


__all__ = [
    "ARCH_AVAILABLE",
    "ArchNotInstalledError",
    "DCCResult",
    "estimate_dcc_covariance",
]
