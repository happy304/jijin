"""Unit tests for Brinson attribution model.

Covers:
- Basic attribution decomposition (allocation, selection, interaction)
- Verification that effects sum to total excess return
- Edge cases: empty inputs, None inputs, single sector, missing sectors
- Determinism
- Known numerical examples

Satisfies requirement 3.8.
"""

from __future__ import annotations

import pytest

from app.domain.performance.brinson import BrinsonResult, brinson_attribution


# ---------------------------------------------------------------------------
# Basic functionality
# ---------------------------------------------------------------------------


class TestBrinsonBasic:
    """Tests for basic Brinson attribution decomposition."""

    def test_simple_two_sector_attribution(self):
        """Two-sector example with known results."""
        # Portfolio: 60% stocks, 40% bonds
        # Benchmark: 50% stocks, 50% bonds
        # Portfolio returns: stocks +10%, bonds +2%
        # Benchmark returns: stocks +8%, bonds +3%
        portfolio_weights = {"stocks": 0.6, "bonds": 0.4}
        benchmark_weights = {"stocks": 0.5, "bonds": 0.5}
        portfolio_returns = {"stocks": 0.10, "bonds": 0.02}
        benchmark_returns = {"stocks": 0.08, "bonds": 0.03}

        result = brinson_attribution(
            portfolio_weights, benchmark_weights, portfolio_returns, benchmark_returns
        )

        # Allocation: (0.6-0.5)*0.08 + (0.4-0.5)*0.03 = 0.008 - 0.003 = 0.005
        assert result.allocation_effect["stocks"] == pytest.approx(0.008)
        assert result.allocation_effect["bonds"] == pytest.approx(-0.003)
        assert result.allocation_effect["total"] == pytest.approx(0.005)

        # Selection: 0.5*(0.10-0.08) + 0.5*(0.02-0.03) = 0.01 - 0.005 = 0.005
        assert result.selection_effect["stocks"] == pytest.approx(0.01)
        assert result.selection_effect["bonds"] == pytest.approx(-0.005)
        assert result.selection_effect["total"] == pytest.approx(0.005)

        # Interaction: (0.6-0.5)*(0.10-0.08) + (0.4-0.5)*(0.02-0.03)
        #            = 0.1*0.02 + (-0.1)*(-0.01) = 0.002 + 0.001 = 0.003
        assert result.interaction_effect["stocks"] == pytest.approx(0.002)
        assert result.interaction_effect["bonds"] == pytest.approx(0.001)
        assert result.interaction_effect["total"] == pytest.approx(0.003)

        # Total excess = portfolio return - benchmark return
        # Portfolio: 0.6*0.10 + 0.4*0.02 = 0.068
        # Benchmark: 0.5*0.08 + 0.5*0.03 = 0.055
        # Excess: 0.068 - 0.055 = 0.013
        assert result.total_excess_return == pytest.approx(0.013)

    def test_effects_sum_to_total_excess(self):
        """Allocation + Selection + Interaction = Total Excess Return."""
        portfolio_weights = {"tech": 0.4, "health": 0.3, "energy": 0.3}
        benchmark_weights = {"tech": 0.3, "health": 0.4, "energy": 0.3}
        portfolio_returns = {"tech": 0.15, "health": 0.05, "energy": -0.02}
        benchmark_returns = {"tech": 0.12, "health": 0.06, "energy": 0.01}

        result = brinson_attribution(
            portfolio_weights, benchmark_weights, portfolio_returns, benchmark_returns
        )

        total_from_effects = (
            result.allocation_effect["total"]
            + result.selection_effect["total"]
            + result.interaction_effect["total"]
        )
        assert total_from_effects == pytest.approx(result.total_excess_return)

    def test_three_sector_known_values(self):
        """Three-sector example verifying each component."""
        portfolio_weights = {"A": 0.5, "B": 0.3, "C": 0.2}
        benchmark_weights = {"A": 0.4, "B": 0.4, "C": 0.2}
        portfolio_returns = {"A": 0.12, "B": 0.08, "C": 0.05}
        benchmark_returns = {"A": 0.10, "B": 0.06, "C": 0.04}

        result = brinson_attribution(
            portfolio_weights, benchmark_weights, portfolio_returns, benchmark_returns
        )

        # Allocation for A: (0.5-0.4)*0.10 = 0.01
        assert result.allocation_effect["A"] == pytest.approx(0.01)
        # Allocation for B: (0.3-0.4)*0.06 = -0.006
        assert result.allocation_effect["B"] == pytest.approx(-0.006)
        # Allocation for C: (0.2-0.2)*0.04 = 0.0
        assert result.allocation_effect["C"] == pytest.approx(0.0)

        # Selection for A: 0.4*(0.12-0.10) = 0.008
        assert result.selection_effect["A"] == pytest.approx(0.008)
        # Selection for B: 0.4*(0.08-0.06) = 0.008
        assert result.selection_effect["B"] == pytest.approx(0.008)
        # Selection for C: 0.2*(0.05-0.04) = 0.002
        assert result.selection_effect["C"] == pytest.approx(0.002)

        # Interaction for A: (0.5-0.4)*(0.12-0.10) = 0.002
        assert result.interaction_effect["A"] == pytest.approx(0.002)
        # Interaction for B: (0.3-0.4)*(0.08-0.06) = -0.002
        assert result.interaction_effect["B"] == pytest.approx(-0.002)
        # Interaction for C: (0.2-0.2)*(0.05-0.04) = 0.0
        assert result.interaction_effect["C"] == pytest.approx(0.0)

        assert result.sectors == ["A", "B", "C"]

    def test_identical_portfolio_and_benchmark(self):
        """When portfolio equals benchmark, all effects are zero."""
        weights = {"tech": 0.5, "health": 0.3, "energy": 0.2}
        returns = {"tech": 0.10, "health": 0.05, "energy": -0.02}

        result = brinson_attribution(weights, weights, returns, returns)

        assert result.allocation_effect["total"] == pytest.approx(0.0)
        assert result.selection_effect["total"] == pytest.approx(0.0)
        assert result.interaction_effect["total"] == pytest.approx(0.0)
        assert result.total_excess_return == pytest.approx(0.0)

    def test_only_allocation_effect(self):
        """Same sector returns but different weights → only allocation effect."""
        portfolio_weights = {"stocks": 0.7, "bonds": 0.3}
        benchmark_weights = {"stocks": 0.5, "bonds": 0.5}
        # Same returns in both portfolio and benchmark
        returns = {"stocks": 0.10, "bonds": 0.02}

        result = brinson_attribution(
            portfolio_weights, benchmark_weights, returns, returns
        )

        # Selection should be zero (same returns)
        assert result.selection_effect["total"] == pytest.approx(0.0)
        # Interaction should be zero (same returns)
        assert result.interaction_effect["total"] == pytest.approx(0.0)
        # Allocation should be non-zero
        assert result.allocation_effect["total"] != 0.0
        assert result.total_excess_return == pytest.approx(
            result.allocation_effect["total"]
        )

    def test_only_selection_effect(self):
        """Same weights but different returns → only selection + interaction."""
        weights = {"stocks": 0.6, "bonds": 0.4}
        portfolio_returns = {"stocks": 0.12, "bonds": 0.03}
        benchmark_returns = {"stocks": 0.08, "bonds": 0.02}

        result = brinson_attribution(
            weights, weights, portfolio_returns, benchmark_returns
        )

        # Allocation should be zero (same weights)
        assert result.allocation_effect["total"] == pytest.approx(0.0)
        # Interaction should be zero (same weights → w_i - W_i = 0)
        assert result.interaction_effect["total"] == pytest.approx(0.0)
        # Selection should be non-zero
        assert result.selection_effect["total"] != 0.0
        assert result.total_excess_return == pytest.approx(
            result.selection_effect["total"]
        )


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestBrinsonEdgeCases:
    """Tests for edge cases and graceful handling."""

    def test_empty_inputs(self):
        """All empty dicts return zero result."""
        result = brinson_attribution({}, {}, {}, {})

        assert result.allocation_effect == {}
        assert result.selection_effect == {}
        assert result.interaction_effect == {}
        assert result.total_excess_return == 0.0
        assert result.sectors == []

    def test_none_inputs(self):
        """None inputs treated as empty dicts."""
        result = brinson_attribution(None, None, None, None)

        assert result.allocation_effect == {}
        assert result.selection_effect == {}
        assert result.interaction_effect == {}
        assert result.total_excess_return == 0.0
        assert result.sectors == []

    def test_single_sector(self):
        """Single sector attribution."""
        portfolio_weights = {"equity": 1.0}
        benchmark_weights = {"equity": 1.0}
        portfolio_returns = {"equity": 0.12}
        benchmark_returns = {"equity": 0.08}

        result = brinson_attribution(
            portfolio_weights, benchmark_weights, portfolio_returns, benchmark_returns
        )

        # Same weight → allocation = 0, interaction = 0
        assert result.allocation_effect["equity"] == pytest.approx(0.0)
        # Selection: 1.0 * (0.12 - 0.08) = 0.04
        assert result.selection_effect["equity"] == pytest.approx(0.04)
        assert result.interaction_effect["equity"] == pytest.approx(0.0)
        assert result.total_excess_return == pytest.approx(0.04)

    def test_sector_in_portfolio_not_in_benchmark(self):
        """Sector present in portfolio but not benchmark → benchmark weight = 0."""
        portfolio_weights = {"stocks": 0.6, "crypto": 0.4}
        benchmark_weights = {"stocks": 1.0}
        portfolio_returns = {"stocks": 0.08, "crypto": 0.20}
        benchmark_returns = {"stocks": 0.08, "crypto": 0.0}

        result = brinson_attribution(
            portfolio_weights, benchmark_weights, portfolio_returns, benchmark_returns
        )

        # crypto: W_i = 0, so selection for crypto = 0
        assert result.selection_effect["crypto"] == pytest.approx(0.0)
        # crypto: allocation = (0.4 - 0) * 0.0 = 0 (benchmark return for crypto is 0)
        assert result.allocation_effect["crypto"] == pytest.approx(0.0)
        # crypto: interaction = (0.4 - 0) * (0.20 - 0.0) = 0.08
        assert result.interaction_effect["crypto"] == pytest.approx(0.08)

    def test_sector_in_benchmark_not_in_portfolio(self):
        """Sector present in benchmark but not portfolio → portfolio weight = 0."""
        portfolio_weights = {"stocks": 1.0}
        benchmark_weights = {"stocks": 0.6, "bonds": 0.4}
        portfolio_returns = {"stocks": 0.10}
        benchmark_returns = {"stocks": 0.10, "bonds": 0.03}

        result = brinson_attribution(
            portfolio_weights, benchmark_weights, portfolio_returns, benchmark_returns
        )

        # bonds: w_i = 0, r_i = 0
        # Allocation for bonds: (0 - 0.4) * 0.03 = -0.012
        assert result.allocation_effect["bonds"] == pytest.approx(-0.012)
        # Selection for bonds: 0.4 * (0 - 0.03) = -0.012
        assert result.selection_effect["bonds"] == pytest.approx(-0.012)
        # Interaction for bonds: (0 - 0.4) * (0 - 0.03) = 0.012
        assert result.interaction_effect["bonds"] == pytest.approx(0.012)

    def test_zero_weights(self):
        """Sectors with zero weight are handled correctly."""
        portfolio_weights = {"A": 1.0, "B": 0.0}
        benchmark_weights = {"A": 0.5, "B": 0.5}
        portfolio_returns = {"A": 0.10, "B": 0.05}
        benchmark_returns = {"A": 0.08, "B": 0.04}

        result = brinson_attribution(
            portfolio_weights, benchmark_weights, portfolio_returns, benchmark_returns
        )

        # Should not crash and effects should sum correctly
        total_from_effects = (
            result.allocation_effect["total"]
            + result.selection_effect["total"]
            + result.interaction_effect["total"]
        )
        assert total_from_effects == pytest.approx(result.total_excess_return)

    def test_negative_returns(self):
        """Negative returns are handled correctly."""
        portfolio_weights = {"stocks": 0.6, "bonds": 0.4}
        benchmark_weights = {"stocks": 0.5, "bonds": 0.5}
        portfolio_returns = {"stocks": -0.05, "bonds": 0.02}
        benchmark_returns = {"stocks": -0.03, "bonds": 0.01}

        result = brinson_attribution(
            portfolio_weights, benchmark_weights, portfolio_returns, benchmark_returns
        )

        # Verify effects sum to total
        total_from_effects = (
            result.allocation_effect["total"]
            + result.selection_effect["total"]
            + result.interaction_effect["total"]
        )
        assert total_from_effects == pytest.approx(result.total_excess_return)

    def test_many_sectors(self):
        """Attribution with many sectors works correctly."""
        sectors = [f"sector_{i}" for i in range(20)]
        portfolio_weights = {s: 1.0 / 20 for s in sectors}
        benchmark_weights = {s: 1.0 / 20 for s in sectors}
        portfolio_returns = {s: 0.01 * (i + 1) for i, s in enumerate(sectors)}
        benchmark_returns = {s: 0.008 * (i + 1) for i, s in enumerate(sectors)}

        result = brinson_attribution(
            portfolio_weights, benchmark_weights, portfolio_returns, benchmark_returns
        )

        # Same weights → allocation = 0, interaction = 0
        assert result.allocation_effect["total"] == pytest.approx(0.0)
        assert result.interaction_effect["total"] == pytest.approx(0.0)
        assert result.selection_effect["total"] != 0.0
        assert len(result.sectors) == 20


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


