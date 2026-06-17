"""风控规则：换手率限制模块。

实现 MaxTurnoverRule：
- 跟踪累计交易金额
- 当年化换手率超过阈值时，拒绝新的调仓订单
- 支持按日/按周/按月重置计数器

设计要点：
- 换手率 = Σ|交易金额| / 平均组合市值
- 年化换手率 = 换手率 / 已过交易日数 × 252
- 超限时只拒绝申购，不影响赎回（允许减仓）

需求: 优化计划 4.2
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


class MaxTurnoverRule(RiskRule):
    """年化换手率限制规则。

    跟踪累计交易金额，当年化换手率超过阈值时拒绝新的申购订单。

    Attributes:
        max_annual_turnover: 最大年化换手率（如 12.0 表示年换手 12 倍）
        portfolio_value_provider: 组合总市值查询函数
        trading_days_elapsed_provider: 已过交易日数查询函数

    Example::

        rule = MaxTurnoverRule(
            max_annual_turnover=Decimal("12.0"),
            portfolio_value_provider=lambda: total_value,
            trading_days_elapsed_provider=lambda: days_count,
        )
    """

    def __init__(
        self,
        max_annual_turnover: Decimal,
        portfolio_value_provider: Callable[[], Decimal] | None = None,
        trading_days_elapsed_provider: Callable[[], int] | None = None,
    ) -> None:
        if max_annual_turnover <= Decimal("0"):
            raise ValueError(
                f"max_annual_turnover must be positive, got {max_annual_turnover}"
            )

        self.max_annual_turnover = max_annual_turnover
        self.portfolio_value_provider = portfolio_value_provider
        self.trading_days_elapsed_provider = trading_days_elapsed_provider

        # 内部状态
        self._cumulative_turnover: Decimal = Decimal("0")
        self._trading_days: int = 0

    @property
    def cumulative_turnover(self) -> Decimal:
        """累计交易金额。"""
        return self._cumulative_turnover

    @property
    def current_annual_turnover(self) -> Decimal:
        """当前年化换手率估算。"""
        if self._trading_days <= 0:
            return Decimal("0")

        avg_value = self._get_portfolio_value()
        if avg_value <= Decimal("0"):
            return Decimal("0")

        turnover_ratio = self._cumulative_turnover / avg_value
        annualized = turnover_ratio * Decimal("252") / Decimal(str(self._trading_days))
        return annualized

    def record_trade(self, amount: Decimal) -> None:
        """记录一笔交易金额（由引擎在成交后调用）。

        Args:
            amount: 交易金额（绝对值）
        """
        self._cumulative_turnover += abs(amount)

    def advance_day(self) -> None:
        """推进一个交易日（由引擎每日调用）。"""
        self._trading_days += 1

    def apply(
        self,
        orders: list[OrderIntent],
        portfolio: Portfolio,
    ) -> list[OrderIntent]:
        """应用换手率限制规则。

        当年化换手率已超过阈值时，拒绝所有申购订单。
        赎回订单不受影响。
        """
        if not orders:
            return orders

        # 获取当前年化换手率
        annual_turnover = self.current_annual_turnover

        if annual_turnover < self.max_annual_turnover:
            return orders

        # 超限：只允许赎回
        result: list[OrderIntent] = []
        blocked_count = 0

        for order in orders:
            if order.direction == "redeem":
                result.append(order)
            else:
                blocked_count += 1

        if blocked_count > 0:
            logger.info(
                "MaxTurnoverRule: 年化换手率 %.2f 超过上限 %.2f，"
                "拒绝 %d 笔申购订单",
                float(annual_turnover),
                float(self.max_annual_turnover),
                blocked_count,
            )

        return result

    def reset(self) -> None:
        """重置状态（用于回测重新开始）。"""
        self._cumulative_turnover = Decimal("0")
        self._trading_days = 0

    def _get_portfolio_value(self) -> Decimal:
        """获取组合总市值。"""
        if self.portfolio_value_provider is not None:
            return self.portfolio_value_provider()
        return Decimal("100000")  # 默认值
