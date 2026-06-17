"""Risk-adjusted return factors for fund NAV series.

Implements:
- sharpe: Sharpe ratio (annualized return - risk_free_rate) / annualized volatility.
- sortino: Sortino ratio (annualized return - risk_free_rate) / annualized downside deviation.
- information_ratio: Information ratio — annualized excess return / tracking error.
- treynor: Treynor ratio (annualized return - risk_free_rate) / beta.

All functions follow the factor library contract:
- Input: ``pd.Series`` with a DatetimeIndex (NAV values).
- Output: ``float`` (scalar) or ``pd.Series`` (rolling mode).
- Empty/insufficient data returns ``np.nan`` — never raises exceptions.

Satisfies requirement 3.3.
"""

from __future__ import annotations

from typing import Union

import numpy as np
import pandas as pd

from app.domain.factors.registry import factor
from app.domain.performance.metrics import (
    returns_from_nav,
    sharpe_ratio_from_returns,
    sortino_ratio_from_returns,
)


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _nav_to_returns(nav: pd.Series) -> pd.Series:
    """Convert NAV series to simple daily returns, dropping NaN."""
    if nav is None or len(nav) < 2:
        return pd.Series([], dtype=float)
    clean = nav.dropna()
    if len(clean) < 2:
        return pd.Series([], dtype=float)
    returns = clean.pct_change().dropna()
    returns = returns[np.isfinite(returns)]
    return returns


# ---------------------------------------------------------------------------
# Sharpe Ratio
# ---------------------------------------------------------------------------


@factor("sharpe", category="risk_adjusted")
def sharpe(
    nav: pd.Series,
    risk_free_rate: float = 0.0,
    freq: int = 252,
    window: int | None = None,
) -> Union[float, pd.Series]:
    """Sharpe ratio: (annualized return - risk_free_rate) / annualized volatility.

    Parameters:
        nav: Date-indexed NAV series.
        risk_free_rate: Annualized risk-free rate (default 0.0).
        freq: Trading days per year for annualization (default 252).
        window: If provided, compute rolling Sharpe ratio with this window size.

    Returns:
        Sharpe ratio as a float.
        Returns np.nan if data is insufficient or volatility is zero.
        In rolling mode, returns a pd.Series.
    """
    if nav is None or len(nav) < 2:
        return np.nan if window is None else pd.Series([], dtype=float)

    nav_clean = nav.dropna()
    if len(nav_clean) < 2:
        return np.nan if window is None else pd.Series([], dtype=float)

    if window is not None:
        if window < 2:
            return pd.Series(np.nan, index=nav_clean.index[1:])

        returns = nav_clean.pct_change().dropna()
        returns = returns[np.isfinite(returns)]
        if len(returns) < window:
            return pd.Series(np.nan, index=returns.index)

        daily_rf = (1 + risk_free_rate) ** (1.0 / freq) - 1

        def _rolling_sharpe(chunk: pd.Series) -> float:
            if len(chunk) < 2:
                return np.nan
            excess = chunk - daily_rf
            mean_excess = excess.mean()
            std_excess = chunk.std(ddof=1)
            if std_excess == 0 or np.isnan(std_excess):
                return np.nan
            return float(mean_excess / std_excess * np.sqrt(freq))

        rolling_result = returns.rolling(window=window, min_periods=window).apply(
            _rolling_sharpe, raw=False
        )
        return rolling_result

    # Scalar mode uses the shared arithmetic excess-return Sharpe 口径,
    # matching the backtest result layer and unified performance helpers.
    returns = returns_from_nav(nav_clean)
    return sharpe_ratio_from_returns(
        returns,
        risk_free_rate=risk_free_rate,
        freq=freq,
    )


# ---------------------------------------------------------------------------
# Sortino Ratio
# ---------------------------------------------------------------------------