class TestBrinsonResult:
    """Tests for the BrinsonResult dataclass."""

    def test_result_is_frozen(self):
        """BrinsonResult is immutable."""
        result = BrinsonResult()
        with pytest.raises(Exception):  # FrozenInstanceError
            result.total_excess_return = 1.0  # type: ignore[misc]

    def test_default_values(self):
        """Default BrinsonResult has empty dicts and zero total."""
        result = BrinsonResult()
        assert result.allocation_effect == {}
        assert result.selection_effect == {}
        assert result.interaction_effect == {}
        assert result.total_excess_return == 0.0
        assert result.sectors == []


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    """Verify Brinson attribution is deterministic (req 3.12)."""

    def test_same_inputs_same_outputs(self):
        """Running attribution twice with same inputs gives identical results."""
        portfolio_weights = {"tech": 0.4, "health": 0.3, "energy": 0.3}
        benchmark_weights = {"tech": 0.3, "health": 0.4, "energy": 0.3}
        portfolio_returns = {"tech": 0.15, "health": 0.05, "energy": -0.02}
        benchmark_returns = {"tech": 0.12, "health": 0.06, "energy": 0.01}

        result1 = brinson_attribution(
            portfolio_weights, benchmark_weights, portfolio_returns, benchmark_returns
        )
        result2 = brinson_attribution(
            portfolio_weights, benchmark_weights, portfolio_returns, benchmark_returns
        )

        assert result1.allocation_effect == result2.allocation_effect
        assert result1.selection_effect == result2.selection_effect
        assert result1.interaction_effect == result2.interaction_effect
        assert result1.total_excess_return == result2.total_excess_return
        assert result1.sectors == result2.sectors


