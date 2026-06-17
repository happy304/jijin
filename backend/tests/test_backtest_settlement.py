"""结算规则模块单元测试。

覆盖：
- SettlementRule 数据类创建与不可变性
- 内置规则表完整性（7 种基金类型）
- get_settlement_rule 查询（正常 + 异常）
- get_confirm_date 确认日期计算（含跨周末、跨节假日）
- get_cash_arrival_date 资金到账日期计算
- QDII/FOF 的 T+2 确认规则
"""

from __future__ import annotations

from datetime import date

import pytest

from app.domain.backtest.settlement import (
    SETTLEMENT_RULES,
    SettlementRule,
    get_cash_arrival_date,
    get_confirm_date,
    get_settlement_rule,
)


# ---------------------------------------------------------------------------
# SettlementRule 数据类测试
# ---------------------------------------------------------------------------


class TestSettlementRule:
    """SettlementRule 数据类测试。"""

    def test_create_rule(self) -> None:
        """正常创建结算规则。"""
        rule = SettlementRule(t_plus_confirm=1, t_plus_cash=4)
        assert rule.t_plus_confirm == 1
        assert rule.t_plus_cash == 4

    def test_frozen(self) -> None:
        """SettlementRule 应为不可变。"""
        rule = SettlementRule(t_plus_confirm=1, t_plus_cash=4)
        with pytest.raises(Exception):
            rule.t_plus_confirm = 2  # type: ignore[misc]

    def test_negative_confirm_raises(self) -> None:
        """t_plus_confirm 为负数应抛出 ValueError。"""
        with pytest.raises(ValueError, match="t_plus_confirm"):
            SettlementRule(t_plus_confirm=-1, t_plus_cash=4)

    def test_negative_cash_raises(self) -> None:
        """t_plus_cash 为负数应抛出 ValueError。"""
        with pytest.raises(ValueError, match="t_plus_cash"):
            SettlementRule(t_plus_confirm=1, t_plus_cash=-1)

    def test_zero_values_allowed(self) -> None:
        """T+0 应允许（如某些特殊场景）。"""
        rule = SettlementRule(t_plus_confirm=0, t_plus_cash=0)
        assert rule.t_plus_confirm == 0
        assert rule.t_plus_cash == 0


# ---------------------------------------------------------------------------
# 内置规则表测试
# ---------------------------------------------------------------------------


class TestSettlementRulesTable:
    """内置结算规则表测试。"""

    def test_all_fund_types_present(self) -> None:
        """规则表应包含所有 7 种基金类型。"""
        expected_types = {"stock", "bond", "mixed", "money", "qdii", "index", "fof"}
        assert set(SETTLEMENT_RULES.keys()) == expected_types

    def test_stock_rule(self) -> None:
        """股票型：T+1 确认，T+4 到账。"""
        rule = SETTLEMENT_RULES["stock"]
        assert rule.t_plus_confirm == 1
        assert rule.t_plus_cash == 4

    def test_bond_rule(self) -> None:
        """债券型：T+1 确认，T+3 到账。"""
        rule = SETTLEMENT_RULES["bond"]
        assert rule.t_plus_confirm == 1
        assert rule.t_plus_cash == 3

    def test_mixed_rule(self) -> None:
        """混合型：T+1 确认，T+4 到账。"""
        rule = SETTLEMENT_RULES["mixed"]
        assert rule.t_plus_confirm == 1
        assert rule.t_plus_cash == 4

    def test_money_rule(self) -> None:
        """货币型：T+1 确认，T+1 到账。"""
        rule = SETTLEMENT_RULES["money"]
        assert rule.t_plus_confirm == 1
        assert rule.t_plus_cash == 1

    def test_qdii_rule(self) -> None:
        """QDII：T+2 确认，T+7 到账。"""
        rule = SETTLEMENT_RULES["qdii"]
        assert rule.t_plus_confirm == 2
        assert rule.t_plus_cash == 7

    def test_index_rule(self) -> None:
        """指数型：T+1 确认，T+4 到账。"""
        rule = SETTLEMENT_RULES["index"]
        assert rule.t_plus_confirm == 1
        assert rule.t_plus_cash == 4

    def test_fof_rule(self) -> None:
        """FOF：T+2 确认，T+7 到账。"""
        rule = SETTLEMENT_RULES["fof"]
        assert rule.t_plus_confirm == 2
        assert rule.t_plus_cash == 7


