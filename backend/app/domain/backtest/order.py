"""订单模型模块。

定义回测引擎中的订单相关数据模型：
- OrderIntent: 策略产生的调仓意图
- Order: 经过风控验证后的正式订单
- OrderStatus: 订单状态枚举
- Fill: 订单成交记录

设计要点：
- OrderIntent 是策略层产出，表达"我想买/卖"的意图
- 经过风控检查和资金验证后，OrderIntent 转为 Order 进入队列
- Order 状态流转：pending → confirmed → filled（或 rejected）
- Fill 记录最终成交明细（份额、金额、费用、确认日期）

用法示例::

    from datetime import date
    from decimal import Decimal
    from app.domain.backtest.order import OrderIntent, Order, OrderStatus, Fill

    intent = OrderIntent(
        fund_code="000001",
        direction="subscribe",
        amount=Decimal("10000"),
    )

    order = Order.from_intent(
        intent=intent,
        order_id="ORD-20240102-001",
        order_date=date(2024, 1, 2),
    )

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
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# 订单状态枚举
# ---------------------------------------------------------------------------


class OrderStatus(str, Enum):
    """订单状态。

    状态流转：
    - pending: 已提交，等待确认（T+1 结算中）
    - confirmed: 已确认份额/金额
    - filled: 已完全成交（资金到账）
    - rejected: 被拒绝（限购、暂停申购、资金不足等）
    """

    PENDING = "pending"
    CONFIRMED = "confirmed"
    FILLED = "filled"
    REJECTED = "rejected"


# ---------------------------------------------------------------------------
# 订单意图（策略层产出）
# ---------------------------------------------------------------------------


class OrderIntent(BaseModel):
    """策略产生的调仓意图。

    策略的 on_bar 方法返回 OrderIntent 列表，表达调仓需求。
    经过风控、资金检查、限购校验后才转为正式 Order。

    Attributes:
        fund_code: 基金代码
        direction: 交易方向（subscribe=申购, redeem=赎回）
        amount: 申购金额（申购时使用）
        shares: 赎回份额（赎回时使用）
        target_weight: 目标权重（可选，用于 rebalance 场景）
    """

    model_config = ConfigDict(frozen=True)

    fund_code: str
    direction: Literal["subscribe", "redeem"]
    amount: Decimal | None = None
    shares: Decimal | None = None
    target_weight: Decimal | None = None


# ---------------------------------------------------------------------------
# 正式订单
# ---------------------------------------------------------------------------


class Order(BaseModel):
    """正式订单。

    经过风控验证后的订单，进入结算队列等待 T+1 确认。

    Attributes:
        order_id: 唯一订单标识
        fund_code: 基金代码
        direction: 交易方向
        amount: 申购金额
        shares: 赎回份额
        target_weight: 目标权重
        order_date: 下单日期
        status: 当前状态
        confirm_date: 确认日期（T+1 确认后填入）
        reject_reason: 拒绝原因（被拒绝时填入）
    """

    model_config = ConfigDict(validate_assignment=True)

    order_id: str
    fund_code: str
    direction: Literal["subscribe", "redeem"]
    amount: Decimal | None = None
    shares: Decimal | None = None
    target_weight: Decimal | None = None
    order_date: date
    status: OrderStatus = OrderStatus.PENDING
    confirm_date: date | None = None
    reject_reason: str | None = None

    @classmethod
    def from_intent(
        cls,
        intent: OrderIntent,
        order_id: str,
        order_date: date,
    ) -> Order:
        """从 OrderIntent 创建正式订单。

        Args:
            intent: 策略产生的订单意图
            order_id: 分配的唯一订单 ID
            order_date: 下单日期

        Returns:
            状态为 PENDING 的新订单
        """
        return cls(
            order_id=order_id,
            fund_code=intent.fund_code,
            direction=intent.direction,
            amount=intent.amount,
            shares=intent.shares,
            target_weight=intent.target_weight,
            order_date=order_date,
        )

    def confirm(self, confirm_date: date) -> None:
        """确认订单。

        Args:
            confirm_date: 确认日期

        Raises:
            ValueError: 如果订单不处于 PENDING 状态
        """
        if self.status != OrderStatus.PENDING:
            raise ValueError(
                f"Cannot confirm order {self.order_id}: "
                f"current status is {self.status.value}, expected pending"
            )
        self.status = OrderStatus.CONFIRMED
        self.confirm_date = confirm_date

    def fill(self) -> None:
        """标记订单为已成交。

        Raises:
            ValueError: 如果订单不处于 CONFIRMED 状态
        """
        if self.status != OrderStatus.CONFIRMED:
            raise ValueError(
                f"Cannot fill order {self.order_id}: "
                f"current status is {self.status.value}, expected confirmed"
            )
        self.status = OrderStatus.FILLED

    def reject(self, reason: str) -> None:
        """拒绝订单。

        Args:
            reason: 拒绝原因

        Raises:
            ValueError: 如果订单不处于 PENDING 状态
        """
        if self.status != OrderStatus.PENDING:
            raise ValueError(
                f"Cannot reject order {self.order_id}: "
                f"current status is {self.status.value}, expected pending"
            )
        self.status = OrderStatus.REJECTED
        self.reject_reason = reason


# ---------------------------------------------------------------------------
# 成交记录
# ---------------------------------------------------------------------------


class Fill(BaseModel):
    """订单成交记录。

    记录订单最终的执行明细，包括成交份额、金额、净值、费用和确认日期。

    Attributes:
        order_id: 关联的订单 ID
        fund_code: 基金代码
        direction: 交易方向
        shares: 成交份额
        amount: 成交金额
        nav: 成交净值
        fee: 交易费用
        confirm_date: 确认日期
        order_date: 下单日期（从 Order 传入）
    """

    model_config = ConfigDict(frozen=True)

    order_id: str
    fund_code: str
    direction: Literal["subscribe", "redeem"]
    shares: Decimal
    amount: Decimal
    nav: Decimal
    fee: Decimal = Field(default=Decimal("0"))
    confirm_date: date
    order_date: date | None = None
    lot_details: list[dict[str, Any]] = Field(default_factory=list)