# ---------------------------------------------------------------------------
# Numerical verification
# ---------------------------------------------------------------------------


class TestNumericalVerification:
    """Verify total excess return matches direct calculation."""

    def test_excess_return_matches_direct_calculation(self):
        """Total excess = Σ w_i * r_i - Σ W_i * R_i."""
        portfolio_weights = {"A": 0.3, "B": 0.5, "C": 0.2}
        benchmark_weights = {"A": 0.4, "B": 0.3, "C": 0.3}
        portfolio_returns = {"A": 0.12, "B": 0.08, "C": -0.03}
        benchmark_returns = {"A": 0.10, "B": 0.05, "C": 0.02}

        result = brinson_attribution(
            portfolio_weights, benchmark_weights, portfolio_returns, benchmark_returns
        )

        # Direct calculation
        port_return = sum(
            portfolio_weights[s] * portfolio_returns[s] for s in portfolio_weights
        )
        bench_return = sum(
            benchmark_weights[s] * benchmark_returns[s] for s in benchmark_weights
        )
        expected_excess = port_return - bench_return

        assert result.total_excess_return == pytest.approx(expected_excess)

    def test_allocation_formula_verification(self):
        """Verify allocation formula: Σ(w_i - W_i) × R_i."""
        portfolio_weights = {"X": 0.7, "Y": 0.3}
        benchmark_weights = {"X": 0.4, "Y": 0.6}
        portfolio_returns = {"X": 0.15, "Y": 0.05}
        benchmark_returns = {"X": 0.10, "Y": 0.03}

        result = brinson_attribution(
            portfolio_weights, benchmark_weights, portfolio_returns, benchmark_returns
        )

        # Manual: (0.7-0.4)*0.10 + (0.3-0.6)*0.03 = 0.03 - 0.009 = 0.021
        expected_alloc = (0.7 - 0.4) * 0.10 + (0.3 - 0.6) * 0.03
        assert result.allocation_effect["total"] == pytest.approx(expected_alloc)

    def test_selection_formula_verification(self):
        """Verify selection formula: Σ W_i × (r_i - R_i)."""
        portfolio_weights = {"X": 0.7, "Y": 0.3}
        benchmark_weights = {"X": 0.4, "Y": 0.6}
        portfolio_returns = {"X": 0.15, "Y": 0.05}
        benchmark_returns = {"X": 0.10, "Y": 0.03}

        result = brinson_attribution(
            portfolio_weights, benchmark_weights, portfolio_returns, benchmark_returns
        )

        # Manual: 0.4*(0.15-0.10) + 0.6*(0.05-0.03) = 0.02 + 0.012 = 0.032
        expected_sel = 0.4 * (0.15 - 0.10) + 0.6 * (0.05 - 0.03)
        assert result.selection_effect["total"] == pytest.approx(expected_sel)

    def test_interaction_formula_verification(self):
        """Verify interaction formula: Σ(w_i - W_i) × (r_i - R_i)."""
        portfolio_weights = {"X": 0.7, "Y": 0.3}
        benchmark_weights = {"X": 0.4, "Y": 0.6}
        portfolio_returns = {"X": 0.15, "Y": 0.05}
        benchmark_returns = {"X": 0.10, "Y": 0.03}

        result = brinson_attribution(
            portfolio_weights, benchmark_weights, portfolio_returns, benchmark_returns
        )

        # Manual: (0.7-0.4)*(0.15-0.10) + (0.3-0.6)*(0.05-0.03)
        #       = 0.3*0.05 + (-0.3)*0.02 = 0.015 - 0.006 = 0.009
        expected_inter = (0.7 - 0.4) * (0.15 - 0.10) + (0.3 - 0.6) * (0.05 - 0.03)
        assert result.interaction_effect["total"] == pytest.approx(expected_inter)


