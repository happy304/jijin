"""Fama-French factor attribution models (3-factor and 5-factor).

Implements OLS regression-based attribution following:
    r_fund - r_f = α + β_MKT × MKT + β_SMB × SMB + β_HML × HML
                   (+ β_RMW × RMW + β_CMA × CMA for 5-factor) + ε

References:
    - Fama & French (1993): Three-factor model
    - Fama & French (2015): Five-factor model

Satisfies requirement 3.7.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FamaFrenchResult:
    """Result of a Fama-French factor regression.

    Attributes:
        alpha: Annualized alpha (intercept × annualization factor).
        alpha_daily: Raw daily alpha (intercept of regression).
        betas: Dict mapping factor name to its beta coefficient.
        r_squared: Coefficient of determination (R²).
        adj_r_squared: Adjusted R² accounting for number of factors.
        residual_std: Annualized standard deviation of residuals.
        t_stats: Dict mapping coefficient name to its t-statistic.
        n_obs: Number of observations used in regression.
        model_type: '3-factor' or '5-factor'.
    """

    alpha: float
    alpha_daily: float
    betas: dict[str, float] = field(default_factory=dict)
    r_squared: float = np.nan
    adj_r_squared: float = np.nan
    residual_std: float = np.nan
    t_stats: dict[str, float] = field(default_factory=dict)
    n_obs: int = 0
    model_type: str = "3-factor"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_THREE_FACTOR_COLS = ["MKT", "SMB", "HML"]
_FIVE_FACTOR_COLS = ["MKT", "SMB", "HML", "RMW", "CMA"]
_MIN_OBSERVATIONS = 30  # Minimum data points for meaningful regression
_ANNUALIZATION_FACTOR = 252  # Trading days per year


# ---------------------------------------------------------------------------
# Core OLS implementation (numpy-based, no statsmodels dependency)
# ---------------------------------------------------------------------------


def _newey_west_cov(
    X: np.ndarray, residuals: np.ndarray, lags: int
) -> np.ndarray:
    """Newey-West HAC covariance matrix of OLS coefficients.

    Robust to both heteroskedasticity and autocorrelation in residuals.
    Essential for daily / overlapping-period factor regressions where
    residuals are typically autocorrelated (Lo & MacKinlay 1990).

    Formula:
        V_NW(β̂) = (X'X)^{-1} S (X'X)^{-1}
        S = Γ_0 + Σ_{l=1}^{L} w_l × (Γ_l + Γ_l')
        Γ_l = Σ_{t=l+1}^{T} ε_t ε_{t-l} x_t x_{t-l}'
        w_l = 1 - l/(L+1)   (Bartlett kernel)

    Parameters:
        X: Design matrix shape (n, k).
        residuals: OLS residuals shape (n,).
        lags: Truncation lag L. Common rule: L = floor(4 × (n/100)^(2/9)).

    Returns:
        HAC variance-covariance matrix shape (k, k).
    """
    n, k = X.shape
    if n <= k:
        return np.full((k, k), np.nan)

    # White (HC0) component: S0 = Σ ε² x x'
    eps_sq = residuals**2
    S = (X * eps_sq[:, None]).T @ X / n  # (k, k) matrix

    max_lag = min(lags, n - 1)
    for lag in range(1, max_lag + 1):
        w = 1.0 - lag / (max_lag + 1)
        # Cross-product: Σ_t ε_t ε_{t-lag} x_t x_{t-lag}'
        # We need both Γ_l and Γ_l' for symmetry
        e_lag = residuals[lag:] * residuals[:-lag]
        x_lag = X[lag:].T @ (X[:-lag] * e_lag[:, None])  # (k, k)
        S = S + w * (x_lag + x_lag.T) / n

    try:
        XtX_inv = np.linalg.inv(X.T @ X / n)
    except np.linalg.LinAlgError:
        return np.full((k, k), np.nan)

    # Sandwich: V = (X'X/n)^{-1} S (X'X/n)^{-1} / n
    V = XtX_inv @ S @ XtX_inv / n
    return V


def _ols_regression(
    y: np.ndarray, X: np.ndarray, *, nw_lags: int | None = None
) -> tuple[np.ndarray, float, float, np.ndarray, np.ndarray]:
    """Perform OLS regression: y = X @ beta + epsilon.

    X should already include a constant column (intercept).

    Parameters:
        y: Dependent variable, shape (n,).
        X: Design matrix, shape (n, k), with intercept column.
        nw_lags: If provided, compute Newey-West HAC standard errors
            with this lag truncation. If None, use classical (assumes iid)
            standard errors.

    Returns:
        coefficients: (k,) array of estimated coefficients.
        r_squared: R² of the regression.
        adj_r_squared: Adjusted R².
        residuals: (n,) array of residuals.
        t_stats: (k,) array of t-statistics for each coefficient.
    """
    n, k = X.shape

    # Solve normal equations using least squares
    # np.linalg.lstsq is numerically stable
    result = np.linalg.lstsq(X, y, rcond=None)
    coefficients = result[0]

    # Compute residuals and R²
    y_hat = X @ coefficients
    residuals = y - y_hat
    ss_res = float(np.sum(residuals**2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))

    if ss_tot < 1e-15:
        # Constant dependent variable
        r_squared = np.nan
        adj_r_squared = np.nan
    else:
        r_squared = 1.0 - ss_res / ss_tot
        # Adjusted R²
        if n - k > 0:
            adj_r_squared = 1.0 - (1.0 - r_squared) * (n - 1) / (n - k)
        else:
            adj_r_squared = np.nan

    # Compute t-statistics
    if n - k > 0:
        if nw_lags is not None and nw_lags > 0:
            # Newey-West HAC standard errors
            try:
                V = _newey_west_cov(X, residuals, lags=nw_lags)
                se = np.sqrt(np.maximum(np.diag(V), 0.0))
                with np.errstate(divide="ignore", invalid="ignore"):
                    t_stats = coefficients / se
                t_stats = np.where(np.isfinite(t_stats), t_stats, 0.0)
            except (np.linalg.LinAlgError, ValueError):
                t_stats = np.zeros(k)
        else:
            mse = ss_res / (n - k)
            # Classical OLS variance-covariance matrix (iid assumption)
            try:
                XtX_inv = np.linalg.inv(X.T @ X)
                se = np.sqrt(np.diag(XtX_inv) * mse)
                # Avoid division by zero
                with np.errstate(divide="ignore", invalid="ignore"):
                    t_stats = coefficients / se
                t_stats = np.where(np.isfinite(t_stats), t_stats, 0.0)
            except np.linalg.LinAlgError:
                t_stats = np.zeros(k)
    else:
        t_stats = np.zeros(k)

    return coefficients, r_squared, adj_r_squared, residuals, t_stats


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fama_french_3factor(
    fund_returns: pd.Series,
    factor_returns: pd.DataFrame,
    risk_free_rate: Optional[pd.Series] = None,
    annualization: int = _ANNUALIZATION_FACTOR,
    nw_lags: Optional[int] = None,
) -> Optional[FamaFrenchResult]:
    """Run Fama-French 3-factor regression.

    Parameters:
        fund_returns: Daily fund return series (index: DatetimeIndex).
        factor_returns: DataFrame with columns ['MKT', 'SMB', 'HML'].
            MKT should already be the market excess return (Rm - Rf).
        risk_free_rate: Daily risk-free rate series. If provided, fund excess
            returns are computed as fund_returns - risk_free_rate.
            If None, fund_returns is used directly (assumed already excess).
        annualization: Number of trading days per year for annualizing alpha.
        nw_lags: If provided (recommended for daily data, e.g. 5), compute
            Newey-West HAC standard errors. Robust to autocorrelation in
            residuals. None = classical iid OLS standard errors.

    Returns:
        FamaFrenchResult or None if insufficient data.
    """
    return _run_regression(
        fund_returns=fund_returns,
        factor_returns=factor_returns,
        risk_free_rate=risk_free_rate,
        factor_cols=_THREE_FACTOR_COLS,
        model_type="3-factor",
        annualization=annualization,
        nw_lags=nw_lags,
    )


def fama_french_5factor(
    fund_returns: pd.Series,
    factor_returns: pd.DataFrame,
    risk_free_rate: Optional[pd.Series] = None,
    annualization: int = _ANNUALIZATION_FACTOR,
    nw_lags: Optional[int] = None,
) -> Optional[FamaFrenchResult]:
    """Run Fama-French 5-factor regression.

    Parameters:
        fund_returns: Daily fund return series (index: DatetimeIndex).
        factor_returns: DataFrame with columns ['MKT', 'SMB', 'HML', 'RMW', 'CMA'].
            MKT should already be the market excess return (Rm - Rf).
        risk_free_rate: Daily risk-free rate series. If provided, fund excess
            returns are computed as fund_returns - risk_free_rate.
            If None, fund_returns is used directly (assumed already excess).
        annualization: Number of trading days per year for annualizing alpha.
        nw_lags: If provided, compute Newey-West HAC standard errors.

    Returns:
        FamaFrenchResult or None if insufficient data.
    """
    return _run_regression(
        fund_returns=fund_returns,
        factor_returns=factor_returns,
        risk_free_rate=risk_free_rate,
        factor_cols=_FIVE_FACTOR_COLS,
        model_type="5-factor",
        annualization=annualization,
        nw_lags=nw_lags,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _run_regression(
    fund_returns: pd.Series,
    factor_returns: pd.DataFrame,
    risk_free_rate: Optional[pd.Series],
    factor_cols: list[str],
    model_type: str,
    annualization: int,
    nw_lags: Optional[int] = None,
) -> Optional[FamaFrenchResult]:
    """Core regression logic shared by 3-factor and 5-factor models."""
    # --- Input validation ---
    if fund_returns is None or factor_returns is None:
        return None

    if isinstance(fund_returns, pd.Series) and fund_returns.empty:
        return None

    if isinstance(factor_returns, pd.DataFrame) and factor_returns.empty:
        return None

    # Check required columns exist
    missing_cols = [c for c in factor_cols if c not in factor_returns.columns]
    if missing_cols:
        return None

    # --- Align data on common dates ---
    # Combine into a single DataFrame for alignment
    combined = pd.DataFrame({"fund": fund_returns})
    for col in factor_cols:
        combined[col] = factor_returns[col]

    if risk_free_rate is not None:
        combined["rf"] = risk_free_rate

    # Drop rows with any NaN
    combined = combined.dropna()

    n_obs = len(combined)
    if n_obs < _MIN_OBSERVATIONS:
        return None

    # --- Compute excess returns ---
    if risk_free_rate is not None and "rf" in combined.columns:
        y = (combined["fund"] - combined["rf"]).values
    else:
        y = combined["fund"].values

    # --- Build factor matrix with intercept ---
    X_data = combined[factor_cols].values
    # Add constant (intercept) as first column
    ones = np.ones((n_obs, 1))
    X = np.hstack([ones, X_data])

    # --- Run OLS ---
    coefficients, r_squared, adj_r_squared, residuals, t_stats = _ols_regression(
        y, X, nw_lags=nw_lags
    )

    # --- Extract results ---
    alpha_daily = float(coefficients[0])
    # Use geometric (compound) annualization for alpha, NOT linear scaling.
    # Linear scaling (alpha_daily * 252) systematically biases small alpha values
    # and is mathematically inconsistent with how returns compound.
    # Reference: standard practice in academic papers (Carhart 1997, Fama-French 2015).
    if alpha_daily > -1.0:
        alpha_annualized = (1.0 + alpha_daily) ** annualization - 1.0
    else:
        # Daily alpha cannot be < -100%; clamp to avoid math domain error
        alpha_annualized = -1.0

    betas = {col: float(coefficients[i + 1]) for i, col in enumerate(factor_cols)}

    residual_std_daily = float(np.std(residuals, ddof=1)) if n_obs > 1 else np.nan
    residual_std_annualized = residual_std_daily * np.sqrt(annualization)

    # t-stats dict: alpha + each factor
    t_stat_dict: dict[str, float] = {"alpha": float(t_stats[0])}
    for i, col in enumerate(factor_cols):
        t_stat_dict[col] = float(t_stats[i + 1])

    return FamaFrenchResult(
        alpha=alpha_annualized,
        alpha_daily=alpha_daily,
        betas=betas,
        r_squared=float(r_squared) if not np.isnan(r_squared) else np.nan,
        adj_r_squared=float(adj_r_squared) if not np.isnan(adj_r_squared) else np.nan,
        residual_std=residual_std_annualized,
        t_stats=t_stat_dict,
        n_obs=n_obs,
        model_type=model_type,
    )


# ---------------------------------------------------------------------------
# China market factor construction helpers
# ---------------------------------------------------------------------------


def build_china_market_factors(
    market_index_returns: pd.Series,
    small_cap_returns: pd.Series,
    large_cap_returns: pd.Series,
    value_returns: pd.Series,
    growth_returns: pd.Series,
    risk_free_rate: Optional[pd.Series] = None,
    robust_returns: Optional[pd.Series] = None,
    weak_returns: Optional[pd.Series] = None,
    conservative_returns: Optional[pd.Series] = None,
    aggressive_returns: Optional[pd.Series] = None,
) -> pd.DataFrame:
    """Construct Fama-French factors for the Chinese A-share market.

    This builds factor returns from publicly available index data.
    Typical proxies:
        - Market: CSI All Share Index (中证全指) excess return
        - SMB: Small-cap index return - Large-cap index return
        - HML: Value index return - Growth index return
        - RMW (optional): Robust profitability - Weak profitability
        - CMA (optional): Conservative investment - Aggressive investment

    Parameters:
        market_index_returns: Daily returns of market proxy (e.g., CSI All Share).
        small_cap_returns: Daily returns of small-cap index (e.g., CSI 500/1000).
        large_cap_returns: Daily returns of large-cap index (e.g., CSI 300).
        value_returns: Daily returns of value index.
        growth_returns: Daily returns of growth index.
        risk_free_rate: Daily risk-free rate (e.g., SHIBOR O/N / 360).
            If None, MKT = market_index_returns directly.
        robust_returns: Daily returns of high-profitability portfolio (for RMW).
        weak_returns: Daily returns of low-profitability portfolio (for RMW).
        conservative_returns: Daily returns of conservative-investment portfolio (for CMA).
        aggressive_returns: Daily returns of aggressive-investment portfolio (for CMA).

    Returns:
        DataFrame with columns: MKT, SMB, HML (and optionally RMW, CMA).
    """
    # Align all series on common dates
    data: dict[str, pd.Series] = {}

    # MKT: market excess return
    if risk_free_rate is not None:
        combined = pd.DataFrame({"mkt": market_index_returns, "rf": risk_free_rate}).dropna()
        data["MKT"] = combined["mkt"] - combined["rf"]
    else:
        data["MKT"] = market_index_returns

    # SMB: Small Minus Big
    data["SMB"] = small_cap_returns - large_cap_returns

    # HML: High (value) Minus Low (growth)
    data["HML"] = value_returns - growth_returns

    # RMW: Robust Minus Weak (optional)
    if robust_returns is not None and weak_returns is not None:
        data["RMW"] = robust_returns - weak_returns

    # CMA: Conservative Minus Aggressive (optional)
    if conservative_returns is not None and aggressive_returns is not None:
        data["CMA"] = conservative_returns - aggressive_returns

    result = pd.DataFrame(data)
    return result.dropna()
