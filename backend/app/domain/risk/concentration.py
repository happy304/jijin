"""风控规则：持仓集中度限制模块。

实现 ConcentrationRule：
- 使用 HHI（赫芬达尔-赫希曼指数）衡量持仓集中度
- 当 HHI 超过阈值时，拒绝会进一步增加集中度的申购订单

HHI 计算：
    HHI = Σ(w_i²)
    其中 w_i 为第 i 只基金的持仓权重

HHI 取值范围：
- 1/N（完全分散，N 只基金等权）到 1.0（完全集中于一只基金）
- HHI > 0.25 通常视为高度集中

需求: 优化计划 6.4
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Callable

from app.domain.backtest.order import OrderIntent
from app.domain.backtest.portfolio import Portfolio
from app.domain.risk.limits import RiskRule

logger = logging.getLogger(__name__)


def compute_hhi(weights: dict[str, float]) -> float:
    """计算 HHI（赫芬达尔-赫希曼指数）。

    Args:
        weights: 持仓权重字典 {fund_code: weight}

    Returns:
        HHI 值（0~1），空持仓返回 0
    """
    if not weights:
        return 0.0

    total = sum(weights.values())
    if total <= 0:
        return 0.0

    # 归一化权重
    normalized = [w / total for w in weights.values() if w > 0]
    return sum(w ** 2 for w in normalized)


class ConcentrationRule(RiskRule):
    """持仓集中度限制规则。

    当组合 HHI 超过阈值时，拒绝会进一步增加集中度的申购订单。

    Attributes:
        max_hhi: 最大允许 HHI（默认 0.35，约等于 3 只基金等权）
        nav_provider: 净值查询函数

    Example::

        rule = ConcentrationRule(
            max_hhi=Decimal("0.35"),
            nav_provider=lambda code: nav_dict.get(code),
        )
    """

    def __init__(
        self,
        max_hhi: Decimal = Decimal("0.35"),
        nav_provider: Callable[[str], Decimal | None] | None = None,
    ) -> None:
        if max_hhi <= Decimal("0") or max_hhi > Decimal("1"):
            raise ValueError(f"max_hhi must be in (0, 1], got {max_hhi}")

        self.max_hhi = max_hhi
        self.nav_provider = nav_provider

    def apply(
        self,
        orders: list[OrderIntent],
        portfolio: Portfolio,
    ) -> list[OrderIntent]:
        """应用集中度限制规则。

        计算当前 HHI，如果已超限则拒绝申购订单。
        """
        if not orders:
            return orders

        # 计算当前持仓权重
        current_weights = self._compute_weights(portfolio)
        current_hhi = compute_hhi(current_weights)

        if current_hhi <= float(self.max_hhi):
            return orders

        # HHI 超限：拒绝申购，允许赎回（赎回会降低集中度）
        result: list[OrderIntent] = []
        blocked = 0

        for order in orders:
            if order.direction == "redeem":
                result.append(order)
            else:
                blocked += 1

        if blocked > 0:
            logger.info(
                "ConcentrationRule: HHI %.4f 超过上限 %.4f，拒绝 %d 笔申购订单",
                current_hhi,
                float(self.max_hhi),
                blocked,
            )

        return result

    def _compute_weights(self, portfolio: Portfolio) -> dict[str, float]:
        """计算当前持仓权重。"""
        weights: dict[str, float] = {}
        total_value = float(portfolio.cash)

        for fund_code, shares in portfolio.positions.items():
            nav = self._get_nav(fund_code)
            if nav is not None and nav > Decimal("0"):
                value = float(shares * nav)
                weights[fund_code] = value
                total_value += value

        if total_value <= 0:
            return {}

        # 归一化为权重
        return {code: val / total_value for code, val in weights.items()}

    def _get_nav(self, fund_code: str) -> Decimal | None:
        """获取基金净值。"""
        if self.nav_provider is None:
            return None
        return self.nav_provider(fund_code)
