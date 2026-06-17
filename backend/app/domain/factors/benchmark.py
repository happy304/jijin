"""Benchmark-related factors for fund NAV series.

Implements:
- beta: Beta coefficient (systematic risk relative to benchmark).
- tracking_error: Annualized standard deviation of excess returns.
- r_squared: R-squared (coefficient of determination from OLS regression).
- up_capture: Up capture ratio (fund performance in up-market periods).
- down_capture: Down capture ratio (fund performance in down-market periods).

All functions follow the factor library contract:
- Input: ``pd.Series`` with a DatetimeIndex (NAV values).
- Output: ``float`` (scalar) or ``pd.Series`` (rolling mode).
- Empty/insufficient data returns ``np.nan`` — never raises exceptions.
- All require a ``benchmark_nav`` parameter.

Satisfies requirement 3.4.
"""

from __future__ import annotations

from typing import Union

import numpy as np
import pandas as pd

from app.domain.factors.registry import factor


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _align_returns(
    nav: pd.Series, benchmark_nav: pd.Series, min_periods: int = 2
) -> tuple[pd.Series, pd.Series] | None:
    """Compute daily returns and align fund/benchmark on common dates.

    Returns None if insufficient data after alignment.
    """
    if nav is None or benchmark_nav is None:
        return None
    nav_clean = nav.dropna()
    bench_clean = benchmark_nav.dropna()
    if len(nav_clean) < 2 or len(bench_clean) < 2:
        return None

    fund_returns = nav_clean.pct_change().dropna()
    bench_returns = bench_clean.pct_change().dropna()

    # Filter out non-finite values
    fund_returns = fund_returns[np.isfinite(fund_returns)]
    bench_returns = bench_returns[np.isfinite(bench_returns)]

    # Align on common dates
    common_idx = fund_returns.index.intersection(bench_returns.index)
    if len(common_idx) < min_periods:
        return None

    return fund_returns.loc[common_idx], bench_returns.loc[common_idx]


# ---------------------------------------------------------------------------
# Beta
# ---------------------------------------------------------------------------


@factor("beta", category="benchmark")
def beta(
    nav: pd.Series,
    benchmark_nav: pd.Series | None = None,
    freq: int = 252,
    window: int | None = None,
) -> Union[float, pd.Series]:
    """Beta coefficient: systematic risk relative to benchmark.

    Computed via OLS regression of fund excess returns on benchmark excess
    returns. Beta = Cov(R_fund, R_bench) / Var(R_bench).

    Parameters:
        nav: Date-indexed fund NAV series.
        benchmark_nav: Date-indexed benchmark NAV series. If None, returns np.nan.
        freq: Trading days per year (default 252). Not used in beta calculation
            but kept for API consistency.
        window: If provided, compute rolling beta with this window size.

    Returns:
        Beta as a float (scalar mode) or pd.Series (rolling mode).
        Returns np.nan if data is insufficient or benchmark variance is zero.
    """
    if nav is None or len(nav) < 2:
        return np.nan if window is None else pd.Series([], dtype=float)
    if benchmark_nav is None or len(benchmark_nav) < 2:
        return np.nan if window is None else pd.Series([], dtype=float)

    aligned = _align_returns(nav, benchmark_nav, min_periods=10)
    if aligned is None:
        return np.nan if window is None else pd.Series([], dtype=float)

    fund_ret, bench_ret = aligned

    if window is not None:
        if window < 10:
            return pd.Series(np.nan, index=fund_ret.index)
        if len(fund_ret) < window:
            return pd.Series(np.nan, index=fund_ret.index)

        def _rolling_beta(idx: int) -> float:
            if idx < window:
                return np.nan
            y = fund_ret.iloc[idx - window : idx].values.astype(np.float64)
            x = bench_ret.iloc[idx - window : idx].values.astype(np.float64)
            valid = np.isfinite(x) & np.isfinite(y)
            x, y = x[valid], y[valid]
            if len(x) < 10:
                return np.nan
            x_mean = x.mean()
            var_x = ((x - x_mean) ** 2).mean()
            if var_x == 0:
                return np.nan
            return float(((x - x_mean) * (y - y.mean())).mean() / var_x)

        results = [_rolling_beta(i) for i in range(len(fund_ret))]
        return pd.Series(results, index=fund_ret.index)

    # Scalar mode — OLS beta
    x = bench_ret.values.astype(np.float64)
    y = fund_ret.values.astype(np.float64)
    valid = np.isfinite(x) & np.isfinite(y)
    x, y = x[valid], y[valid]

    if len(x) < 10:
        return np.nan

    x_mean = x.mean()
    var_x = ((x - x_mean) ** 2).mean()
    if var_x == 0:
        return np.nan

    return float(((x - x_mean) * (y - y.mean())).mean() / var_x)


