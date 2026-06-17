"""SimulatedBroker - 模拟实盘 Broker 实现。

在 PaperBroker 基础上增加更真实的市场模拟：
- 随机滑点（模拟真实成交价偏差）
- 订单延迟（模拟网络和系统延迟）
- 部分成交（大额订单可能分批成交）
- 市场冲击成本（大额订单对价格的影响）

适用场景：
- 策略上线前的压力测试
- 评估策略在真实市场条件下的表现衰减
- 对比理想回测与模拟实盘的差异

设计要点：
- 实现 Broker Protocol 的所有方法
- 通过参数控制模拟的"真实程度"
- 可配置随机种子，确保可复现性

需求: 10.3（扩展平台 - 新增 Broker）
"""

from __future__ import annotations

import random
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Callable
from uuid import uuid4

from app.domain.backtest.order import Fill, Order, OrderIntent, OrderStatus


class SimulatedBroker:
    """模拟实盘 Broker。

    在纸面撮合基础上增加市场摩擦模拟：
    - 滑点：成交价格偏离理论净值
    - 延迟：订单确认可能延迟 1-2 天
    - 部分成交：大额订单可能只成交一部分
    - 市场冲击：大额订单导致成交价格不利偏移

    Usage::

        broker = SimulatedBroker(
            initial_cash=Decimal("100000"),
            nav_provider=lambda code, dt: Decimal("1.5"),
            slippage_bps=5,       # 5 个基点的滑点
            fill_ratio=0.95,      # 95% 成交率
            seed=42,              # 固定随机种子
        )

        intent = OrderIntent(fund_code="000001", direction="subscribe", amount=Decimal("10000"))
        order = broker.submit_order(intent)
        fills = broker.settle(date(2024, 1, 3))

    Args:
        initial_cash: 初始资金
        nav_provider: 净值查询函数 (fund_code, date) -> Decimal
        fee_rate: 基础费率（默认 0.15%）
        slippage_bps: 滑点基点数（默认 5 bps = 0.05%）
        fill_ratio: 成交比例（0-1，默认 1.0 全部成交）
        impact_bps_per_million: 每百万元市场冲击基点数（默认 2 bps）
        delay_probability: 订单延迟概率（0-1，默认 0.1）
        seed: 随机种子（None 表示不固定）
        current_date: 当前日期
    """

    def __init__(
        self,
        initial_cash: Decimal = Decimal("100000"),
        nav_provider: Callable[[str, date], Decimal | None] | None = None,
        fee_rate: Decimal = Decimal("0.0015"),
        slippage_bps: int = 5,
        fill_ratio: float = 1.0,
        impact_bps_per_million: int = 2,
        delay_probability: float = 0.1,
        seed: int | None = None,
        current_date: date | None = None,
    ) -> None:
        self._cash = initial_cash
        self._positions: dict[str, Decimal] = {}
        self._orders: dict[str, Order] = {}
        self._fills: list[Fill] = []
        self._nav_provider = nav_provider or self._default_nav_provider
        self._fee_rate = fee_rate
        self._slippage_bps = slippage_bps
        self._fill_ratio = fill_ratio
        self._impact_bps_per_million = impact_bps_per_million
        self._delay_probability = delay_probability
        self._current_date = current_date or date.today()
        self._nav_cache: dict[str, Decimal] = {}
        self._delayed_orders: dict[str, int] = {}  # order_id -> 剩余延迟天数

        # 随机数生成器（可复现）
        self._rng = random.Random(seed)

    # ------------------------------------------------------------------
    # Broker Protocol 实现
    # ------------------------------------------------------------------

    def submit_order(self, intent: OrderIntent) -> Order:
        """提交订单意图，返回正式订单。

        校验逻辑与 PaperBroker 相同，额外添加延迟模拟。

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
                    f"份额不足: 需要 {intent.shares}, 持有 {current_shares}"
                )

        # 生成订单
        order_id = f"SIM-{uuid4().hex[:8].upper()}"
        order = Order.from_intent(
            intent=intent,
            order_id=order_id,
            order_date=self._current_date,
        )
        self._orders[order_id] = order

        # 模拟延迟
        if self._rng.random() < self._delay_probability:
            self._delayed_orders[order_id] = 1  # 延迟 1 天
        else:
            self._delayed_orders[order_id] = 0  # 无延迟

        return order

    def cancel_order(self, order_id: str) -> bool:
        """取消未确认的订单。"""
        order = self._orders.get(order_id)
        if order is None or order.status != OrderStatus.PENDING:
            return False

        # 归还冻结资金
        if order.direction == "subscribe" and order.amount is not None:
            self._cash += order.amount

        order.reject("用户取消")
        self._delayed_orders.pop(order_id, None)
        return True

    def get_positions(self) -> dict[str, Decimal]:
        """获取当前持仓。"""
        return dict(self._positions)

    def get_cash(self) -> Decimal:
        """获取可用现金余额。"""
        return self._cash

    def get_order_status(self, order_id: str) -> OrderStatus | None:
        """查询订单状态。"""
        order = self._orders.get(order_id)
        return order.status if order else None

    # ------------------------------------------------------------------
    # 结算方法（带市场摩擦模拟）
    # ------------------------------------------------------------------

    def settle(self, settle_date: date) -> list[Fill]:
        """执行结算，模拟真实市场摩擦。

        与 PaperBroker 的区别：
        1. 滑点：成交净值在理论净值基础上随机偏移
        2. 部分成交：大额订单可能只成交一部分
        3. 市场冲击：大额订单导致额外成本
        4. 延迟：部分订单需要额外等待

        Args:
            settle_date: 结算日期

        Returns:
            本次结算产生的成交记录列表
        """
        fills: list[Fill] = []

        pending_orders = [
            o for o in self._orders.values()
            if o.status == OrderStatus.PENDING
        ]

        for order in pending_orders:
            # 检查延迟
            delay = self._delayed_orders.get(order.order_id, 0)
            if delay > 0:
                self._delayed_orders[order.order_id] = delay - 1
                continue

            nav = self._get_nav(order.fund_code, settle_date)
            if nav is None or nav <= Decimal("0"):
                if order.direction == "subscribe" and order.amount is not None:
                    self._cash += order.amount
                order.reject(f"无法获取 {order.fund_code} 在 {settle_date} 的净值")
                self._delayed_orders.pop(order.order_id, None)
                continue

            # 应用滑点和市场冲击
            adjusted_nav = self._apply_market_friction(nav, order)

            fill = self._execute_order(order, adjusted_nav, settle_date)
            if fill is not None:
                fills.append(fill)
                self._fills.append(fill)

            self._delayed_orders.pop(order.order_id, None)

        self._current_date = settle_date
        return fills

    def advance_date(self, new_date: date) -> None:
        """推进当前日期。"""
        self._current_date = new_date

    # ------------------------------------------------------------------
    # 市场摩擦模拟
    # ------------------------------------------------------------------

    def _apply_market_friction(self, nav: Decimal, order: Order) -> Decimal:
        """应用滑点和市场冲击到净值。

        Args:
            nav: 理论净值
            order: 订单

        Returns:
            调整后的成交净值
        """
        # 1. 随机滑点（正态分布，均值为 0，标准差为 slippage_bps）
        slippage_pct = self._rng.gauss(0, self._slippage_bps / 10000.0)

        # 2. 市场冲击（与订单金额成正比，方向不利）
        impact_pct = Decimal("0")
        if order.direction == "subscribe" and order.amount is not None:
            # 申购时价格上移（买入推高价格）
            amount_millions = order.amount / Decimal("1000000")
            impact_pct = (
                amount_millions
                * Decimal(str(self._impact_bps_per_million))
                / Decimal("10000")
            )
        elif order.direction == "redeem" and order.shares is not None:
            # 赎回时价格下移（卖出压低价格）
            estimated_amount = order.shares * nav
            amount_millions = estimated_amount / Decimal("1000000")
            impact_pct = -(
                amount_millions
                * Decimal(str(self._impact_bps_per_million))
                / Decimal("10000")
            )

        # 合并调整
        total_adjustment = Decimal("1") + Decimal(str(slippage_pct)) + impact_pct
        adjusted_nav = (nav * total_adjustment).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )

        # 确保净值为正
        if adjusted_nav <= Decimal("0"):
            adjusted_nav = nav

        return adjusted_nav

    # ------------------------------------------------------------------
    # 订单执行
    # ------------------------------------------------------------------

    def _execute_order(
        self, order: Order, nav: Decimal, settle_date: date
    ) -> Fill | None:
        """执行单笔订单撮合（含部分成交模拟）。"""
        if order.direction == "subscribe":
            return self._execute_subscribe(order, nav, settle_date)
        else:
            return self._execute_redeem(order, nav, settle_date)

    def _execute_subscribe(
        self, order: Order, nav: Decimal, settle_date: date
    ) -> Fill | None:
        """执行申购订单（含部分成交）。"""
        if order.amount is None:
            order.reject("申购订单缺少金额")
            return None

        # 模拟部分成交
        actual_ratio = self._get_fill_ratio()
        actual_amount = (order.amount * Decimal(str(actual_ratio))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        if actual_amount <= Decimal("0"):
            # 未成交，归还资金
            self._cash += order.amount
            order.reject("模拟未成交")
            return None

        # 未成交部分归还
        unfilled_amount = order.amount - actual_amount
        if unfilled_amount > Decimal("0"):
            self._cash += unfilled_amount

        # 计算费用（外扣法）
        fee = (
            actual_amount * self._fee_rate / (Decimal("1") + self._fee_rate)
        ).quantize(Decimal("0.01"))
        net_amount = actual_amount - fee
        shares = (net_amount / nav).quantize(Decimal("0.01"))

        # 更新持仓
        self._positions[order.fund_code] = (
            self._positions.get(order.fund_code, Decimal("0")) + shares
        )

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
        """执行赎回订单（含部分成交）。"""
        if order.shares is None:
            order.reject("赎回订单缺少份额")
            return None

        # 模拟部分成交
        actual_ratio = self._get_fill_ratio()
        actual_shares = (order.shares * Decimal(str(actual_ratio))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        if actual_shares <= Decimal("0"):
            order.reject("模拟未成交")
            return None

        current_shares = self._positions.get(order.fund_code, Decimal("0"))
        if actual_shares > current_shares:
            actual_shares = current_shares

        gross_amount = (actual_shares * nav).quantize(Decimal("0.01"))
        fee = (gross_amount * self._fee_rate).quantize(Decimal("0.01"))
        net_amount = gross_amount - fee

        # 更新持仓
        self._positions[order.fund_code] = current_shares - actual_shares
        if self._positions[order.fund_code] == Decimal("0"):
            del self._positions[order.fund_code]

        # 到账现金
        self._cash += net_amount

        order.confirm(settle_date)
        order.fill()

        return Fill(
            order_id=order.order_id,
            fund_code=order.fund_code,
            direction="redeem",
            shares=actual_shares,
            amount=gross_amount,
            nav=nav,
            fee=fee,
            confirm_date=settle_date,
        )

    def _get_fill_ratio(self) -> float:
        """获取本次成交比例（模拟部分成交）。

        基于配置的 fill_ratio 加上随机扰动。
        """
        if self._fill_ratio >= 1.0:
            return 1.0

        # 在 fill_ratio 附近随机波动
        ratio = self._rng.uniform(
            max(0.5, self._fill_ratio - 0.1),
            min(1.0, self._fill_ratio + 0.05),
        )
        return max(0.0, min(1.0, ratio))

    # ------------------------------------------------------------------
    # 查询方法
    # ------------------------------------------------------------------

    def get_order(self, order_id: str) -> Order | None:
        """获取订单详情。"""
        return self._orders.get(order_id)

    def get_fills(self) -> list[Fill]:
        """获取所有成交记录。"""
        return list(self._fills)

    def set_nav(self, fund_code: str, nav: Decimal) -> None:
        """手动设置基金净值（用于测试）。"""
        self._nav_cache[fund_code] = nav

    # ------------------------------------------------------------------
    # 统计方法
    # ------------------------------------------------------------------

    def get_slippage_stats(self) -> dict[str, float]:
        """获取滑点统计信息。

        Returns:
            包含平均滑点、最大滑点等统计的字典
        """
        if not self._fills:
            return {"avg_slippage_bps": 0.0, "total_fills": 0}

        return {
            "avg_slippage_bps": float(self._slippage_bps),
            "total_fills": len(self._fills),
            "fill_ratio_config": self._fill_ratio,
            "delay_probability": self._delay_probability,
        }

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _get_nav(self, fund_code: str, dt: date) -> Decimal | None:
        """获取基金净值。"""
        if fund_code in self._nav_cache:
            return self._nav_cache[fund_code]
        return self._nav_provider(fund_code, dt)

    @staticmethod
    def _default_nav_provider(fund_code: str, dt: date) -> Decimal | None:
        """默认净值提供者。"""
        return None
