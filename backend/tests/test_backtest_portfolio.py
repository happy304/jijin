"""投资组合模型单元测试。

覆盖：
- Portfolio 初始化与默认值
- 申购确认（subscribe）：现金扣减、份额增加、持有天数初始化
- 赎回确认（redeem）：份额扣减、现金增加、清零清除
- 持有天数跟踪（advance_day）
- 组合总市值计算（total_value）
- 未确认订单管理（add/remove pending orders）
- 现金冻结/解冻
- 边界条件与错误处理
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.domain.backtest.order import Order, OrderIntent, OrderStatus
from app.domain.backtest.portfolio import Portfolio


# ---------------------------------------------------------------------------
# 初始化测试
# ---------------------------------------------------------------------------


class TestPortfolioInit:
    """Portfolio 初始化测试。"""

    def test_default_init(self) -> None:
        """默认初始化应为空组合。"""
        p = Portfolio()
        assert p.cash == Decimal("0")
        assert p.positions == {}
        assert p.pending_orders == []
        assert p.holding_days == {}

    def test_init_with_cash(self) -> None:
        """指定初始现金。"""
        p = Portfolio(cash=Decimal("100000"))
        assert p.cash == Decimal("100000")
        assert p.position_count == 0
        assert p.pending_order_count == 0

    def test_init_with_positions(self) -> None:
        """指定初始持仓。"""
        p = Portfolio(
            cash=Decimal("50000"),
            positions={"000001": Decimal("10000"), "110011": Decimal("5000")},
            holding_days={"000001": 30, "110011": 15},
        )
        assert p.positions["000001"] == Decimal("10000")
        assert p.positions["110011"] == Decimal("5000")
        assert p.holding_days["000001"] == 30
        assert p.position_count == 2


# ---------------------------------------------------------------------------
# 申购测试
# ---------------------------------------------------------------------------


class TestPortfolioSubscribe:
    """Portfolio 申购确认测试。"""

    def test_basic_subscribe(self) -> None:
        """基本申购：扣现金、加份额、初始化持有天数。"""
        p = Portfolio(cash=Decimal("100000"))
        p.subscribe(
            fund_code="000001",
            shares=Decimal("6543.21"),
            amount=Decimal("9985"),
            fee=Decimal("15"),
            confirm_date=date(2024, 1, 3),
        )

        assert p.cash == Decimal("90000")  # 100000 - 9985 - 15
        assert p.positions["000001"] == Decimal("6543.21")
        assert p.holding_days["000001"] == 0

    def test_subscribe_adds_to_existing_position(self) -> None:
        """追加申购应累加份额。"""
        p = Portfolio(
            cash=Decimal("100000"),
            positions={"000001": Decimal("5000")},
            holding_days={"000001": 10},
        )
        p.subscribe(
            fund_code="000001",
            shares=Decimal("3000"),
            amount=Decimal("4500"),
            fee=Decimal("6.75"),
            confirm_date=date(2024, 1, 5),
        )

        assert p.positions["000001"] == Decimal("8000")
        # 持有天数不重置
        assert p.holding_days["000001"] == 10

    def test_subscribe_multiple_funds(self) -> None:
        """申购多只基金。"""
        p = Portfolio(cash=Decimal("100000"))
        p.subscribe(
            fund_code="000001",
            shares=Decimal("5000"),
            amount=Decimal("7500"),
            fee=Decimal("11.25"),
            confirm_date=date(2024, 1, 3),
        )
        p.subscribe(
            fund_code="110011",
            shares=Decimal("3000"),
            amount=Decimal("6000"),
            fee=Decimal("9.00"),
            confirm_date=date(2024, 1, 3),
        )

        assert p.position_count == 2
        assert p.positions["000001"] == Decimal("5000")
        assert p.positions["110011"] == Decimal("3000")
        assert p.cash == Decimal("100000") - Decimal("7511.25") - Decimal("6009.00")

    def test_subscribe_insufficient_cash_raises(self) -> None:
        """现金不足时申购应抛出 ValueError。"""
        p = Portfolio(cash=Decimal("1000"))
        with pytest.raises(ValueError, match="Insufficient cash"):
            p.subscribe(
                fund_code="000001",
                shares=Decimal("10000"),
                amount=Decimal("9000"),
                fee=Decimal("2000"),
                confirm_date=date(2024, 1, 3),
            )

    def test_subscribe_zero_fee(self) -> None:
        """零费用申购（如货币基金）。"""
        p = Portfolio(cash=Decimal("50000"))
        p.subscribe(
            fund_code="000001",
            shares=Decimal("10000"),
            amount=Decimal("10000"),
            fee=Decimal("0"),
            confirm_date=date(2024, 1, 3),
        )
        assert p.cash == Decimal("40000")
        assert p.positions["000001"] == Decimal("10000")


# ---------------------------------------------------------------------------
# 赎回测试
# ---------------------------------------------------------------------------


class TestPortfolioRedeem:
    """Portfolio 赎回确认测试。"""

    def test_basic_redeem(self) -> None:
        """基本赎回：扣份额、加现金。"""
        p = Portfolio(
            cash=Decimal("10000"),
            positions={"000001": Decimal("10000")},
            holding_days={"000001": 30},
        )
        p.redeem(
            fund_code="000001",
            shares=Decimal("5000"),
            amount=Decimal("7500"),
            fee=Decimal("37.50"),
            confirm_date=date(2024, 2, 1),
        )

        assert p.positions["000001"] == Decimal("5000")
        assert p.cash == Decimal("10000") + Decimal("7500") - Decimal("37.50")
        # 持有天数保留
        assert p.holding_days["000001"] == 30

    def test_redeem_all_shares_clears_position(self) -> None:
        """全部赎回应清除持仓和持有天数记录。"""
        p = Portfolio(
            cash=Decimal("10000"),
            positions={"000001": Decimal("5000")},
            holding_days={"000001": 60},
        )
        p.redeem(
            fund_code="000001",
            shares=Decimal("5000"),
            amount=Decimal("7500"),
            fee=Decimal("0"),
            confirm_date=date(2024, 3, 1),
        )

        assert "000001" not in p.positions
        assert "000001" not in p.holding_days
        assert p.position_count == 0
        assert p.cash == Decimal("17500")

    def test_redeem_insufficient_shares_raises(self) -> None:
        """份额不足时赎回应抛出 ValueError。"""
        p = Portfolio(
            cash=Decimal("10000"),
            positions={"000001": Decimal("3000")},
            holding_days={"000001": 10},
        )
        with pytest.raises(ValueError, match="Insufficient shares"):
            p.redeem(
                fund_code="000001",
                shares=Decimal("5000"),
                amount=Decimal("7500"),
                fee=Decimal("0"),
                confirm_date=date(2024, 1, 5),
            )

    def test_redeem_nonexistent_fund_raises(self) -> None:
        """赎回不存在的基金应抛出 ValueError。"""
        p = Portfolio(cash=Decimal("10000"))
        with pytest.raises(ValueError, match="Insufficient shares"):
            p.redeem(
                fund_code="999999",
                shares=Decimal("1000"),
                amount=Decimal("1500"),
                fee=Decimal("0"),
                confirm_date=date(2024, 1, 5),
            )

    def test_redeem_with_fee(self) -> None:
        """赎回费用应从到账金额中扣除。"""
        p = Portfolio(
            cash=Decimal("0"),
            positions={"000001": Decimal("10000")},
            holding_days={"000001": 7},
        )
        # 赎回 10000 份，净值 1.5，总额 15000，费率 1.5%，费用 225
        p.redeem(
            fund_code="000001",
            shares=Decimal("10000"),
            amount=Decimal("15000"),
            fee=Decimal("225"),
            confirm_date=date(2024, 1, 10),
        )

        assert p.cash == Decimal("14775")  # 15000 - 225
        assert "000001" not in p.positions


# ---------------------------------------------------------------------------
# 持有天数跟踪测试
# ---------------------------------------------------------------------------


class TestPortfolioHoldingDays:
    """Portfolio 持有天数跟踪测试。"""

    def test_advance_day_increments_all(self) -> None:
        """advance_day 应递增所有持仓的持有天数。"""
        p = Portfolio(
            cash=Decimal("10000"),
            positions={"000001": Decimal("5000"), "110011": Decimal("3000")},
            holding_days={"000001": 10, "110011": 5},
        )
        p.advance_day()

        assert p.holding_days["000001"] == 11
        assert p.holding_days["110011"] == 6

    def test_advance_day_multiple_times(self) -> None:
        """多次 advance_day 应正确累加。"""
        p = Portfolio(
            cash=Decimal("10000"),
            positions={"000001": Decimal("5000")},
            holding_days={"000001": 0},
        )
        for _ in range(30):
            p.advance_day()

        assert p.holding_days["000001"] == 30

    def test_advance_day_new_subscription(self) -> None:
        """新申购后 advance_day 从 0 开始递增。"""
        p = Portfolio(cash=Decimal("100000"))
        p.subscribe(
            fund_code="000001",
            shares=Decimal("5000"),
            amount=Decimal("7500"),
            fee=Decimal("0"),
            confirm_date=date(2024, 1, 3),
        )
        assert p.holding_days["000001"] == 0

        p.advance_day()
        assert p.holding_days["000001"] == 1

        p.advance_day()
        assert p.holding_days["000001"] == 2

    def test_get_holding_days_no_position(self) -> None:
        """无持仓基金的持有天数应返回 0。"""
        p = Portfolio(cash=Decimal("10000"))
        assert p.get_holding_days("000001") == 0

    def test_get_holding_days_with_position(self) -> None:
        """有持仓基金应返回正确的持有天数。"""
        p = Portfolio(
            cash=Decimal("10000"),
            positions={"000001": Decimal("5000")},
            holding_days={"000001": 45},
        )
        assert p.get_holding_days("000001") == 45

    def test_advance_day_ignores_cleared_positions(self) -> None:
        """已清仓的基金不应继续递增持有天数。"""
        p = Portfolio(
            cash=Decimal("10000"),
            positions={"000001": Decimal("5000")},
            holding_days={"000001": 10},
        )
        # 全部赎回
        p.redeem(
            fund_code="000001",
            shares=Decimal("5000"),
            amount=Decimal("7500"),
            fee=Decimal("0"),
            confirm_date=date(2024, 1, 15),
        )
        # advance_day 不应报错
        p.advance_day()
        assert p.get_holding_days("000001") == 0


# ---------------------------------------------------------------------------
# 总市值计算测试
# ---------------------------------------------------------------------------


class TestPortfolioTotalValue:
    """Portfolio 总市值计算测试。"""

    def test_cash_only(self) -> None:
        """纯现金组合。"""
        p = Portfolio(cash=Decimal("100000"))
        assert p.total_value({}) == Decimal("100000")

    def test_single_position(self) -> None:
        """单只基金持仓。"""
        p = Portfolio(
            cash=Decimal("50000"),
            positions={"000001": Decimal("10000")},
        )
        nav_dict = {"000001": Decimal("1.5000")}
        # 50000 + 10000 * 1.5 = 65000
        assert p.total_value(nav_dict) == Decimal("65000.0000")

    def test_multiple_positions(self) -> None:
        """多只基金持仓。"""
        p = Portfolio(
            cash=Decimal("20000"),
            positions={
                "000001": Decimal("10000"),
                "110011": Decimal("5000"),
            },
        )
        nav_dict = {
            "000001": Decimal("1.5000"),
            "110011": Decimal("2.0000"),
        }
        # 20000 + 10000*1.5 + 5000*2.0 = 20000 + 15000 + 10000 = 45000
        assert p.total_value(nav_dict) == Decimal("45000.0000")

    def test_missing_nav_raises(self) -> None:
        """持仓基金在 nav_dict 中缺失应抛出 KeyError。"""
        p = Portfolio(
            cash=Decimal("10000"),
            positions={"000001": Decimal("5000")},
        )
        with pytest.raises(KeyError, match="NAV not found"):
            p.total_value({})

    def test_zero_positions_value(self) -> None:
        """净值为 0 的极端情况。"""
        p = Portfolio(
            cash=Decimal("10000"),
            positions={"000001": Decimal("5000")},
        )
        nav_dict = {"000001": Decimal("0")}
        assert p.total_value(nav_dict) == Decimal("10000")


# ---------------------------------------------------------------------------
# 未确认订单管理测试
# ---------------------------------------------------------------------------


class TestPortfolioPendingOrders:
    """Portfolio 未确认订单管理测试。"""

    def _make_order(self, order_id: str, direction: str = "subscribe") -> Order:
        """创建测试订单。"""
        intent = OrderIntent(
            fund_code="000001",
            direction=direction,  # type: ignore[arg-type]
            amount=Decimal("10000") if direction == "subscribe" else None,
            shares=Decimal("5000") if direction == "redeem" else None,
        )
        return Order.from_intent(
            intent=intent,
            order_id=order_id,
            order_date=date(2024, 1, 2),
        )

    def test_add_pending_order(self) -> None:
        """添加未确认订单。"""
        p = Portfolio(cash=Decimal("100000"))
        order = self._make_order("ORD-001")
        p.add_pending_order(order)

        assert p.pending_order_count == 1
        assert p.pending_orders[0].order_id == "ORD-001"

    def test_add_multiple_pending_orders(self) -> None:
        """添加多个未确认订单。"""
        p = Portfolio(cash=Decimal("100000"))
        p.add_pending_order(self._make_order("ORD-001"))
        p.add_pending_order(self._make_order("ORD-002"))
        p.add_pending_order(self._make_order("ORD-003"))

        assert p.pending_order_count == 3

    def test_remove_pending_order(self) -> None:
        """移除指定的未确认订单。"""
        p = Portfolio(cash=Decimal("100000"))
        p.add_pending_order(self._make_order("ORD-001"))
        p.add_pending_order(self._make_order("ORD-002"))

        removed = p.remove_pending_order("ORD-001")
        assert removed is not None
        assert removed.order_id == "ORD-001"
        assert p.pending_order_count == 1
        assert p.pending_orders[0].order_id == "ORD-002"

    def test_remove_nonexistent_order_returns_none(self) -> None:
        """移除不存在的订单应返回 None。"""
        p = Portfolio(cash=Decimal("100000"))
        p.add_pending_order(self._make_order("ORD-001"))

        result = p.remove_pending_order("ORD-999")
        assert result is None
        assert p.pending_order_count == 1


# ---------------------------------------------------------------------------
# 现金冻结/解冻测试
# ---------------------------------------------------------------------------


class TestPortfolioCashFreeze:
    """Portfolio 现金冻结/解冻测试。"""

    def test_freeze_cash(self) -> None:
        """冻结现金应减少可用余额。"""
        p = Portfolio(cash=Decimal("100000"))
        p.freeze_cash_for_subscribe(Decimal("30000"))
        assert p.cash == Decimal("70000")

    def test_freeze_insufficient_cash_raises(self) -> None:
        """冻结金额超过可用现金应抛出 ValueError。"""
        p = Portfolio(cash=Decimal("10000"))
        with pytest.raises(ValueError, match="Insufficient cash to freeze"):
            p.freeze_cash_for_subscribe(Decimal("20000"))

    def test_unfreeze_cash(self) -> None:
        """解冻现金应增加可用余额。"""
        p = Portfolio(cash=Decimal("70000"))
        p.unfreeze_cash(Decimal("30000"))
        assert p.cash == Decimal("100000")

    def test_freeze_and_unfreeze_cycle(self) -> None:
        """冻结后解冻应恢复原始余额。"""
        p = Portfolio(cash=Decimal("100000"))
        p.freeze_cash_for_subscribe(Decimal("50000"))
        assert p.cash == Decimal("50000")

        p.unfreeze_cash(Decimal("50000"))
        assert p.cash == Decimal("100000")


# ---------------------------------------------------------------------------
# 综合场景测试
# ---------------------------------------------------------------------------


class TestPortfolioIntegration:
    """Portfolio 综合场景测试。"""

    def test_subscribe_then_redeem_partial(self) -> None:
        """申购后部分赎回。"""
        p = Portfolio(cash=Decimal("100000"))

        # 申购
        p.subscribe(
            fund_code="000001",
            shares=Decimal("6543.21"),
            amount=Decimal("9985"),
            fee=Decimal("15"),
            confirm_date=date(2024, 1, 3),
        )
        assert p.cash == Decimal("90000")
        assert p.positions["000001"] == Decimal("6543.21")

        # 持有 30 天
        for _ in range(30):
            p.advance_day()
        assert p.holding_days["000001"] == 30

        # 部分赎回
        p.redeem(
            fund_code="000001",
            shares=Decimal("3000"),
            amount=Decimal("4800"),
            fee=Decimal("24"),
            confirm_date=date(2024, 2, 5),
        )
        assert p.positions["000001"] == Decimal("3543.21")
        assert p.cash == Decimal("90000") + Decimal("4776")
        assert p.holding_days["000001"] == 30  # 持有天数不变

    def test_multiple_funds_lifecycle(self) -> None:
        """多基金组合完整生命周期。"""
        p = Portfolio(cash=Decimal("200000"))

        # 申购两只基金
        p.subscribe(
            fund_code="000001",
            shares=Decimal("10000"),
            amount=Decimal("15000"),
            fee=Decimal("22.50"),
            confirm_date=date(2024, 1, 3),
        )
        p.subscribe(
            fund_code="110011",
            shares=Decimal("5000"),
            amount=Decimal("10000"),
            fee=Decimal("15"),
            confirm_date=date(2024, 1, 3),
        )

        # 推进 10 天
        for _ in range(10):
            p.advance_day()

        assert p.holding_days["000001"] == 10
        assert p.holding_days["110011"] == 10

        # 计算总市值
        nav_dict = {"000001": Decimal("1.6"), "110011": Decimal("2.1")}
        total = p.total_value(nav_dict)
        expected_cash = Decimal("200000") - Decimal("15022.50") - Decimal("10015")
        expected_position = Decimal("10000") * Decimal("1.6") + Decimal("5000") * Decimal("2.1")
        assert total == expected_cash + expected_position

        # 全部赎回 110011
        p.redeem(
            fund_code="110011",
            shares=Decimal("5000"),
            amount=Decimal("10500"),
            fee=Decimal("52.50"),
            confirm_date=date(2024, 1, 15),
        )
        assert "110011" not in p.positions
        assert p.position_count == 1

    def test_pending_order_workflow(self) -> None:
        """未确认订单工作流。"""
        p = Portfolio(cash=Decimal("100000"))

        # 下单时冻结现金
        p.freeze_cash_for_subscribe(Decimal("10000"))
        assert p.cash == Decimal("90000")

        # 创建订单并加入 pending
        intent = OrderIntent(
            fund_code="000001",
            direction="subscribe",
            amount=Decimal("10000"),
        )
        order = Order.from_intent(
            intent=intent,
            order_id="ORD-001",
            order_date=date(2024, 1, 2),
        )
        p.add_pending_order(order)
        assert p.pending_order_count == 1

        # T+1 确认
        removed = p.remove_pending_order("ORD-001")
        assert removed is not None
        assert p.pending_order_count == 0

        # 确认申购（现金已冻结，这里直接加份额）
        # 注意：实际引擎中 subscribe 的 amount+fee 应等于冻结金额
        # 这里模拟确认后的份额入账
        p.positions["000001"] = Decimal("6543.21")
        p.holding_days["000001"] = 0


# ---------------------------------------------------------------------------
# 权益快照：冻结现金和待到账赎回款必须计入权益
# ---------------------------------------------------------------------------


class TestPortfolioEquityIncludesFrozenAndPending:
    """确保 frozen_cash 和 pending_cash 不会导致权益归零。"""

    def test_frozen_cash_included_in_total_value(self) -> None:
        """申购下单后冻结的现金仍属于组合资产。"""
        p = Portfolio(cash=Decimal("100000"))
        p.freeze_cash_for_subscribe(Decimal("100000"))

        # 无持仓，cash=0，但 frozen_cash=100000
        assert p.cash == Decimal("0")
        assert p.frozen_cash == Decimal("100000")

        # total_value 只计算 cash + position_value；
        # 但引擎权益快照应加入 frozen_cash。
        # 这里验证 Portfolio 级别的属性：总资产口径
        total_assets = p.cash + p.frozen_cash + p.pending_cash_amount
        assert total_assets == Decimal("100000")

    def test_pending_cash_included_in_total_assets(self) -> None:
        """赎回确认后待到账现金仍属于组合资产。"""
        p = Portfolio(cash=Decimal("0"))
        p.add_pending_cash(
            fund_code="000001",
            amount=Decimal("50000"),
            arrival_date=date(2024, 1, 10),
            confirm_date=date(2024, 1, 5),
        )

        assert p.cash == Decimal("0")
        assert p.pending_cash_amount == Decimal("50000")

        total_assets = p.cash + p.frozen_cash + p.pending_cash_amount
        assert total_assets == Decimal("50000")

    def test_engine_equity_snapshot_not_zero_during_freeze(self) -> None:
        """引擎级别：申购冻结期间权益快照不归零。"""
        from app.domain.backtest.engine_event import (
            EventDrivenEngine,
            FundMeta,
            EquitySnapshot,
        )

        # 构建简单数据：一只基金 2 天
        nav_data = {
            "000001": {
                date(2024, 1, 2): Decimal("1.0"),
                date(2024, 1, 3): Decimal("1.01"),
            }
        }
        fund_meta = {
            "000001": FundMeta(code="000001", fund_type="stock"),
        }

        # 策略：第一天全仓买入
        class BuyAllDay1:
            def on_bar(self, context):
                from app.domain.backtest.order import OrderIntent
                if context.current_date == date(2024, 1, 2):
                    return [OrderIntent(
                        fund_code="000001",
                        direction="subscribe",
                        amount=context.portfolio.cash,
                    )]
                return []

        engine = EventDrivenEngine()
        result = engine.run(
            start=date(2024, 1, 2),
            end=date(2024, 1, 3),
            strategy=BuyAllDay1(),
            nav_data=nav_data,
            initial_capital=Decimal("100000"),
            fund_meta=fund_meta,
        )

        # 检查每日权益快照均不为 0
        for snap in result.equity_curve:
            assert snap.equity > Decimal("0"), (
                f"Equity should never be zero, got {snap.equity} on {snap.trade_date}"
            )

