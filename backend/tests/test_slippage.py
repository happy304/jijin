"""Tests for slippage and market impact cost models."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.backtest.slippage import (
    SlippageConfig,
    SlippageModel,
    compute_slippage,
)


class TestFixedBpsModel:
    def test_basic_bps_cost(self):
        config = SlippageConfig(model=SlippageModel.FIXED_BPS, cost_bps=10.0)
        result = compute_slippage(Decimal("100000"), config)
        # 10 bps = 0.1% → 100
        assert result.bps_cost == Decimal("100")
        assert result.total_cost == Decimal("100")

    def test_zero_bps_zero_cost(self):
        config = SlippageConfig(cost_bps=0)
        result = compute_slippage(Decimal("50000"), config)
        assert result.total_cost == Decimal("0")

    def test_zero_trade_value_zero_cost(self):
        config = SlippageConfig(cost_bps=10.0)
        assert compute_slippage(Decimal("0"), config).total_cost == Decimal("0")
        assert compute_slippage(Decimal("-100"), config).total_cost == Decimal("0")


class TestSpread:
    def test_half_spread_one_way(self):
        config = SlippageConfig(spread_bps=4.0)
        result = compute_slippage(Decimal("100000"), config)
        # half spread = 4/2 = 2bps → 20
        assert result.spread_cost == Decimal("20")
        assert result.total_cost == Decimal("20")


class TestLinearImpact:
    def test_linear_impact_scales_with_participation(self):
        # α = 1 bp per 1% ADV. 1% participation → 1 bp on top.
        config = SlippageConfig(
            linear_alpha_bps=1.0,
            adv=Decimal("10000000"),
        )
        # 1% of 10M = 100k participation
        result = compute_slippage(Decimal("100000"), config)
        # impact_bps = 1 * 1 = 1 bp → 100k * 1bp = 10
        assert result.linear_impact == pytest.approx(Decimal("10"), rel=1e-9)

    def test_linear_impact_skipped_without_adv(self):
        config = SlippageConfig(linear_alpha_bps=5.0, adv=None)
        result = compute_slippage(Decimal("100000"), config)
        assert result.linear_impact == Decimal("0")

    def test_linear_impact_quadratic_growth(self):
        """Doubling participation should quadruple linear cost (impact × value)."""
        config = SlippageConfig(linear_alpha_bps=2.0, adv=Decimal("10000000"))
        small = compute_slippage(Decimal("100000"), config)  # 1% ADV
        big = compute_slippage(Decimal("200000"), config)  # 2% ADV
        # impact_bps grows linearly (1bp → 2bp), trade_value also doubles → 4×
        ratio = float(big.linear_impact) / float(small.linear_impact)
        assert ratio == pytest.approx(4.0, rel=1e-6)


class TestSqrtImpact:
    def test_sqrt_impact_grows_with_sqrt_participation(self):
        config = SlippageConfig(
            sqrt_gamma=0.5,
            adv=Decimal("1000000"),
            annualized_volatility=0.20,
        )
        # 100% participation → cost = γ × σ × √1 × tv = 0.1 × tv
        result = compute_slippage(Decimal("1000000"), config)
        assert float(result.sqrt_impact) == pytest.approx(100000.0, rel=1e-6)

    def test_sqrt_impact_requires_volatility(self):
        config = SlippageConfig(
            sqrt_gamma=0.5,
            adv=Decimal("1000000"),
            annualized_volatility=None,
        )
        result = compute_slippage(Decimal("100000"), config)
        assert result.sqrt_impact == Decimal("0")


class TestComposite:
    def test_all_components_additive(self):
        config = SlippageConfig(
            cost_bps=5.0,
            spread_bps=4.0,
            linear_alpha_bps=1.0,
            sqrt_gamma=0.1,
            adv=Decimal("10000000"),
            annualized_volatility=0.20,
        )
        result = compute_slippage(Decimal("100000"), config)
        # Each component non-zero
        assert result.bps_cost > Decimal("0")
        assert result.spread_cost > Decimal("0")
        assert result.linear_impact > Decimal("0")
        assert result.sqrt_impact > Decimal("0")
        assert result.total_cost == (
            result.bps_cost
            + result.spread_cost
            + result.linear_impact
            + result.sqrt_impact
        )

    def test_to_dict(self):
        config = SlippageConfig(cost_bps=10.0)
        result = compute_slippage(Decimal("100000"), config)
        d = result.to_dict()
        assert "total_cost" in d
        assert "bps_cost" in d
        assert isinstance(d["total_cost"], float)


class TestConfigValidation:
    def test_negative_bps_raises(self):
        with pytest.raises(ValueError):
            SlippageConfig(cost_bps=-1.0)

    def test_negative_adv_raises(self):
        with pytest.raises(ValueError):
            SlippageConfig(adv=Decimal("-1"))
