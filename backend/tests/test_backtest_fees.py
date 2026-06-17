"""费率计算模块单元测试。

覆盖：
- FeeTier 数据类创建与校验
- find_subscribe_tier 阶梯匹配（多档、边界）
- find_redeem_tier 阶梯匹配（持有期边界）
- calc_subscribe_fee 外扣法计算（多档阶梯、零费率、边界金额）
- calc_redeem_fee 赎回费计算（多档阶梯、持有期边界、零费率长期持有）
- 异常场景（无效参数、无匹配阶梯）

需求: 4.4, 4.5
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.backtest.fees import (
    FeeTier,
    RedeemFeeResult,
    SubscribeFeeResult,
    calc_redeem_fee,
    calc_subscribe_fee,
    find_redeem_tier,
    find_subscribe_tier,
)


# ---------------------------------------------------------------------------
# 测试用费率阶梯数据
# ---------------------------------------------------------------------------

# 典型申购费阶梯（3 档）
SUBSCRIBE_TIERS = [
    FeeTier(
        min_amount=Decimal("0"),
        max_amount=Decimal("1000000"),
        rate=Decimal("0.015"),
    ),
    FeeTier(
        min_amount=Decimal("1000000"),
        max_amount=Decimal("5000000"),
        rate=Decimal("0.012"),
    ),
    FeeTier(
        min_amount=Decimal("5000000"),
        max_amount=None,
        rate=Decimal("0.001"),
    ),
]

# 典型赎回费阶梯（4 档，含长期持有免费）
REDEEM_TIERS = [
    FeeTier(
        min_holding_days=0,
        max_holding_days=7,
        rate=Decimal("0.015"),
    ),
    FeeTier(
        min_holding_days=7,
        max_holding_days=365,
        rate=Decimal("0.005"),
    ),
    FeeTier(
        min_holding_days=365,
        max_holding_days=730,
        rate=Decimal("0.0025"),
    ),
    FeeTier(
        min_holding_days=730,
        max_holding_days=None,
        rate=Decimal("0"),
    ),
]


# ---------------------------------------------------------------------------
# FeeTier 数据类测试
# ---------------------------------------------------------------------------


class TestFeeTier:
    """FeeTier 数据类测试。"""

    def test_create_subscribe_tier(self) -> None:
        """正常创建申购费阶梯。"""
        tier = FeeTier(
            min_amount=Decimal("0"),
            max_amount=Decimal("1000000"),
            rate=Decimal("0.015"),
        )
        assert tier.min_amount == Decimal("0")
        assert tier.max_amount == Decimal("1000000")
        assert tier.rate == Decimal("0.015")

    def test_create_redeem_tier(self) -> None:
        """正常创建赎回费阶梯。"""
        tier = FeeTier(
            min_holding_days=7,
            max_holding_days=365,
            rate=Decimal("0.005"),
        )
        assert tier.min_holding_days == 7
        assert tier.max_holding_days == 365
        assert tier.rate == Decimal("0.005")

    def test_no_cap_tier(self) -> None:
        """无上限阶梯（max 为 None）。"""
        tier = FeeTier(
            min_amount=Decimal("5000000"),
            max_amount=None,
            rate=Decimal("0.001"),
        )
        assert tier.max_amount is None

    def test_zero_rate_tier(self) -> None:
        """零费率阶梯（长期持有免费）。"""
        tier = FeeTier(
            min_holding_days=730,
            max_holding_days=None,
            rate=Decimal("0"),
        )
        assert tier.rate == Decimal("0")

    def test_frozen(self) -> None:
        """FeeTier 应为不可变。"""
        tier = FeeTier(rate=Decimal("0.015"))
        with pytest.raises(Exception):
            tier.rate = Decimal("0.02")  # type: ignore[misc]

    def test_negative_rate_raises(self) -> None:
        """负费率应抛出 ValueError。"""
        with pytest.raises(ValueError, match="rate must be non-negative"):
            FeeTier(rate=Decimal("-0.01"))

    def test_negative_min_amount_raises(self) -> None:
        """负 min_amount 应抛出 ValueError。"""
        with pytest.raises(ValueError, match="min_amount must be non-negative"):
            FeeTier(min_amount=Decimal("-100"), rate=Decimal("0.01"))

    def test_max_amount_less_than_min_raises(self) -> None:
        """max_amount <= min_amount 应抛出 ValueError。"""
        with pytest.raises(ValueError, match="max_amount.*must be greater"):
            FeeTier(
                min_amount=Decimal("1000"),
                max_amount=Decimal("500"),
                rate=Decimal("0.01"),
            )

    def test_max_amount_equal_min_raises(self) -> None:
        """max_amount == min_amount 应抛出 ValueError。"""
        with pytest.raises(ValueError, match="max_amount.*must be greater"):
            FeeTier(
                min_amount=Decimal("1000"),
                max_amount=Decimal("1000"),
                rate=Decimal("0.01"),
            )

    def test_negative_min_holding_days_raises(self) -> None:
        """负 min_holding_days 应抛出 ValueError。"""
        with pytest.raises(ValueError, match="min_holding_days must be non-negative"):
            FeeTier(min_holding_days=-1, rate=Decimal("0.01"))

    def test_max_holding_days_less_than_min_raises(self) -> None:
        """max_holding_days <= min_holding_days 应抛出 ValueError。"""
        with pytest.raises(ValueError, match="max_holding_days.*must be greater"):
            FeeTier(
                min_holding_days=30,
                max_holding_days=10,
                rate=Decimal("0.01"),
            )


# ---------------------------------------------------------------------------
# find_subscribe_tier 测试
# ---------------------------------------------------------------------------


class TestFindSubscribeTier:
    """申购费阶梯匹配测试。"""

    def test_first_tier(self) -> None:
        """小额申购匹配第一档。"""
        tier = find_subscribe_tier(Decimal("10000"), SUBSCRIBE_TIERS)
        assert tier is not None
        assert tier.rate == Decimal("0.015")

    def test_second_tier(self) -> None:
        """中等金额匹配第二档。"""
        tier = find_subscribe_tier(Decimal("2000000"), SUBSCRIBE_TIERS)
        assert tier is not None
        assert tier.rate == Decimal("0.012")

    def test_third_tier_no_cap(self) -> None:
        """大额申购匹配无上限档。"""
        tier = find_subscribe_tier(Decimal("10000000"), SUBSCRIBE_TIERS)
        assert tier is not None
        assert tier.rate == Decimal("0.001")

    def test_boundary_lower_inclusive(self) -> None:
        """下限为闭区间（含）。"""
        # 恰好等于第二档下限
        tier = find_subscribe_tier(Decimal("1000000"), SUBSCRIBE_TIERS)
        assert tier is not None
        assert tier.rate == Decimal("0.012")

    def test_boundary_upper_exclusive(self) -> None:
        """上限为开区间（不含）。"""
        # 恰好小于第一档上限
        tier = find_subscribe_tier(Decimal("999999.99"), SUBSCRIBE_TIERS)
        assert tier is not None
        assert tier.rate == Decimal("0.015")

    def test_zero_amount(self) -> None:
        """金额为 0 匹配第一档（min_amount=0）。"""
        tier = find_subscribe_tier(Decimal("0"), SUBSCRIBE_TIERS)
        assert tier is not None
        assert tier.rate == Decimal("0.015")

    def test_no_match_returns_none(self) -> None:
        """无匹配阶梯返回 None。"""
        # 只有一个阶梯，金额不在范围内
        tiers = [
            FeeTier(
                min_amount=Decimal("1000"),
                max_amount=Decimal("5000"),
                rate=Decimal("0.01"),
            )
        ]
        tier = find_subscribe_tier(Decimal("500"), tiers)
        assert tier is None

    def test_empty_tiers_returns_none(self) -> None:
        """空阶梯列表返回 None。"""
        tier = find_subscribe_tier(Decimal("10000"), [])
        assert tier is None


# ---------------------------------------------------------------------------
# find_redeem_tier 测试
# ---------------------------------------------------------------------------


class TestFindRedeemTier:
    """赎回费阶梯匹配测试。"""

    def test_short_holding(self) -> None:
        """短期持有（< 7 天）匹配惩罚费率。"""
        tier = find_redeem_tier(3, REDEEM_TIERS)
        assert tier is not None
        assert tier.rate == Decimal("0.015")

    def test_medium_holding(self) -> None:
        """中期持有匹配第二档。"""
        tier = find_redeem_tier(30, REDEEM_TIERS)
        assert tier is not None
        assert tier.rate == Decimal("0.005")

    def test_one_year_holding(self) -> None:
        """持有一年匹配第三档。"""
        tier = find_redeem_tier(400, REDEEM_TIERS)
        assert tier is not None
        assert tier.rate == Decimal("0.0025")

    def test_long_term_free(self) -> None:
        """长期持有（>= 730 天）免费。"""
        tier = find_redeem_tier(800, REDEEM_TIERS)
        assert tier is not None
        assert tier.rate == Decimal("0")

    def test_boundary_7_days(self) -> None:
        """恰好 7 天匹配第二档（下限含）。"""
        tier = find_redeem_tier(7, REDEEM_TIERS)
        assert tier is not None
        assert tier.rate == Decimal("0.005")

    def test_boundary_6_days(self) -> None:
        """6 天仍在第一档（上限不含）。"""
        tier = find_redeem_tier(6, REDEEM_TIERS)
        assert tier is not None
        assert tier.rate == Decimal("0.015")

    def test_boundary_365_days(self) -> None:
        """恰好 365 天匹配第三档。"""
        tier = find_redeem_tier(365, REDEEM_TIERS)
        assert tier is not None
        assert tier.rate == Decimal("0.0025")

    def test_boundary_364_days(self) -> None:
        """364 天仍在第二档。"""
        tier = find_redeem_tier(364, REDEEM_TIERS)
        assert tier is not None
        assert tier.rate == Decimal("0.005")

    def test_boundary_730_days(self) -> None:
        """恰好 730 天匹配免费档。"""
        tier = find_redeem_tier(730, REDEEM_TIERS)
        assert tier is not None
        assert tier.rate == Decimal("0")

    def test_zero_days(self) -> None:
        """持有 0 天匹配第一档。"""
        tier = find_redeem_tier(0, REDEEM_TIERS)
        assert tier is not None
        assert tier.rate == Decimal("0.015")

    def test_no_match_returns_none(self) -> None:
        """无匹配阶梯返回 None。"""
        tiers = [
            FeeTier(min_holding_days=30, max_holding_days=365, rate=Decimal("0.005"))
        ]
        tier = find_redeem_tier(10, tiers)
        assert tier is None

    def test_empty_tiers_returns_none(self) -> None:
        """空阶梯列表返回 None。"""
        tier = find_redeem_tier(100, [])
        assert tier is None


# ---------------------------------------------------------------------------
# calc_subscribe_fee 测试
# ---------------------------------------------------------------------------


class TestCalcSubscribeFee:
    """申购费计算测试（外扣法）。"""

    def test_basic_calculation(self) -> None:
        """基本外扣法计算。

        amount=100000, rate=0.015
        fee = 100000 * 0.015 / 1.015 ≈ 1477.83
        net_amount = 100000 - 1477.83 = 98522.17
        """
        result = calc_subscribe_fee(Decimal("100000"), SUBSCRIBE_TIERS)
        assert isinstance(result, SubscribeFeeResult)
        assert result.rate == Decimal("0.015")
        # 外扣法：fee = 100000 * 0.015 / 1.015
        expected_fee = (
            Decimal("100000") * Decimal("0.015") / Decimal("1.015")
        ).quantize(Decimal("0.01"))
        assert result.fee == expected_fee
        assert result.net_amount == Decimal("100000") - expected_fee

    def test_second_tier(self) -> None:
        """第二档费率计算。"""
        result = calc_subscribe_fee(Decimal("2000000"), SUBSCRIBE_TIERS)
        assert result.rate == Decimal("0.012")
        expected_fee = (
            Decimal("2000000") * Decimal("0.012") / Decimal("1.012")
        ).quantize(Decimal("0.01"))
        assert result.fee == expected_fee

    def test_large_amount_low_rate(self) -> None:
        """大额申购低费率。"""
        result = calc_subscribe_fee(Decimal("10000000"), SUBSCRIBE_TIERS)
        assert result.rate == Decimal("0.001")
        expected_fee = (
            Decimal("10000000") * Decimal("0.001") / Decimal("1.001")
        ).quantize(Decimal("0.01"))
        assert result.fee == expected_fee

    def test_fee_plus_net_equals_amount(self) -> None:
        """fee + net_amount 应等于原始 amount。"""
        result = calc_subscribe_fee(Decimal("50000"), SUBSCRIBE_TIERS)
        assert result.fee + result.net_amount == Decimal("50000")

    def test_zero_rate(self) -> None:
        """零费率时费用为 0，净金额等于原始金额。"""
        tiers = [
            FeeTier(
                min_amount=Decimal("0"),
                max_amount=None,
                rate=Decimal("0"),
            )
        ]
        result = calc_subscribe_fee(Decimal("100000"), tiers)
        assert result.fee == Decimal("0")
        assert result.net_amount == Decimal("100000")
        assert result.rate == Decimal("0")

    def test_boundary_amount_at_tier_switch(self) -> None:
        """恰好在阶梯切换点的金额。"""
        # 恰好 1000000 应使用第二档
        result = calc_subscribe_fee(Decimal("1000000"), SUBSCRIBE_TIERS)
        assert result.rate == Decimal("0.012")

    def test_small_amount(self) -> None:
        """小额申购（如 100 元）。"""
        result = calc_subscribe_fee(Decimal("100"), SUBSCRIBE_TIERS)
        assert result.rate == Decimal("0.015")
        assert result.fee > Decimal("0")
        assert result.fee + result.net_amount == Decimal("100")

    def test_negative_amount_raises(self) -> None:
        """负金额应抛出 ValueError。"""
        with pytest.raises(ValueError, match="must be positive"):
            calc_subscribe_fee(Decimal("-1000"), SUBSCRIBE_TIERS)

    def test_zero_amount_raises(self) -> None:
        """零金额应抛出 ValueError。"""
        with pytest.raises(ValueError, match="must be positive"):
            calc_subscribe_fee(Decimal("0"), SUBSCRIBE_TIERS)

    def test_no_matching_tier_raises(self) -> None:
        """无匹配阶梯应抛出 ValueError。"""
        tiers = [
            FeeTier(
                min_amount=Decimal("1000"),
                max_amount=Decimal("5000"),
                rate=Decimal("0.01"),
            )
        ]
        with pytest.raises(ValueError, match="No matching subscribe fee tier"):
            calc_subscribe_fee(Decimal("500"), tiers)


# ---------------------------------------------------------------------------
# calc_redeem_fee 测试
# ---------------------------------------------------------------------------


class TestCalcRedeemFee:
    """赎回费计算测试。"""

    def test_short_term_penalty(self) -> None:
        """短期持有惩罚费率（< 7 天）。

        shares=10000, nav=1.5, holding_days=3, rate=0.015
        gross = 10000 * 1.5 = 15000
        fee = 15000 * 0.015 = 225.00
        """
        result = calc_redeem_fee(
            shares=Decimal("10000"),
            nav=Decimal("1.5"),
            holding_days=3,
            fee_tiers=REDEEM_TIERS,
        )
        assert isinstance(result, RedeemFeeResult)
        assert result.rate == Decimal("0.015")
        assert result.gross_amount == Decimal("15000.00")
        assert result.fee == Decimal("225.00")
        assert result.net_amount == Decimal("14775.00")

    def test_medium_term(self) -> None:
        """中期持有费率（7-365 天）。"""
        result = calc_redeem_fee(
            shares=Decimal("5000"),
            nav=Decimal("2.0"),
            holding_days=30,
            fee_tiers=REDEEM_TIERS,
        )
        assert result.rate == Decimal("0.005")
        assert result.gross_amount == Decimal("10000.00")
        assert result.fee == Decimal("50.00")
        assert result.net_amount == Decimal("9950.00")

    def test_one_year_plus(self) -> None:
        """持有超过一年费率。"""
        result = calc_redeem_fee(
            shares=Decimal("10000"),
            nav=Decimal("1.2"),
            holding_days=500,
            fee_tiers=REDEEM_TIERS,
        )
        assert result.rate == Decimal("0.0025")
        assert result.gross_amount == Decimal("12000.00")
        assert result.fee == Decimal("30.00")
        assert result.net_amount == Decimal("11970.00")

    def test_long_term_free(self) -> None:
        """长期持有免赎回费（>= 730 天）。"""
        result = calc_redeem_fee(
            shares=Decimal("10000"),
            nav=Decimal("1.5"),
            holding_days=800,
            fee_tiers=REDEEM_TIERS,
        )
        assert result.rate == Decimal("0")
        assert result.fee == Decimal("0")
        assert result.gross_amount == Decimal("15000.00")
        assert result.net_amount == Decimal("15000.00")

    def test_boundary_exactly_7_days(self) -> None:
        """恰好 7 天应使用第二档费率。"""
        result = calc_redeem_fee(
            shares=Decimal("1000"),
            nav=Decimal("1.0"),
            holding_days=7,
            fee_tiers=REDEEM_TIERS,
        )
        assert result.rate == Decimal("0.005")

    def test_boundary_exactly_6_days(self) -> None:
        """6 天仍使用第一档惩罚费率。"""
        result = calc_redeem_fee(
            shares=Decimal("1000"),
            nav=Decimal("1.0"),
            holding_days=6,
            fee_tiers=REDEEM_TIERS,
        )
        assert result.rate == Decimal("0.015")

    def test_boundary_exactly_730_days(self) -> None:
        """恰好 730 天应免费。"""
        result = calc_redeem_fee(
            shares=Decimal("1000"),
            nav=Decimal("1.0"),
            holding_days=730,
            fee_tiers=REDEEM_TIERS,
        )
        assert result.rate == Decimal("0")
        assert result.fee == Decimal("0")

    def test_fee_plus_net_equals_gross(self) -> None:
        """fee + net_amount 应等于 gross_amount。"""
        result = calc_redeem_fee(
            shares=Decimal("8000"),
            nav=Decimal("1.35"),
            holding_days=100,
            fee_tiers=REDEEM_TIERS,
        )
        assert result.fee + result.net_amount == result.gross_amount

    def test_negative_shares_raises(self) -> None:
        """负份额应抛出 ValueError。"""
        with pytest.raises(ValueError, match="must be positive"):
            calc_redeem_fee(
                shares=Decimal("-1000"),
                nav=Decimal("1.5"),
                holding_days=30,
                fee_tiers=REDEEM_TIERS,
            )

    def test_zero_shares_raises(self) -> None:
        """零份额应抛出 ValueError。"""
        with pytest.raises(ValueError, match="must be positive"):
            calc_redeem_fee(
                shares=Decimal("0"),
                nav=Decimal("1.5"),
                holding_days=30,
                fee_tiers=REDEEM_TIERS,
            )

    def test_negative_nav_raises(self) -> None:
        """负净值应抛出 ValueError。"""
        with pytest.raises(ValueError, match="NAV must be positive"):
            calc_redeem_fee(
                shares=Decimal("1000"),
                nav=Decimal("-1.5"),
                holding_days=30,
                fee_tiers=REDEEM_TIERS,
            )

    def test_negative_holding_days_raises(self) -> None:
        """负持有天数应抛出 ValueError。"""
        with pytest.raises(ValueError, match="must be non-negative"):
            calc_redeem_fee(
                shares=Decimal("1000"),
                nav=Decimal("1.5"),
                holding_days=-1,
                fee_tiers=REDEEM_TIERS,
            )

    def test_no_matching_tier_raises(self) -> None:
        """无匹配阶梯应抛出 ValueError。"""
        tiers = [
            FeeTier(min_holding_days=30, max_holding_days=365, rate=Decimal("0.005"))
        ]
        with pytest.raises(ValueError, match="No matching redeem fee tier"):
            calc_redeem_fee(
                shares=Decimal("1000"),
                nav=Decimal("1.5"),
                holding_days=10,
                fee_tiers=tiers,
            )


# ---------------------------------------------------------------------------
# 综合场景测试
# ---------------------------------------------------------------------------


class TestIntegrationScenarios:
    """综合场景测试。"""

    def test_typical_retail_subscribe(self) -> None:
        """典型散户申购场景：1 万元申购。"""
        result = calc_subscribe_fee(Decimal("10000"), SUBSCRIBE_TIERS)
        # rate=1.5%, fee = 10000 * 0.015 / 1.015 ≈ 147.78
        assert result.rate == Decimal("0.015")
        assert result.fee == Decimal("147.78")
        assert result.net_amount == Decimal("9852.22")

    def test_typical_retail_redeem_short(self) -> None:
        """典型散户短期赎回：持有 5 天赎回 1 万份。"""
        result = calc_redeem_fee(
            shares=Decimal("10000"),
            nav=Decimal("1.0"),
            holding_days=5,
            fee_tiers=REDEEM_TIERS,
        )
        # rate=1.5%, gross=10000, fee=150
        assert result.fee == Decimal("150.00")

    def test_institutional_subscribe(self) -> None:
        """机构大额申购：500 万以上。"""
        result = calc_subscribe_fee(Decimal("10000000"), SUBSCRIBE_TIERS)
        assert result.rate == Decimal("0.001")
        # fee = 10000000 * 0.001 / 1.001 ≈ 9990.01
        expected_fee = (
            Decimal("10000000") * Decimal("0.001") / Decimal("1.001")
        ).quantize(Decimal("0.01"))
        assert result.fee == expected_fee

    def test_long_term_holder_free_redeem(self) -> None:
        """长期持有者免费赎回：持有 3 年。"""
        result = calc_redeem_fee(
            shares=Decimal("50000"),
            nav=Decimal("2.5"),
            holding_days=1095,  # 3 年
            fee_tiers=REDEEM_TIERS,
        )
        assert result.fee == Decimal("0")
        assert result.net_amount == result.gross_amount

    def test_single_tier_subscribe(self) -> None:
        """只有一档费率的基金（如 C 类份额零申购费）。"""
        tiers = [
            FeeTier(
                min_amount=Decimal("0"),
                max_amount=None,
                rate=Decimal("0"),
            )
        ]
        result = calc_subscribe_fee(Decimal("50000"), tiers)
        assert result.fee == Decimal("0")
        assert result.net_amount == Decimal("50000")

    def test_five_tier_redeem(self) -> None:
        """五档赎回费率（更细粒度的阶梯）。"""
        tiers = [
            FeeTier(min_holding_days=0, max_holding_days=7, rate=Decimal("0.015")),
            FeeTier(min_holding_days=7, max_holding_days=30, rate=Decimal("0.0075")),
            FeeTier(min_holding_days=30, max_holding_days=180, rate=Decimal("0.005")),
            FeeTier(min_holding_days=180, max_holding_days=365, rate=Decimal("0.0025")),
            FeeTier(min_holding_days=365, max_holding_days=None, rate=Decimal("0")),
        ]

        # 持有 15 天 → 第二档
        result = calc_redeem_fee(
            shares=Decimal("1000"),
            nav=Decimal("1.0"),
            holding_days=15,
            fee_tiers=tiers,
        )
        assert result.rate == Decimal("0.0075")

        # 持有 100 天 → 第三档
        result = calc_redeem_fee(
            shares=Decimal("1000"),
            nav=Decimal("1.0"),
            holding_days=100,
            fee_tiers=tiers,
        )
        assert result.rate == Decimal("0.005")

        # 持有 366 天 → 免费
        result = calc_redeem_fee(
            shares=Decimal("1000"),
            nav=Decimal("1.0"),
            holding_days=366,
            fee_tiers=tiers,
        )
        assert result.rate == Decimal("0")
        assert result.fee == Decimal("0")
