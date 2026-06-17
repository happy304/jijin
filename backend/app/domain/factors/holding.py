"""Holding-category factors for fund portfolio analysis.

Implements:
- concentration_hhi: Herfindahl-Hirschman Index of portfolio concentration.
- top10_weight: Sum of top 10 holdings' weights.
- industry_exposure: Industry weight distribution (dominant industry weight).
- turnover: Portfolio turnover between two periods.

All functions follow the factor library contract:
- Input: ``pd.DataFrame`` with columns (stock_code, weight, industry).
- Output: ``float``, ``dict``, or ``pd.Series``.
- Empty/insufficient data returns ``np.nan`` — never raises exceptions.
- Deterministic: same input always produces same output.

Satisfies requirement 3.5.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.domain.factors.registry import factor


@factor("concentration_hhi", category="holding")
def concentration_hhi(holdings: pd.DataFrame | None) -> float:
    """Herfindahl-Hirschman Index of portfolio concentration.

    HHI = sum(weight_i^2) for all holdings.
    A higher HHI indicates more concentrated portfolio.
    Range: [1/N, 1] where N is the number of holdings.

    Parameters:
        holdings: DataFrame with at least a 'weight' column.
            Weights should be decimals (e.g. 0.05 for 5%).

    Returns:
        HHI value as a float.
        Returns np.nan if holdings is None, empty, or has no valid weights.
    """
    if holdings is None or holdings.empty:
        return np.nan
    if "weight" not in holdings.columns:
        return np.nan

    weights = holdings["weight"].dropna()
    if weights.empty:
        return np.nan

    weights = weights.astype(float)
    hhi = float((weights**2).sum())
    return hhi


@factor("top10_weight", category="holding")
def top10_weight(holdings: pd.DataFrame | None) -> float:
    """Sum of top 10 holdings' weights.

    Measures how much of the portfolio is concentrated in the
    largest 10 positions.

    Parameters:
        holdings: DataFrame with at least a 'weight' column.
            Weights should be decimals (e.g. 0.05 for 5%).

    Returns:
        Sum of top 10 weights as a decimal.
        Returns np.nan if holdings is None or empty.
    """
    if holdings is None or holdings.empty:
        return np.nan
    if "weight" not in holdings.columns:
        return np.nan

    weights = holdings["weight"].dropna().astype(float)
    if weights.empty:
        return np.nan

    # Sort descending and take top 10
    top_weights = weights.nlargest(10)
    return float(top_weights.sum())


@factor("industry_exposure", category="holding", return_type="scalar")
def industry_exposure(holdings: pd.DataFrame | None) -> dict[str, float] | float:
    """Industry weight distribution of the portfolio.

    Aggregates weights by industry to show sector allocation.

    Parameters:
        holdings: DataFrame with 'weight' and 'industry' columns.
            Weights should be decimals (e.g. 0.05 for 5%).

    Returns:
        A dict mapping industry names to their total weight.
        Returns np.nan if holdings is None, empty, or missing required columns.
    """
    if holdings is None or holdings.empty:
        return np.nan
    if "weight" not in holdings.columns or "industry" not in holdings.columns:
        return np.nan

    # Drop rows where industry or weight is NaN
    valid = holdings[["industry", "weight"]].dropna()
    if valid.empty:
        return np.nan

    valid = valid.copy()
    valid["weight"] = valid["weight"].astype(float)

    # Group by industry and sum weights
    exposure: dict[str, float] = (
        valid.groupby("industry")["weight"].sum().to_dict()
    )

    if not exposure:
        return np.nan

    return exposure


@factor("turnover", category="holding")
def turnover(
    holdings_current: pd.DataFrame | None,
    holdings_previous: pd.DataFrame | None = None,
) -> float:
    """Portfolio turnover between two periods.

    Turnover = sum(|weight_current_i - weight_previous_i|) / 2
    for all stocks appearing in either period.

    A turnover of 1.0 means the portfolio was completely replaced.

    Parameters:
        holdings_current: Current period holdings DataFrame with
            'stock_code' and 'weight' columns.
        holdings_previous: Previous period holdings DataFrame with
            'stock_code' and 'weight' columns. If None, returns np.nan.

    Returns:
        Turnover as a decimal (0 to 1).
        Returns np.nan if either period's data is missing or invalid.
    """
    if holdings_current is None or holdings_current.empty:
        return np.nan
    if holdings_previous is None or holdings_previous.empty:
        return np.nan
    if "stock_code" not in holdings_current.columns or "weight" not in holdings_current.columns:
        return np.nan
    if "stock_code" not in holdings_previous.columns or "weight" not in holdings_previous.columns:
        return np.nan

    # Build weight maps keyed by stock_code
    current = (
        holdings_current[["stock_code", "weight"]]
        .dropna()
        .set_index("stock_code")["weight"]
        .astype(float)
    )
    previous = (
        holdings_previous[["stock_code", "weight"]]
        .dropna()
        .set_index("stock_code")["weight"]
        .astype(float)
    )

    if current.empty and previous.empty:
        return np.nan

    # Union of all stock codes
    all_codes = current.index.union(previous.index)

    # Reindex to include all codes, filling missing with 0
    current_aligned = current.reindex(all_codes, fill_value=0.0)
    previous_aligned = previous.reindex(all_codes, fill_value=0.0)

    # Turnover = sum of absolute weight changes / 2
    abs_changes = (current_aligned - previous_aligned).abs().sum()
    return float(abs_changes / 2)
