"""Unified performance metric helpers.

These helpers define stable calculation口径 for reusable performance metrics.
Domain-level functions return ``np.nan`` when data is insufficient; API or
reporting layers may choose to render those as ``null`` or fallback values.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

import numpy as np
import pandas as pd


METRIC_VERSION = "2026-06-03"


def _as_clean_returns(returns: Sequence[float] | pd.Series) -> pd.Series:
    """Convert returns to a finite float Series, dropping NaN/inf values."""
    if returns is None:
        return pd.Series([], dtype=float)
    series = returns if isinstance(returns, pd.Series) else pd.Series(list(returns), dtype=float)
    series = series.dropna().astype(float)
    return series[np.isfinite(series)]


def returns_from_nav(nav: pd.Series | None) -> pd.Series:
    """Convert a NAV series to simple period returns.

    Returns an empty Series when fewer than two valid NAV points are available.
    """
    if nav is None or len(nav) < 2:
        return pd.Series([], dtype=float)
    clean = nav.dropna().astype(float)
    if len(clean) < 2:
        return pd.Series([], dtype=float)
    returns = clean.pct_change().dropna()
    return returns[np.isfinite(returns)]


def total_return_from_nav(nav: pd.Series | None) -> float:
    """Compute total return from first to last valid NAV point."""
    if nav is None or len(nav) < 2:
        return np.nan
    clean = nav.dropna().astype(float)
    if len(clean) < 2:
        return np.nan

    start_val = clean.iloc[0]
    end_val = clean.iloc[-1]
    if start_val <= 0 or not np.isfinite(start_val) or not np.isfinite(end_val):
        return np.nan
    return float(end_val / start_val - 1)


def annualized_return_from_nav(nav: pd.Series | None, freq: int = 252) -> float:
    """Compute CAGR from NAV using ``n - 1`` return intervals."""
    if nav is None or len(nav) < 2:
        return np.nan
    clean = nav.dropna().astype(float)
    if len(clean) < 2:
        return np.nan

    total = total_return_from_nav(clean)
    if not np.isfinite(total):
        return np.nan

    base = 1 + total
    if base <= 0:
        return np.nan

    n_periods = len(clean) - 1
    years = n_periods / freq
    if years <= 0:
        return np.nan
    return float(base ** (1.0 / years) - 1)


def annualized_volatility_from_returns(
    returns: Sequence[float] | pd.Series,
    freq: int = 252,
) -> float:
    """Annualized sample volatility, using ``ddof=1``."""
    clean = _as_clean_returns(returns)
    if len(clean) < 2:
        return np.nan
    std = clean.std(ddof=1)
    if not np.isfinite(std):
        return np.nan
    return float(std * np.sqrt(freq))


def sharpe_ratio_from_returns(
    returns: Sequence[float] | pd.Series,
    risk_free_rate: float = 0.0,
    freq: int = 252,
) -> float:
    """Annualized Sharpe ratio from period returns.

    Uses geometric daily risk-free conversion and sample standard deviation of
    raw period returns.
    """
    clean = _as_clean_returns(returns)
    if len(clean) < 2:
        return np.nan

    daily_rf = (1 + risk_free_rate) ** (1.0 / freq) - 1
    excess = clean - daily_rf
    std = clean.std(ddof=1)
    if std == 0 or not np.isfinite(std):
        return np.nan
    return float(excess.mean() / std * np.sqrt(freq))


def downside_deviation_from_returns(
    returns: Sequence[float] | pd.Series,
    target_return: float = 0.0,
    freq: int = 252,
) -> float:
    """Annualized downside deviation using the full sample as denominator.

    口径：``sqrt(mean(min(r_i - target, 0)^2)) * sqrt(freq)``.
    This matches the backtest result module and avoids inflating Sortino when
    downside observations are rare.
    """
    clean = _as_clean_returns(returns)
    if len(clean) < 2:
        return np.nan

    diff = clean - target_return
    downside = np.minimum(diff, 0.0)
    daily_downside_std = float(np.sqrt(np.mean(np.square(downside))))
    if not np.isfinite(daily_downside_std):
        return np.nan
    return daily_downside_std * np.sqrt(freq)


def sortino_ratio_from_returns(
    returns: Sequence[float] | pd.Series,
    risk_free_rate: float = 0.0,
    freq: int = 252,
) -> float:
    """Annualized Sortino ratio using full-sample downside deviation."""
    clean = _as_clean_returns(returns)
    if len(clean) < 2:
        return np.nan

    daily_rf = (1 + risk_free_rate) ** (1.0 / freq) - 1
    excess = clean - daily_rf
    ann_downside = downside_deviation_from_returns(
        clean,
        target_return=daily_rf,
        freq=freq,
    )
    if ann_downside == 0 or not np.isfinite(ann_downside):
        return np.nan
    ann_excess = float(excess.mean() * freq)
    return ann_excess / ann_downside


def max_drawdown_from_nav(nav: pd.Series | None) -> float:
    """Compute maximum drawdown from NAV as a negative decimal."""
    if nav is None or len(nav) < 2:
        return np.nan
    clean = nav.dropna().astype(float)
    if len(clean) < 2:
        return np.nan

    drawdown = drawdown_series_from_nav(clean)
    if len(drawdown) == 0:
        return np.nan
    return float(drawdown.min())


def _index_to_date(value: Any) -> date | None:
    """Best-effort conversion of a pandas index value to ``date``."""
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        ts = pd.Timestamp(value)
        if pd.isna(ts):
            return None
        return ts.date()
    except Exception:
        return None


def drawdown_details_from_nav(nav: pd.Series | None) -> dict[str, Any]:
    """Return max drawdown details using the unified negative-drawdown口径.

    Returns an auditable dictionary with peak/trough/recovery dates. Recovery
    date is the first later point where NAV returns to or above the peak that
    started the maximum drawdown. If no recovery occurs, it is ``None``.
    """
    empty = {
        "max_drawdown": np.nan,
        "peak_date": None,
        "trough_date": None,
        "recovery_date": None,
        "recovery_days": None,
    }
    if nav is None or len(nav) < 2:
        return empty
    clean = nav.dropna().astype(float)
    clean = clean[np.isfinite(clean)]
    if len(clean) < 2:
        return empty

    values = clean.to_numpy(dtype=float)
    if np.any(values <= 0):
        return empty

    running_peak = np.maximum.accumulate(values)
    drawdowns = (values - running_peak) / running_peak
    trough_idx = int(np.argmin(drawdowns))
    max_dd = float(drawdowns[trough_idx])
    if max_dd >= 0:
        return {
            "max_drawdown": 0.0,
            "peak_date": None,
            "trough_date": None,
            "recovery_date": None,
            "recovery_days": None,
        }

    peak_value = running_peak[trough_idx]
    peak_candidates = np.where(values[: trough_idx + 1] >= peak_value - 1e-12)[0]
    peak_idx = int(peak_candidates[0]) if len(peak_candidates) else 0

    recovery_idx: int | None = None
    for idx in range(trough_idx + 1, len(values)):
        if values[idx] >= peak_value - 1e-12:
            recovery_idx = idx
            break

    peak_date = _index_to_date(clean.index[peak_idx])
    trough_date = _index_to_date(clean.index[trough_idx])
    recovery_date = _index_to_date(clean.index[recovery_idx]) if recovery_idx is not None else None
    recovery_days = (
        (recovery_date - trough_date).days
        if recovery_date is not None and trough_date is not None
        else None
    )

    return {
        "max_drawdown": max_dd,
        "peak_date": peak_date,
        "trough_date": trough_date,
        "recovery_date": recovery_date,
        "recovery_days": recovery_days,
    }


def drawdown_series_from_nav(nav: pd.Series | None) -> pd.Series:
    """Compute point-in-time drawdown series from NAV as negative decimals."""
    if nav is None or len(nav) == 0:
        return pd.Series([], dtype=float)
    clean = nav.dropna().astype(float)
    if len(clean) == 0:
        return pd.Series([], dtype=float)

    peak = clean.expanding().max()
    drawdown = (clean - peak) / peak
    return drawdown[np.isfinite(drawdown)]


def rolling_max_drawdown_from_nav(nav: pd.Series | None, window: int) -> pd.Series:
    """Compute rolling maximum drawdown from NAV as negative decimals."""
    if nav is None or len(nav) == 0:
        return pd.Series([], dtype=float)
    clean = nav.dropna().astype(float)
    if len(clean) == 0:
        return pd.Series([], dtype=float)
    if window < 2:
        return pd.Series(np.nan, index=clean.index)

    def _rolling_mdd(chunk: pd.Series) -> float:
        value = max_drawdown_from_nav(chunk)
        return float(value) if np.isfinite(value) else np.nan

    return clean.rolling(window=window, min_periods=window).apply(_rolling_mdd, raw=False)


def calmar_ratio_from_nav(nav: pd.Series | None, freq: int = 252) -> float:
    """Compute Calmar ratio as CAGR divided by absolute max drawdown."""
    ann_return = annualized_return_from_nav(nav, freq=freq)
    mdd = max_drawdown_from_nav(nav)
    if not np.isfinite(ann_return) or not np.isfinite(mdd) or mdd == 0:
        return np.nan
    return float(ann_return / abs(mdd))


def historical_var(
    returns: Sequence[float] | pd.Series,
    confidence: float = 0.95,
    min_periods: int = 10,
) -> float:
    """Historical VaR as a positive loss number."""
    clean = _as_clean_returns(returns)
    if len(clean) < min_periods:
        return np.nan
    sorted_returns = np.sort(clean.to_numpy(dtype=float))
    index = int((1 - confidence) * len(sorted_returns))
    index = max(0, min(index, len(sorted_returns) - 1))
    return float(max(0.0, -sorted_returns[index]))


def historical_cvar(
    returns: Sequence[float] | pd.Series,
    confidence: float = 0.95,
    min_periods: int = 10,
) -> float:
    """Historical CVaR / expected shortfall as a positive loss number."""
    clean = _as_clean_returns(returns)
    if len(clean) < min_periods:
        return np.nan
    sorted_returns = np.sort(clean.to_numpy(dtype=float))
    cutoff_index = int((1 - confidence) * len(sorted_returns))
    cutoff_index = max(1, cutoff_index)
    tail_returns = sorted_returns[:cutoff_index]
    if len(tail_returns) == 0:
        return np.nan
    return float(max(0.0, -tail_returns.mean()))