# ---------------------------------------------------------------------------
# Tracking Error
# ---------------------------------------------------------------------------


@factor("tracking_error", category="benchmark")
def tracking_error(
    nav: pd.Series,
    benchmark_nav: pd.Series | None = None,
    freq: int = 252,
    window: int | None = None,
) -> Union[float, pd.Series]:
    """Tracking error: annualized standard deviation of excess returns.

    Measures how closely the fund tracks its benchmark.

    Parameters:
        nav: Date-indexed fund NAV series.
        benchmark_nav: Date-indexed benchmark NAV series. If None, returns np.nan.
        freq: Trading days per year for annualization (default 252).
        window: If provided, compute rolling tracking error.

    Returns:
        Annualized tracking error as a float or pd.Series.
        Returns np.nan if data is insufficient.
    """
    if nav is None or len(nav) < 2:
        return np.nan if window is None else pd.Series([], dtype=float)
    if benchmark_nav is None or len(benchmark_nav) < 2:
        return np.nan if window is None else pd.Series([], dtype=float)

    aligned = _align_returns(nav, benchmark_nav, min_periods=2)
    if aligned is None:
        return np.nan if window is None else pd.Series([], dtype=float)

    fund_ret, bench_ret = aligned
    excess_returns = fund_ret - bench_ret

    if window is not None:
        if window < 2:
            return pd.Series(np.nan, index=excess_returns.index)
        if len(excess_returns) < window:
            return pd.Series(np.nan, index=excess_returns.index)

        def _rolling_te(chunk: pd.Series) -> float:
            if len(chunk) < 2:
                return np.nan
            std = chunk.std(ddof=1)
            if np.isnan(std):
                return np.nan
            return float(std * np.sqrt(freq))

        rolling_result = excess_returns.rolling(
            window=window, min_periods=window
        ).apply(_rolling_te, raw=False)
        return rolling_result

    # Scalar mode
    if len(excess_returns) < 2:
        return np.nan
    std = excess_returns.std(ddof=1)
    if np.isnan(std):
        return np.nan
    return float(std * np.sqrt(freq))


# ---------------------------------------------------------------------------
# R-Squared
# ---------------------------------------------------------------------------


