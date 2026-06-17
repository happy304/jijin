"""Broker 接口与 PaperBroker 单元测试。

测试覆盖：
- Broker Protocol 合规性验证
- PaperBroker 申购流程（含 T+1 结算）
- PaperBroker 赎回流程（含 T+1 结算）
- 订单取消
- 资金/份额不足校验
- 净值缺失处理
- 依赖注入兼容性验证

需求: 10.3, 10.6
"""

from datetime import date
from decimal import Decimal

import pytest

from app.domain.backtest.order import OrderIntent, OrderStatus
from app.domain.broker.base import Broker
from app.domain.broker.paper import PaperBroker


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def nav_provider():
    """固定净值提供者，用于测试。"""
    navs = {
        "000001": Decimal("1.5000"),
        "000002": Decimal("2.0000"),
        "110011": Decimal("1.2000"),
    }

    def provider(fund_code: str, dt: date) -> Decimal | None:
        return navs.get(fund_code)

    return provider


@pytest.fixture
def broker(nav_provider):
    """标准 PaperBroker 实例。"""
    return PaperBroker(
        initial_cash=Decimal("100000"),
        nav_provider=nav_provider,
        fee_rate=Decimal("0.0015"),
        current_date=date(2024, 1, 2),
    )


# ---------------------------------------------------------------------------
# Protocol 合规性测试
# ---------------------------------------------------------------------------


class TestBrokerProtocol:
    """验证 PaperBroker 满足 Broker Protocol。"""

    def test_paper_broker_is_broker_instance(self, broker):
        """PaperBroker 应满足 Broker Protocol（runtime_checkable）。"""
        assert isinstance(broker, Broker)

    def test_protocol_methods_exist(self, broker):
        """Broker Protocol 定义的所有方法都应存在。"""
        assert hasattr(broker, "submit_order")
        assert hasattr(broker, "cancel_order")
        assert hasattr(broker, "get_positions")
        assert hasattr(broker, "get_cash")
        assert hasattr(broker, "get_order_status")


# ---------------------------------------------------------------------------
# 申购流程测试
# ---------------------------------------------------------------------------


class TestSubscribe:
    """申购订单测试。"""

    def test_submit_subscribe_order(self, broker):
        """提交申购订单应返回 PENDING 状态的订单。"""
        intent = OrderIntent(
            fund_code="000001",
            direction="subscribe",
            amount=Decimal("10000"),
        )
        order = broker.submit_order(intent)

        assert order.status == OrderStatus.PENDING
        assert order.fund_code == "000001"
        assert order.direction == "subscribe"
        assert order.amount == Decimal("10000")
        assert order.order_date == date(2024, 1, 2)

    def test_subscribe_freezes_cash(self, broker):
        """申购订单提交后应冻结对应现金。"""
        intent = OrderIntent(
            fund_code="000001",
            direction="subscribe",
            amount=Decimal("10000"),
        )
        broker.submit_order(intent)

        assert broker.get_cash() == Decimal("90000")

    def test_subscribe_settle_t_plus_1(self, broker):
        """T+1 结算后应正确计算份额并更新持仓。"""
        intent = OrderIntent(
            fund_code="000001",
            direction="subscribe",
            amount=Decimal("10000"),
        )
        broker.submit_order(intent)

        # T+1 结算
        fills = broker.settle(date(2024, 1, 3))

        assert len(fills) == 1
        fill = fills[0]
        assert fill.fund_code == "000001"
        assert fill.direction == "subscribe"
        assert fill.nav == Decimal("1.5000")
        # 费用 = 10000 * 0.0015 / 1.0015 ≈ 14.98
        assert fill.fee > Decimal("0")
        # 份额 = (10000 - fee) / 1.5
        assert fill.shares > Decimal("0")
        assert fill.confirm_date == date(2024, 1, 3)

        # 持仓应更新
        positions = broker.get_positions()
        assert "000001" in positions
        assert positions["000001"] == fill.shares

    def test_subscribe_insufficient_cash(self, broker):
        """现金不足时应拒绝申购。"""
        intent = OrderIntent(
            fund_code="000001",
            direction="subscribe",
            amount=Decimal("200000"),
        )
        with pytest.raises(ValueError, match="现金不足"):
            broker.submit_order(intent)

    def test_subscribe_without_amount_raises(self, broker):
        """申购订单缺少金额应报错。"""
        intent = OrderIntent(
            fund_code="000001",
            direction="subscribe",
            shares=Decimal("100"),
        )
        with pytest.raises(ValueError, match="必须指定金额"):
            broker.submit_order(intent)


# ---------------------------------------------------------------------------
# 赎回流程测试
# ---------------------------------------------------------------------------


