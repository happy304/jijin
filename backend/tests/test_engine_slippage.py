"""Tests for engine integration of slippage / market impact costs."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.domain.backtest.engine_event import (
    EventDrivenEngine,
    FundMeta,
)
from app.domain.backtest.order import OrderIntent
from app.domain.backtest.slippage import SlippageConfig, SlippageModel
from app.domain.strategy.base import BaseStrategy, StrategyParams


class _BuyOnceStrategy(BaseStrategy):
    name = "buy_once"

    def __init__(self, params=None, universe=None):
        super().__init__(params=params or StrategyParams(), universe=universe)
        self._submitted = False

    def on_bar(self, context):
        if self._submitted:
            return []
        self._submitted = True
        return [
            OrderIntent(
                fund_code=self.universe[0],
                direction="subscribe",
                amount=Decimal("100000"),
            )
        ]


def _build_nav_data(code, start, end, base_nav=1.0):
    from app.domain.backtest.calendar import trading_days

    nav_map = {}
    for i, d in enumerate(trading_days(start, end)):
        nav_map[d] = Decimal(str(round(base_nav * (1.0 + 0.0005 * i), 6)))
    return {code: nav_map}


class TestSlippageReducesShares:
    def test_subscription_with_slippage_buys_fewer_shares(self):
        """Slippage on a subscribe → fewer shares confirmed for same cash."""
        start = date(2024, 1, 2)
        end = date(2024, 6, 28)
        code = "000001"
        nav_data = _build_nav_data(code, start, end)

        # Without slippage
        engine_nodrag = EventDrivenEngine()
        result_nodrag = engine_nodrag.run(
            start=start,
            end=end,
            strategy=_BuyOnceStrategy(universe=[code]),
            nav_data=nav_data,
            initial_capital=Decimal("1000000"),
            fund_meta={code: FundMeta(code=code)},
        )
        shares_nodrag = result_nodrag.final_portfolio.positions.get(code, Decimal("0"))

        # With 50 bps slippage
        slippage = SlippageConfig(model=SlippageModel.FIXED_BPS, cost_bps=50.0)
        engine_drag = EventDrivenEngine()
        result_drag = engine_drag.run(
            start=start,
            end=end,
            strategy=_BuyOnceStrategy(universe=[code]),
            nav_data=nav_data,
            initial_capital=Decimal("1000000"),
            fund_meta={code: FundMeta(code=code, slippage_config=slippage)},
        )
        shares_drag = result_drag.final_portfolio.positions.get(code, Decimal("0"))

        # Slippage reduces share count
        assert shares_drag < shares_nodrag
        # Slippage trade should record a non-zero fee
        sub_trades = [t for t in result_drag.trades if t.direction == "subscribe"]
        assert len(sub_trades) >= 1
        assert sub_trades[0].fee > Decimal("0")

    def test_no_slippage_when_config_none(self):
        """Default behaviour preserved when slippage_config is None."""
        start = date(2024, 1, 2)
        end = date(2024, 3, 31)
        code = "000001"
        nav_data = _build_nav_data(code, start, end)

        engine = EventDrivenEngine()
        result = engine.run(
            start=start,
            end=end,
            strategy=_BuyOnceStrategy(universe=[code]),
            nav_data=nav_data,
            initial_capital=Decimal("500000"),
            fund_meta={code: FundMeta(code=code)},
        )
        sub_trades = [t for t in result.trades if t.direction == "subscribe"]
        assert sub_trades[0].fee == Decimal("0")