@factor("sortino", category="risk_adjusted")
def sortino(
    nav: pd.Series,
    risk_free_rate: float = 0.0,
    freq: int = 252,
    window: int | None = None,
) -> Union[float, pd.Series]:
    """Sortino ratio: (annualized return - risk_free_rate) / annualized downside deviation.

    Uses downside deviation (semi-deviation below the daily risk-free rate)
    instead of total volatility, penalizing only downside risk.

    Parameters:
        nav: Date-indexed NAV series.
        risk_free_rate: Annualized risk-free rate (default 0.0).
        freq: Trading days per year for annualization (default 252).
        window: If provided, compute rolling Sortino ratio.

    Returns:
        Sortino ratio as a float.
        Returns np.nan if data is insufficient or downside deviation is zero.
        In rolling mode, returns a pd.Series.
    """
    if nav is None or len(nav) < 2:
        return np.nan if window is None else pd.Series([], dtype=float)

    nav_clean = nav.dropna()
    if len(nav_clean) < 2:
        return np.nan if window is None else pd.Series([], dtype=float)

    if window is not None:
        if window < 2:
            return pd.Series(np.nan, index=nav_clean.index[1:])

        returns = returns_from_nav(nav_clean)
        if len(returns) < window:
            return pd.Series(np.nan, index=returns.index)

        def _rolling_sortino(chunk: pd.Series) -> float:
            return sortino_ratio_from_returns(
                chunk,
                risk_free_rate=risk_free_rate,
                freq=freq,
            )

        rolling_result = returns.rolling(window=window, min_periods=window).apply(
            _rolling_sortino, raw=False
        )
        return rolling_result

    # Scalar mode uses the shared full-sample downside deviation口径.
    returns = returns_from_nav(nav_clean)
    return sortino_ratio_from_returns(
        returns,
        risk_free_rate=risk_free_rate,
        freq=freq,
    )


# ---------------------------------------------------------------------------
# Information Ratio
# ---------------------------------------------------------------------------


@factor("information_ratio", category="risk_adjusted")
def information_ratio(
    nav: pd.Series,
    benchmark_nav: pd.Series | None = None,
    freq: int = 252,
    window: int | None = None,
) -> Union[float, pd.Series]:
    """Information ratio: annualized excess return / tracking error.

    Measures the consistency of excess returns relative to a benchmark.

    Parameters:
        nav: Date-indexed fund NAV series.
        benchmark_nav: Date-indexed benchmark NAV series. If None, returns np.nan.
        freq: Trading days per year for annualization (default 252).
        window: If provided, compute rolling information ratio.

    Returns:
        Information ratio as a float.
        Returns np.nan if data is insufficient or tracking error is zero.
        In rolling mode, returns a pd.Series.
    """
    if nav is None or len(nav) < 2:
        return np.nan if window is None else pd.Series([], dtype=float)
    if benchmark_nav is None or len(benchmark_nav) < 2:
        return np.nan if window is None else pd.Series([], dtype=float)

    nav_clean = nav.dropna()
    bench_clean = benchmark_nav.dropna()
    if len(nav_clean) < 2 or len(bench_clean) < 2:
        return np.nan if window is None else pd.Series([], dtype=float)

    # Compute daily returns
    fund_returns = nav_clean.pct_change().dropna()
    bench_returns = bench_clean.pct_change().dropna()

    # Align on common dates
    common_idx = fund_returns.index.intersection(bench_returns.index)
    if len(common_idx) < 2:
        return np.nan if window is None else pd.Series([], dtype=float)

    fund_ret = fund_returns.loc[common_idx]
    bench_ret = bench_returns.loc[common_idx]

    # Excess returns (active returns)
    excess_returns = fund_ret - bench_ret

    if window is not None:
        if window < 2:
            return pd.Series(np.nan, index=excess_returns.index)

        if len(excess_returns) < window:
            return pd.Series(np.nan, index=excess_returns.index)

        def _rolling_ir(chunk: pd.Series) -> float:
            if len(chunk) < 2:
                return np.nan
            te = chunk.std(ddof=1)
            if te == 0 or np.isnan(te):
                return np.nan
            ann_excess = chunk.mean() * freq
            ann_te = te * np.sqrt(freq)
            return float(ann_excess / ann_te)

        rolling_result = excess_returns.rolling(
            window=window, min_periods=window
        ).apply(_rolling_ir, raw=False)
        return rolling_result

    # Scalar mode
    tracking_error = excess_returns.std(ddof=1)
    if tracking_error == 0 or np.isnan(tracking_error):
        return np.nan

    ann_excess = excess_returns.mean() * freq
    ann_te = tracking_error * np.sqrt(freq)

    return float(ann_excess / ann_te)


