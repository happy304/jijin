"""费率计算模块。

实现基金申购费（外扣法）和赎回费的阶梯费率计算。

申购费计算（外扣法）：
    fee = amount × rate / (1 + rate)
    net_amount = amount - fee

赎回费计算：
    fee = shares × nav × rate

费率阶梯匹配规则：
- 申购费：按申购金额匹配 min_amount <= amount < max_amount
- 赎回费：按持有天数匹配 min_holding_days <= holding_days < max_holding_days
- max_amount / max_holding_days 为 None 表示无上限

用法示例::

    from decimal import Decimal
    from app.domain.backtest.fees import (
        FeeTier,
        calc_subscribe_fee,
        calc_redeem_fee,
        find_matching_tier,
    )

    # 定义申购费阶梯
    subscribe_tiers = [
        FeeTier(min_amount=Decimal("0"), max_amount=Decimal("1000000"),
                rate=Decimal("0.015")),
        FeeTier(min_amount=Decimal("1000000"), max_amount=Decimal("5000000"),
                rate=Decimal("0.012")),
        FeeTier(min_amount=Decimal("5000000"), max_amount=None,
                rate=Decimal("0.001")),
    ]

    result = calc_subscribe_fee(Decimal("100000"), subscribe_tiers)
    # result.fee ≈ 1477.83, result.net_amount ≈ 98522.17

    # 定义赎回费阶梯
    redeem_tiers = [
        FeeTier(min_holding_days=0, max_holding_days=7,
                rate=Decimal("0.015")),
        FeeTier(min_holding_days=7, max_holding_days=365,
                rate=Decimal("0.005")),
        FeeTier(min_holding_days=365, max_holding_days=730,
                rate=Decimal("0.0025")),
        FeeTier(min_holding_days=730, max_holding_days=None,
                rate=Decimal("0")),
    ]

    result = calc_redeem_fee(
        shares=Decimal("10000"),
        nav=Decimal("1.5"),
        holding_days=30,
        fee_tiers=redeem_tiers,
    )
    # result.fee = 75.00, result.gross_amount = 15000.00

需求: 4.4, 4.5
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Sequence


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FeeTier:
    """费率阶梯定义。

    申购费使用 min_amount / max_amount 匹配金额区间。
    赎回费使用 min_holding_days / max_holding_days 匹配持有天数区间。

    区间规则：[min, max)，即左闭右开。
    max 为 None 表示无上限。

    Attributes:
        min_amount: 申购金额下限（含），默认 0。
        max_amount: 申购金额上限（不含），None 表示无上限。
        min_holding_days: 持有天数下限（含），默认 0。
        max_holding_days: 持有天数上限（不含），None 表示无上限。
        rate: 费率（小数形式，如 0.015 表示 1.5%）。
    """

    min_amount: Decimal = Decimal("0")
    max_amount: Decimal | None = None
    min_holding_days: int = 0
    max_holding_days: int | None = None
    rate: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.rate < Decimal("0"):
            raise ValueError(f"rate must be non-negative, got {self.rate}")
        if self.min_amount < Decimal("0"):
            raise ValueError(
                f"min_amount must be non-negative, got {self.min_amount}"
            )
        if self.max_amount is not None and self.max_amount <= self.min_amount:
            raise ValueError(
                f"max_amount ({self.max_amount}) must be greater than "
                f"min_amount ({self.min_amount})"
            )
        if self.min_holding_days < 0:
            raise ValueError(
                f"min_holding_days must be non-negative, got {self.min_holding_days}"
            )
        if (
            self.max_holding_days is not None
            and self.max_holding_days <= self.min_holding_days
        ):
            raise ValueError(
                f"max_holding_days ({self.max_holding_days}) must be greater than "
                f"min_holding_days ({self.min_holding_days})"
            )


@dataclass(frozen=True)
class SubscribeFeeResult:
    """申购费计算结果。

    Attributes:
        fee: 申购费用。
        net_amount: 净申购金额（实际用于购买份额的金额）。
        rate: 适用的费率。
    """

    fee: Decimal
    net_amount: Decimal
    rate: Decimal


@dataclass(frozen=True)
class RedeemFeeResult:
    """赎回费计算结果。

    Attributes:
        fee: 赎回费用。
        gross_amount: 赎回总金额（shares × nav）。
        net_amount: 净赎回金额（扣除费用后到手金额）。
        rate: 适用的费率。
    """

    fee: Decimal
    gross_amount: Decimal
    net_amount: Decimal
    rate: Decimal


# ---------------------------------------------------------------------------
# 阶梯匹配
# ---------------------------------------------------------------------------


def find_subscribe_tier(
    amount: Decimal,
    fee_tiers: Sequence[FeeTier],
) -> FeeTier | None:
    """根据申购金额查找匹配的费率阶梯。

    匹配规则：min_amount <= amount < max_amount（max_amount 为 None 表示无上限）。

    Args:
        amount: 申购金额（CNY）。
        fee_tiers: 费率阶梯列表。

    Returns:
        匹配的费率阶梯，未找到返回 None。
    """
    for tier in fee_tiers:
        if amount < tier.min_amount:
            continue
        if tier.max_amount is not None and amount >= tier.max_amount:
            continue
        return tier
    return None


def find_redeem_tier(
    holding_days: int,
    fee_tiers: Sequence[FeeTier],
) -> FeeTier | None:
    """根据持有天数查找匹配的费率阶梯。

    匹配规则：min_holding_days <= holding_days < max_holding_days
    （max_holding_days 为 None 表示无上限）。

    Args:
        holding_days: 持有天数。
        fee_tiers: 费率阶梯列表。

    Returns:
        匹配的费率阶梯，未找到返回 None。
    """
    for tier in fee_tiers:
        if holding_days < tier.min_holding_days:
            continue
        if tier.max_holding_days is not None and holding_days >= tier.max_holding_days:
            continue
        return tier
    return None


# ---------------------------------------------------------------------------
# 费率计算
# ---------------------------------------------------------------------------

# 精度：保留 2 位小数（金额），四舍五入
_PRECISION = Decimal("0.01")


def calc_subscribe_fee(
    amount: Decimal,
    fee_tiers: Sequence[FeeTier],
) -> SubscribeFeeResult:
    """计算申购费（外扣法）。

    外扣法公式：
        fee = amount × rate / (1 + rate)
        net_amount = amount - fee

    这意味着 net_amount × (1 + rate) = amount，即费用是在净金额基础上
    按费率计算的，而非在总金额上直接乘以费率。

    Args:
        amount: 申购金额（CNY），必须为正数。
        fee_tiers: 申购费率阶梯列表。

    Returns:
        SubscribeFeeResult 包含费用、净金额和适用费率。

    Raises:
        ValueError: 如果 amount <= 0 或未找到匹配的费率阶梯。
    """
    if amount <= Decimal("0"):
        raise ValueError(f"Subscribe amount must be positive, got {amount}")

    tier = find_subscribe_tier(amount, fee_tiers)
    if tier is None:
        raise ValueError(
            f"No matching subscribe fee tier found for amount={amount}"
        )

    rate = tier.rate

    if rate == Decimal("0"):
        return SubscribeFeeResult(
            fee=Decimal("0"),
            net_amount=amount,
            rate=rate,
        )

    # 外扣法：fee = amount * rate / (1 + rate)
    fee = (amount * rate / (Decimal("1") + rate)).quantize(
        _PRECISION, rounding=ROUND_HALF_UP
    )
    net_amount = amount - fee

    return SubscribeFeeResult(
        fee=fee,
        net_amount=net_amount,
        rate=rate,
    )


def calc_redeem_fee(
    shares: Decimal,
    nav: Decimal,
    holding_days: int,
    fee_tiers: Sequence[FeeTier],
) -> RedeemFeeResult:
    """计算赎回费。

    赎回费公式：
        gross_amount = shares × nav
        fee = gross_amount × rate
        net_amount = gross_amount - fee

    Args:
        shares: 赎回份额，必须为正数。
        nav: 当前净值，必须为正数。
        holding_days: 持有天数，必须非负。
        fee_tiers: 赎回费率阶梯列表。

    Returns:
        RedeemFeeResult 包含费用、总金额、净金额和适用费率。

    Raises:
        ValueError: 如果参数无效或未找到匹配的费率阶梯。
    """
    if shares <= Decimal("0"):
        raise ValueError(f"Redeem shares must be positive, got {shares}")
    if nav <= Decimal("0"):
        raise ValueError(f"NAV must be positive, got {nav}")
    if holding_days < 0:
        raise ValueError(f"holding_days must be non-negative, got {holding_days}")

    tier = find_redeem_tier(holding_days, fee_tiers)
    if tier is None:
        raise ValueError(
            f"No matching redeem fee tier found for holding_days={holding_days}"
        )

    rate = tier.rate
    gross_amount = (shares * nav).quantize(_PRECISION, rounding=ROUND_HALF_UP)

    if rate == Decimal("0"):
        return RedeemFeeResult(
            fee=Decimal("0"),
            gross_amount=gross_amount,
            net_amount=gross_amount,
            rate=rate,
        )

    fee = (gross_amount * rate).quantize(_PRECISION, rounding=ROUND_HALF_UP)
    net_amount = gross_amount - fee

    return RedeemFeeResult(
        fee=fee,
        gross_amount=gross_amount,
        net_amount=net_amount,
        rate=rate,
    )
