"""大额赎回限制模拟模块。

模拟中国公募基金的大额赎回规则：
- 单日单只基金赎回超过基金净资产 10% 时，基金公司可延期确认
- 延期部分按 T+2 或更晚确认
- 巨额赎回时可能触发按比例确认

规则来源：
《公开募集证券投资基金运作管理办法》第二十一条：
开放式基金单个开放日净赎回申请超过基金总份额的 10% 时，
为巨额赎回。基金管理人可以延期办理。

设计要点：
- 在引擎确认赎回订单时调用此模块
- 如果赎回份额超过基金总份额的 10%，拆分为当日确认部分和延期部分
- 延期部分生成新的 pending order，确认日期推后

需求: 优化计划 6.2
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

logger = logging.getLogger(__name__)

# 巨额赎回阈值：单日赎回超过基金总份额的 10%
LARGE_REDEMPTION_THRESHOLD = Decimal("0.10")

# 延期确认天数（额外的交易日）
DELAY_TRADING_DAYS = 2


@dataclass
class RedemptionSplitResult:
    """大额赎回拆分结果。

    Attributes:
        is_large_redemption: 是否触发大额赎回
        immediate_shares: 当日可确认的份额
        delayed_shares: 需要延期确认的份额
        delay_days: 延期的交易日数
        message: 说明信息
    """

    is_large_redemption: bool
    immediate_shares: Decimal
    delayed_shares: Decimal
    delay_days: int = 0
    message: str = ""


def check_large_redemption(
    redeem_shares: Decimal,
    fund_total_shares: Decimal | None,
    threshold: Decimal = LARGE_REDEMPTION_THRESHOLD,
) -> RedemptionSplitResult:
    """检查是否触发大额赎回限制。

    Args:
        redeem_shares: 赎回份额
        fund_total_shares: 基金总份额（如果未知则不限制）
        threshold: 巨额赎回阈值（默认 10%）

    Returns:
        RedemptionSplitResult 拆分结果
    """
    # 如果不知道基金总份额，不做限制
    if fund_total_shares is None or fund_total_shares <= Decimal("0"):
        return RedemptionSplitResult(
            is_large_redemption=False,
            immediate_shares=redeem_shares,
            delayed_shares=Decimal("0"),
        )

    # 计算赎回比例
    redemption_ratio = redeem_shares / fund_total_shares

    if redemption_ratio <= threshold:
        # 未触发大额赎回
        return RedemptionSplitResult(
            is_large_redemption=False,
            immediate_shares=redeem_shares,
            delayed_shares=Decimal("0"),
        )

    # 触发大额赎回：按比例确认
    # 当日最多确认 threshold 比例的份额
    max_immediate = (fund_total_shares * threshold).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    immediate_shares = min(redeem_shares, max_immediate)
    delayed_shares = redeem_shares - immediate_shares

    logger.info(
        "大额赎回触发: 赎回 %s 份（占总份额 %.2f%%），"
        "当日确认 %s 份，延期 %s 份",
        redeem_shares,
        float(redemption_ratio * 100),
        immediate_shares,
        delayed_shares,
    )

    return RedemptionSplitResult(
        is_large_redemption=True,
        immediate_shares=immediate_shares,
        delayed_shares=delayed_shares,
        delay_days=DELAY_TRADING_DAYS,
        message=(
            f"触发大额赎回（赎回比例 {float(redemption_ratio * 100):.1f}% > "
            f"{float(threshold * 100):.0f}%），"
            f"当日确认 {immediate_shares} 份，"
            f"延期 {delayed_shares} 份（+{DELAY_TRADING_DAYS} 交易日）"
        ),
    )