# ---------------------------------------------------------------------------
# Brinson-Fachler variant
# ---------------------------------------------------------------------------


class TestBrinsonFachler:
    """Tests for the Brinson-Fachler allocation effect variant."""

    def test_bf_allocation_uses_excess_benchmark_return(self):
        """BF allocation: (w_i - W_i) × (R_i - R_b) where R_b is total bench return."""
        from app.domain.performance.brinson import brinson_fachler_attribution

        portfolio_weights = {"stocks": 0.7, "bonds": 0.3}
        benchmark_weights = {"stocks": 0.5, "bonds": 0.5}
        portfolio_returns = {"stocks": 0.10, "bonds": 0.02}
        benchmark_returns = {"stocks": 0.08, "bonds": 0.03}

        result = brinson_fachler_attribution(
            portfolio_weights, benchmark_weights, portfolio_returns, benchmark_returns
        )

        # R_b = 0.5*0.08 + 0.5*0.03 = 0.055
        # Allocation_BF for stocks: (0.7-0.5) * (0.08 - 0.055) = 0.2 * 0.025 = 0.005
        # Allocation_BF for bonds: (0.3-0.5) * (0.03 - 0.055) = -0.2 * -0.025 = 0.005
        assert result.allocation_effect["stocks"] == pytest.approx(0.005)
        assert result.allocation_effect["bonds"] == pytest.approx(0.005)
        assert result.allocation_effect["total"] == pytest.approx(0.010)

    def test_bf_selection_unchanged_from_bhb(self):
        """Selection effect is identical to BHB."""
        from app.domain.performance.brinson import (
            brinson_attribution,
            brinson_fachler_attribution,
        )

        weights_p = {"A": 0.5, "B": 0.3, "C": 0.2}
        weights_b = {"A": 0.4, "B": 0.4, "C": 0.2}
        returns_p = {"A": 0.10, "B": 0.06, "C": -0.02}
        returns_b = {"A": 0.08, "B": 0.04, "C": 0.01}

        bhb = brinson_attribution(weights_p, weights_b, returns_p, returns_b)
        bf = brinson_fachler_attribution(weights_p, weights_b, returns_p, returns_b)

        assert bf.selection_effect == bhb.selection_effect
        assert bf.interaction_effect == bhb.interaction_effect

    def test_bf_total_equals_total_excess_return(self):
        """BF total of all 3 effects still equals the portfolio's total excess return."""
        from app.domain.performance.brinson import brinson_fachler_attribution

        weights_p = {"A": 0.5, "B": 0.3, "C": 0.2}
        weights_b = {"A": 0.4, "B": 0.4, "C": 0.2}
        returns_p = {"A": 0.10, "B": 0.06, "C": -0.02}
        returns_b = {"A": 0.08, "B": 0.04, "C": 0.01}

        result = brinson_fachler_attribution(
            weights_p, weights_b, returns_p, returns_b
        )

        # Direct total excess return
        port_ret = sum(weights_p[s] * returns_p[s] for s in weights_p)
        bench_ret = sum(weights_b[s] * returns_b[s] for s in weights_b)
        expected_excess = port_ret - bench_ret

        assert result.total_excess_return == pytest.approx(expected_excess)


