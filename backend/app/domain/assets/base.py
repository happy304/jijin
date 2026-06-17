"""Asset 基类定义。

提供资产类型的统一抽象，不同资产类型（基金、股票、债券、ETF）
在结算规则、费率计算、交易单位等方面存在差异，通过继承 Asset
基类实现多态。

设计要点：
- 使用 ABC 强制子类实现核心方法
- 每种资产类型定义自己的结算天数、交易单位、费率逻辑
- 策略和回测引擎通过 asset_type 字符串查找对应的 Asset 实例
- 开发者扩展新资产类型只需继承此基类并注册

需求: 10.7（扩展平台 - 新增资产类型）
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, timedelta
from decimal import Decimal
from typing import Any


class Asset(ABC):
    """资产类型抽象基类。

    所有资产类型必须继承此类并实现抽象方法。
    通过 AssetRegistry 注册后，可在策略和回测引擎中使用。

    Attributes:
        asset_type: 资产类型标识（如 "fund", "stock", "bond"）
        settlement_days: 结算天数（T+N 中的 N）
        trading_unit: 最小交易单位（基金=1份，股票=100股）
        price_tick: 最小价格变动单位
        currency: 计价货币

    Example::

        class MyAsset(Asset):
            asset_type = "my_asset"
            settlement_days = 2

            def calc_fee(self, amount, direction):
                return amount * Decimal("0.001")
    """

    asset_type: str = ""
    settlement_days: int = 1
    trading_unit: int = 1
    price_tick: Decimal = Decimal("0.001")
    currency: str = "CNY"

    @abstractmethod
    def calc_fee(self, amount: Decimal, direction: str) -> Decimal:
        """计算交易费用。

        Args:
            amount: 交易金额（申购为金额，赎回为份额×净值）
            direction: 交易方向（"subscribe"/"redeem" 或 "buy"/"sell"）

        Returns:
            交易费用金额
        """
        ...

    def calc_stamp_tax(self, amount: Decimal, direction: str) -> Decimal:
        """计算印花税（默认无印花税，股票子类覆盖）。

        Args:
            amount: 交易金额
            direction: 交易方向

        Returns:
            印花税金额，默认返回 0
        """
        return Decimal("0")

    def calc_total_cost(self, amount: Decimal, direction: str) -> Decimal:
        """计算总交易成本（手续费 + 印花税）。

        Args:
            amount: 交易金额
            direction: 交易方向

        Returns:
            总交易成本
        """
        return self.calc_fee(amount, direction) + self.calc_stamp_tax(amount, direction)

    def calc_settlement_date(self, trade_date: date) -> date:
        """计算结算日期（T+N）。

        简化实现：跳过周末，不考虑节假日。
        生产环境应接入交易日历。

        Args:
            trade_date: 交易日期

        Returns:
            结算日期
        """
        result = trade_date
        days_added = 0
        while days_added < self.settlement_days:
            result += timedelta(days=1)
            # 跳过周末（周六=5，周日=6）
            if result.weekday() < 5:
                days_added += 1
        return result

    def validate_order(
        self,
        amount: Decimal | None = None,
        shares: Decimal | None = None,
        nav: Decimal | None = None,
    ) -> tuple[bool, str]:
        """校验订单合法性。

        检查交易金额/份额是否满足最小交易单位等约束。

        Args:
            amount: 交易金额（申购时）
            shares: 交易份额（赎回时）
            nav: 当前净值

        Returns:
            (is_valid, error_message) 元组
        """
        if amount is not None and amount <= Decimal("0"):
            return False, "交易金额必须大于 0"

        if shares is not None:
            if shares <= Decimal("0"):
                return False, "交易份额必须大于 0"
            if self.trading_unit > 1:
                # 检查是否满足最小交易单位
                unit = Decimal(str(self.trading_unit))
                if shares % unit != Decimal("0"):
                    return False, f"交易份额必须是 {self.trading_unit} 的整数倍"

        return True, ""

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"asset_type={self.asset_type!r}, "
            f"settlement_days={self.settlement_days}, "
            f"trading_unit={self.trading_unit})"
        )
