"""限购与状态检查模块。

实现基金申购资格校验，包括：
- 基金暂停申购状态检查
- 限购额度检查
- 赎回订单始终放行

需求: 4.8
"""

from __future__ import annotations

from decimal import Decimal


def check_purchase_eligibility(
    fund_code: str,
    direction: str,
    amount: Decimal | None,
    is_purchasable: bool,
    purchase_limit: Decimal | None,
) -> tuple[bool, str | None]:
    """检查订单是否满足限购与状态要求。

    Args:
        fund_code: 基金代码
        direction: 交易方向，"subscribe" 或 "redeem"
        amount: 申购金额（赎回时可为 None）
        is_purchasable: 基金是否可申购
        purchase_limit: 限购额度，None 表示无限制

    Returns:
        (eligible, reason) 元组：
        - eligible=True, reason=None 表示允许
        - eligible=False, reason=拒绝原因 表示拒绝
    """
    # 赎回订单始终放行
    if direction == "redeem":
        return (True, None)

    # 申购订单：检查基金是否暂停申购
    if not is_purchasable:
        return (False, "基金暂停申购")

    # 申购订单：检查限购额度
    if purchase_limit is not None and amount is not None:
        if amount > purchase_limit:
            return (False, "超过限购额度")

    return (True, None)
