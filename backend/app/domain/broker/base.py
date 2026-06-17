"""Broker Protocol 定义。

定义实盘交易接口的统一抽象，策略代码通过此接口与交易系统交互，
无需关心底层是回测引擎还是实盘券商。

设计要点：
- 使用 Protocol 而非 ABC，允许结构化子类型（duck typing）
- 方法签名与回测引擎的 Portfolio/Order 模型对齐
- 策略代码在回测与实盘下无需修改（需求 10.6）
- 开发者接入实盘只需实现此 Protocol（需求 10.3）

需求: 10.3, 10.6
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Protocol, runtime_checkable

from app.domain.backtest.order import Fill, Order, OrderIntent, OrderStatus


@runtime_checkable
class Broker(Protocol):
    """交易 Broker 接口。

    所有 Broker 实现（纸面撮合、实盘券商等）必须满足此协议。
    策略层通过依赖注入获取 Broker 实例，实现回测/实盘代码统一。

    Methods:
        submit_order: 提交订单意图，返回正式订单
        cancel_order: 取消未确认的订单
        get_positions: 获取当前持仓
        get_cash: 获取可用现金
        get_order_status: 查询订单状态
    """

    def submit_order(self, intent: OrderIntent) -> Order:
        """提交订单意图，返回正式订单。

        Broker 接收策略的调仓意图，进行基本校验后生成正式订单。
        订单初始状态为 PENDING，等待结算确认。

        Args:
            intent: 策略产生的订单意图

        Returns:
            生成的正式订单（状态为 PENDING）

        Raises:
            ValueError: 如果订单不合法（如资金不足、份额不足）
        """
        ...

    def cancel_order(self, order_id: str) -> bool:
        """取消未确认的订单。

        只有状态为 PENDING 的订单可以取消。

        Args:
            order_id: 要取消的订单 ID

        Returns:
            True 表示取消成功，False 表示订单不存在或无法取消
        """
        ...

    def get_positions(self) -> dict[str, Decimal]:
        """获取当前持仓。

        Returns:
            持仓字典 {fund_code: shares}
        """
        ...

    def get_cash(self) -> Decimal:
        """获取可用现金余额。

        Returns:
            可用现金金额
        """
        ...

    def get_order_status(self, order_id: str) -> OrderStatus | None:
        """查询订单状态。

        Args:
            order_id: 订单 ID

        Returns:
            订单状态，如果订单不存在返回 None
        """
        ...
