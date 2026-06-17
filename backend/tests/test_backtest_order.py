"""订单模型单元测试。

覆盖：
- OrderIntent 创建与不可变性
- Order 从 OrderIntent 创建、状态流转（confirm/fill/reject）
- OrderStatus 枚举值
- Fill 成交记录创建与字段验证
- 非法状态转换的错误处理
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.domain.backtest.order import Fill, Order, OrderIntent, OrderStatus


# ---------------------------------------------------------------------------
# OrderStatus 枚举测试
# ---------------------------------------------------------------------------


class TestOrderStatus:
    """OrderStatus 枚举测试。"""

    def test_status_values(self) -> None:
        """枚举值应与设计一致。"""
        assert OrderStatus.PENDING == "pending"
        assert OrderStatus.CONFIRMED == "confirmed"
        assert OrderStatus.FILLED == "filled"
        assert OrderStatus.REJECTED == "rejected"

    def test_status_is_str_enum(self) -> None:
        """OrderStatus 应为字符串枚举。"""
        assert isinstance(OrderStatus.PENDING, str)
        assert OrderStatus.PENDING.value == "pending"


# ---------------------------------------------------------------------------
# OrderIntent 测试
# ---------------------------------------------------------------------------


class TestOrderIntent:
    """OrderIntent 订单意图测试。"""

    def test_subscribe_intent(self) -> None:
        """申购意图创建。"""
        intent = OrderIntent(
            fund_code="000001",
            direction="subscribe",
            amount=Decimal("10000"),
        )
        assert intent.fund_code == "000001"
        assert intent.direction == "subscribe"
        assert intent.amount == Decimal("10000")
        assert intent.shares is None
        assert intent.target_weight is None

    def test_redeem_intent(self) -> None:
        """赎回意图创建。"""
        intent = OrderIntent(
            fund_code="110011",
            direction="redeem",
            shares=Decimal("5000"),
        )
        assert intent.fund_code == "110011"
        assert intent.direction == "redeem"
        assert intent.shares == Decimal("5000")
        assert intent.amount is None

    def test_intent_with_target_weight(self) -> None:
        """带目标权重的意图。"""
        intent = OrderIntent(
            fund_code="000001",
            direction="subscribe",
            amount=Decimal("20000"),
            target_weight=Decimal("0.3"),
        )
        assert intent.target_weight == Decimal("0.3")

    def test_intent_is_frozen(self) -> None:
        """OrderIntent 应为不可变。"""
        intent = OrderIntent(
            fund_code="000001",
            direction="subscribe",
            amount=Decimal("10000"),
        )
        with pytest.raises(Exception):
            intent.amount = Decimal("20000")  # type: ignore[misc]

    def test_intent_invalid_direction(self) -> None:
        """无效方向应抛出验证错误。"""
        with pytest.raises(Exception):
            OrderIntent(
                fund_code="000001",
                direction="invalid",  # type: ignore[arg-type]
                amount=Decimal("10000"),
            )

    def test_intent_serialization(self) -> None:
        """OrderIntent 应支持序列化。"""
        intent = OrderIntent(
            fund_code="000001",
            direction="subscribe",
            amount=Decimal("10000"),
        )
        data = intent.model_dump()
        assert data["fund_code"] == "000001"
        assert data["direction"] == "subscribe"
        assert data["amount"] == Decimal("10000")


# ---------------------------------------------------------------------------
# Order 测试
# ---------------------------------------------------------------------------


class TestOrder:
    """Order 正式订单测试。"""

    def _make_subscribe_order(self) -> Order:
        """创建一个申购订单用于测试。"""
        intent = OrderIntent(
            fund_code="000001",
            direction="subscribe",
            amount=Decimal("10000"),
        )
        return Order.from_intent(
            intent=intent,
            order_id="ORD-20240102-001",
            order_date=date(2024, 1, 2),
        )

    def _make_redeem_order(self) -> Order:
        """创建一个赎回订单用于测试。"""
        intent = OrderIntent(
            fund_code="110011",
            direction="redeem",
            shares=Decimal("5000"),
        )
        return Order.from_intent(
            intent=intent,
            order_id="ORD-20240102-002",
            order_date=date(2024, 1, 2),
        )

    def test_from_intent_subscribe(self) -> None:
        """从申购意图创建订单。"""
        order = self._make_subscribe_order()
        assert order.order_id == "ORD-20240102-001"
        assert order.fund_code == "000001"
        assert order.direction == "subscribe"
        assert order.amount == Decimal("10000")
        assert order.shares is None
        assert order.order_date == date(2024, 1, 2)
        assert order.status == OrderStatus.PENDING
        assert order.confirm_date is None
        assert order.reject_reason is None

    def test_from_intent_redeem(self) -> None:
        """从赎回意图创建订单。"""
        order = self._make_redeem_order()
        assert order.order_id == "ORD-20240102-002"
        assert order.fund_code == "110011"
        assert order.direction == "redeem"
        assert order.shares == Decimal("5000")
        assert order.amount is None

    def test_from_intent_with_target_weight(self) -> None:
        """从带目标权重的意图创建订单。"""
        intent = OrderIntent(
            fund_code="000001",
            direction="subscribe",
            amount=Decimal("10000"),
            target_weight=Decimal("0.25"),
        )
        order = Order.from_intent(
            intent=intent,
            order_id="ORD-001",
            order_date=date(2024, 1, 2),
        )
        assert order.target_weight == Decimal("0.25")

    def test_confirm_order(self) -> None:
        """确认订单状态流转。"""
        order = self._make_subscribe_order()
        order.confirm(confirm_date=date(2024, 1, 3))

        assert order.status == OrderStatus.CONFIRMED
        assert order.confirm_date == date(2024, 1, 3)

    def test_fill_order(self) -> None:
        """成交订单状态流转。"""
        order = self._make_subscribe_order()
        order.confirm(confirm_date=date(2024, 1, 3))
        order.fill()

        assert order.status == OrderStatus.FILLED

    def test_reject_order(self) -> None:
        """拒绝订单状态流转。"""
        order = self._make_subscribe_order()
        order.reject(reason="基金暂停申购")

        assert order.status == OrderStatus.REJECTED
        assert order.reject_reason == "基金暂停申购"

    def test_confirm_non_pending_raises(self) -> None:
        """非 PENDING 状态的订单不能确认。"""
        order = self._make_subscribe_order()
        order.reject(reason="限购")

        with pytest.raises(ValueError, match="expected pending"):
            order.confirm(confirm_date=date(2024, 1, 3))

    def test_fill_non_confirmed_raises(self) -> None:
        """非 CONFIRMED 状态的订单不能成交。"""
        order = self._make_subscribe_order()

        with pytest.raises(ValueError, match="expected confirmed"):
            order.fill()

    def test_reject_non_pending_raises(self) -> None:
        """非 PENDING 状态的订单不能拒绝。"""
        order = self._make_subscribe_order()
        order.confirm(confirm_date=date(2024, 1, 3))

        with pytest.raises(ValueError, match="expected pending"):
            order.reject(reason="too late")

    def test_full_lifecycle_subscribe(self) -> None:
        """申购订单完整生命周期：pending → confirmed → filled。"""
        order = self._make_subscribe_order()
        assert order.status == OrderStatus.PENDING

        order.confirm(confirm_date=date(2024, 1, 3))
        assert order.status == OrderStatus.CONFIRMED

        order.fill()
        assert order.status == OrderStatus.FILLED

    def test_full_lifecycle_reject(self) -> None:
        """订单拒绝生命周期：pending → rejected。"""
        order = self._make_subscribe_order()
        assert order.status == OrderStatus.PENDING

        order.reject(reason="资金不足")
        assert order.status == OrderStatus.REJECTED


# ---------------------------------------------------------------------------
# Fill 测试
# ---------------------------------------------------------------------------


class TestFill:
    """Fill 成交记录测试。"""

    def test_subscribe_fill(self) -> None:
        """申购成交记录。"""
        fill = Fill(
            order_id="ORD-20240102-001",
            fund_code="000001",
            direction="subscribe",
            shares=Decimal("6543.21"),
            amount=Decimal("10000"),
            nav=Decimal("1.5280"),
            fee=Decimal("15.00"),
            confirm_date=date(2024, 1, 3),
        )
        assert fill.order_id == "ORD-20240102-001"
        assert fill.fund_code == "000001"
        assert fill.direction == "subscribe"
        assert fill.shares == Decimal("6543.21")
        assert fill.amount == Decimal("10000")
        assert fill.nav == Decimal("1.5280")
        assert fill.fee == Decimal("15.00")
        assert fill.confirm_date == date(2024, 1, 3)

    def test_redeem_fill(self) -> None:
        """赎回成交记录。"""
        fill = Fill(
            order_id="ORD-20240102-002",
            fund_code="110011",
            direction="redeem",
            shares=Decimal("5000"),
            amount=Decimal("7500"),
            nav=Decimal("1.5000"),
            fee=Decimal("37.50"),
            confirm_date=date(2024, 1, 3),
        )
        assert fill.direction == "redeem"
        assert fill.shares == Decimal("5000")
        assert fill.amount == Decimal("7500")
        assert fill.fee == Decimal("37.50")

    def test_fill_default_fee(self) -> None:
        """Fill 默认费用为 0。"""
        fill = Fill(
            order_id="ORD-001",
            fund_code="000001",
            direction="subscribe",
            shares=Decimal("1000"),
            amount=Decimal("1000"),
            nav=Decimal("1.0000"),
            confirm_date=date(2024, 1, 3),
        )
        assert fill.fee == Decimal("0")

    def test_fill_is_frozen(self) -> None:
        """Fill 应为不可变。"""
        fill = Fill(
            order_id="ORD-001",
            fund_code="000001",
            direction="subscribe",
            shares=Decimal("1000"),
            amount=Decimal("1000"),
            nav=Decimal("1.0000"),
            confirm_date=date(2024, 1, 3),
        )
        with pytest.raises(Exception):
            fill.shares = Decimal("2000")  # type: ignore[misc]

    def test_fill_serialization(self) -> None:
        """Fill 应支持序列化。"""
        fill = Fill(
            order_id="ORD-001",
            fund_code="000001",
            direction="subscribe",
            shares=Decimal("1000"),
            amount=Decimal("1500"),
            nav=Decimal("1.5000"),
            fee=Decimal("10"),
            confirm_date=date(2024, 1, 3),
        )
        data = fill.model_dump()
        assert data["order_id"] == "ORD-001"
        assert data["shares"] == Decimal("1000")
        assert data["confirm_date"] == date(2024, 1, 3)
