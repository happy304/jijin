"""Tests for delisted-fund handling in EventDrivenEngine.

Delisting is the minimum-viable survivorship-bias fix: when a held fund
hits its delisting_date during a backtest window, the engine must:
1. Liquidate the position at that day's NAV (no fee)
2. Add the proceeds to cash
3. Reject any new orders for the delisted fund afterwards
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.domain.backtest.engine_event import (
    BarContext,
    DividendInfo,
    EventDrivenEngine,
    FundMeta,
)
from app.domain.backtest.order import OrderIntent
from app.domain.strategy.base import BaseStrategy, StrategyParams


class _BuyOnceParams(StrategyParams):
    invest_amount: float = 10000.0


class _BuyOnceStrategy(BaseStrategy):
    """Submit a single buy order on the first bar."""

    name = "buy_once"

    def __init__(self, params=None, universe=None):
        super().__init__(params=params or _BuyOnceParams(), universe=universe)
        self._submitted = False

    def on_bar(self, context: BarContext) -> list[OrderIntent]:
        if self._submitted:
            return []
        self._submitted = True
        return [
            OrderIntent(
                fund_code=self.universe[0],
                direction="subscribe",
                amount=Decimal(str(self.params.invest_amount)),
            )
        ]


class _BuyEveryDayStrategy(BaseStrategy):
    """Submit a small buy on every bar."""

    name = "buy_every_day"

    def __init__(self, params=None, universe=None):
        super().__init__(params=params or _BuyOnceParams(), universe=universe)

    def on_bar(self, context: BarContext) -> list[OrderIntent]:
        return [
            OrderIntent(
                fund_code=self.universe[0],
                direction="subscribe",
                amount=Decimal("100.0"),
            )
        ]


def _build_nav_data(
    fund_code: str, start: date, end: date, base_nav: float = 1.0
) -> dict[str, dict[date, Decimal]]:
    """Generate continuous daily NAVs for the universe between [start, end]."""
    from app.domain.backtest.calendar import trading_days

    nav_map: dict[date, Decimal] = {}
    for i, d in enumerate(trading_days(start, end)):
        nav = base_nav * (1.0 + 0.001 * i)
        nav_map[d] = Decimal(str(round(nav, 6)))
    return {fund_code: nav_map}


class TestDelistingForceLiquidation:
    def test_held_fund_liquidated_on_delisting_date(self):
        """A position must be force-liquidated on the delisting date."""
        start = date(2024, 1, 2)
        end = date(2024, 6, 28)
        delisting = date(2024, 4, 1)
        code = "000001"
        nav_data = _build_nav_data(code, start, end)

        strategy = _BuyOnceStrategy(universe=[code])
        engine = EventDrivenEngine()

        result = engine.run(
            start=start,
            end=end,
            strategy=strategy,
            nav_data=nav_data,
            initial_capital=Decimal("100000"),
            fund_meta={code: FundMeta(code=code, delisting_date=delisting)},
        )

        # Position should be empty by the time backtest finishes
        assert code not in result.final_portfolio.positions

        # There must be at least one liquidation trade with order_id starting "DELIST-"
        delist_trades = [t for t in result.trades if t.order_id.startswith("DELIST-")]
        assert len(delist_trades) == 1

        liq_trade = delist_trades[0]
        assert liq_trade.fund_code == code
        assert liq_trade.direction == "redeem"
        # Liquidation date is on/after the delisting date
        assert liq_trade.confirm_date >= delisting

    def test_no_liquidation_when_no_position(self):
        """If we never bought the fund, no liquidation trade should be emitted."""
        start = date(2024, 1, 2)
        end = date(2024, 6, 28)
        delisting = date(2024, 4, 1)
        code = "000001"
        nav_data = _build_nav_data(code, start, end)

        # A strategy that never trades
        class _NoOp(BaseStrategy):
            name = "noop"

            def on_bar(self, context):
                return []

        strategy = _NoOp(params=StrategyParams(), universe=[code])
        engine = EventDrivenEngine()

        result = engine.run(
            start=start,
            end=end,
            strategy=strategy,
            nav_data=nav_data,
            initial_capital=Decimal("100000"),
            fund_meta={code: FundMeta(code=code, delisting_date=delisting)},
        )

        delist_trades = [t for t in result.trades if t.order_id.startswith("DELIST-")]
        assert len(delist_trades) == 0

    def test_no_delisting_metadata_unchanged_behavior(self):
        """When delisting_date is None, original behaviour preserved."""
        start = date(2024, 1, 2)
        end = date(2024, 6, 28)
        code = "000001"
        nav_data = _build_nav_data(code, start, end)

        strategy = _BuyOnceStrategy(universe=[code])
        engine = EventDrivenEngine()

        result = engine.run(
            start=start,
            end=end,
            strategy=strategy,
            nav_data=nav_data,
            initial_capital=Decimal("100000"),
            fund_meta={code: FundMeta(code=code, delisting_date=None)},
        )

        # Position should remain (no forced liquidation)
        assert code in result.final_portfolio.positions
        delist_trades = [t for t in result.trades if t.order_id.startswith("DELIST-")]
        assert len(delist_trades) == 0


class TestPostDelistingOrderRejection:
    def test_orders_for_delisted_fund_rejected(self):
        """Any order submitted on/after the delisting date is dropped silently."""
        start = date(2024, 1, 2)
        end = date(2024, 6, 28)
        delisting = date(2024, 4, 1)
        code = "000001"
        nav_data = _build_nav_data(code, start, end)

        # Strategy attempts to buy every day
        strategy = _BuyEveryDayStrategy(universe=[code])
        engine = EventDrivenEngine()

        result = engine.run(
            start=start,
            end=end,
            strategy=strategy,
            nav_data=nav_data,
            initial_capital=Decimal("100000"),
            fund_meta={code: FundMeta(code=code, delisting_date=delisting)},
        )

        # Trades after the delisting date (excluding the DELIST liquidation)
        non_delist_trades_after = [
            t for t in result.trades
            if not t.order_id.startswith("DELIST-")
            and t.confirm_date > delisting
        ]
        # There should be no new subscribes confirmed after delisting
        # (the engine rejected the order intents at queue time)
        for t in non_delist_trades_after:
            # Any post-delisting trade should only be a redemption from
            # already-pending orders before delisting
            assert t.direction != "subscribe", (
                f"Subscribe leaked through after delisting: {t}"
            )
