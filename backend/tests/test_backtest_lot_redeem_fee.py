from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.domain.backtest.engine_event import BarContext, EventDrivenEngine, FundMeta
from app.domain.backtest.fees import FeeTier
from app.domain.backtest.order import OrderIntent
from app.domain.backtest.portfolio import Portfolio, PositionLot


class ScriptedStrategy:
    def __init__(self, orders_by_date: dict[date, list[OrderIntent]]) -> None:
        self.orders_by_date = orders_by_date

    def on_bar(self, context: BarContext) -> list[OrderIntent]:
        return self.orders_by_date.get(context.date, [])


def _nav_data(start: date, days: int) -> dict[str, dict[date, Decimal]]:
    return {"000001": {start + timedelta(days=i): Decimal("1.00") for i in range(days)}}


def test_portfolio_position_lots_keep_audit_fields_and_fifo_consumption() -> None:
    p = Portfolio(cash=Decimal("2000"))
    p.subscribe("000001", Decimal("1000"), Decimal("1000"), Decimal("0"), date(2024, 1, 2))
    p.subscribe("000001", Decimal("500"), Decimal("750"), Decimal("0"), date(2024, 1, 10))

    assert p.positions["000001"] == Decimal("1500")
    assert len(p.position_lots["000001"]) == 2
    first = p.position_lots["000001"][0]
    assert isinstance(first, PositionLot)
    assert first.fund_code == "000001"
    assert first.cost_amount == Decimal("1000")
    assert first.cost_nav == Decimal("1")

    consumed = p.consume_lots_fifo("000001", Decimal("1200"))
    assert [lot.shares for lot in consumed] == [Decimal("1000"), Decimal("200")]
    assert p.positions["000001"] == Decimal("300")
    assert p.position_lots["000001"][0].confirm_date == date(2024, 1, 10)


def test_event_engine_redeem_fee_uses_each_lot_holding_days() -> None:
    start = date(2024, 1, 2)
    strategy = ScriptedStrategy(
        {
            date(2024, 1, 2): [OrderIntent(fund_code="000001", direction="subscribe", amount=Decimal("10000"))],
            date(2024, 1, 10): [OrderIntent(fund_code="000001", direction="subscribe", amount=Decimal("10000"))],
            date(2024, 1, 12): [OrderIntent(fund_code="000001", direction="redeem", shares=Decimal("15000"))],
        }
    )
    result = EventDrivenEngine().run(
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
    assert redeem.fee == Decimal("75.00")
    assert len(redeem.lot_details) == 2
    assert redeem.lot_details[0]["shares"] == "10000.00"
    assert Decimal(redeem.lot_details[0]["fee"]) == Decimal("0.00")
    assert redeem.lot_details[1]["shares"] == "5000.00"
    assert Decimal(redeem.lot_details[1]["fee"]) == Decimal("75.00")
    assert result.final_portfolio.position_lots["000001"][0].fund_code == "000001"
    assert result.final_portfolio.position_lots["000001"][0].confirm_date == date(2024, 1, 11)