@factor("r_squared", category="benchmark")
def r_squared(
    nav: pd.Series,
    benchmark_nav: pd.Series | None = None,
    freq: int = 252,
    window: int | None = None,
) -> Union[float, pd.Series]:
    """R-squared: coefficient of determination from OLS regression.

    Measures the proportion of fund return variance explained by the benchmark.
    R² = 1 - SS_res / SS_tot, where the regression is fund_returns ~ benchmark_returns.

    Parameters:
        nav: Date-indexed fund NAV series.
        benchmark_nav: Date-indexed benchmark NAV series. If None, returns np.nan.
        freq: Trading days per year (default 252). Not used in R² calculation
            but kept for API consistency.
        window: If provided, compute rolling R-squared.

    Returns:
        R-squared as a float in [0, 1] or pd.Series.
        Returns np.nan if data is insufficient.
    """
    if nav is None or len(nav) < 2:
        return np.nan if window is None else pd.Series([], dtype=float)
    if benchmark_nav is None or len(benchmark_nav) < 2:
        return np.nan if window is None else pd.Series([], dtype=float)

    aligned = _align_returns(nav, benchmark_nav, min_periods=10)
    if aligned is None:
        return np.nan if window is None else pd.Series([], dtype=float)

    fund_ret, bench_ret = aligned

    if window is not None:
        if window < 10:
            return pd.Series(np.nan, index=fund_ret.index)
        if len(fund_ret) < window:
            return pd.Series(np.nan, index=fund_ret.index)

        def _rolling_r2(idx: int) -> float:
            if idx < window:
                return np.nan
            y = fund_ret.iloc[idx - window : idx].values.astype(np.float64)
            x = bench_ret.iloc[idx - window : idx].values.astype(np.float64)
            valid = np.isfinite(x) & np.isfinite(y)
            x, y = x[valid], y[valid]
            if len(x) < 10:
                return np.nan
            # OLS: y = alpha + beta * x + epsilon
            x_mean = x.mean()
            y_mean = y.mean()
            var_x = ((x - x_mean) ** 2).mean()
            if var_x == 0:
                return np.nan
            beta_val = ((x - x_mean) * (y - y_mean)).mean() / var_x
            alpha_val = y_mean - beta_val * x_mean
            y_pred = alpha_val + beta_val * x
            ss_res = ((y - y_pred) ** 2).sum()
            ss_tot = ((y - y_mean) ** 2).sum()
            if ss_tot == 0:
                return np.nan
            return float(1.0 - ss_res / ss_tot)

        results = [_rolling_r2(i) for i in range(len(fund_ret))]
        return pd.Series(results, index=fund_ret.index)

    # Scalar mode — OLS R²
    x = bench_ret.values.astype(np.float64)
    y = fund_ret.values.astype(np.float64)
    valid = np.isfinite(x) & np.isfinite(y)
    x, y = x[valid], y[valid]

    if len(x) < 10:
        return np.nan

    x_mean = x.mean()
    y_mean = y.mean()
    var_x = ((x - x_mean) ** 2).mean()
    if var_x == 0:
        return np.nan

    beta_val = ((x - x_mean) * (y - y_mean)).mean() / var_x
    alpha_val = y_mean - beta_val * x_mean
    y_pred = alpha_val + beta_val * x
    ss_res = ((y - y_pred) ** 2).sum()
    ss_tot = ((y - y_mean) ** 2).sum()
    if ss_tot == 0:
        return np.nan

    return float(1.0 - ss_res / ss_tot)


# ---------------------------------------------------------------------------
# Up Capture Ratio
# ---------------------------------------------------------------------------


@factor("up_capture", category="benchmark")
def up_capture(
    nav: pd.Series,
    benchmark_nav: pd.Series | None = None,
    freq: int = 252,
    window: int | None = None,
) -> Union[float, pd.Series]:
    """Up capture ratio: fund performance in up-market periods.

    Measures how well the fund captures benchmark gains during periods when
    the benchmark has positive returns.

    Up Capture = (geometric mean of fund returns in up periods) /
                 (geometric mean of benchmark returns in up periods) × 100

    A value > 100 means the fund outperforms the benchmark in up markets.

    Parameters:
        nav: Date-indexed fund NAV series.
        benchmark_nav: Date-indexed benchmark NAV series. If None, returns np.nan.
        freq: Trading days per year (default 252). Not used directly but kept
            for API consistency.
        window: If provided, compute rolling up capture ratio.

    Returns:
        Up capture ratio as a float (percentage) or pd.Series.
        Returns np.nan if data is insufficient or no up-market periods exist.
    """
    if nav is None or len(nav) < 2:
        return np.nan if window is None else pd.Series([], dtype=float)
    if benchmark_nav is None or len(benchmark_nav) < 2:
        return np.nan if window is None else pd.Series([], dtype=float)

    aligned = _align_returns(nav, benchmark_nav, min_periods=2)
    if aligned is None:
        return np.nan if window is None else pd.Series([], dtype=float)

    fund_ret, bench_ret = aligned

    if window is not None:
        if window < 2:
            return pd.Series(np.nan, index=fund_ret.index)
        if len(fund_ret) < window:
            return pd.Series(np.nan, index=fund_ret.index)

        def _rolling_up_capture(idx: int) -> float:
            if idx < window:
                return np.nan
            f = fund_ret.iloc[idx - window : idx].values.astype(np.float64)
            b = bench_ret.iloc[idx - window : idx].values.astype(np.float64)
            up_mask = b > 0
            if not up_mask.any():
                return np.nan
            f_up = f[up_mask]
            b_up = b[up_mask]
            # Geometric mean via compounded return
            fund_compound = np.prod(1 + f_up) ** (1.0 / len(f_up)) - 1
            bench_compound = np.prod(1 + b_up) ** (1.0 / len(b_up)) - 1
            if bench_compound == 0:
                return np.nan
            return float(fund_compound / bench_compound * 100)

        results = [_rolling_up_capture(i) for i in range(len(fund_ret))]
        return pd.Series(results, index=fund_ret.index)

    # Scalar mode
    f = fund_ret.values.astype(np.float64)
    b = bench_ret.values.astype(np.float64)
    up_mask = b > 0
    if not up_mask.any():
        return np.nan

    f_up = f[up_mask]
    b_up = b[up_mask]

    # Geometric mean via compounded return
    fund_compound = np.prod(1 + f_up) ** (1.0 / len(f_up)) - 1
    bench_compound = np.prod(1 + b_up) ** (1.0 / len(b_up)) - 1
    if bench_compound == 0:
        return np.nan

    return float(fund_compound / bench_compound * 100)