# ---------------------------------------------------------------------------
# get_settlement_rule 测试
# ---------------------------------------------------------------------------


class TestGetSettlementRule:
    """get_settlement_rule 查询函数测试。"""

    def test_valid_type(self) -> None:
        """查询有效基金类型。"""
        rule = get_settlement_rule("stock")
        assert rule.t_plus_confirm == 1
        assert rule.t_plus_cash == 4

    def test_case_insensitive(self) -> None:
        """查询应不区分大小写。"""
        rule = get_settlement_rule("QDII")
        assert rule.t_plus_confirm == 2
        assert rule.t_plus_cash == 7

    def test_mixed_case(self) -> None:
        """混合大小写也应正常工作。"""
        rule = get_settlement_rule("Stock")
        assert rule.t_plus_confirm == 1

    def test_unknown_type_raises(self) -> None:
        """未知基金类型应抛出 KeyError。"""
        with pytest.raises(KeyError, match="Unknown fund type"):
            get_settlement_rule("unknown")

    def test_empty_string_raises(self) -> None:
        """空字符串应抛出 KeyError。"""
        with pytest.raises(KeyError, match="Unknown fund type"):
            get_settlement_rule("")


# ---------------------------------------------------------------------------
# get_confirm_date 测试
# ---------------------------------------------------------------------------


class TestGetConfirmDate:
    """get_confirm_date 确认日期计算测试。"""

    def test_stock_normal_day(self) -> None:
        """股票型 T+1：周二下单 → 周三确认。"""
        # 2024-01-02 是周二
        confirm = get_confirm_date(date(2024, 1, 2), "stock")
        assert confirm == date(2024, 1, 3)

    def test_stock_friday_order(self) -> None:
        """股票型 T+1：周五下单 → 下周一确认。"""
        # 2024-01-05 是周五
        confirm = get_confirm_date(date(2024, 1, 5), "stock")
        assert confirm == date(2024, 1, 8)  # 下周一

    def test_qdii_normal_day(self) -> None:
        """QDII T+2：周一下单 → 周三确认。"""
        # 2024-01-08 是周一
        confirm = get_confirm_date(date(2024, 1, 8), "qdii")
        assert confirm == date(2024, 1, 10)  # 周三

    def test_qdii_thursday_order(self) -> None:
        """QDII T+2：周四下单 → 下周一确认。"""
        # 2024-01-04 是周四
        confirm = get_confirm_date(date(2024, 1, 4), "qdii")
        # T+1 = 周五(1/5), T+2 = 下周一(1/8)
        assert confirm == date(2024, 1, 8)

    def test_cross_holiday(self) -> None:
        """跨节假日：国庆前下单应跳过假期。"""
        # 2024-09-30 是周一（国庆前最后一个交易日）
        # 国庆假期 10/1-10/7，10/8 是周二（假期后第一个交易日）
        confirm = get_confirm_date(date(2024, 9, 30), "stock")
        assert confirm == date(2024, 10, 8)

    def test_money_fund(self) -> None:
        """货币型 T+1：正常交易日。"""
        confirm = get_confirm_date(date(2024, 1, 2), "money")
        assert confirm == date(2024, 1, 3)

    def test_fof_t_plus_2(self) -> None:
        """FOF T+2：与 QDII 相同规则。"""
        # 2024-01-08 是周一
        confirm = get_confirm_date(date(2024, 1, 8), "fof")
        assert confirm == date(2024, 1, 10)  # 周三

    def test_cross_spring_festival(self) -> None:
        """跨春节假期：2024 春节前下单。"""
        # 2024 春节假期：2/9-2/17
        # 2024-02-08 是周四（春节前最后一个交易日）
        confirm = get_confirm_date(date(2024, 2, 8), "stock")
        # 下一个交易日应该是 2/19（周一）
        assert confirm == date(2024, 2, 19)


