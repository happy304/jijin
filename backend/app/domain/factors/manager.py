"""Manager and scale-category factors for fund metadata analysis.

Implements:
- fund_size: Current fund size (AUM in 亿元).
- size_change_rate: Rate of change in fund size between two periods.
- manager_tenure: Manager tenure in years from start_date to reference_date.
- manager_fund_count: Number of funds currently managed by the manager.

All functions follow the factor library contract:
- Input: scalar values (float, int, date) representing fund metadata.
- Output: ``float``.
- Empty/invalid data returns ``np.nan`` — never raises exceptions.
- Deterministic: same input always produces same output.

Satisfies requirement 3.6.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

import numpy as np

from app.domain.factors.registry import factor


@factor("fund_size", category="manager")
def fund_size(aum: Optional[float] = None) -> float:
    """Current fund size (AUM in 亿元).

    Parameters:
        aum: Assets under management in 亿元. None or negative
            values are treated as invalid.

    Returns:
        The AUM value as a float.
        Returns np.nan if aum is None or negative.
    """
    if aum is None:
        return np.nan
    try:
        value = float(aum)
    except (TypeError, ValueError):
        return np.nan
    if value < 0:
        return np.nan
    return value


@factor("size_change_rate", category="manager")
def size_change_rate(
    current_size: Optional[float] = None,
    previous_size: Optional[float] = None,
) -> float:
    """Rate of change in fund size between two periods.

    Calculated as: (current_size - previous_size) / previous_size

    Parameters:
        current_size: Current period AUM in 亿元.
        previous_size: Previous period AUM in 亿元.

    Returns:
        The rate of change as a decimal (e.g. 0.1 means 10% growth).
        Returns np.nan if either size is None, negative, or previous_size is zero.
    """
    if current_size is None or previous_size is None:
        return np.nan
    try:
        curr = float(current_size)
        prev = float(previous_size)
    except (TypeError, ValueError):
        return np.nan
    if curr < 0 or prev <= 0:
        return np.nan
    return (curr - prev) / prev


@factor("manager_tenure", category="manager")
def manager_tenure(
    manager_start_date: Optional[date] = None,
    reference_date: Optional[date] = None,
) -> float:
    """Manager tenure in years from start_date to reference_date.

    Calculated as the number of days between start_date and reference_date
    divided by 365.25.

    Parameters:
        manager_start_date: The date the manager started managing the fund.
        reference_date: The reference date for calculation. Defaults to today
            if None.

    Returns:
        Tenure in years as a float.
        Returns np.nan if manager_start_date is None or if reference_date
        is before manager_start_date.
    """
    if manager_start_date is None:
        return np.nan
    if reference_date is None:
        reference_date = date.today()
    try:
        delta = reference_date - manager_start_date
    except TypeError:
        return np.nan
    if delta.days < 0:
        return np.nan
    return delta.days / 365.25


@factor("manager_fund_count", category="manager")
def manager_fund_count(count: Optional[int] = None) -> float:
    """Number of funds currently managed by the manager.

    Parameters:
        count: The number of funds managed. None or negative values
            are treated as invalid.

    Returns:
        The count as a float.
        Returns np.nan if count is None or negative.
    """
    if count is None:
        return np.nan
    try:
        value = int(count)
    except (TypeError, ValueError):
        return np.nan
    if value < 0:
        return np.nan
    return float(value)