# ---------------------------------------------------------------------------
# Down Capture Ratio
# ---------------------------------------------------------------------------


@factor("down_capture", category="benchmark")
def down_capture(
    nav: pd.Series,
    benchmark_nav: pd.Series | None = None,
    freq: int = 252,
    window: int | None = None,
) -> Union[float, pd.Series]:
    """Down capture ratio: fund performance in down-market periods.

    Measures how much the fund participates in benchmark declines during
    periods when the benchmark has negative returns.

    Down Capture = (geometric mean of fund returns in down periods) /
                   (geometric mean of benchmark returns in down periods) × 100

    A value < 100 means the fund loses less than the benchmark in down markets.

    Parameters:
        nav: Date-indexed fund NAV series.
        benchmark_nav: Date-indexed benchmark NAV series. If None, returns np.nan.
        freq: Trading days per year (default 252). Not used directly but kept
            for API consistency.
        window: If provided, compute rolling down capture ratio.

    Returns:
        Down capture ratio as a float (percentage) or pd.Series.
        Returns np.nan if data is insufficient or no down-market periods exist.
    """
    if nav is None or len(nav) < 2:
        return np.nan if window is None else pd.Series([], dtype=float)
    if benchmark_nav is None or len(benchmark_nav) < 2:
        return np.nan if window is None else pd.Series([], dtype=float)

    aligned = _align_returns(nav, benchmark_nav, min_periods=2)
    if aligned is None:
        return np.nan if window is None else pd.Series([], dtype=float)

    fund_ret, bench_ret = aligned

    if window is not None:
        if window < 2:
            return pd.Series(np.nan, index=fund_ret.index)
        if len(fund_ret) < window:
            return pd.Series(np.nan, index=fund_ret.index)

        def _rolling_down_capture(idx: int) -> float:
            if idx < window:
                return np.nan
            f = fund_ret.iloc[idx - window : idx].values.astype(np.float64)
            b = bench_ret.iloc[idx - window : idx].values.astype(np.float64)
            down_mask = b < 0
            if not down_mask.any():
                return np.nan
            f_down = f[down_mask]
            b_down = b[down_mask]
            # Geometric mean via compounded return
            fund_compound = np.prod(1 + f_down) ** (1.0 / len(f_down)) - 1
            bench_compound = np.prod(1 + b_down) ** (1.0 / len(b_down)) - 1
            if bench_compound == 0:
                return np.nan
            return float(fund_compound / bench_compound * 100)

        results = [_rolling_down_capture(i) for i in range(len(fund_ret))]
        return pd.Series(results, index=fund_ret.index)

    # Scalar mode
    f = fund_ret.values.astype(np.float64)
    b = bench_ret.values.astype(np.float64)
    down_mask = b < 0
    if not down_mask.any():
        return np.nan

    f_down = f[down_mask]
    b_down = b[down_mask]

    # Geometric mean via compounded return
    fund_compound = np.prod(1 + f_down) ** (1.0 / len(f_down)) - 1
    bench_compound = np.prod(1 + b_down) ** (1.0 / len(b_down)) - 1
    if bench_compound == 0:
        return np.nan

    return float(fund_compound / bench_compound * 100)