# ---------------------------------------------------------------------------
# get_cash_arrival_date 测试
# ---------------------------------------------------------------------------


class TestGetCashArrivalDate:
    """get_cash_arrival_date 资金到账日期计算测试。"""

    def test_stock_normal(self) -> None:
        """股票型 T+4 到账：从确认日起算。"""
        # 确认日 2024-01-03（周三）
        # T+1=1/4, T+2=1/5, T+3=1/8(跳过周末), T+4=1/9
        cash_date = get_cash_arrival_date(date(2024, 1, 3), "stock")
        assert cash_date == date(2024, 1, 9)

    def test_bond_normal(self) -> None:
        """债券型 T+3 到账。"""
        # 确认日 2024-01-03（周三）
        # T+1=1/4, T+2=1/5, T+3=1/8(跳过周末)
        cash_date = get_cash_arrival_date(date(2024, 1, 3), "bond")
        assert cash_date == date(2024, 1, 8)

    def test_money_fast(self) -> None:
        """货币型 T+1 到账（最快）。"""
        cash_date = get_cash_arrival_date(date(2024, 1, 3), "money")
        assert cash_date == date(2024, 1, 4)

    def test_qdii_long(self) -> None:
        """QDII T+7 到账（最慢）。"""
        # 确认日 2024-01-03（周三）
        # T+1=1/4, T+2=1/5, T+3=1/8, T+4=1/9, T+5=1/10, T+6=1/11, T+7=1/12
        cash_date = get_cash_arrival_date(date(2024, 1, 3), "qdii")
        assert cash_date == date(2024, 1, 12)

    def test_cross_weekend(self) -> None:
        """到账日期跨周末。"""
        # 确认日 2024-01-04（周四），stock T+4
        # T+1=1/5(周五), T+2=1/8(周一), T+3=1/9, T+4=1/10
        cash_date = get_cash_arrival_date(date(2024, 1, 4), "stock")
        assert cash_date == date(2024, 1, 10)

    def test_cross_holiday(self) -> None:
        """到账日期跨节假日。"""
        # 确认日 2024-09-30（周一，国庆前），stock T+4
        # 国庆 10/1-10/7 休市
        # T+1=10/8, T+2=10/9, T+3=10/10, T+4=10/11
        cash_date = get_cash_arrival_date(date(2024, 9, 30), "stock")
        assert cash_date == date(2024, 10, 11)


# ---------------------------------------------------------------------------
# 端到端场景测试
# ---------------------------------------------------------------------------


class TestEndToEndSettlement:
    """端到端结算场景测试。"""

    def test_full_subscribe_settlement_stock(self) -> None:
        """股票型完整申购结算流程：下单 → 确认 → 可赎回。"""
        order_date = date(2024, 1, 2)  # 周二
        confirm = get_confirm_date(order_date, "stock")
        assert confirm == date(2024, 1, 3)  # T+1 确认

    def test_full_redeem_settlement_stock(self) -> None:
        """股票型完整赎回结算流程：确认 → 资金到账。"""
        confirm_date = date(2024, 1, 3)  # 周三确认
        cash_date = get_cash_arrival_date(confirm_date, "stock")
        assert cash_date == date(2024, 1, 9)  # T+4 到账

    def test_full_qdii_flow(self) -> None:
        """QDII 完整流程：T+2 确认 + T+7 到账。"""
        order_date = date(2024, 1, 8)  # 周一
        confirm = get_confirm_date(order_date, "qdii")
        assert confirm == date(2024, 1, 10)  # T+2 = 周三

        cash_date = get_cash_arrival_date(confirm, "qdii")
        # 从 1/10 起 T+7: 1/11, 1/12, 1/15, 1/16, 1/17, 1/18, 1/19
        assert cash_date == date(2024, 1, 19)

    def test_money_fund_fastest(self) -> None:
        """货币型是最快的结算类型。"""
        order_date = date(2024, 1, 2)
        confirm = get_confirm_date(order_date, "money")
        cash_date = get_cash_arrival_date(confirm, "money")
        # T+1 确认 + T+1 到账 = 下单后 2 个交易日资金到账
        assert confirm == date(2024, 1, 3)
        assert cash_date == date(2024, 1, 4)
