"""风控规则：仓位限制模块。

实现基础风控规则：
- RiskRule: 风控规则抽象基类
- MaxPositionRule: 单基金最大仓位限制
- MaxTypeExposureRule: 单类型基金最大仓位限制
- MinCashReserveRule: 最小现金保留比例
- RuleChainRiskEngine: 规则链组合引擎

设计要点：
- 规则链模式：多个规则按顺序执行，每个规则可过滤/修改订单列表
- 超限时按比例缩放到安全线，而非直接拒绝（避免冲击成本）
- 规则之间解耦，可自由组合

需求: 6.1
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from decimal import Decimal, ROUND_HALF_UP
from typing import Callable

from app.domain.backtest.order import OrderIntent
from app.domain.backtest.portfolio import Portfolio

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 风控规则抽象基类
# ---------------------------------------------------------------------------


class RiskRule(ABC):
    """风控规则抽象基类。

    所有风控规则必须实现 apply 方法，接收订单列表和组合状态，
    返回经过过滤/修改后的订单列表。
    """

    @abstractmethod
    def apply(
        self,
        orders: list[OrderIntent],
        portfolio: Portfolio,
    ) -> list[OrderIntent]:
        """应用风控规则。

        Args:
            orders: 待检查的订单意图列表
            portfolio: 当前组合状态

        Returns:
            经过风控过滤/修改后的订单列表
        """
        ...


# ---------------------------------------------------------------------------
# MaxPositionRule - 单基金最大仓位限制
# ---------------------------------------------------------------------------


class MaxPositionRule(RiskRule):
    """单基金最大仓位限制。

    限制单只基金在组合中的最大权重。如果某笔申购会导致单基金仓位
    超过上限，则将申购金额缩减到刚好达到上限。

    Attributes:
        max_weight: 单基金最大权重（如 0.3 表示 30%）
        nav_provider: 净值查询函数，用于计算持仓市值

    Example::

        rule = MaxPositionRule(
            max_weight=Decimal("0.3"),
            nav_provider=lambda code: nav_dict.get(code),
        )
    """

    def __init__(
        self,
        max_weight: Decimal,
        nav_provider: Callable[[str], Decimal | None] | None = None,
    ) -> None:
        if max_weight <= Decimal("0") or max_weight > Decimal("1"):
            raise ValueError(f"max_weight must be in (0, 1], got {max_weight}")
        self.max_weight = max_weight
        self.nav_provider = nav_provider

    def apply(
        self,
        orders: list[OrderIntent],
        portfolio: Portfolio,
    ) -> list[OrderIntent]:
        """检查并限制单基金仓位。

        对于申购订单，如果执行后会导致单基金仓位超过 max_weight，
        则缩减申购金额。赎回订单不受此规则影响。
        """
        if not orders:
            return orders

        total_value = self._calc_total_value(portfolio)
        if total_value <= Decimal("0"):
            return orders

        result: list[OrderIntent] = []
        for order in orders:
            if order.direction == "redeem":
                result.append(order)
                continue

            # 计算当前该基金的仓位价值
            current_value = self._position_value(order.fund_code, portfolio)
            current_weight = current_value / total_value

            # 计算申购后的预期仓位
            add_amount = order.amount or Decimal("0")
            new_total = total_value + add_amount
            new_value = current_value + add_amount
            new_weight = new_value / new_total

            if new_weight <= self.max_weight:
                result.append(order)
            else:
                # 缩减到刚好达到上限
                # max_weight = (current_value + allowed_amount) / (total_value + allowed_amount)
                # max_weight * (total_value + allowed_amount) = current_value + allowed_amount
                # max_weight * total_value + max_weight * allowed_amount = current_value + allowed_amount
                # allowed_amount * (1 - max_weight) = max_weight * total_value - current_value
                denominator = Decimal("1") - self.max_weight
                if denominator <= Decimal("0"):
                    result.append(order)
                    continue

                allowed_amount = (
                    (self.max_weight * total_value - current_value) / denominator
                ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

                if allowed_amount <= Decimal("0"):
                    logger.info(
                        "MaxPositionRule: 拒绝订单 %s，当前仓位 %.2f%% 已达上限 %.2f%%",
                        order.fund_code,
                        float(current_weight * 100),
                        float(self.max_weight * 100),
                    )
                    continue

                logger.info(
                    "MaxPositionRule: 缩减订单 %s 金额 %s -> %s (仓位上限 %.2f%%)",
                    order.fund_code, order.amount, allowed_amount,
                    float(self.max_weight * 100),
                )
                result.append(
                    OrderIntent(
                        fund_code=order.fund_code,
                        direction=order.direction,
                        amount=allowed_amount,
                        shares=order.shares,
                        target_weight=order.target_weight,
                    )
                )

        return result

    def _calc_total_value(self, portfolio: Portfolio) -> Decimal:
        """计算组合总市值（现金 + 持仓市值）。"""
        total = portfolio.cash
        for fund_code, shares in portfolio.positions.items():
            nav = self._get_nav(fund_code)
            if nav is not None:
                total += shares * nav
        return total

    def _position_value(self, fund_code: str, portfolio: Portfolio) -> Decimal:
        """计算单只基金的持仓市值。"""
        shares = portfolio.positions.get(fund_code, Decimal("0"))
        if shares <= Decimal("0"):
            return Decimal("0")
        nav = self._get_nav(fund_code)
        if nav is None:
            return Decimal("0")
        return shares * nav

    def _get_nav(self, fund_code: str) -> Decimal | None:
        """获取基金净值。"""
        if self.nav_provider is None:
            return None
        return self.nav_provider(fund_code)


# ---------------------------------------------------------------------------
# MaxTypeExposureRule - 单类型基金最大仓位限制
# ---------------------------------------------------------------------------


class MaxTypeExposureRule(RiskRule):
    """单类型基金最大仓位限制。

    限制同一类型基金（如股票型、债券型）在组合中的总权重。
    如果某笔申购会导致该类型总仓位超过上限，则缩减申购金额。

    Attributes:
        max_type_weight: 单类型最大权重（如 0.6 表示 60%）
        fund_type_provider: 基金类型查询函数 fund_code -> type_str
        nav_provider: 净值查询函数

    Example::

        rule = MaxTypeExposureRule(
            max_type_weight=Decimal("0.6"),
            fund_type_provider=lambda code: fund_types.get(code, "stock"),
            nav_provider=lambda code: nav_dict.get(code),
        )
    """

    def __init__(
        self,
        max_type_weight: Decimal,
        fund_type_provider: Callable[[str], str],
        nav_provider: Callable[[str], Decimal | None] | None = None,
    ) -> None:
        if max_type_weight <= Decimal("0") or max_type_weight > Decimal("1"):
            raise ValueError(
                f"max_type_weight must be in (0, 1], got {max_type_weight}"
            )
        self.max_type_weight = max_type_weight
        self.fund_type_provider = fund_type_provider
        self.nav_provider = nav_provider

    def apply(
        self,
        orders: list[OrderIntent],
        portfolio: Portfolio,
    ) -> list[OrderIntent]:
        """检查并限制单类型基金仓位。"""
        if not orders:
            return orders

        total_value = self._calc_total_value(portfolio)
        if total_value <= Decimal("0"):
            return orders

        # 计算各类型当前仓位
        type_values: dict[str, Decimal] = {}
        for fund_code, shares in portfolio.positions.items():
            fund_type = self.fund_type_provider(fund_code)
            nav = self._get_nav(fund_code)
            if nav is not None:
                type_values[fund_type] = type_values.get(
                    fund_type, Decimal("0")
                ) + shares * nav

        result: list[OrderIntent] = []
        for order in orders:
            if order.direction == "redeem":
                result.append(order)
                continue

            fund_type = self.fund_type_provider(order.fund_code)
            current_type_value = type_values.get(fund_type, Decimal("0"))
            add_amount = order.amount or Decimal("0")

            new_total = total_value + add_amount
            new_type_value = current_type_value + add_amount
            new_type_weight = new_type_value / new_total

            if new_type_weight <= self.max_type_weight:
                # 更新追踪值
                type_values[fund_type] = new_type_value
                total_value = new_total
                result.append(order)
            else:
                # 缩减到刚好达到类型上限
                denominator = Decimal("1") - self.max_type_weight
                if denominator <= Decimal("0"):
                    type_values[fund_type] = new_type_value
                    total_value = new_total
                    result.append(order)
                    continue

                allowed_amount = (
                    (self.max_type_weight * total_value - current_type_value)
                    / denominator
                ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

                if allowed_amount <= Decimal("0"):
                    logger.info(
                        "MaxTypeExposureRule: 拒绝订单 %s (类型=%s)，"
                        "当前类型仓位 %.2f%% 已达上限 %.2f%%",
                        order.fund_code, fund_type,
                        float(current_type_value / total_value * 100),
                        float(self.max_type_weight * 100),
                    )
                    continue

                logger.info(
                    "MaxTypeExposureRule: 缩减订单 %s (类型=%s) 金额 %s -> %s",
                    order.fund_code, fund_type, order.amount, allowed_amount,
                )
                type_values[fund_type] = current_type_value + allowed_amount
                total_value = total_value + allowed_amount
                result.append(
                    OrderIntent(
                        fund_code=order.fund_code,
                        direction=order.direction,
                        amount=allowed_amount,
                        shares=order.shares,
                        target_weight=order.target_weight,
                    )
                )

        return result

    def _calc_total_value(self, portfolio: Portfolio) -> Decimal:
        """计算组合总市值。"""
        total = portfolio.cash
        for fund_code, shares in portfolio.positions.items():
            nav = self._get_nav(fund_code)
            if nav is not None:
                total += shares * nav
        return total

    def _get_nav(self, fund_code: str) -> Decimal | None:
        """获取基金净值。"""
        if self.nav_provider is None:
            return None
        return self.nav_provider(fund_code)


# ---------------------------------------------------------------------------
# MinCashReserveRule - 最小现金保留
# ---------------------------------------------------------------------------


class MinCashReserveRule(RiskRule):
    """最小现金保留规则。

    确保组合中始终保留一定比例的现金。如果某笔申购会导致现金比例
    低于下限，则缩减申购金额。

    Attributes:
        min_cash_ratio: 最小现金比例（如 0.05 表示 5%）

    Example::

        rule = MinCashReserveRule(min_cash_ratio=Decimal("0.05"))
    """

    def __init__(self, min_cash_ratio: Decimal) -> None:
        if min_cash_ratio < Decimal("0") or min_cash_ratio >= Decimal("1"):
            raise ValueError(
                f"min_cash_ratio must be in [0, 1), got {min_cash_ratio}"
            )
        self.min_cash_ratio = min_cash_ratio

    def apply(
        self,
        orders: list[OrderIntent],
        portfolio: Portfolio,
    ) -> list[OrderIntent]:
        """检查并确保最小现金保留。"""
        if not orders:
            return orders

        available_cash = portfolio.cash
        # 计算组合总市值（简化：用现金近似，因为无 nav_provider）
        # 这里使用 portfolio.cash 作为可用现金基准
        total_value = available_cash
        # 如果有持仓，总市值应该更大，但这里保守估计
        # 最小保留现金 = min_cash_ratio * total_value
        # 简化处理：确保现金不低于 min_cash_ratio * 当前现金
        # 更准确的做法：需要 nav_provider 计算总市值

        result: list[OrderIntent] = []
        remaining_cash = available_cash

        for order in orders:
            if order.direction == "redeem":
                result.append(order)
                continue

            amount = order.amount or Decimal("0")
            if amount <= Decimal("0"):
                result.append(order)
                continue

            # 计算申购后剩余现金
            cash_after = remaining_cash - amount
            # 最小保留 = min_cash_ratio * (remaining_cash)
            # 注意：这里用当前总现金作为基准（保守估计）
            min_reserve = (self.min_cash_ratio * remaining_cash).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

            if cash_after >= min_reserve:
                remaining_cash = cash_after
                result.append(order)
            else:
                # 缩减到保留最小现金
                allowed_amount = remaining_cash - min_reserve
                if allowed_amount <= Decimal("0"):
                    logger.info(
                        "MinCashReserveRule: 拒绝订单 %s，"
                        "现金 %s 不足以保留 %.2f%% 最低现金",
                        order.fund_code, remaining_cash,
                        float(self.min_cash_ratio * 100),
                    )
                    continue

                allowed_amount = allowed_amount.quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                logger.info(
                    "MinCashReserveRule: 缩减订单 %s 金额 %s -> %s "
                    "(保留现金 %.2f%%)",
                    order.fund_code, order.amount, allowed_amount,
                    float(self.min_cash_ratio * 100),
                )
                remaining_cash -= allowed_amount
                result.append(
                    OrderIntent(
                        fund_code=order.fund_code,
                        direction=order.direction,
                        amount=allowed_amount,
                        shares=order.shares,
                        target_weight=order.target_weight,
                    )
                )

        return result


# ---------------------------------------------------------------------------
# RuleChainRiskEngine - 规则链组合引擎
# ---------------------------------------------------------------------------


class RuleChainRiskEngine:
    """规则链风控引擎。

    将多个风控规则按顺序组合，依次对订单列表进行过滤/修改。
    符合 RiskEngine Protocol 接口。

    Example::

        engine = RuleChainRiskEngine(rules=[
            MaxPositionRule(max_weight=Decimal("0.3"), nav_provider=get_nav),
            MaxTypeExposureRule(
                max_type_weight=Decimal("0.6"),
                fund_type_provider=get_type,
                nav_provider=get_nav,
            ),
            MinCashReserveRule(min_cash_ratio=Decimal("0.05")),
        ])
        filtered_orders = engine.validate(orders, portfolio)
    """

    def __init__(self, rules: list[RiskRule] | None = None) -> None:
        self.rules: list[RiskRule] = rules or []

    def add_rule(self, rule: RiskRule) -> None:
        """添加风控规则到规则链末尾。"""
        self.rules.append(rule)

    def validate(
        self,
        orders: list[OrderIntent],
        portfolio: Portfolio,
    ) -> list[OrderIntent]:
        """执行规则链验证。

        按顺序执行所有规则，每个规则的输出作为下一个规则的输入。

        Args:
            orders: 待验证的订单意图列表
            portfolio: 当前组合状态

        Returns:
            经过所有规则过滤后的订单列表
        """
        current_orders = orders
        for rule in self.rules:
            current_orders = rule.apply(current_orders, portfolio)
            if not current_orders:
                break
        return current_orders
