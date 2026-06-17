"""Fama-MacBeth (1973) two-pass cross-sectional regression.

The standard academic method for estimating **factor risk premia** (λ):

Pass 1 (Time-series): For each asset i, regress its excess returns on
    K factor returns to get factor loadings (betas):
        r_i,t - r_f = α_i + Σ_k β_i,k × F_k,t + ε_i,t

Pass 2 (Cross-section): At each date t, regress the cross-section of
    asset returns on the estimated betas from Pass 1:
        r_i,t = γ_0,t + Σ_k γ_k,t × β̂_i,k + η_i,t

    The time-series average of γ_k,t is the estimated risk premium λ_k.

Standard errors:
    - Naive: SE(λ̂_k) = std(γ_k,t) / √T
    - Shanken (1992) correction: adjusts for estimation error in β̂
    - Newey-West: adjusts for autocorrelation in γ_k,t

References:
    - Fama & MacBeth (1973): "Risk, Return, and Equilibrium: Empirical Tests"
    - Shanken (1992): "On the Estimation of Beta-Pricing Models"
    - Cochrane (2005): "Asset Pricing", Ch. 12
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class FamaMacBethResult:
    """Result of Fama-MacBeth two-pass regression.

    Attributes:
        risk_premia: Dict mapping factor name → estimated risk premium (λ̂_k).
            This is the time-series average of the cross-sectional slope γ_k,t.
        t_stats: Dict mapping factor name → t-statistic (Newey-West adjusted).
        p_values: Dict mapping factor name → two-sided p-value.
        intercept: Average cross-sectional intercept (γ_0 mean).
            Under CAPM/APT, should be close to zero.
        intercept_t_stat: T-stat for the intercept.
        n_assets: Number of assets in the cross-section.
        n_periods: Number of time periods in Pass 2.
        r_squared_avg: Average cross-sectional R² across all periods.
        gamma_series: DataFrame of γ_k,t time series (index=date, columns=factor names).
            Useful for plotting risk premium stability over time.
    """

    risk_premia: dict[str, float] = field(default_factory=dict)
    t_stats: dict[str, float] = field(default_factory=dict)
    p_values: dict[str, float] = field(default_factory=dict)
    intercept: float = 0.0
    intercept_t_stat: float = 0.0
    n_assets: int = 0
    n_periods: int = 0
    r_squared_avg: float = 0.0
    gamma_series: pd.DataFrame = field(default_factory=pd.DataFrame)

    def to_dict(self) -> dict:
        return {
            "risk_premia": {k: round(v, 6) for k, v in self.risk_premia.items()},
            "t_stats": {k: round(v, 4) for k, v in self.t_stats.items()},
            "p_values": {k: round(v, 6) for k, v in self.p_values.items()},
            "intercept": round(self.intercept, 6),
            "intercept_t_stat": round(self.intercept_t_stat, 4),
            "n_assets": self.n_assets,
            "n_periods": self.n_periods,
            "r_squared_avg": round(self.r_squared_avg, 4),
        }


def _newey_west_se_1d(x: np.ndarray, lags: int = 5) -> float:
    """Newey-West HAC standard error for the mean of a 1-D series."""
    n = len(x)
    if n < 2:
        return float("nan")
    centered = x - np.mean(x)
    gamma_0 = float(np.sum(centered**2)) / n
    var_est = gamma_0
    max_lag = min(lags, n - 1)
    for lag in range(1, max_lag + 1):
        w = 1.0 - lag / (max_lag + 1)
        gamma_l = float(np.sum(centered[lag:] * centered[:-lag])) / n
        var_est += 2.0 * w * gamma_l
    if var_est < 0:
        var_est = gamma_0
    return float(np.sqrt(max(var_est / n, 0.0)))


def fama_macbeth(
    returns_panel: pd.DataFrame,
    factor_returns: pd.DataFrame,
    risk_free_rate: Optional[pd.Series] = None,
    nw_lags: int = 5,
    min_obs_pass1: int = 60,
) -> Optional[FamaMacBethResult]:
    """Run Fama-MacBeth two-pass cross-sectional regression.

    Parameters:
        returns_panel: Wide DataFrame of asset returns (index=date,
            columns=asset_codes). Daily or monthly frequency.
        factor_returns: DataFrame of factor returns (index=date,
            columns=factor_names, e.g. ['MKT', 'SMB', 'HML']).
            Must be aligned with returns_panel on dates.
        risk_free_rate: Optional daily risk-free rate series. If provided,
            excess returns = returns - rf. If None, returns are used directly.
        nw_lags: Newey-West lag order for Pass 2 standard errors.
        min_obs_pass1: Minimum observations required for Pass 1 OLS per asset.

    Returns:
        FamaMacBethResult or None if insufficient data.

    Notes:
        - Assets with fewer than ``min_obs_pass1`` observations in Pass 1
          are excluded from the cross-section.
        - Dates where fewer than 5 assets have valid betas are skipped in Pass 2.
    """
    if returns_panel is None or factor_returns is None:
        return None
    if returns_panel.empty or factor_returns.empty:
        return None

    # Align dates
    common_dates = returns_panel.index.intersection(factor_returns.index)
    if len(common_dates) < min_obs_pass1:
        return None

    ret = returns_panel.loc[common_dates]
    factors = factor_returns.loc[common_dates]
    factor_names = list(factors.columns)
    k = len(factor_names)

    if k == 0:
        return None

    # Subtract risk-free rate if provided
    if risk_free_rate is not None:
        rf_aligned = risk_free_rate.reindex(common_dates).fillna(0.0)
        ret = ret.sub(rf_aligned, axis=0)

    # ---------------------------------------------------------------
    # Pass 1: Time-series regression for each asset → β̂_i
    # ---------------------------------------------------------------
    asset_codes = list(ret.columns)
    betas: dict[str, np.ndarray] = {}  # asset_code → (k,) array of betas

    X_ts = factors.values.astype(np.float64)
    # Add intercept
    ones = np.ones((len(X_ts), 1))
    X_ts_full = np.hstack([ones, X_ts])

    for code in asset_codes:
        y = ret[code].values.astype(np.float64)
        valid = np.isfinite(y) & np.all(np.isfinite(X_ts_full), axis=1)
        if valid.sum() < min_obs_pass1:
            continue
        y_v = y[valid]
        X_v = X_ts_full[valid]
        try:
            coef, *_ = np.linalg.lstsq(X_v, y_v, rcond=None)
        except np.linalg.LinAlgError:
            continue
        # coef[0] = alpha, coef[1:] = betas
        betas[code] = coef[1:]

    if len(betas) < 5:
        return None

    # Build beta matrix: (n_assets, k)
    valid_assets = list(betas.keys())
    beta_matrix = np.array([betas[a] for a in valid_assets])

    # ---------------------------------------------------------------
    # Pass 2: Cross-sectional regression at each date t
    # ---------------------------------------------------------------
    # At each t: r_i,t = γ_0 + Σ_k γ_k × β̂_i,k + η_i,t
    gamma_records: list[dict[str, float]] = []
    r2_list: list[float] = []

    for t_date in common_dates:
        # Get cross-section of returns at date t
        r_t = ret.loc[t_date, valid_assets].values.astype(np.float64)
        valid_mask = np.isfinite(r_t)
        if valid_mask.sum() < 5:
            continue

        y_cs = r_t[valid_mask]
        X_cs = beta_matrix[valid_mask]
        # Add intercept
        X_cs_full = np.hstack([np.ones((len(y_cs), 1)), X_cs])

        try:
            coef_cs, *_ = np.linalg.lstsq(X_cs_full, y_cs, rcond=None)
        except np.linalg.LinAlgError:
            continue

        record = {"_intercept": float(coef_cs[0])}
        for i, fname in enumerate(factor_names):
            record[fname] = float(coef_cs[i + 1])

        # R² for this cross-section
        y_hat = X_cs_full @ coef_cs
        ss_res = float(np.sum((y_cs - y_hat) ** 2))
        ss_tot = float(np.sum((y_cs - y_cs.mean()) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-15 else 0.0
        r2_list.append(r2)

        gamma_records.append(record)

    if len(gamma_records) < 10:
        return None

    gamma_df = pd.DataFrame(gamma_records, index=common_dates[: len(gamma_records)])
    n_periods = len(gamma_df)

    # ---------------------------------------------------------------
    # Compute risk premia = time-series mean of γ_k,t
    # Standard errors via Newey-West
    # ---------------------------------------------------------------
    from scipy import stats as sp_stats

    risk_premia: dict[str, float] = {}
    t_stats: dict[str, float] = {}
    p_values: dict[str, float] = {}

    for fname in factor_names:
        gamma_k = gamma_df[fname].values
        lam = float(np.mean(gamma_k))
        se = _newey_west_se_1d(gamma_k, lags=nw_lags)
        if se > 0 and not np.isnan(se):
            t = lam / se
        else:
            t = 0.0
        p = float(2.0 * (1.0 - sp_stats.norm.cdf(abs(t))))
        risk_premia[fname] = lam
        t_stats[fname] = t
        p_values[fname] = p

    # Intercept
    intercept_arr = gamma_df["_intercept"].values
    intercept_mean = float(np.mean(intercept_arr))
    intercept_se = _newey_west_se_1d(intercept_arr, lags=nw_lags)
    intercept_t = (
        intercept_mean / intercept_se
        if intercept_se > 0 and not np.isnan(intercept_se)
        else 0.0
    )

    r2_avg = float(np.mean(r2_list)) if r2_list else 0.0

    return FamaMacBethResult(
        risk_premia=risk_premia,
        t_stats=t_stats,
        p_values=p_values,
        intercept=intercept_mean,
        intercept_t_stat=intercept_t,
        n_assets=len(valid_assets),
        n_periods=n_periods,
        r_squared_avg=r2_avg,
        gamma_series=gamma_df,
    )


__all__ = [
    "FamaMacBethResult",
    "fama_macbeth",
]