class TestRedeem:
    """赎回订单测试。"""

    def test_redeem_after_subscribe(self, broker):
        """先申购再赎回，应正确扣减份额并增加现金。"""
        # 先申购
        sub_intent = OrderIntent(
            fund_code="000001",
            direction="subscribe",
            amount=Decimal("10000"),
        )
        broker.submit_order(sub_intent)
        fills = broker.settle(date(2024, 1, 3))
        subscribed_shares = fills[0].shares

        # 赎回一半
        redeem_shares = (subscribed_shares / Decimal("2")).quantize(Decimal("0.01"))
        redeem_intent = OrderIntent(
            fund_code="000001",
            direction="redeem",
            shares=redeem_shares,
        )
        broker.advance_date(date(2024, 1, 3))
        order = broker.submit_order(redeem_intent)
        assert order.status == OrderStatus.PENDING

        # T+1 结算赎回
        redeem_fills = broker.settle(date(2024, 1, 4))
        assert len(redeem_fills) == 1
        rf = redeem_fills[0]
        assert rf.direction == "redeem"
        assert rf.shares == redeem_shares
        assert rf.fee > Decimal("0")

        # 持仓应减少
        positions = broker.get_positions()
        assert positions["000001"] == subscribed_shares - redeem_shares

    def test_redeem_insufficient_shares(self, broker):
        """份额不足时应拒绝赎回。"""
        intent = OrderIntent(
            fund_code="000001",
            direction="redeem",
            shares=Decimal("1000"),
        )
        with pytest.raises(ValueError, match="份额不足"):
            broker.submit_order(intent)

    def test_redeem_without_shares_raises(self, broker):
        """赎回订单缺少份额应报错。"""
        intent = OrderIntent(
            fund_code="000001",
            direction="redeem",
            amount=Decimal("5000"),
        )
        with pytest.raises(ValueError, match="必须指定份额"):
            broker.submit_order(intent)

    def test_full_redeem_removes_position(self, broker):
        """全部赎回后持仓应清除。"""
        # 申购
        broker.submit_order(OrderIntent(
            fund_code="000001", direction="subscribe", amount=Decimal("10000")
        ))
        fills = broker.settle(date(2024, 1, 3))
        all_shares = fills[0].shares

        # 全部赎回
        broker.advance_date(date(2024, 1, 3))
        broker.submit_order(OrderIntent(
            fund_code="000001", direction="redeem", shares=all_shares
        ))
        broker.settle(date(2024, 1, 4))

        positions = broker.get_positions()
        assert "000001" not in positions


# ---------------------------------------------------------------------------
# 订单取消测试
# ---------------------------------------------------------------------------


class TestCancelOrder:
    """订单取消测试。"""

    def test_cancel_pending_order(self, broker):
        """取消 PENDING 订单应成功并归还冻结资金。"""
        intent = OrderIntent(
            fund_code="000001",
            direction="subscribe",
            amount=Decimal("10000"),
        )
        order = broker.submit_order(intent)
        assert broker.get_cash() == Decimal("90000")

        result = broker.cancel_order(order.order_id)
        assert result is True
        assert broker.get_cash() == Decimal("100000")

    def test_cancel_nonexistent_order(self, broker):
        """取消不存在的订单应返回 False。"""
        result = broker.cancel_order("NONEXISTENT-ID")
        assert result is False

    def test_cancel_filled_order(self, broker):
        """已成交的订单不能取消。"""
        intent = OrderIntent(
            fund_code="000001",
            direction="subscribe",
            amount=Decimal("10000"),
        )
        order = broker.submit_order(intent)
        broker.settle(date(2024, 1, 3))

        result = broker.cancel_order(order.order_id)
        assert result is False


# ---------------------------------------------------------------------------
# 订单状态查询测试
# ---------------------------------------------------------------------------


class TestOrderStatus:
    """订单状态查询测试。"""

    def test_get_pending_status(self, broker):
        """新提交的订单状态应为 PENDING。"""
        intent = OrderIntent(
            fund_code="000001",
            direction="subscribe",
            amount=Decimal("10000"),
        )
        order = broker.submit_order(intent)
        status = broker.get_order_status(order.order_id)
        assert status == OrderStatus.PENDING

    def test_get_filled_status(self, broker):
        """结算后订单状态应为 FILLED。"""
        intent = OrderIntent(
            fund_code="000001",
            direction="subscribe",
            amount=Decimal("10000"),
        )
        order = broker.submit_order(intent)
        broker.settle(date(2024, 1, 3))

        status = broker.get_order_status(order.order_id)
        assert status == OrderStatus.FILLED

    def test_get_nonexistent_order_status(self, broker):
        """查询不存在的订单应返回 None。"""
        status = broker.get_order_status("NONEXISTENT")
        assert status is None


