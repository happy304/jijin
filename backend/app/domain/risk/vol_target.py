"""风控规则：波动率目标自适应杠杆模块。

实现 VolTargetRule：
- 根据组合近期实现波动率动态调整仓位杠杆
- 当实现波动率 > 目标波动率时，缩减仓位
- 当实现波动率 < 目标波动率时，允许加仓（但不主动加仓）
- 通过调整申购/赎回金额实现杠杆控制

设计要点：
- 杠杆因子 = target_vol / realized_vol
- 杠杆因子 > 1 时不主动加仓，只是不限制
- 杠杆因子 < 1 时按比例缩减申购金额
- 支持杠杆上下限（避免极端情况）
- 使用滚动窗口计算实现波动率

需求: 6.3
"""

from __future__ import annotations

import logging
import math
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Callable, Sequence

from app.domain.backtest.order import OrderIntent
from app.domain.backtest.portfolio import Portfolio
from app.domain.risk.limits import RiskRule

logger = logging.getLogger(__name__)


def compute_realized_volatility(
    returns: Sequence[Decimal],
    annualization_factor: int = 252,
) -> Decimal:
    """计算年化实现波动率。

    使用简单标准差方法计算年化波动率。

    Args:
        returns: 日收益率序列
        annualization_factor: 年化因子（默认 252 个交易日）

    Returns:
        年化波动率（Decimal）
    """
    if len(returns) < 2:
        return Decimal("0")

    # 转为 float 计算（避免 Decimal 开方的复杂性）
    float_returns = [float(r) for r in returns]
    n = len(float_returns)
    mean = sum(float_returns) / n
    variance = sum((r - mean) ** 2 for r in float_returns) / (n - 1)
    daily_vol = math.sqrt(variance)
    annual_vol = daily_vol * math.sqrt(annualization_factor)

    return Decimal(str(annual_vol)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


class VolTargetRule(RiskRule):
    """波动率目标自适应杠杆规则。

    根据组合近期实现波动率动态调整仓位规模：
    - leverage_factor = target_vol / realized_vol
    - 当 leverage_factor < 1（波动率过高）：缩减申购金额
    - 当 leverage_factor >= 1（波动率正常或偏低）：不限制

    Attributes:
        target_vol: 目标年化波动率（如 0.10 表示 10%）
        lookback_days: 计算波动率的回看窗口（交易日数）
        max_leverage: 最大杠杆因子上限（防止极端放大）
        min_leverage: 最小杠杆因子下限（防止极端缩减）
        returns_provider: 组合日收益率序列提供函数

    Example::

        rule = VolTargetRule(
            target_vol=Decimal("0.10"),
            lookback_days=20,
            returns_provider=lambda: portfolio_daily_returns[-20:],
        )
    """

    def __init__(
        self,
        target_vol: Decimal,
        lookback_days: int = 20,
        max_leverage: Decimal = Decimal("2.0"),
        min_leverage: Decimal = Decimal("0.1"),
        returns_provider: Callable[[], Sequence[Decimal]] | None = None,
    ) -> None:
        if target_vol <= Decimal("0"):
            raise ValueError(f"target_vol must be positive, got {target_vol}")
        if lookback_days < 2:
            raise ValueError(
                f"lookback_days must be >= 2, got {lookback_days}"
            )
        if max_leverage < min_leverage:
            raise ValueError(
                f"max_leverage ({max_leverage}) must be >= min_leverage ({min_leverage})"
            )

        self.target_vol = target_vol
        self.lookback_days = lookback_days
        self.max_leverage = max_leverage
        self.min_leverage = min_leverage
        self.returns_provider = returns_provider

        # 内部状态：缓存最近的杠杆因子
        self._last_leverage: Decimal = Decimal("1.0")
        self._last_realized_vol: Decimal = Decimal("0")

    @property
    def last_leverage(self) -> Decimal:
        """最近计算的杠杆因子。"""
        return self._last_leverage

    @property
    def last_realized_vol(self) -> Decimal:
        """最近计算的实现波动率。"""
        return self._last_realized_vol

    def compute_leverage(
        self, returns: Sequence[Decimal] | None = None
    ) -> Decimal:
        """计算当前杠杆因子。

        Args:
            returns: 日收益率序列，如果为 None 则通过 provider 获取

        Returns:
            杠杆因子（clipped to [min_leverage, max_leverage]）
        """
        if returns is None:
            if self.returns_provider is not None:
                returns = self.returns_provider()
            else:
                return Decimal("1.0")

        # 取最近 lookback_days 的数据
        recent_returns = list(returns)[-self.lookback_days:]

        if len(recent_returns) < 2:
            # 数据不足，不调整
            return Decimal("1.0")

        realized_vol = compute_realized_volatility(recent_returns)
        self._last_realized_vol = realized_vol

        if realized_vol <= Decimal("0"):
            # 波动率为零（如全部持有货基），不限制
            leverage = self.max_leverage
        else:
            leverage = (self.target_vol / realized_vol).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            )

        # Clip to bounds
        leverage = max(self.min_leverage, min(self.max_leverage, leverage))
        self._last_leverage = leverage

        return leverage

    def apply(
        self,
        orders: list[OrderIntent],
        portfolio: Portfolio,
    ) -> list[OrderIntent]:
        """应用波动率目标规则。

        当杠杆因子 < 1 时，按比例缩减申购金额。
        当杠杆因子 >= 1 时，不限制（但也不主动加仓）。
        赎回订单不受影响。
        """
        if not orders:
            return orders

        leverage = self.compute_leverage()

        if leverage >= Decimal("1.0"):
            # 波动率在目标以下，不限制
            logger.debug(
                "VolTargetRule: 杠杆因子 %.4f >= 1.0，不限制 "
                "(实现波动率 %.4f, 目标 %.4f)",
                float(leverage), float(self._last_realized_vol),
                float(self.target_vol),
            )
            return orders

        # 波动率超过目标，按杠杆因子缩减申购金额
        logger.info(
            "VolTargetRule: 杠杆因子 %.4f < 1.0，缩减申购金额 "
            "(实现波动率 %.4f, 目标 %.4f)",
            float(leverage), float(self._last_realized_vol),
            float(self.target_vol),
        )

        result: list[OrderIntent] = []
        for order in orders:
            if order.direction == "redeem":
                result.append(order)
                continue

            if order.amount is None or order.amount <= Decimal("0"):
                result.append(order)
                continue

            # 按杠杆因子缩减金额
            adjusted_amount = (order.amount * leverage).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

            if adjusted_amount <= Decimal("0"):
                logger.info(
                    "VolTargetRule: 过滤订单 %s (缩减后金额为 0)",
                    order.fund_code,
                )
                continue

            if adjusted_amount < order.amount:
                logger.info(
                    "VolTargetRule: 缩减订单 %s 金额 %s -> %s (杠杆 %.4f)",
                    order.fund_code, order.amount, adjusted_amount,
                    float(leverage),
                )

            result.append(
                OrderIntent(
                    fund_code=order.fund_code,
                    direction=order.direction,
                    amount=adjusted_amount,
                    shares=order.shares,
                    target_weight=order.target_weight,
                )
            )

        return result
