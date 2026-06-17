"""Risk-category factors for fund NAV series.

Implements:
- volatility: Annualized volatility (std of returns × sqrt(freq)).
- downside_deviation: Downside deviation (semi-deviation below target).
- max_drawdown: Maximum drawdown from peak to trough.
- calmar: Calmar ratio (annualized return / |max drawdown|).
- var: Value at Risk at given confidence level.
- cvar: Conditional VaR (Expected Shortfall).

All functions follow the factor library contract:
- Input: ``pd.Series`` with a DatetimeIndex (NAV values).
- Output: ``float`` (scalar) or ``pd.Series`` (rolling mode).
- Empty/insufficient data returns ``np.nan`` — never raises exceptions.

Satisfies requirement 3.2.
"""

from __future__ import annotations

from typing import Union

import numpy as np
import pandas as pd

from app.domain.factors.registry import factor
from app.domain.performance.metrics import (
    annualized_return_from_nav,
    annualized_volatility_from_returns,
    calmar_ratio_from_nav,
    downside_deviation_from_returns,
    historical_cvar,
    historical_var,
    max_drawdown_from_nav,
    returns_from_nav,
    rolling_max_drawdown_from_nav,
)


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _nav_to_returns(nav: pd.Series) -> pd.Series:
    """Convert NAV series to simple daily returns, dropping NaN."""
    return returns_from_nav(nav)


# ---------------------------------------------------------------------------
# Volatility
# ---------------------------------------------------------------------------


@factor("volatility", category="risk")
def volatility(
    nav: pd.Series,
    freq: int = 252,
    window: int | None = None,
) -> Union[float, pd.Series]:
    """Annualized volatility (standard deviation of returns × sqrt(freq)).

    Parameters:
        nav: Date-indexed NAV series.
        freq: Trading days per year for annualization (default 252).
        window: If provided, compute rolling volatility with this window size.
            Returns a pd.Series instead of a scalar.

    Returns:
        Annualized volatility as a decimal (e.g. 0.20 for 20%).
        Returns np.nan if data is insufficient.
        In rolling mode, returns a pd.Series with NaN for positions
        where the window is not yet filled.
    """
    if nav is None or len(nav) < 2:
        return np.nan if window is None else pd.Series([], dtype=float)

    returns = _nav_to_returns(nav)
    if len(returns) < 2:
        return np.nan if window is None else pd.Series([], dtype=float)

    if window is not None:
        if window < 2:
            return pd.Series(np.nan, index=returns.index)
        rolling_std = returns.rolling(window=window, min_periods=window).std(ddof=1)
        return rolling_std * np.sqrt(freq)

    return annualized_volatility_from_returns(returns, freq=freq)


# ---------------------------------------------------------------------------
# Downside Deviation
# ---------------------------------------------------------------------------


@factor("downside_deviation", category="risk")
def downside_deviation(
    nav: pd.Series,
    target: float = 0.0,
    freq: int = 252,
    window: int | None = None,
) -> Union[float, pd.Series]:
    """Downside deviation (semi-deviation below target return).

    Only considers returns below the target when computing standard deviation.
    This captures downside risk more accurately than symmetric volatility.

    Parameters:
        nav: Date-indexed NAV series.
        target: Target return threshold (default 0.0, i.e. negative returns).
        freq: Trading days per year for annualization (default 252).
        window: If provided, compute rolling downside deviation.

    Returns:
        Annualized downside deviation as a decimal.
        Returns np.nan if data is insufficient.
    """
    if nav is None or len(nav) < 2:
        return np.nan if window is None else pd.Series([], dtype=float)

    returns = _nav_to_returns(nav)
    if len(returns) < 2:
        return np.nan if window is None else pd.Series([], dtype=float)

    if window is not None:
        if window < 2:
            return pd.Series(np.nan, index=returns.index)

        def _rolling_dd(chunk: pd.Series) -> float:
            diff = chunk - target
            downside = np.minimum(diff, 0.0)
            return float(np.sqrt(np.mean(np.square(downside))))

        rolling_result = returns.rolling(window=window, min_periods=window).apply(
            _rolling_dd, raw=False
        )
        return rolling_result * np.sqrt(freq)

    # Scalar mode
    return downside_deviation_from_returns(returns, target_return=target, freq=freq)


# ---------------------------------------------------------------------------
# Maximum Drawdown
# ---------------------------------------------------------------------------


@factor("max_drawdown", category="risk")
def max_drawdown(
    nav: pd.Series,
    window: int | None = None,
) -> Union[float, pd.Series]:
    """Maximum drawdown from peak to trough.

    Drawdown is expressed as a negative number (e.g. -0.20 for 20% drawdown).

    Parameters:
        nav: Date-indexed NAV series.
        window: If provided, compute rolling max drawdown over this window.

    Returns:
        Maximum drawdown as a negative decimal.
        Returns np.nan if data is insufficient (fewer than 2 points).
        Returns 0.0 if NAV is monotonically increasing.
    """
    if nav is None or len(nav) < 2:
        return np.nan if window is None else pd.Series([], dtype=float)

    clean = nav.dropna()
    if len(clean) < 2:
        return np.nan if window is None else pd.Series([], dtype=float)

    if window is not None:
        if window < 2:
            return pd.Series(np.nan, index=clean.index)

        return rolling_max_drawdown_from_nav(clean, window=window)

    # Scalar mode
    return max_drawdown_from_nav(clean)


# ---------------------------------------------------------------------------
# Calmar Ratio
# ---------------------------------------------------------------------------


