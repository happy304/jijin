"""Slippage and market impact cost models.

Models how much the realized price deviates from the quoted NAV/price
when an order is large relative to market liquidity.

For open-end mutual funds the quoted NAV is by-construction tradable in
arbitrarily large size at end-of-day, so impact cost is generally zero.
For exchange-traded funds (ETF/LOF) and for very large mutual fund
trades that move the underlying basket, the following models apply.

Models implemented:

- **Fixed basis points** (default for all funds):
      cost = trade_value × cost_bps / 10000

- **Linear market impact**:
      cost = α × (trade_value / ADV) × trade_value
  α typically 1-10 bps per 1% of ADV. Source: Almgren & Chriss (2000)
  and Kissell (2014) implementation guides.

- **Square-root impact** (Almgren-Chriss / Kissell-Glantz):
      cost = γ × σ × √(trade_value / ADV) × trade_value
  σ is annualized return volatility of the underlying. The square-root
  form is supported by both theoretical microstructure models and the
  empirical Barra/MSCI cost models.

- **Bid-ask spread**:
      cost_one_way = trade_value × spread_bps / 2 / 10000
  Half the quoted spread on each side of the trade. Negligible for OEFs
  (no bid-ask), 2-5 bps typical for liquid A-share ETFs.

These cost components are **additive**:
    total_cost = bps_cost + linear_impact + sqrt_impact + spread_cost
The engine subtracts the total cost from the proceeds (redemption) or
adds it to the cash outflow (subscription).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class SlippageModel(str, Enum):
    """Supported slippage model types."""

    FIXED_BPS = "fixed_bps"
    LINEAR_IMPACT = "linear_impact"
    SQRT_IMPACT = "sqrt_impact"
    COMPOSITE = "composite"


@dataclass(frozen=True)
class SlippageConfig:
    """Configuration for a slippage model.

    Attributes:
        model: Which model to apply.
        cost_bps: Fixed cost in basis points (used by ``fixed_bps`` and
            as a baseline in ``composite``). Default 0.
        spread_bps: Half-spread in basis points. Cost on one side =
            trade_value × spread_bps / 2 / 10000. Default 0.
        linear_alpha_bps: Linear impact coefficient α in bps per 1% ADV.
            cost_bps_extra = α × (trade_value / ADV × 100). Default 0.
        sqrt_gamma: Square-root impact coefficient γ. Used as
            cost = γ × σ × √(trade_value / ADV) × trade_value.
            Default 0.
        annualized_volatility: σ used by sqrt_impact. Required when
            ``sqrt_gamma > 0``. Default None (skip if missing).
        adv: Average Daily Volume in CNY (for ETFs) or fund AUM
            (treated as effective liquidity for OEFs). Required for
            linear/sqrt models; skip if None.
    """

    model: SlippageModel = SlippageModel.FIXED_BPS
    cost_bps: float = 0.0
    spread_bps: float = 0.0
    linear_alpha_bps: float = 0.0
    sqrt_gamma: float = 0.0
    annualized_volatility: float | None = None
    adv: Decimal | None = None

    def __post_init__(self) -> None:
        for name, val in (
            ("cost_bps", self.cost_bps),
            ("spread_bps", self.spread_bps),
            ("linear_alpha_bps", self.linear_alpha_bps),
            ("sqrt_gamma", self.sqrt_gamma),
        ):
            if val < 0:
                raise ValueError(f"{name} must be non-negative, got {val}")
        if self.annualized_volatility is not None and self.annualized_volatility < 0:
            raise ValueError(
                f"annualized_volatility must be non-negative, got {self.annualized_volatility}"
            )
        if self.adv is not None and self.adv <= Decimal("0"):
            raise ValueError(f"adv must be positive when provided, got {self.adv}")


@dataclass(frozen=True)
class SlippageResult:
    """Decomposed slippage cost for a single trade.

    Attributes:
        bps_cost: Fixed-bps component.
        spread_cost: Half bid-ask spread (one-way).
        linear_impact: Linear market impact.
        sqrt_impact: Square-root impact.
        total_cost: Sum of all components.
    """

    bps_cost: Decimal = Decimal("0")
    spread_cost: Decimal = Decimal("0")
    linear_impact: Decimal = Decimal("0")
    sqrt_impact: Decimal = Decimal("0")
    total_cost: Decimal = Decimal("0")

    def to_dict(self) -> dict[str, float]:
        return {
            "bps_cost": float(self.bps_cost),
            "spread_cost": float(self.spread_cost),
            "linear_impact": float(self.linear_impact),
            "sqrt_impact": float(self.sqrt_impact),
            "total_cost": float(self.total_cost),
        }


def compute_slippage(
    trade_value: Decimal,
    config: SlippageConfig,
) -> SlippageResult:
    """Compute slippage cost components for a trade.

    Args:
        trade_value: The CNY value of the trade (gross, before slippage).
            Must be positive.
        config: Slippage configuration.

    Returns:
        SlippageResult with each component and total.

    Notes:
        - Negative trade_value or zero returns zero cost (defensive).
        - Components missing required inputs (e.g. ADV missing for
          linear_impact) are skipped silently with zero contribution.
    """
    if trade_value <= Decimal("0"):
        return SlippageResult()

    tv = float(trade_value)

    # 1. Fixed bps
    bps_cost = Decimal("0")
    if config.cost_bps > 0:
        bps_cost = (
            trade_value * Decimal(str(config.cost_bps)) / Decimal("10000")
        )

    # 2. Half bid-ask spread (one-way)
    spread_cost = Decimal("0")
    if config.spread_bps > 0:
        spread_cost = (
            trade_value
            * Decimal(str(config.spread_bps))
            / Decimal("2")
            / Decimal("10000")
        )

    # 3. Linear impact: α bps per 1% of ADV
    linear_impact = Decimal("0")
    if config.linear_alpha_bps > 0 and config.adv is not None:
        adv_f = float(config.adv)
        if adv_f > 0:
            participation_pct = (tv / adv_f) * 100.0  # in percent of ADV
            impact_bps = config.linear_alpha_bps * participation_pct
            # impact_bps is in basis points, convert to fraction
            linear_impact = trade_value * Decimal(str(impact_bps)) / Decimal("10000")

    # 4. Square-root impact: γ × σ × √(tv/ADV) × tv
    sqrt_impact = Decimal("0")
    if (
        config.sqrt_gamma > 0
        and config.adv is not None
        and config.annualized_volatility is not None
    ):
        adv_f = float(config.adv)
        sigma = config.annualized_volatility
        if adv_f > 0 and sigma > 0:
            participation = tv / adv_f
            impact_fraction = (
                config.sqrt_gamma * sigma * math.sqrt(max(participation, 0.0))
            )
            sqrt_impact = trade_value * Decimal(str(impact_fraction))

    total = bps_cost + spread_cost + linear_impact + sqrt_impact

    return SlippageResult(
        bps_cost=bps_cost,
        spread_cost=spread_cost,
        linear_impact=linear_impact,
        sqrt_impact=sqrt_impact,
        total_cost=total,
    )


__all__ = [
    "SlippageConfig",
    "SlippageModel",
    "SlippageResult",
    "compute_slippage",
]
