"""风控规则：最大回撤熔断模块。

实现 MaxDrawdownCircuitBreaker：
- 监控组合从高点的回撤幅度
- 当回撤超过阈值时触发熔断
- 熔断时按比例缩减仓位到安全线（而非清仓）
- 支持冷却期（熔断后 N 天内不允许新增仓位）

设计要点：
- 熔断触发时将超限仓位按比例缩放到安全线，避免冲击成本
- 安全线 = 1 - (当前回撤 / 最大允许回撤) 的比例保留仓位
- 冷却期内只允许赎回，不允许申购

需求: 6.2
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Callable

from app.domain.backtest.order import OrderIntent
from app.domain.backtest.portfolio import Portfolio
from app.domain.risk.limits import RiskRule

logger = logging.getLogger(__name__)


class MaxDrawdownCircuitBreaker(RiskRule):
    """最大回撤熔断规则。

    当组合回撤超过阈值时触发熔断，按比例缩减仓位。

    工作原理：
    1. 每次 apply 时计算当前回撤（需要外部提供权益曲线高点和当前值）
    2. 如果回撤 > max_drawdown，触发熔断：
       - 生成赎回订单，将仓位缩减到 target_position_ratio
       - 过滤掉所有申购订单
    3. 冷却期内（cooldown_days）只允许赎回

    Attributes:
        max_drawdown: 最大允许回撤（如 0.15 表示 15%）
        target_position_ratio: 熔断后目标仓位比例（如 0.5 表示缩减到 50%）
        cooldown_days: 熔断后冷却天数，期间不允许新增仓位
        nav_provider: 净值查询函数
        equity_peak_provider: 权益高点查询函数
        current_equity_provider: 当前权益查询函数
        current_date_provider: 当前日期查询函数

    Example::

        breaker = MaxDrawdownCircuitBreaker(
            max_drawdown=Decimal("0.15"),
            target_position_ratio=Decimal("0.5"),
            cooldown_days=5,
            nav_provider=lambda code: nav_dict.get(code),
            equity_peak_provider=lambda: peak_equity,
            current_equity_provider=lambda: current_equity,
        )
    """

    def __init__(
        self,
        max_drawdown: Decimal,
        target_position_ratio: Decimal = Decimal("0.5"),
        cooldown_days: int = 5,
        nav_provider: Callable[[str], Decimal | None] | None = None,
        equity_peak_provider: Callable[[], Decimal] | None = None,
        current_equity_provider: Callable[[], Decimal] | None = None,
        current_date_provider: Callable[[], date] | None = None,
    ) -> None:
        if max_drawdown <= Decimal("0") or max_drawdown >= Decimal("1"):
            raise ValueError(
                f"max_drawdown must be in (0, 1), got {max_drawdown}"
            )
        if target_position_ratio < Decimal("0") or target_position_ratio > Decimal("1"):
            raise ValueError(
                f"target_position_ratio must be in [0, 1], got {target_position_ratio}"
            )

        self.max_drawdown = max_drawdown
        self.target_position_ratio = target_position_ratio
        self.cooldown_days = cooldown_days
        self.nav_provider = nav_provider
        self.equity_peak_provider = equity_peak_provider
        self.current_equity_provider = current_equity_provider
        self.current_date_provider = current_date_provider

        # 内部状态
        self._is_triggered = False
        self._trigger_date: date | None = None
        self._peak_equity: Decimal = Decimal("0")

    @property
    def is_triggered(self) -> bool:
        """当前是否处于熔断状态。"""
        return self._is_triggered

    @property
    def trigger_date(self) -> date | None:
        """熔断触发日期。"""
        return self._trigger_date

    def update_peak(self, equity: Decimal) -> None:
        """更新权益高点（外部调用，每日更新）。

        Args:
            equity: 当前权益值
        """
        if equity > self._peak_equity:
            self._peak_equity = equity

    def current_drawdown(self, current_equity: Decimal | None = None) -> Decimal:
        """计算当前回撤幅度。

        Args:
            current_equity: 当前权益值，如果为 None 则通过 provider 获取

        Returns:
            回撤幅度（0~1 之间的 Decimal）
        """
        if current_equity is None:
            if self.current_equity_provider is not None:
                current_equity = self.current_equity_provider()
            else:
                return Decimal("0")

        peak = self._peak_equity
        if self.equity_peak_provider is not None:
            peak = self.equity_peak_provider()

        if peak <= Decimal("0"):
            return Decimal("0")

        drawdown = (peak - current_equity) / peak
        return max(drawdown, Decimal("0"))

    def apply(
        self,
        orders: list[OrderIntent],
        portfolio: Portfolio,
    ) -> list[OrderIntent]:
        """应用最大回撤熔断规则。

        逻辑：
        1. 计算当前回撤
        2. 如果回撤超过阈值且未触发熔断 → 触发熔断，生成缩仓订单
        3. 如果处于冷却期 → 过滤掉所有申购订单
        4. 如果冷却期结束 → 解除熔断状态
        """
        # 获取当前日期
        current_date_val: date | None = None
        if self.current_date_provider is not None:
            current_date_val = self.current_date_provider()

        # 检查冷却期是否结束
        if self._is_triggered and current_date_val is not None and self._trigger_date is not None:
            days_since_trigger = (current_date_val - self._trigger_date).days
            if days_since_trigger > self.cooldown_days:
                self._is_triggered = False
                self._trigger_date = None
                logger.info(
                    "MaxDrawdownCircuitBreaker: 冷却期结束，解除熔断状态"
                )

        # 计算当前回撤
        dd = self.current_drawdown()

        # 检查是否需要触发熔断
        if dd >= self.max_drawdown and not self._is_triggered:
            self._is_triggered = True
            self._trigger_date = current_date_val
            logger.warning(
                "MaxDrawdownCircuitBreaker: 触发熔断！回撤 %.2f%% 超过阈值 %.2f%%，"
                "将仓位缩减到 %.2f%%",
                float(dd * 100),
                float(self.max_drawdown * 100),
                float(self.target_position_ratio * 100),
            )

            # 生成缩仓赎回订单
            redeem_orders = self._generate_reduction_orders(portfolio)
            # 过滤掉原始订单中的申购，保留赎回
            filtered = [o for o in orders if o.direction == "redeem"]
            return filtered + redeem_orders

        # 冷却期内：过滤掉所有申购订单
        if self._is_triggered:
            filtered = [o for o in orders if o.direction == "redeem"]
            if len(filtered) < len(orders):
                logger.info(
                    "MaxDrawdownCircuitBreaker: 冷却期内，过滤 %d 笔申购订单",
                    len(orders) - len(filtered),
                )
            return filtered

        # 正常状态：放行所有订单
        return orders

    def _generate_reduction_orders(
        self,
        portfolio: Portfolio,
    ) -> list[OrderIntent]:
        """生成按比例缩仓的赎回订单。

        将每个持仓按 (1 - target_position_ratio) 的比例赎回。

        Args:
            portfolio: 当前组合

        Returns:
            赎回订单列表
        """
        redeem_orders: list[OrderIntent] = []
        reduction_ratio = Decimal("1") - self.target_position_ratio

        for fund_code, shares in portfolio.positions.items():
            if shares <= Decimal("0"):
                continue

            redeem_shares = (shares * reduction_ratio).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

            if redeem_shares <= Decimal("0"):
                continue

            redeem_orders.append(
                OrderIntent(
                    fund_code=fund_code,
                    direction="redeem",
                    shares=redeem_shares,
                )
            )
            logger.info(
                "MaxDrawdownCircuitBreaker: 生成缩仓订单 %s 赎回 %s 份 "
                "(缩减比例 %.2f%%)",
                fund_code, redeem_shares, float(reduction_ratio * 100),
            )

        return redeem_orders

    def reset(self) -> None:
        """重置熔断状态（用于回测重新开始）。"""
        self._is_triggered = False
        self._trigger_date = None
        self._peak_equity = Decimal("0")