# ---------------------------------------------------------------------------
# Treynor Ratio
# ---------------------------------------------------------------------------


@factor("treynor", category="risk_adjusted")
def treynor(
    nav: pd.Series,
    benchmark_nav: pd.Series | None = None,
    risk_free_rate: float = 0.0,
    freq: int = 252,
    window: int | None = None,
) -> Union[float, pd.Series]:
    """Treynor ratio: (annualized return - risk_free_rate) / beta.

    Measures excess return per unit of systematic risk (beta).

    Parameters:
        nav: Date-indexed fund NAV series.
        benchmark_nav: Date-indexed benchmark NAV series. If None, returns np.nan.
        risk_free_rate: Annualized risk-free rate (default 0.0).
        freq: Trading days per year for annualization (default 252).
        window: If provided, compute rolling Treynor ratio.

    Returns:
        Treynor ratio as a float.
        Returns np.nan if data is insufficient or beta is zero.
        In rolling mode, returns a pd.Series.
    """
    if nav is None or len(nav) < 2:
        return np.nan if window is None else pd.Series([], dtype=float)
    if benchmark_nav is None or len(benchmark_nav) < 2:
        return np.nan if window is None else pd.Series([], dtype=float)

    nav_clean = nav.dropna()
    bench_clean = benchmark_nav.dropna()
    if len(nav_clean) < 2 or len(bench_clean) < 2:
        return np.nan if window is None else pd.Series([], dtype=float)

    # Compute daily returns
    fund_returns = nav_clean.pct_change().dropna()
    bench_returns = bench_clean.pct_change().dropna()

    # Align on common dates
    common_idx = fund_returns.index.intersection(bench_returns.index)
    if len(common_idx) < 10:
        return np.nan if window is None else pd.Series([], dtype=float)

    fund_ret = fund_returns.loc[common_idx]
    bench_ret = bench_returns.loc[common_idx]

    # Daily risk-free rate
    daily_rf = (1 + risk_free_rate) ** (1.0 / freq) - 1

    # Excess returns
    fund_excess = fund_ret - daily_rf
    bench_excess = bench_ret - daily_rf

    if window is not None:
        if window < 10:
            return pd.Series(np.nan, index=fund_excess.index)

        def _rolling_treynor(idx: int) -> float:
            if idx < window:
                return np.nan
            chunk_fund = fund_excess.iloc[idx - window : idx]
            chunk_bench = bench_excess.iloc[idx - window : idx]
            x = chunk_bench.values.astype(np.float64)
            y = chunk_fund.values.astype(np.float64)
            valid = np.isfinite(x) & np.isfinite(y)
            x = x[valid]
            y = y[valid]
            if len(x) < 10:
                return np.nan
            var_x = ((x - x.mean()) ** 2).mean()
            if var_x == 0:
                return np.nan
            beta = ((x - x.mean()) * (y - y.mean())).mean() / var_x
            if beta == 0:
                return np.nan
            # Annualized excess return for the window
            ann_excess = y.mean() * freq
            return float(ann_excess / beta)

        results = [_rolling_treynor(i) for i in range(len(fund_excess))]
        return pd.Series(results, index=fund_excess.index)

    # Scalar mode
    x = bench_excess.values.astype(np.float64)
    y = fund_excess.values.astype(np.float64)

    valid = np.isfinite(x) & np.isfinite(y)
    x = x[valid]
    y = y[valid]

    if len(x) < 10:
        return np.nan

    # Beta via OLS
    x_mean = x.mean()
    var_x = ((x - x_mean) ** 2).mean()
    if var_x == 0:
        return np.nan
    beta = ((x - x_mean) * (y - y.mean())).mean() / var_x
    if beta == 0:
        return np.nan

    # Annualized excess return must use the same aligned sample as beta.
    # 否则当基金和基准日期范围不一致时，分子用全样本收益、分母用重叠样本 beta，
    # Treynor 会被样本错配污染。
    ann_excess = float(y.mean() * freq)

    return float(ann_excess / beta)
