"""策略基类与上下文单元测试。

覆盖：
- StrategyParams 参数基类
- BaseStrategy 抽象类与生命周期方法
- rebalance_to 辅助函数

需求: 5.9, 10.5, 10.6
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.domain.backtest.engine_event import BarContext
from app.domain.backtest.order import OrderIntent
from app.domain.backtest.portfolio import Portfolio
from app.domain.strategy.base import BaseStrategy, StrategyParams, rebalance_to


# ---------------------------------------------------------------------------
# StrategyParams 测试
# ---------------------------------------------------------------------------


class TestStrategyParams:
    """StrategyParams 参数基类测试。"""

    def test_empty_params(self) -> None:
        """空参数创建。"""
        params = StrategyParams()
        assert params is not None

    def test_subclass_params(self) -> None:
        """子类参数。"""

        class MyParams(StrategyParams):
            lookback: int = 20
            top_n: int = 3

        params = MyParams(lookback=30, top_n=5)
        assert params.lookback == 30
        assert params.top_n == 5

    def test_extra_fields_forbidden(self) -> None:
        """不允许额外字段。"""
        with pytest.raises(Exception):
            StrategyParams(unknown_field="value")  # type: ignore

    def test_serialization(self) -> None:
        """参数可序列化。"""

        class MyParams(StrategyParams):
            window: int = 60

        params = MyParams(window=120)
        d = params.model_dump()
        assert d == {"window": 120}


# ---------------------------------------------------------------------------
# BaseStrategy 测试
# ---------------------------------------------------------------------------


class SimpleStrategy(BaseStrategy):
    """简单测试策略。"""

    name = "simple_test"

    def on_bar(self, context: BarContext) -> list[OrderIntent]:
        return []


class BuyFirstDayStrategy(BaseStrategy):
    """第一天买入策略。"""

    name = "buy_first_day"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._bought = False

    def on_bar(self, context: BarContext) -> list[OrderIntent]:
        if not self._bought and self.universe:
            self._bought = True
            return [
                OrderIntent(
                    fund_code=self.universe[0],
                    direction="subscribe",
                    amount=context.cash,
                )
            ]
        return []


class TestBaseStrategy:
    """BaseStrategy 抽象类测试。"""

    def test_cannot_instantiate_abstract(self) -> None:
        """不能直接实例化抽象类。"""
        with pytest.raises(TypeError):
            BaseStrategy()  # type: ignore

    def test_simple_strategy_creation(self) -> None:
        """简单策略创建。"""
        strategy = SimpleStrategy()
        assert strategy.name == "simple_test"
        assert strategy.universe == []
        assert isinstance(strategy.params, StrategyParams)

    def test_strategy_with_params(self) -> None:
        """带参数的策略。"""

        class MyParams(StrategyParams):
            window: int = 20

        strategy = SimpleStrategy(params=MyParams(window=60))
        assert strategy.params.model_dump() == {"window": 60}

    def test_strategy_with_universe(self) -> None:
        """带基金池的策略。"""
        strategy = SimpleStrategy(universe=["000001", "110011", "519003"])
        assert strategy.universe == ["000001", "110011", "519003"]

    def test_on_bar_returns_empty_list(self) -> None:
        """on_bar 返回空列表。"""
        strategy = SimpleStrategy()
        ctx = BarContext(
            current_date=date(2024, 1, 2),
            portfolio=Portfolio(cash=Decimal("100000")),
            nav_history={},
            _cutoff_date=date(2024, 1, 1),
        )
        result = strategy.on_bar(ctx)
        assert result == []

    def test_on_init_default_noop(self) -> None:
        """on_init 默认不做任何操作。"""
        strategy = SimpleStrategy()
        ctx = BarContext(
            current_date=date(2024, 1, 2),
            portfolio=Portfolio(cash=Decimal("100000")),
            nav_history={},
            _cutoff_date=date(2024, 1, 1),
        )
        # 不应抛出异常
        strategy.on_init(ctx)

    def test_on_dividend_default_noop(self) -> None:
        """on_dividend 默认不做任何操作。"""
        strategy = SimpleStrategy()
        ctx = BarContext(
            current_date=date(2024, 1, 2),
            portfolio=Portfolio(cash=Decimal("100000")),
            nav_history={},
            _cutoff_date=date(2024, 1, 1),
        )
        strategy.on_dividend(ctx, "000001", Decimal("0.5"))

    def test_on_order_filled_default_noop(self) -> None:
        """on_order_filled 默认不做任何操作。"""
        strategy = SimpleStrategy()
        ctx = BarContext(
            current_date=date(2024, 1, 2),
            portfolio=Portfolio(cash=Decimal("100000")),
            nav_history={},
            _cutoff_date=date(2024, 1, 1),
        )
        strategy.on_order_filled(
            ctx, "ORD-001", "000001", "subscribe",
            Decimal("1000"), Decimal("1500"), Decimal("10"),
        )

    def test_buy_first_day_strategy(self) -> None:
        """买入策略生成正确的 OrderIntent。"""
        strategy = BuyFirstDayStrategy(universe=["000001"])
        ctx = BarContext(
            current_date=date(2024, 1, 2),
            portfolio=Portfolio(cash=Decimal("100000")),
            nav_history={"000001": {date(2024, 1, 1): Decimal("1.5")}},
            _cutoff_date=date(2024, 1, 1),
        )
        orders = strategy.on_bar(ctx)
        assert len(orders) == 1
        assert orders[0].fund_code == "000001"
        assert orders[0].direction == "subscribe"
        assert orders[0].amount == Decimal("100000")


# ---------------------------------------------------------------------------
# rebalance_to 测试
# ---------------------------------------------------------------------------


def _make_context(
    cash: Decimal,
    positions: dict[str, Decimal],
    nav_data: dict[str, dict[date, Decimal]],
    current_date: date = date(2024, 1, 3),
    cutoff_date: date = date(2024, 1, 2),
) -> BarContext:
    """构建测试用 BarContext。"""
    portfolio = Portfolio(cash=cash, positions=positions)
    return BarContext(
        current_date=current_date,
        portfolio=portfolio,
        nav_history=nav_data,
        _cutoff_date=cutoff_date,
    )


class TestRebalanceTo:
    """rebalance_to 辅助函数测试。"""

    def test_empty_portfolio_buy(self) -> None:
        """空仓时按目标权重买入。"""
        nav_data = {
            "000001": {date(2024, 1, 2): Decimal("1.5")},
            "110011": {date(2024, 1, 2): Decimal("2.0")},
        }
        ctx = _make_context(
            cash=Decimal("100000"),
            positions={},
            nav_data=nav_data,
        )
        target = {"000001": 0.6, "110011": 0.4}
        orders = rebalance_to(ctx, target)

        assert len(orders) == 2
        subscribe_orders = [o for o in orders if o.direction == "subscribe"]
        assert len(subscribe_orders) == 2

        # 000001: 目标 60000, 当前 0 → 买入 60000
        order_a = next(o for o in orders if o.fund_code == "000001")
        assert order_a.amount == Decimal("60000")

        # 110011: 目标 40000, 当前 0 → 买入 40000
        order_b = next(o for o in orders if o.fund_code == "110011")
        assert order_b.amount == Decimal("40000")

    def test_rebalance_increase_decrease(self) -> None:
        """调仓：增加一只，减少一只。"""
        nav_data = {
            "000001": {date(2024, 1, 2): Decimal("1.5")},
            "110011": {date(2024, 1, 2): Decimal("2.0")},
        }
        # 当前：000001 持有 20000 份 × 1.5 = 30000, 110011 持有 25000 份 × 2.0 = 50000
        # 现金 20000, 总市值 = 100000
        ctx = _make_context(
            cash=Decimal("20000"),
            positions={"000001": Decimal("20000"), "110011": Decimal("25000")},
            nav_data=nav_data,
        )
        # 目标：000001 = 50%, 110011 = 30%
        target = {"000001": 0.5, "110011": 0.3}
        orders = rebalance_to(ctx, target)

        # 000001: 目标 50000, 当前 30000 → 买入 20000
        buy_orders = [o for o in orders if o.direction == "subscribe"]
        sell_orders = [o for o in orders if o.direction == "redeem"]

        assert len(buy_orders) == 1
        assert buy_orders[0].fund_code == "000001"
        assert buy_orders[0].amount == Decimal("20000")

        assert len(sell_orders) == 1
        assert sell_orders[0].fund_code == "110011"
        # 110011: 目标 30000, 当前 50000 → 赎回 20000/2.0 = 10000 份
        assert sell_orders[0].shares == Decimal("10000.00")

    def test_no_change_needed(self) -> None:
        """已在目标权重，不生成订单。"""
        nav_data = {"000001": {date(2024, 1, 2): Decimal("2.0")}}
        # 当前：000001 持有 50000 份 × 2.0 = 100000, 现金 0
        # 总市值 100000, 目标权重 1.0 → 目标金额 100000 = 当前金额
        ctx = _make_context(
            cash=Decimal("0"),
            positions={"000001": Decimal("50000")},
            nav_data=nav_data,
        )
        target = {"000001": 1.0}
        orders = rebalance_to(ctx, target)
        assert orders == []

    def test_sell_all_when_target_zero(self) -> None:
        """目标权重为 0 时全部赎回。"""
        nav_data = {"000001": {date(2024, 1, 2): Decimal("1.5")}}
        ctx = _make_context(
            cash=Decimal("50000"),
            positions={"000001": Decimal("20000")},
            nav_data=nav_data,
        )
        # 总市值 = 50000 + 20000*1.5 = 80000
        # 目标 000001 = 0 → 赎回全部
        target = {"000001": 0.0}
        orders = rebalance_to(ctx, target)

        assert len(orders) == 1
        assert orders[0].direction == "redeem"
        assert orders[0].fund_code == "000001"
        assert orders[0].shares == Decimal("20000.00")

    def test_sell_unlisted_positions(self) -> None:
        """当前持有但不在目标中的基金被赎回。"""
        nav_data = {
            "000001": {date(2024, 1, 2): Decimal("1.5")},
            "110011": {date(2024, 1, 2): Decimal("2.0")},
        }
        ctx = _make_context(
            cash=Decimal("20000"),
            positions={"000001": Decimal("10000"), "110011": Decimal("15000")},
            nav_data=nav_data,
        )
        # 总市值 = 20000 + 10000*1.5 + 15000*2.0 = 20000 + 15000 + 30000 = 65000
        # 目标只有 000001 = 0.5 → 110011 应被赎回
        target = {"000001": 0.5}
        orders = rebalance_to(ctx, target)

        redeem_orders = [o for o in orders if o.direction == "redeem"]
        assert any(o.fund_code == "110011" for o in redeem_orders)

    def test_small_diff_ignored(self) -> None:
        """差额小于 100 元的调仓被忽略。"""
        nav_data = {"000001": {date(2024, 1, 2): Decimal("1.5")}}
        # 当前：000001 = 66600 份 × 1.5 = 99900, 现金 100
        # 总市值 = 100000, 目标 1.0 → 目标 100000, 差额 100 → 忽略
        ctx = _make_context(
            cash=Decimal("100"),
            positions={"000001": Decimal("66600")},
            nav_data=nav_data,
        )
        target = {"000001": 1.0}
        orders = rebalance_to(ctx, target)
        assert orders == []

    def test_zero_total_value_returns_empty(self) -> None:
        """总市值为 0 时返回空列表。"""
        ctx = _make_context(
            cash=Decimal("0"),
            positions={},
            nav_data={},
        )
        target = {"000001": 1.0}
        orders = rebalance_to(ctx, target)
        assert orders == []

    def test_target_weight_stored_in_intent(self) -> None:
        """OrderIntent 中记录目标权重。"""
        nav_data = {"000001": {date(2024, 1, 2): Decimal("1.5")}}
        ctx = _make_context(
            cash=Decimal("100000"),
            positions={},
            nav_data=nav_data,
        )
        target = {"000001": 0.8}
        orders = rebalance_to(ctx, target)

        assert len(orders) == 1
        assert orders[0].target_weight == Decimal("0.8")
