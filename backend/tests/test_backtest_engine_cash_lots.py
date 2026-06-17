from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.domain.backtest.engine_event import BarContext, EventDrivenEngine, FundMeta
from app.domain.backtest.fees import FeeTier
from app.domain.backtest.order import OrderIntent


class ScriptedStrategy:
    def __init__(self, orders_by_date: dict[date, list[OrderIntent]]) -> None:
        self.orders_by_date = orders_by_date
        self.cash_seen: dict[date, Decimal] = {}

    def on_bar(self, context: BarContext) -> list[OrderIntent]:
        self.cash_seen[context.date] = context.cash
        return self.orders_by_date.get(context.date, [])


def _nav_data(start: date, days: int, nav: Decimal = Decimal("1.00")) -> dict[str, dict[date, Decimal]]:
    return {
        "000001": {start + timedelta(days=i): nav for i in range(days)}
    }


def test_redeem_cash_not_available_until_cash_arrival_date() -> None:
    start = date(2024, 1, 2)  # Tuesday
    strategy = ScriptedStrategy(
        {
            date(2024, 1, 2): [OrderIntent(fund_code="000001", direction="subscribe", amount=Decimal("10000"))],
            date(2024, 1, 4): [OrderIntent(fund_code="000001", direction="redeem", shares=Decimal("10000"))],
        }
    )
    engine = EventDrivenEngine()
    result = engine.run(
        start=start,
        end=date(2024, 1, 12),
        strategy=strategy,
        nav_data=_nav_data(start, 11),
        initial_capital=Decimal("10000"),
        fund_meta={"000001": FundMeta(code="000001", fund_type="stock")},
    )

    # 赎回在 2024-01-05 确认，但股票/混合/指数基金确认后 T+4 才到账：2024-01-11。
    assert strategy.cash_seen[date(2024, 1, 8)] == Decimal("0")
    assert result.final_portfolio.pending_cash == []
    assert result.final_portfolio.cash == Decimal("10000.00")


def test_redeem_fee_uses_lot_level_holding_days_fifo() -> None:
    start = date(2024, 1, 2)
    strategy = ScriptedStrategy(
        {
            date(2024, 1, 2): [OrderIntent(fund_code="000001", direction="subscribe", amount=Decimal("10000"))],
            date(2024, 1, 10): [OrderIntent(fund_code="000001", direction="subscribe", amount=Decimal("10000"))],
            date(2024, 1, 12): [OrderIntent(fund_code="000001", direction="redeem", shares=Decimal("15000"))],
        }
    )
    engine = EventDrivenEngine()
    result = engine.run(
        start=start,
        end=date(2024, 1, 22),
        strategy=strategy,
        nav_data=_nav_data(start, 21),
        initial_capital=Decimal("20000"),
        fund_meta={
            "000001": FundMeta(
                code="000001",
                fund_type="money",
                redeem_fee_tiers=[
                    FeeTier(min_holding_days=0, max_holding_days=7, rate=Decimal("0.015")),
                    FeeTier(min_holding_days=7, max_holding_days=None, rate=Decimal("0")),
                ],
            )
        },
    )

    redeem = [t for t in result.trades if t.direction == "redeem"][0]
    # 第一批 10000 份确认于 1/3，1/15 赎回确认时已超过 7 自然日免赎回费；
    # 第二批 5000 份确认于 1/11，1/15 持有 4 天，按 1.5% 收费 = 75。
    assert redeem.confirm_date == date(2024, 1, 15)
    assert redeem.fee == Decimal("75.00")
    assert result.final_portfolio.positions["000001"] == Decimal("5000.00")
    assert result.final_portfolio.lots["000001"][0].confirm_date == date(2024, 1, 11)
