"""结算规则模块。

根据基金类型定义 T+N 结算规则，提供确认日期和资金到账日期的计算。
结算日期计算跳过非交易日（使用交易日历）。

用法示例::

    from datetime import date
    from app.domain.backtest.settlement import (
        get_settlement_rule,
        get_confirm_date,
        get_cash_arrival_date,
    )

    rule = get_settlement_rule("stock")
    # rule.t_plus_confirm == 1, rule.t_plus_cash == 4

    confirm = get_confirm_date(date(2024, 1, 2), "stock")
    # 返回 T+1 交易日

    cash_date = get_cash_arrival_date(confirm, "stock")
    # 返回确认日后 T+4 交易日
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.domain.backtest.calendar import next_trading_day


@dataclass(frozen=True)
class SettlementRule:
    """结算规则。

    Attributes:
        t_plus_confirm: 申购确认所需交易日数（T+N），即下单后第 N 个交易日确认份额。
        t_plus_cash: 赎回资金到账所需交易日数（T+N），即确认后第 N 个交易日资金到账。
    """

    t_plus_confirm: int
    t_plus_cash: int

    def __post_init__(self) -> None:
        if self.t_plus_confirm < 0:
            raise ValueError(
                f"t_plus_confirm must be non-negative, got {self.t_plus_confirm}"
            )
        if self.t_plus_cash < 0:
            raise ValueError(
                f"t_plus_cash must be non-negative, got {self.t_plus_cash}"
            )


# ---------------------------------------------------------------------------
# 内置结算规则表（按基金类型）
# ---------------------------------------------------------------------------

SETTLEMENT_RULES: dict[str, SettlementRule] = {
    "stock": SettlementRule(t_plus_confirm=1, t_plus_cash=4),
    "bond": SettlementRule(t_plus_confirm=1, t_plus_cash=3),
    "mixed": SettlementRule(t_plus_confirm=1, t_plus_cash=4),
    "money": SettlementRule(t_plus_confirm=1, t_plus_cash=1),
    "qdii": SettlementRule(t_plus_confirm=2, t_plus_cash=7),
    "index": SettlementRule(t_plus_confirm=1, t_plus_cash=4),
    "fof": SettlementRule(t_plus_confirm=2, t_plus_cash=7),
}


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------


def get_settlement_rule(fund_type: str) -> SettlementRule:
    """获取指定基金类型的结算规则。

    Args:
        fund_type: 基金类型，支持 stock/bond/mixed/money/qdii/index/fof。

    Returns:
        对应的结算规则。

    Raises:
        KeyError: 如果基金类型不在内置规则表中。
    """
    fund_type_lower = fund_type.lower()
    if fund_type_lower not in SETTLEMENT_RULES:
        raise KeyError(
            f"Unknown fund type: '{fund_type}'. "
            f"Supported types: {list(SETTLEMENT_RULES.keys())}"
        )
    return SETTLEMENT_RULES[fund_type_lower]


def get_confirm_date(order_date: date, fund_type: str) -> date:
    """计算申购/赎回确认日期。

    从下单日起，跳过 N 个交易日得到确认日期。
    例如 T+1 表示下单日后的第 1 个交易日确认。

    Args:
        order_date: 下单日期。
        fund_type: 基金类型。

    Returns:
        确认日期（交易日）。
    """
    rule = get_settlement_rule(fund_type)
    current = order_date
    for _ in range(rule.t_plus_confirm):
        current = next_trading_day(current)
    return current


def get_cash_arrival_date(confirm_date: date, fund_type: str) -> date:
    """计算赎回资金到账日期。

    从确认日起，跳过 N 个交易日得到资金到账日期。

    Args:
        confirm_date: 确认日期。
        fund_type: 基金类型。

    Returns:
        资金到账日期（交易日）。
    """
    rule = get_settlement_rule(fund_type)
    current = confirm_date
    for _ in range(rule.t_plus_cash):
        current = next_trading_day(current)
    return current
