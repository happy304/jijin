"""内置资产类型实现。

提供平台默认支持的 5 种资产类型：
- FundAsset: 开放式基金
- MoneyFundAsset: 货币基金
- ETFAsset: 交易所 ETF
- StockAsset: 股票
- BondAsset: 债券

每种资产类型定义了各自的结算规则和费率计算逻辑。
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.assets.base import Asset


class FundAsset(Asset):
    """开放式基金资产类型。

    特点：
    - T+1 结算（申购确认需 T+1）
    - 最小交易单位 1 份
    - 申购费率：外扣法，通常 0.15%（打折后）
    - 赎回费率：按持有天数阶梯收费
    """

    asset_type = "fund"
    settlement_days = 1
    trading_unit = 1
    price_tick = Decimal("0.001")

    def __init__(
        self,
        subscribe_rate: Decimal = Decimal("0.0015"),
        redeem_rate: Decimal = Decimal("0.005"),
    ) -> None:
        """初始化基金资产。

        Args:
            subscribe_rate: 申购费率（默认 0.15%，已打折）
            redeem_rate: 赎回费率（默认 0.5%，持有 < 7 天）
        """
        self._subscribe_rate = subscribe_rate
        self._redeem_rate = redeem_rate

    def calc_fee(self, amount: Decimal, direction: str) -> Decimal:
        """计算基金交易费用。

        申购：外扣法，费用 = 金额 × 费率 / (1 + 费率)
        赎回：内扣法，费用 = 金额 × 费率

        Args:
            amount: 交易金额
            direction: "subscribe" 或 "redeem"

        Returns:
            交易费用
        """
        if direction == "subscribe":
            # 外扣法
            fee = (amount * self._subscribe_rate / (Decimal("1") + self._subscribe_rate))
            return fee.quantize(Decimal("0.01"))
        else:
            # 赎回费
            fee = amount * self._redeem_rate
            return fee.quantize(Decimal("0.01"))


class MoneyFundAsset(Asset):
    """货币基金资产类型。

    特点：
    - T+0 结算（快速赎回当日到账）
    - 无申购/赎回费
    - 最小交易单位 1 份（通常 1 元起）
    """

    asset_type = "money_fund"
    settlement_days = 0
    trading_unit = 1
    price_tick = Decimal("0.0001")

    def calc_fee(self, amount: Decimal, direction: str) -> Decimal:
        """货币基金无交易费用。"""
        return Decimal("0")


class ETFAsset(Asset):
    """交易所 ETF 资产类型。

    特点：
    - T+1 结算（场内交易 T+1 交收）
    - 最小交易单位 100 份
    - 佣金：万二点五，最低 5 元（部分券商免最低）
    """

    asset_type = "etf"
    settlement_days = 1
    trading_unit = 100
    price_tick = Decimal("0.001")

    def __init__(
        self,
        commission_rate: Decimal = Decimal("0.00025"),
        min_commission: Decimal = Decimal("0"),
    ) -> None:
        """初始化 ETF 资产。

        Args:
            commission_rate: 佣金费率（默认万二点五）
            min_commission: 最低佣金（默认 0，多数券商 ETF 免最低）
        """
        self._commission_rate = commission_rate
        self._min_commission = min_commission

    def calc_fee(self, amount: Decimal, direction: str) -> Decimal:
        """计算 ETF 交易佣金。

        Args:
            amount: 交易金额
            direction: "buy" 或 "sell"

        Returns:
            佣金金额
        """
        fee = amount * self._commission_rate
        return max(fee, self._min_commission).quantize(Decimal("0.01"))


class StockAsset(Asset):
    """股票资产类型。

    特点：
    - T+1 结算
    - 最小交易单位 100 股（1 手）
    - 佣金：万三，最低 5 元
    - 印花税：卖出千一（2023 年 8 月起降为万五，此处保留千一作为保守估计）
    """

    asset_type = "stock"
    settlement_days = 1
    trading_unit = 100
    price_tick = Decimal("0.01")

    def __init__(
        self,
        commission_rate: Decimal = Decimal("0.0003"),
        min_commission: Decimal = Decimal("5"),
        stamp_tax_rate: Decimal = Decimal("0.0005"),
    ) -> None:
        """初始化股票资产。

        Args:
            commission_rate: 佣金费率（默认万三）
            min_commission: 最低佣金（默认 5 元）
            stamp_tax_rate: 印花税率（默认万五，仅卖出收取）
        """
        self._commission_rate = commission_rate
        self._min_commission = min_commission
        self._stamp_tax_rate = stamp_tax_rate

    def calc_fee(self, amount: Decimal, direction: str) -> Decimal:
        """计算股票交易佣金。

        Args:
            amount: 交易金额
            direction: "buy" 或 "sell"

        Returns:
            佣金金额（不含印花税）
        """
        fee = amount * self._commission_rate
        return max(fee, self._min_commission).quantize(Decimal("0.01"))

    def calc_stamp_tax(self, amount: Decimal, direction: str) -> Decimal:
        """计算印花税（仅卖出收取）。

        Args:
            amount: 交易金额
            direction: "buy" 或 "sell"

        Returns:
            印花税金额
        """
        if direction in ("sell", "redeem"):
            return (amount * self._stamp_tax_rate).quantize(Decimal("0.01"))
        return Decimal("0")


class BondAsset(Asset):
    """债券资产类型。

    特点：
    - T+0 结算（当日买入当日可卖）
    - 最小交易单位 10 张
    - 佣金：万二，最低 1 元（部分券商免最低）
    - 无印花税
    """

    asset_type = "bond"
    settlement_days = 0
    trading_unit = 10
    price_tick = Decimal("0.001")

    def __init__(
        self,
        commission_rate: Decimal = Decimal("0.0002"),
        min_commission: Decimal = Decimal("1"),
    ) -> None:
        """初始化债券资产。

        Args:
            commission_rate: 佣金费率（默认万二）
            min_commission: 最低佣金（默认 1 元）
        """
        self._commission_rate = commission_rate
        self._min_commission = min_commission

    def calc_fee(self, amount: Decimal, direction: str) -> Decimal:
        """计算债券交易佣金。

        Args:
            amount: 交易金额
            direction: "buy" 或 "sell"

        Returns:
            佣金金额
        """
        fee = amount * self._commission_rate
        return max(fee, self._min_commission).quantize(Decimal("0.01"))