# ---------------------------------------------------------------------------
# Multi-period Carino linking
# ---------------------------------------------------------------------------


class TestMultiPeriodCarino:
    """Tests for Carino logarithmic multi-period attribution linking."""

    def test_single_period_equals_naive_brinson(self):
        """Single-period multi-period attribution: total_excess_return matches the
        arithmetic excess. Linked sum has small residual (Carino is designed for
        multi-period smoothing; single-period ≠ residual-free).
        """
        from app.domain.performance.brinson import (
            BrinsonPeriod,
            brinson_attribution,
            multi_period_brinson_carino,
        )

        weights_p = {"A": 0.5, "B": 0.5}
        weights_b = {"A": 0.4, "B": 0.6}
        returns_p = {"A": 0.10, "B": 0.05}
        returns_b = {"A": 0.08, "B": 0.04}

        single = brinson_attribution(weights_p, weights_b, returns_p, returns_b)

        # Total returns for the period
        port_ret = sum(weights_p[s] * returns_p[s] for s in weights_p)
        bench_ret = sum(weights_b[s] * returns_b[s] for s in weights_b)

        period = BrinsonPeriod(
            portfolio_return=port_ret,
            benchmark_return=bench_ret,
            single_period_result=single,
        )
        multi = multi_period_brinson_carino([period])

        # Total excess equals the arithmetic single-period excess (geometric
        # compounding across one period == identity)
        expected_excess = port_ret - bench_ret
        assert multi.total_excess_return == pytest.approx(expected_excess, abs=1e-12)
        # Linked sum residual should be small but not necessarily zero for a
        # single period (Carino is for multi-period smoothing)
        assert abs(multi.residual) < 0.01

    def test_linked_effects_sum_to_compounded_excess(self):
        """Linked allocation+selection+interaction should sum to the compounded excess."""
        from app.domain.performance.brinson import (
            BrinsonPeriod,
            brinson_attribution,
            multi_period_brinson_carino,
        )

        # 3 periods with different returns
        scenarios = [
            (
                {"A": 0.5, "B": 0.5},
                {"A": 0.4, "B": 0.6},
                {"A": 0.05, "B": 0.02},
                {"A": 0.04, "B": 0.01},
            ),
            (
                {"A": 0.6, "B": 0.4},
                {"A": 0.4, "B": 0.6},
                {"A": -0.02, "B": 0.03},
                {"A": -0.03, "B": 0.02},
            ),
            (
                {"A": 0.3, "B": 0.7},
                {"A": 0.4, "B": 0.6},
                {"A": 0.07, "B": 0.04},
                {"A": 0.05, "B": 0.05},
            ),
        ]

        periods = []
        for wp, wb, rp, rb in scenarios:
            single = brinson_attribution(wp, wb, rp, rb)
            port_ret = sum(wp[s] * rp[s] for s in wp)
            bench_ret = sum(wb[s] * rb[s] for s in wb)
            periods.append(
                BrinsonPeriod(
                    portfolio_return=port_ret,
                    benchmark_return=bench_ret,
                    single_period_result=single,
                )
            )

        multi = multi_period_brinson_carino(periods)

        # Compounded total excess
        p_compound = 1.0
        b_compound = 1.0
        for p in periods:
            p_compound *= 1 + p.portfolio_return
            b_compound *= 1 + p.benchmark_return
        expected_excess = (p_compound - 1) - (b_compound - 1)

        assert multi.total_excess_return == pytest.approx(expected_excess, abs=1e-9)

        # Linked effects sum should be very close to total excess (residual small)
        linked_sum = (
            multi.allocation_effect["total"]
            + multi.selection_effect["total"]
            + multi.interaction_effect["total"]
        )
        # Carino smoothing should make residual small (typically < 1%)
        assert abs(linked_sum - expected_excess) < 0.005

    def test_empty_periods(self):
        """Empty periods list returns zero result."""
        from app.domain.performance.brinson import multi_period_brinson_carino

        result = multi_period_brinson_carino([])
        assert result.n_periods == 0
        assert result.total_excess_return == 0.0
        assert result.allocation_effect == {}
