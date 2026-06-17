"""Return-category factors for fund NAV series.

Implements:
- total_return: Total return over the entire period.
- annualized_return: Annualized (CAGR) return.
- excess_return: Return in excess of a benchmark.
- jensen_alpha: Jensen's alpha (CAPM-based risk-adjusted excess return).

All functions follow the factor library contract:
- Input: ``pd.Series`` with a DatetimeIndex (NAV values).
- Output: ``float`` (scalar) or ``pd.Series``.
- Empty/insufficient data returns ``np.nan`` — never raises exceptions.

Satisfies requirements 3.1, 3.10, 3.12.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.domain.factors.registry import factor
from app.domain.performance.metrics import (
    annualized_return_from_nav,
    returns_from_nav,
    total_return_from_nav,
)


@factor("total_return", category="return")
def total_return(nav: pd.Series) -> float:
    """Total return over the full NAV series period.

    Calculated as (NAV_end / NAV_start) - 1.

    Parameters:
        nav: Date-indexed NAV series.

    Returns:
        Total return as a decimal (e.g. 0.15 for 15%).
        Returns np.nan if the series has fewer than 2 data points
        or the starting NAV is zero/NaN.
    """
    return total_return_from_nav(nav)


@factor("annualized_return", category="return")
def annualized_return(nav: pd.Series, freq: int = 252) -> float:
    """Annualized (CAGR) return of the NAV series.

    Uses the formula: (1 + total_return) ^ (freq / n_periods) - 1
    where n_periods is the number of data points in the series.

    Parameters:
        nav: Date-indexed NAV series.
        freq: Trading days per year (default 252 for daily data).

    Returns:
        Annualized return as a decimal.
        Returns np.nan if data is insufficient or years <= 0.
    """
    return annualized_return_from_nav(nav, freq=freq)


@factor("excess_return", category="return")
def excess_return(nav: pd.Series, benchmark_nav: pd.Series | None = None) -> float:
    """Excess return of the fund over a benchmark.

    Calculated as fund total return minus benchmark total return
    over the overlapping period.

    Parameters:
        nav: Date-indexed fund NAV series.
        benchmark_nav: Date-indexed benchmark NAV series. If None,
            returns np.nan.

    Returns:
        Excess return as a decimal.
        Returns np.nan if either series is insufficient.
    """
    if nav is None or len(nav) < 2:
        return np.nan
    if benchmark_nav is None or len(benchmark_nav) < 2:
        return np.nan

    # Align to overlapping dates
    nav_clean = nav.dropna()
    bench_clean = benchmark_nav.dropna()
    if len(nav_clean) < 2 or len(bench_clean) < 2:
        return np.nan

    # Align on exact common valuation dates. 仅按共同日期计算区间收益，
    # 避免基金和基准因节假日/缺失数据导致起止点错位。
    common_idx = nav_clean.index.intersection(bench_clean.index)
    if len(common_idx) < 2:
        return np.nan

    nav_slice = nav_clean.loc[common_idx].sort_index()
    bench_slice = bench_clean.loc[common_idx].sort_index()

    if len(nav_slice) < 2 or len(bench_slice) < 2:
        return np.nan

    fund_ret = nav_slice.iloc[-1] / nav_slice.iloc[0] - 1
    bench_ret = bench_slice.iloc[-1] / bench_slice.iloc[0] - 1

    if np.isnan(fund_ret) or np.isnan(bench_ret):
        return np.nan

    return float(fund_ret - bench_ret)


@factor("jensen_alpha", category="return")
def jensen_alpha(
    nav: pd.Series,
    benchmark_nav: pd.Series | None = None,
    risk_free_rate: float = 0.0,
    freq: int = 252,
) -> float:
    """Jensen's alpha — CAPM-based risk-adjusted excess return.

    alpha = R_fund - [R_f + beta * (R_benchmark - R_f)]

    Where beta is estimated via OLS regression of fund excess returns
    on benchmark excess returns.

    Parameters:
        nav: Date-indexed fund NAV series.
        benchmark_nav: Date-indexed benchmark NAV series.
        risk_free_rate: Annualized risk-free rate (default 0.0).
        freq: Trading days per year for converting risk-free rate to daily.

    Returns:
        Annualized Jensen's alpha as a decimal.
        Returns np.nan if data is insufficient for regression.
    """
    if nav is None or len(nav) < 2:
        return np.nan
    if benchmark_nav is None or len(benchmark_nav) < 2:
        return np.nan

    nav_clean = nav.dropna()
    bench_clean = benchmark_nav.dropna()
    if len(nav_clean) < 2 or len(bench_clean) < 2:
        return np.nan

    # Compute daily returns
    fund_returns = returns_from_nav(nav_clean)
    bench_returns = returns_from_nav(bench_clean)

    # Align on common dates
    common_idx = fund_returns.index.intersection(bench_returns.index)
    if len(common_idx) < 10:
        # Need minimum observations for meaningful regression
        return np.nan

    fund_ret = fund_returns.loc[common_idx]
    bench_ret = bench_returns.loc[common_idx]

    # Daily risk-free rate
    daily_rf = (1 + risk_free_rate) ** (1.0 / freq) - 1

    # Excess returns
    fund_excess = fund_ret - daily_rf
    bench_excess = bench_ret - daily_rf

    # OLS regression: fund_excess = alpha + beta * bench_excess
    # Using numpy for simplicity and speed
    x = bench_excess.values.astype(np.float64)
    y = fund_excess.values.astype(np.float64)

    # Remove any remaining NaN/inf
    valid = np.isfinite(x) & np.isfinite(y)
    x = x[valid]
    y = y[valid]

    if len(x) < 10:
        return np.nan

    # OLS: y = alpha + beta * x
    x_mean = x.mean()
    y_mean = y.mean()
    cov_xy = ((x - x_mean) * (y - y_mean)).mean()
    var_x = ((x - x_mean) ** 2).mean()

    if var_x == 0:
        return np.nan

    beta = cov_xy / var_x
    daily_alpha = y_mean - beta * x_mean

    # Annualize alpha
    annualized_alpha = float((1 + daily_alpha) ** freq - 1)
    return annualized_alpha