# ---------------------------------------------------------------------------
# NAV 缺失处理测试
# ---------------------------------------------------------------------------


class TestNavMissing:
    """净值缺失场景测试。"""

    def test_settle_with_missing_nav_rejects_order(self):
        """结算时无法获取净值应拒绝订单并归还资金。"""
        broker = PaperBroker(
            initial_cash=Decimal("100000"),
            nav_provider=lambda code, dt: None,  # 始终返回 None
            current_date=date(2024, 1, 2),
        )

        intent = OrderIntent(
            fund_code="999999",
            direction="subscribe",
            amount=Decimal("10000"),
        )
        order = broker.submit_order(intent)
        assert broker.get_cash() == Decimal("90000")

        fills = broker.settle(date(2024, 1, 3))
        assert len(fills) == 0
        # 资金应归还
        assert broker.get_cash() == Decimal("100000")
        # 订单应被拒绝
        assert broker.get_order_status(order.order_id) == OrderStatus.REJECTED


# ---------------------------------------------------------------------------
# 依赖注入兼容性测试
# ---------------------------------------------------------------------------


class TestDependencyInjection:
    """验证策略层可通过依赖注入使用 Broker。"""

    def test_strategy_uses_broker_interface(self, broker):
        """模拟策略通过 Broker 接口操作。"""

        def execute_strategy(b: Broker) -> None:
            """模拟策略执行逻辑，接受 Broker Protocol。"""
            # 查看现金
            cash = b.get_cash()
            assert cash > Decimal("0")

            # 提交订单
            intent = OrderIntent(
                fund_code="000001",
                direction="subscribe",
                amount=Decimal("5000"),
            )
            order = b.submit_order(intent)
            assert order.status == OrderStatus.PENDING

            # 查看持仓
            positions = b.get_positions()
            assert isinstance(positions, dict)

            # 查询订单状态
            status = b.get_order_status(order.order_id)
            assert status == OrderStatus.PENDING

        # PaperBroker 应能作为 Broker 使用
        execute_strategy(broker)

    def test_multiple_brokers_same_interface(self, nav_provider):
        """不同 Broker 实例应通过相同接口操作。"""
        broker1 = PaperBroker(
            initial_cash=Decimal("50000"),
            nav_provider=nav_provider,
            current_date=date(2024, 1, 2),
        )
        broker2 = PaperBroker(
            initial_cash=Decimal("200000"),
            nav_provider=nav_provider,
            current_date=date(2024, 1, 2),
        )

        # 两个 broker 都满足 Protocol
        assert isinstance(broker1, Broker)
        assert isinstance(broker2, Broker)

        # 独立操作互不影响
        broker1.submit_order(OrderIntent(
            fund_code="000001", direction="subscribe", amount=Decimal("10000")
        ))
        assert broker1.get_cash() == Decimal("40000")
        assert broker2.get_cash() == Decimal("200000")


# ---------------------------------------------------------------------------
# 多笔订单与多基金测试
# ---------------------------------------------------------------------------


class TestMultipleOrders:
    """多笔订单场景测试。"""

    def test_multiple_subscribe_orders(self, broker):
        """多笔申购订单应在同一次结算中全部处理。"""
        broker.submit_order(OrderIntent(
            fund_code="000001", direction="subscribe", amount=Decimal("10000")
        ))
        broker.submit_order(OrderIntent(
            fund_code="000002", direction="subscribe", amount=Decimal("20000")
        ))

        fills = broker.settle(date(2024, 1, 3))
        assert len(fills) == 2

        positions = broker.get_positions()
        assert "000001" in positions
        assert "000002" in positions

    def test_settle_only_processes_pending(self, broker):
        """结算只处理 PENDING 状态的订单。"""
        broker.submit_order(OrderIntent(
            fund_code="000001", direction="subscribe", amount=Decimal("10000")
        ))
        broker.settle(date(2024, 1, 3))

        # 第二次结算不应有新的 fill
        fills = broker.settle(date(2024, 1, 4))
        assert len(fills) == 0

    def test_set_nav_for_testing(self, broker):
        """set_nav 应覆盖 nav_provider 的值。"""
        broker.set_nav("000001", Decimal("2.0000"))

        broker.submit_order(OrderIntent(
            fund_code="000001", direction="subscribe", amount=Decimal("10000")
        ))
        fills = broker.settle(date(2024, 1, 3))

        # 应使用 set_nav 设置的值
        assert fills[0].nav == Decimal("2.0000")
