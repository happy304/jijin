from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.domain.backtest.engine_event import BarContext, EventDrivenEngine, FundMeta
from app.domain.backtest.order import OrderIntent
from app.domain.backtest.portfolio import Portfolio
from app.domain.backtest.settlement import get_cash_arrival_date


class CashProbeStrategy:
    def __init__(self, orders_by_date: dict[date, list[OrderIntent]]) -> None:
        self.orders_by_date = orders_by_date
        self.cash_seen: dict[date, Decimal] = {}
        self.available_cash_seen: dict[date, Decimal] = {}

    def on_bar(self, context: BarContext) -> list[OrderIntent]:
        self.cash_seen[context.date] = context.cash
        self.available_cash_seen[context.date] = context.portfolio.available_cash
        return self.orders_by_date.get(context.date, [])


def _nav_data(start: date, days: int) -> dict[str, dict[date, Decimal]]:
    return {"000001": {start + timedelta(days=i): Decimal("1.00") for i in range(days)}}


def test_pending_cash_tracks_confirm_and_arrival_dates() -> None:
    p = Portfolio(cash=Decimal("0"), positions={"000001": Decimal("1000")})
    p.add_lot("000001", Decimal("1000"), date(2024, 1, 2), Decimal("1000"))

    p.redeem(
        "000001",
        Decimal("1000"),
        Decimal("1000"),
        Decimal("0"),
        confirm_date=date(2024, 1, 3),
        cash_arrival_date=date(2024, 1, 8),
        order_id="ORD-1",
    )

    assert p.cash == Decimal("0")
    assert p.pending_cash_amount == Decimal("1000")
    assert p.pending_cash[0].confirm_date == date(2024, 1, 3)
    assert p.pending_cash[0].arrival_date == date(2024, 1, 8)

    p.settle_pending_cash(date(2024, 1, 7))
    assert p.cash == Decimal("0")
    p.settle_pending_cash(date(2024, 1, 8))
    assert p.cash == Decimal("1000")
    assert p.pending_cash == []


def test_redeem_cash_unavailable_until_arrival_for_stock_bond_qdii_fof() -> None:
    order_date = date(2024, 1, 2)
    confirm_date = date(2024, 1, 3)
    expected_offsets = {
        "stock": date(2024, 1, 9),
        "bond": date(2024, 1, 8),
        "qdii": date(2024, 1, 12),
        "fof": date(2024, 1, 12),
    }
    for fund_type, expected in expected_offsets.items():
        assert get_cash_arrival_date(confirm_date, fund_type) == expected

    strategy = CashProbeStrategy(
        {
            date(2024, 1, 2): [OrderIntent(fund_code="000001", direction="subscribe", amount=Decimal("10000"))],
            date(2024, 1, 4): [OrderIntent(fund_code="000001", direction="redeem", shares=Decimal("10000"))],
        }
    )
    result = EventDrivenEngine().run(
        start=order_date,
        end=date(2024, 1, 12),
        strategy=strategy,
        nav_data=_nav_data(order_date, 11),
        initial_capital=Decimal("10000"),
        fund_meta={"000001": FundMeta(code="000001", fund_type="stock")},
    )

    assert strategy.cash_seen[date(2024, 1, 8)] == Decimal("0")
    assert strategy.available_cash_seen[date(2024, 1, 8)] == Decimal("0")
    assert result.final_portfolio.cash == Decimal("10000.00")
    assert result.final_portfolio.pending_cash == []
