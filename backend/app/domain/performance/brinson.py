"""Brinson attribution model.

Decomposes portfolio excess return into three effects:
    - Allocation Effect: Σ(w_i - W_i) × R_i (benchmark sector return)
    - Selection Effect: Σ W_i × (r_i - R_i)
    - Interaction Effect: Σ(w_i - W_i) × (r_i - R_i)

Where:
    w_i = portfolio weight in sector i
    W_i = benchmark weight in sector i
    r_i = portfolio return in sector i
    R_i = benchmark return in sector i

Multi-period linking:
    The arithmetic sum of single-period effects does not equal the
    geometrically-compounded total excess return. This module provides
    the **Carino logarithmic linking method** (Carino 1999) to smooth
    single-period effects so they sum exactly to the multi-period total.

References:
    - Brinson, Hood & Beebower (1986): Determinants of Portfolio Performance
    - Brinson & Fachler (1985): Measuring Non-US Equity Portfolio Performance
    - Carino, D. (1999): Combining Attribution Effects Over Time. JPM Vol 25 No 5.

Satisfies requirement 3.8.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BrinsonResult:
    """Result of Brinson attribution analysis.

    Attributes:
        allocation_effect: Dict mapping sector name to its allocation effect,
            plus a 'total' key with the sum.
        selection_effect: Dict mapping sector name to its selection effect,
            plus a 'total' key with the sum.
        interaction_effect: Dict mapping sector name to its interaction effect,
            plus a 'total' key with the sum.
        total_excess_return: Total portfolio excess return over benchmark.
            Should equal sum of all three total effects.
        sectors: List of all sectors involved in the attribution.
    """

    allocation_effect: dict[str, float] = field(default_factory=dict)
    selection_effect: dict[str, float] = field(default_factory=dict)
    interaction_effect: dict[str, float] = field(default_factory=dict)
    total_excess_return: float = 0.0
    sectors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def brinson_attribution(
    portfolio_weights: dict[str, float],
    benchmark_weights: dict[str, float],
    portfolio_returns: dict[str, float],
    benchmark_returns: dict[str, float],
) -> BrinsonResult:
    """Perform single-period Brinson attribution.

    Decomposes the excess return of a portfolio relative to a benchmark
    into allocation, selection, and interaction effects by sector.

    Parameters:
        portfolio_weights: Dict mapping sector name to portfolio weight in that sector.
            Weights should sum to 1.0 (or close to it).
        benchmark_weights: Dict mapping sector name to benchmark weight in that sector.
            Weights should sum to 1.0 (or close to it).
        portfolio_returns: Dict mapping sector name to portfolio return in that sector.
        benchmark_returns: Dict mapping sector name to benchmark return in that sector.

    Returns:
        BrinsonResult with per-sector and total attribution effects.

    Notes:
        - Sectors present in one dict but not another are treated as having 0 weight
          and/or 0 return in the missing set.
        - Empty inputs return a BrinsonResult with all zeros.
        - The function is deterministic: same inputs always produce same outputs.
    """
    # Handle None inputs gracefully
    if portfolio_weights is None:
        portfolio_weights = {}
    if benchmark_weights is None:
        benchmark_weights = {}
    if portfolio_returns is None:
        portfolio_returns = {}
    if benchmark_returns is None:
        benchmark_returns = {}

    # Collect all sectors from all inputs
    all_sectors = sorted(
        set(portfolio_weights.keys())
        | set(benchmark_weights.keys())
        | set(portfolio_returns.keys())
        | set(benchmark_returns.keys())
    )

    if not all_sectors:
        return BrinsonResult()

    # Compute per-sector effects
    allocation: dict[str, float] = {}
    selection: dict[str, float] = {}
    interaction: dict[str, float] = {}

    total_allocation = 0.0
    total_selection = 0.0
    total_interaction = 0.0

    for sector in all_sectors:
        w_i = portfolio_weights.get(sector, 0.0)  # portfolio weight
        W_i = benchmark_weights.get(sector, 0.0)  # benchmark weight
        r_i = portfolio_returns.get(sector, 0.0)  # portfolio sector return
        R_i = benchmark_returns.get(sector, 0.0)  # benchmark sector return

        # Allocation Effect: (w_i - W_i) × R_i
        alloc_i = (w_i - W_i) * R_i
        allocation[sector] = alloc_i
        total_allocation += alloc_i

        # Selection Effect: W_i × (r_i - R_i)
        sel_i = W_i * (r_i - R_i)
        selection[sector] = sel_i
        total_selection += sel_i

        # Interaction Effect: (w_i - W_i) × (r_i - R_i)
        inter_i = (w_i - W_i) * (r_i - R_i)
        interaction[sector] = inter_i
        total_interaction += inter_i

    # Add totals
    allocation["total"] = total_allocation
    selection["total"] = total_selection
    interaction["total"] = total_interaction

    # Total excess return = allocation + selection + interaction
    total_excess = total_allocation + total_selection + total_interaction

    return BrinsonResult(
        allocation_effect=allocation,
        selection_effect=selection,
        interaction_effect=interaction,
        total_excess_return=total_excess,
        sectors=all_sectors,
    )


# ---------------------------------------------------------------------------
# Brinson-Fachler variant
# ---------------------------------------------------------------------------


def brinson_fachler_attribution(
    portfolio_weights: dict[str, float],
    benchmark_weights: dict[str, float],
    portfolio_returns: dict[str, float],
    benchmark_returns: dict[str, float],
) -> BrinsonResult:
    """Brinson-Fachler variant of Brinson attribution.

    Differs from the original BHB model in the **allocation effect**:
        Allocation_BF = Σ(w_i - W_i) × (R_i - R_b)

    where R_b is the **total benchmark return** (Σ W_i × R_i). This isolates
    the bet on a sector relative to the overall benchmark, which is more
    intuitive for sector rotation strategies.

    Selection and interaction effects are unchanged from the original BHB.

    Parameters:
        portfolio_weights: Sector weights in portfolio.
        benchmark_weights: Sector weights in benchmark.
        portfolio_returns: Sector returns in portfolio.
        benchmark_returns: Sector returns in benchmark.

    Returns:
        BrinsonResult with Brinson-Fachler allocation effects.
    """
    if portfolio_weights is None:
        portfolio_weights = {}
    if benchmark_weights is None:
        benchmark_weights = {}
    if portfolio_returns is None:
        portfolio_returns = {}
    if benchmark_returns is None:
        benchmark_returns = {}

    all_sectors = sorted(
        set(portfolio_weights.keys())
        | set(benchmark_weights.keys())
        | set(portfolio_returns.keys())
        | set(benchmark_returns.keys())
    )

    if not all_sectors:
        return BrinsonResult()

    # Total benchmark return (used in Brinson-Fachler allocation)
    bench_total_return = sum(
        benchmark_weights.get(s, 0.0) * benchmark_returns.get(s, 0.0)
        for s in all_sectors
    )

    allocation: dict[str, float] = {}
    selection: dict[str, float] = {}
    interaction: dict[str, float] = {}

    total_allocation = 0.0
    total_selection = 0.0
    total_interaction = 0.0

    for sector in all_sectors:
        w_i = portfolio_weights.get(sector, 0.0)
        W_i = benchmark_weights.get(sector, 0.0)
        r_i = portfolio_returns.get(sector, 0.0)
        R_i = benchmark_returns.get(sector, 0.0)

        # Brinson-Fachler allocation: (w_i - W_i) × (R_i - R_b)
        alloc_i = (w_i - W_i) * (R_i - bench_total_return)
        allocation[sector] = alloc_i
        total_allocation += alloc_i

        # Selection (unchanged): W_i × (r_i - R_i)
        sel_i = W_i * (r_i - R_i)
        selection[sector] = sel_i
        total_selection += sel_i

        # Interaction (unchanged): (w_i - W_i) × (r_i - R_i)
        inter_i = (w_i - W_i) * (r_i - R_i)
        interaction[sector] = inter_i
        total_interaction += inter_i

    allocation["total"] = total_allocation
    selection["total"] = total_selection
    interaction["total"] = total_interaction

    total_excess = total_allocation + total_selection + total_interaction

    return BrinsonResult(
        allocation_effect=allocation,
        selection_effect=selection,
        interaction_effect=interaction,
        total_excess_return=total_excess,
        sectors=all_sectors,
    )


# ---------------------------------------------------------------------------
# Multi-period Brinson with Carino logarithmic linking
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BrinsonPeriod:
    """A single-period Brinson input + computed effects.

    Attributes:
        portfolio_return: Portfolio total return for the period.
        benchmark_return: Benchmark total return for the period.
        single_period_result: BrinsonResult for this period.
    """

    portfolio_return: float
    benchmark_return: float
    single_period_result: BrinsonResult


@dataclass(frozen=True)
class MultiPeriodBrinsonResult:
    """Result of multi-period Brinson attribution with Carino linking.

    Attributes:
        allocation_effect: Per-sector linked allocation effect (sums to total).
        selection_effect: Per-sector linked selection effect.
        interaction_effect: Per-sector linked interaction effect.
        total_excess_return: Geometric multi-period excess return
            ((1+R_p)^T - (1+R_b)^T using compounded returns).
        n_periods: Number of single-period observations.
        sectors: Sectors involved.
        residual: Remaining error after Carino smoothing (should be near 0).
    """

    allocation_effect: dict[str, float] = field(default_factory=dict)
    selection_effect: dict[str, float] = field(default_factory=dict)
    interaction_effect: dict[str, float] = field(default_factory=dict)
    total_excess_return: float = 0.0
    n_periods: int = 0
    sectors: list[str] = field(default_factory=list)
    residual: float = 0.0


def _carino_period_coefficient(period_return: float) -> float:
    """Carino per-period scaling coefficient.

    k_t = ln(1 + R_t) / R_t   for R_t != 0
    k_t = 1                    for R_t = 0  (limit value)

    This coefficient maps period arithmetic effects to log-space such that
    they sum exactly to the log of compounded growth.
    """
    if abs(period_return) < 1e-15:
        return 1.0
    base = 1.0 + period_return
    if base <= 0:
        # Period loss > 100%; cannot use log-link, fall back to 1
        return 1.0
    return math.log(base) / period_return


def _carino_total_coefficient(
    portfolio_return_total: float, benchmark_return_total: float
) -> float:
    """Carino total scaling coefficient.

    K = (R_p_total - R_b_total) / (ln(1+R_p_total) - ln(1+R_b_total))

    Edge case: when R_p_total == R_b_total, the linked excess is 0; coefficient
    is undefined but irrelevant (numerator dominates). We return 1 as a safe
    default.
    """
    diff = portfolio_return_total - benchmark_return_total
    if abs(diff) < 1e-15:
        return 1.0
    p_base = 1.0 + portfolio_return_total
    b_base = 1.0 + benchmark_return_total
    if p_base <= 0 or b_base <= 0:
        return 1.0
    log_diff = math.log(p_base) - math.log(b_base)
    if abs(log_diff) < 1e-15:
        return 1.0
    return diff / log_diff


def multi_period_brinson_carino(
    periods: Sequence[BrinsonPeriod],
) -> MultiPeriodBrinsonResult:
    """Multi-period Brinson attribution using Carino logarithmic linking.

    Naive arithmetic summation of single-period effects produces a residual
    that grows with the number of periods. Carino's logarithmic linking
    coefficient smooths each period's effects so per-sector linked totals
    sum exactly (up to floating point) to the compounded multi-period
    excess return.

    Algorithm
    ---------
    For each period t with portfolio return R_p_t and benchmark return R_b_t:
        k_t = ln(1 + R_t_avg) / R_t_avg   (we use total return midpoint)
    Total scaling factor over T periods:
        K = (R_p - R_b) / (ln(1+R_p) - ln(1+R_b))
    Linked single-period effect for sector i in period t:
        linked_effect_i_t = (k_t / K) × raw_effect_i_t
    Multi-period linked effect = Σ_t linked_effect_i_t per sector.

    The total of linked allocation + selection + interaction across all
    sectors and periods then equals R_p_total - R_b_total (compounded).

    Parameters:
        periods: Sequence of BrinsonPeriod. Each contains the period's
            portfolio_return, benchmark_return, and single_period_result.

    Returns:
        MultiPeriodBrinsonResult with per-sector linked effects that sum
        to the compounded multi-period excess return.

    References:
        Carino, D. (1999): "Combining Attribution Effects Over Time."
        Journal of Performance Measurement, Vol 25, No 5, pp 5-14.
    """
    if not periods:
        return MultiPeriodBrinsonResult()

    # Compute total compounded portfolio and benchmark returns
    p_compound = 1.0
    b_compound = 1.0
    for period in periods:
        p_compound *= 1.0 + period.portfolio_return
        b_compound *= 1.0 + period.benchmark_return
    portfolio_total = p_compound - 1.0
    benchmark_total = b_compound - 1.0
    excess_total = portfolio_total - benchmark_total

    # Total scaling coefficient
    K = _carino_total_coefficient(portfolio_total, benchmark_total)

    # Collect all sectors across periods
    all_sectors: set[str] = set()
    for period in periods:
        all_sectors.update(period.single_period_result.sectors)
    sectors_sorted = sorted(all_sectors)

    # Initialize linked per-sector effects
    linked_alloc: dict[str, float] = {s: 0.0 for s in sectors_sorted}
    linked_sel: dict[str, float] = {s: 0.0 for s in sectors_sorted}
    linked_inter: dict[str, float] = {s: 0.0 for s in sectors_sorted}

    for period in periods:
        # Per-period coefficient: use the midpoint of portfolio and benchmark
        # period returns so the coefficient is symmetric
        avg_period_return = (period.portfolio_return + period.benchmark_return) / 2.0
        k_t = _carino_period_coefficient(avg_period_return)
        scale = k_t / K if abs(K) > 1e-15 else 1.0

        result = period.single_period_result
        for sector in sectors_sorted:
            linked_alloc[sector] += (
                result.allocation_effect.get(sector, 0.0) * scale
            )
            linked_sel[sector] += result.selection_effect.get(sector, 0.0) * scale
            linked_inter[sector] += (
                result.interaction_effect.get(sector, 0.0) * scale
            )

    # Add totals
    total_alloc = sum(v for k, v in linked_alloc.items())
    total_sel = sum(v for k, v in linked_sel.items())
    total_inter = sum(v for k, v in linked_inter.items())

    linked_alloc["total"] = total_alloc
    linked_sel["total"] = total_sel
    linked_inter["total"] = total_inter

    # Residual = excess_total - (alloc + sel + inter linked)
    linked_sum = total_alloc + total_sel + total_inter
    residual = excess_total - linked_sum

    return MultiPeriodBrinsonResult(
        allocation_effect=linked_alloc,
        selection_effect=linked_sel,
        interaction_effect=linked_inter,
        total_excess_return=excess_total,
        n_periods=len(periods),
        sectors=sectors_sorted,
        residual=residual,
    )