@factor("calmar", category="risk")
def calmar(
    nav: pd.Series,
    freq: int = 252,
    window: int | None = None,
) -> Union[float, pd.Series]:
    """Calmar ratio: annualized return / |max drawdown|.

    A higher Calmar ratio indicates better risk-adjusted performance
    relative to the worst drawdown experienced.

    Parameters:
        nav: Date-indexed NAV series.
        freq: Trading days per year (default 252).
        window: If provided, compute rolling Calmar ratio.

    Returns:
        Calmar ratio as a float.
        Returns np.nan if max drawdown is zero or data is insufficient.
    """
    if nav is None or len(nav) < 2:
        return np.nan if window is None else pd.Series([], dtype=float)

    clean = nav.dropna()
    if len(clean) < 2:
        return np.nan if window is None else pd.Series([], dtype=float)

    if window is not None:
        if window < 2:
            return pd.Series(np.nan, index=clean.index)

        def _rolling_calmar(chunk: pd.Series) -> float:
            return calmar_ratio_from_nav(chunk, freq=freq)

        rolling_result = clean.rolling(window=window, min_periods=window).apply(
            _rolling_calmar, raw=False
        )
        return rolling_result

    # Scalar mode
    return calmar_ratio_from_nav(clean, freq=freq)


# ---------------------------------------------------------------------------
# Calmar Ratio (36-month rolling, industry standard)
# ---------------------------------------------------------------------------


@factor("calmar_36m", category="risk")
def calmar_36m(
    nav: pd.Series,
    freq: int = 252,
) -> float:
    """Calmar ratio over the trailing 36 months (industry standard definition).

    Standard industry definition (used by Morningstar, MAR Hedge etc.):
    Calmar = annualized return over past 36 months / |max drawdown over past 36 months|.

    Falls back to full-sample Calmar if the series is shorter than 36 months.

    The "full-sample" ``calmar`` factor in this module dilutes recent drawdowns
    and tends to over-state risk-adjusted performance for long histories. Use
    this 36-month version for client reports and peer-group comparisons.

    Parameters:
        nav: Date-indexed NAV series.
        freq: Trading days per year (default 252).

    Returns:
        36-month Calmar ratio. np.nan if max drawdown is zero or data is empty.
    """
    if nav is None or len(nav) < 2:
        return np.nan

    clean = nav.dropna()
    if len(clean) < 2:
        return np.nan

    # 36 months ≈ 36 / 12 × freq trading days
    window_days = 3 * freq

    if len(clean) > window_days:
        clean = clean.iloc[-window_days:]

    return calmar_ratio_from_nav(clean, freq=freq)


# ---------------------------------------------------------------------------
# Value at Risk (VaR)
# ---------------------------------------------------------------------------


@factor("var", category="risk")
def var(
    nav: pd.Series,
    confidence: float = 0.95,
    window: int | None = None,
) -> Union[float, pd.Series]:
    """Value at Risk at a given confidence level (historical method).

    VaR represents the maximum expected loss at the given confidence level.
    Returned using the unified positive-loss口径 (e.g. 0.02 means 2% loss).

    Parameters:
        nav: Date-indexed NAV series.
        confidence: Confidence level (default 0.95). Common values: 0.95, 0.99.
        window: If provided, compute rolling VaR.

    Returns:
        VaR as a positive loss decimal. Returns np.nan if data is insufficient.
    """
    if nav is None or len(nav) < 2:
        return np.nan if window is None else pd.Series([], dtype=float)

    returns = _nav_to_returns(nav)
    if len(returns) < 2:
        return np.nan if window is None else pd.Series([], dtype=float)

    if window is not None:
        if window < 2:
            return pd.Series(np.nan, index=returns.index)

        def _rolling_var(chunk: pd.Series) -> float:
            return historical_var(chunk, confidence=confidence, min_periods=window)

        return returns.rolling(window=window, min_periods=window).apply(
            _rolling_var,
            raw=False,
        )

    return historical_var(returns, confidence=confidence, min_periods=2)


# ---------------------------------------------------------------------------
# Conditional VaR (CVaR / Expected Shortfall)
# ---------------------------------------------------------------------------


@factor("cvar", category="risk")
def cvar(
    nav: pd.Series,
    confidence: float = 0.95,
    window: int | None = None,
) -> Union[float, pd.Series]:
    """Conditional VaR (Expected Shortfall) at a given confidence level.

    CVaR is the expected loss given that the loss exceeds VaR.
    It captures tail risk better than VaR alone.
    Returned using the unified positive-loss口径.

    Parameters:
        nav: Date-indexed NAV series.
        confidence: Confidence level (default 0.95).
        window: If provided, compute rolling CVaR.

    Returns:
        CVaR as a positive loss decimal. Returns np.nan if data is insufficient.
    """
    if nav is None or len(nav) < 2:
        return np.nan if window is None else pd.Series([], dtype=float)

    returns = _nav_to_returns(nav)
    if len(returns) < 2:
        return np.nan if window is None else pd.Series([], dtype=float)

    if window is not None:
        if window < 2:
            return pd.Series(np.nan, index=returns.index)

        def _rolling_cvar(chunk: pd.Series) -> float:
            return historical_cvar(chunk, confidence=confidence, min_periods=window)

        return returns.rolling(window=window, min_periods=window).apply(
            _rolling_cvar,
            raw=False,
        )

    return historical_cvar(returns, confidence=confidence, min_periods=2)
