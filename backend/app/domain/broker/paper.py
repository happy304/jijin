"""PaperBroker - 纸面撮合 Broker 实现。

模拟真实交易环境的纸面撮合器，作为 Broker Protocol 的参考实现。
支持 T+1 结算规则，可用于策略验证和模拟交易。

设计要点：
- 完整实现 Broker Protocol 的所有方法
- 模拟 T+1 结算：订单提交后需调用 settle() 推进结算
- 使用内存状态管理持仓和订单
- 支持通过 NAV 字典提供净值数据

需求: 10.3, 10.6
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Callable
from uuid import uuid4

from app.domain.backtest.order import Fill, Order, OrderIntent, OrderStatus


class PaperBroker:
    """纸面撮合 Broker。

    模拟真实交易的纸面撮合器，支持：
    - 订单提交与取消
    - T+1 结算（通过 settle 方法推进）
    - 持仓与现金管理
    - 简化费率计算

    Usage::

        broker = PaperBroker(
            initial_cash=Decimal("100000"),
            nav_provider=lambda code, dt: Decimal("1.5"),
            fee_rate=Decimal("0.0015"),
        )

        # 提交订单
        intent = OrderIntent(fund_code="000001", direction="subscribe", amount=Decimal("10000"))
        order = broker.submit_order(intent)

        # T+1 结算
        fills = broker.settle(date(2024, 1, 3))

    Args:
        initial_cash: 初始资金
        nav_provider: 净值查询函数 (fund_code, date) -> Decimal
        fee_rate: 统一费率（简化），默认 0.15%
        current_date: 当前日期（可选，用于订单日期标记）
    """

    def __init__(
        self,
        initial_cash: Decimal = Decimal("100000"),
        nav_provider: Callable[[str, date], Decimal | None] | None = None,
        fee_rate: Decimal = Decimal("0.0015"),
        current_date: date | None = None,
    ) -> None:
        self._cash = initial_cash
        self._positions: dict[str, Decimal] = {}
        self._orders: dict[str, Order] = {}
        self._fills: list[Fill] = []
        self._nav_provider = nav_provider or self._default_nav_provider
        self._fee_rate = fee_rate
        self._current_date = current_date or date.today()
        self._nav_cache: dict[str, Decimal] = {}

    # ------------------------------------------------------------------
    # Broker Protocol 实现
    # ------------------------------------------------------------------

    def submit_order(self, intent: OrderIntent) -> Order:
        """提交订单意图，返回正式订单。

        校验逻辑：
        - 申购：检查可用现金是否充足
        - 赎回：检查持仓份额是否充足

        Args:
            intent: 订单意图

        Returns:
            状态为 PENDING 的正式订单

        Raises:
            ValueError: 资金不足或份额不足
        """
        # 校验
        if intent.direction == "subscribe":
            if intent.amount is None:
                raise ValueError("申购订单必须指定金额")
            if intent.amount > self._cash:
                raise ValueError(
                    f"现金不足: 需要 {intent.amount}, 可用 {self._cash}"
                )
            # 冻结资金
            self._cash -= intent.amount

        elif intent.direction == "redeem":
            if intent.shares is None:
                raise ValueError("赎回订单必须指定份额")
            current_shares = self._positions.get(intent.fund_code, Decimal("0"))
            if intent.shares > current_shares:
                raise ValueError(
                    f"份额不足: 需要 {intent.shares}, "
                    f"持有 {current_shares}"
                )

        # 生成订单
        order_id = f"PAPER-{uuid4().hex[:8].upper()}"
        order = Order.from_intent(
            intent=intent,
            order_id=order_id,
            order_date=self._current_date,
        )
        self._orders[order_id] = order
        return order

    def cancel_order(self, order_id: str) -> bool:
        """取消未确认的订单。

        取消时归还冻结的资金（申购订单）。

        Args:
            order_id: 订单 ID

        Returns:
            是否取消成功
        """
        order = self._orders.get(order_id)
        if order is None or order.status != OrderStatus.PENDING:
            return False

        # 归还冻结资金
        if order.direction == "subscribe" and order.amount is not None:
            self._cash += order.amount

        order.reject("用户取消")
        return True

    def get_positions(self) -> dict[str, Decimal]:
        """获取当前持仓。

        Returns:
            持仓字典 {fund_code: shares}
        """
        return dict(self._positions)

    def get_cash(self) -> Decimal:
        """获取可用现金余额。

        Returns:
            可用现金
        """
        return self._cash

    def get_order_status(self, order_id: str) -> OrderStatus | None:
        """查询订单状态。

        Args:
            order_id: 订单 ID

        Returns:
            订单状态，不存在返回 None
        """
        order = self._orders.get(order_id)
        return order.status if order else None

    # ------------------------------------------------------------------
    # 结算方法（模拟 T+1）
    # ------------------------------------------------------------------

    def settle(self, settle_date: date) -> list[Fill]:
        """执行 T+1 结算。

        处理所有 PENDING 状态的订单，使用 settle_date 的净值进行撮合。
        模拟真实的 T+1 结算流程：
        - 申购：以结算日净值计算份额，扣除费用
        - 赎回：以结算日净值计算金额，扣除费用

        Args:
            settle_date: 结算日期（T+1 日）

        Returns:
            本次结算产生的成交记录列表
        """
        fills: list[Fill] = []

        pending_orders = [
            o for o in self._orders.values()
            if o.status == OrderStatus.PENDING
        ]

        for order in pending_orders:
            nav = self._get_nav(order.fund_code, settle_date)
            if nav is None or nav <= Decimal("0"):
                # 无法获取净值，拒绝订单
                if order.direction == "subscribe" and order.amount is not None:
                    self._cash += order.amount  # 归还冻结资金
                order.reject(f"无法获取 {order.fund_code} 在 {settle_date} 的净值")
                continue

            fill = self._execute_order(order, nav, settle_date)
            if fill is not None:
                fills.append(fill)
                self._fills.append(fill)

        self._current_date = settle_date
        return fills

    def advance_date(self, new_date: date) -> None:
        """推进当前日期（不触发结算）。

        Args:
            new_date: 新的当前日期
        """
        self._current_date = new_date

    # ------------------------------------------------------------------
    # 查询方法
    # ------------------------------------------------------------------

    def get_order(self, order_id: str) -> Order | None:
        """获取订单详情。

        Args:
            order_id: 订单 ID

        Returns:
            订单对象，不存在返回 None
        """
        return self._orders.get(order_id)

    def get_fills(self) -> list[Fill]:
        """获取所有成交记录。

        Returns:
            成交记录列表
        """
        return list(self._fills)

    def set_nav(self, fund_code: str, nav: Decimal) -> None:
        """手动设置基金净值（用于测试）。

        Args:
            fund_code: 基金代码
            nav: 净值
        """
        self._nav_cache[fund_code] = nav

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _execute_order(
        self, order: Order, nav: Decimal, settle_date: date
    ) -> Fill | None:
        """执行单笔订单撮合。

        Args:
            order: 待执行的订单
            nav: 结算净值
            settle_date: 结算日期

        Returns:
            成交记录，执行失败返回 None
        """
        if order.direction == "subscribe":
            return self._execute_subscribe(order, nav, settle_date)
        else:
            return self._execute_redeem(order, nav, settle_date)

    def _execute_subscribe(
        self, order: Order, nav: Decimal, settle_date: date
    ) -> Fill | None:
        """执行申购订单。

        计算逻辑：
        - 费用 = 金额 × 费率 / (1 + 费率)（外扣法）
        - 净投入 = 金额 - 费用
        - 份额 = 净投入 / 净值
        """
        if order.amount is None:
            order.reject("申购订单缺少金额")
            return None

        amount = order.amount
        fee = (amount * self._fee_rate / (Decimal("1") + self._fee_rate)).quantize(
            Decimal("0.01")
        )
        net_amount = amount - fee
        shares = (net_amount / nav).quantize(Decimal("0.01"))

        # 更新持仓
        self._positions[order.fund_code] = (
            self._positions.get(order.fund_code, Decimal("0")) + shares
        )

        # 更新订单状态
        order.confirm(settle_date)
        order.fill()

        return Fill(
            order_id=order.order_id,
            fund_code=order.fund_code,
            direction="subscribe",
            shares=shares,
            amount=net_amount,
            nav=nav,
            fee=fee,
            confirm_date=settle_date,
        )

    def _execute_redeem(
        self, order: Order, nav: Decimal, settle_date: date
    ) -> Fill | None:
        """执行赎回订单。

        计算逻辑：
        - 赎回金额 = 份额 × 净值
        - 费用 = 赎回金额 × 费率
        - 到账金额 = 赎回金额 - 费用
        """
        if order.shares is None:
            order.reject("赎回订单缺少份额")
            return None

        shares = order.shares
        current_shares = self._positions.get(order.fund_code, Decimal("0"))

        if shares > current_shares:
            order.reject(f"份额不足: 需要 {shares}, 持有 {current_shares}")
            return None

        gross_amount = (shares * nav).quantize(Decimal("0.01"))
        fee = (gross_amount * self._fee_rate).quantize(Decimal("0.01"))
        net_amount = gross_amount - fee

        # 更新持仓
        self._positions[order.fund_code] = current_shares - shares
        if self._positions[order.fund_code] == Decimal("0"):
            del self._positions[order.fund_code]

        # 到账现金
        self._cash += net_amount

        # 更新订单状态
        order.confirm(settle_date)
        order.fill()

        return Fill(
            order_id=order.order_id,
            fund_code=order.fund_code,
            direction="redeem",
            shares=shares,
            amount=gross_amount,
            nav=nav,
            fee=fee,
            confirm_date=settle_date,
        )

    def _get_nav(self, fund_code: str, dt: date) -> Decimal | None:
        """获取基金净值。

        优先从缓存获取，否则调用 nav_provider。

        Args:
            fund_code: 基金代码
            dt: 日期

        Returns:
            净值，无法获取返回 None
        """
        if fund_code in self._nav_cache:
            return self._nav_cache[fund_code]
        return self._nav_provider(fund_code, dt)

    @staticmethod
    def _default_nav_provider(fund_code: str, dt: date) -> Decimal | None:
        """默认净值提供者（返回 None，需用户配置）。"""
        return None
