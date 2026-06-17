"""分红与拆分处理单元测试。

覆盖场景：
- 现金分红：正常分红、无持仓、零分红
- 红利再投：正常再投、NAV 为零异常
- 基金拆分：正常拆分、无持仓、比例为 1、比例为 0 异常
"""

from decimal import Decimal

import pytest

from app.domain.backtest.corporate_actions import process_dividend, process_split
from app.domain.backtest.portfolio import Portfolio


# ============================================================
# 现金分红测试
# ============================================================


class TestCashDividend:
    """现金分红（reinvest=False）测试。"""

    def test_basic_cash_dividend(self) -> None:
        """正常现金分红：cash += shares × dividend_per_share。"""
        portfolio = Portfolio(cash=Decimal("50000"))
        portfolio.positions["000001"] = Decimal("10000")

        process_dividend(
            portfolio,
            fund_code="000001",
            dividend_per_share=Decimal("0.5"),
            nav=Decimal("1.2"),
            reinvest=False,
        )

        # 现金增加 10000 × 0.5 = 5000
        assert portfolio.cash == Decimal("55000")
        # 份额不变
        assert portfolio.positions["000001"] == Decimal("10000")

    def test_cash_dividend_small_amount(self) -> None:
        """小额分红精度验证。"""
        portfolio = Portfolio(cash=Decimal("1000"))
        portfolio.positions["110011"] = Decimal("5432.10")

        process_dividend(
            portfolio,
            fund_code="110011",
            dividend_per_share=Decimal("0.032"),
            nav=Decimal("2.5"),
            reinvest=False,
        )

        # 现金增加 5432.10 × 0.032 = 173.8272
        expected_cash = Decimal("1000") + Decimal("5432.10") * Decimal("0.032")
        assert portfolio.cash == expected_cash
        assert portfolio.positions["110011"] == Decimal("5432.10")

    def test_cash_dividend_no_position(self) -> None:
        """无持仓时分红不做任何操作。"""
        portfolio = Portfolio(cash=Decimal("10000"))

        process_dividend(
            portfolio,
            fund_code="999999",
            dividend_per_share=Decimal("0.5"),
            nav=Decimal("1.0"),
            reinvest=False,
        )

        assert portfolio.cash == Decimal("10000")
        assert "999999" not in portfolio.positions

    def test_cash_dividend_zero_dividend(self) -> None:
        """零分红时不做任何操作。"""
        portfolio = Portfolio(cash=Decimal("10000"))
        portfolio.positions["000001"] = Decimal("5000")

        process_dividend(
            portfolio,
            fund_code="000001",
            dividend_per_share=Decimal("0"),
            nav=Decimal("1.5"),
            reinvest=False,
        )

        assert portfolio.cash == Decimal("10000")
        assert portfolio.positions["000001"] == Decimal("5000")

    def test_cash_dividend_multiple_positions(self) -> None:
        """多只基金持仓时只影响目标基金。"""
        portfolio = Portfolio(cash=Decimal("20000"))
        portfolio.positions["000001"] = Decimal("10000")
        portfolio.positions["000002"] = Decimal("8000")

        process_dividend(
            portfolio,
            fund_code="000001",
            dividend_per_share=Decimal("0.3"),
            nav=Decimal("1.5"),
            reinvest=False,
        )

        # 只有 000001 的分红加入现金
        assert portfolio.cash == Decimal("23000")
        assert portfolio.positions["000001"] == Decimal("10000")
        assert portfolio.positions["000002"] == Decimal("8000")


# ============================================================
# 红利再投测试
# ============================================================


class TestDividendReinvest:
    """红利再投（reinvest=True）测试。"""

    def test_basic_reinvest(self) -> None:
        """正常红利再投：additional_shares = (shares × dividend) / nav。"""
        portfolio = Portfolio(cash=Decimal("50000"))
        portfolio.positions["000001"] = Decimal("10000")

        process_dividend(
            portfolio,
            fund_code="000001",
            dividend_per_share=Decimal("0.6"),
            nav=Decimal("1.2"),
            reinvest=True,
        )

        # 新增份额 = (10000 × 0.6) / 1.2 = 5000
        assert portfolio.positions["000001"] == Decimal("15000")
        # 现金不变
        assert portfolio.cash == Decimal("50000")

    def test_reinvest_fractional_shares(self) -> None:
        """红利再投产生非整数份额。"""
        portfolio = Portfolio(cash=Decimal("10000"))
        portfolio.positions["110022"] = Decimal("3000")

        process_dividend(
            portfolio,
            fund_code="110022",
            dividend_per_share=Decimal("0.25"),
            nav=Decimal("1.8"),
            reinvest=True,
        )

        # 新增份额 = (3000 × 0.25) / 1.8 = 750 / 1.8 ≈ 416.666...
        expected_additional = Decimal("3000") * Decimal("0.25") / Decimal("1.8")
        expected_total = Decimal("3000") + expected_additional
        assert portfolio.positions["110022"] == expected_total
        assert portfolio.cash == Decimal("10000")

    def test_reinvest_no_position(self) -> None:
        """无持仓时红利再投不做任何操作。"""
        portfolio = Portfolio(cash=Decimal("10000"))

        process_dividend(
            portfolio,
            fund_code="999999",
            dividend_per_share=Decimal("0.5"),
            nav=Decimal("1.0"),
            reinvest=True,
        )

        assert portfolio.cash == Decimal("10000")
        assert "999999" not in portfolio.positions

    def test_reinvest_zero_dividend(self) -> None:
        """零分红时红利再投不做任何操作。"""
        portfolio = Portfolio(cash=Decimal("10000"))
        portfolio.positions["000001"] = Decimal("5000")

        process_dividend(
            portfolio,
            fund_code="000001",
            dividend_per_share=Decimal("0"),
            nav=Decimal("1.5"),
            reinvest=True,
        )

        assert portfolio.positions["000001"] == Decimal("5000")
        assert portfolio.cash == Decimal("10000")

    def test_reinvest_zero_nav_raises(self) -> None:
        """红利再投时 NAV 为零应抛出 ValueError。"""
        portfolio = Portfolio(cash=Decimal("10000"))
        portfolio.positions["000001"] = Decimal("5000")

        with pytest.raises(ValueError, match="NAV cannot be zero"):
            process_dividend(
                portfolio,
                fund_code="000001",
                dividend_per_share=Decimal("0.5"),
                nav=Decimal("0"),
                reinvest=True,
            )


# ============================================================
# 基金拆分测试
# ============================================================


class TestSplit:
    """基金拆分测试。"""

    def test_basic_split(self) -> None:
        """正常拆分：份额 × 拆分比例。"""
        portfolio = Portfolio(cash=Decimal("10000"))
        portfolio.positions["000001"] = Decimal("5000")

        process_split(portfolio, "000001", Decimal("2"))

        # 5000 × 2 = 10000
        assert portfolio.positions["000001"] == Decimal("10000")
        # 现金不变
        assert portfolio.cash == Decimal("10000")

    def test_split_fractional_ratio(self) -> None:
        """非整数拆分比例（如缩股）。"""
        portfolio = Portfolio(cash=Decimal("10000"))
        portfolio.positions["000001"] = Decimal("10000")

        process_split(portfolio, "000001", Decimal("0.5"))

        # 10000 × 0.5 = 5000
        assert portfolio.positions["000001"] == Decimal("5000")
        assert portfolio.cash == Decimal("10000")

    def test_split_no_position(self) -> None:
        """无持仓时拆分不做任何操作。"""
        portfolio = Portfolio(cash=Decimal("10000"))

        process_split(portfolio, "999999", Decimal("2"))

        assert portfolio.cash == Decimal("10000")
        assert "999999" not in portfolio.positions

    def test_split_ratio_one(self) -> None:
        """拆分比例为 1 时不做任何操作。"""
        portfolio = Portfolio(cash=Decimal("10000"))
        portfolio.positions["000001"] = Decimal("5000")

        process_split(portfolio, "000001", Decimal("1"))

        assert portfolio.positions["000001"] == Decimal("5000")

    def test_split_ratio_zero_raises(self) -> None:
        """拆分比例为 0 应抛出 ValueError。"""
        portfolio = Portfolio(cash=Decimal("10000"))
        portfolio.positions["000001"] = Decimal("5000")

        with pytest.raises(ValueError, match="Split ratio cannot be zero"):
            process_split(portfolio, "000001", Decimal("0"))

    def test_split_does_not_affect_other_positions(self) -> None:
        """拆分只影响目标基金，不影响其他持仓。"""
        portfolio = Portfolio(cash=Decimal("10000"))
        portfolio.positions["000001"] = Decimal("5000")
        portfolio.positions["000002"] = Decimal("3000")

        process_split(portfolio, "000001", Decimal("3"))

        assert portfolio.positions["000001"] == Decimal("15000")
        assert portfolio.positions["000002"] == Decimal("3000")

    def test_split_large_ratio(self) -> None:
        """大比例拆分。"""
        portfolio = Portfolio(cash=Decimal("0"))
        portfolio.positions["000001"] = Decimal("1000")

        process_split(portfolio, "000001", Decimal("10"))

        assert portfolio.positions["000001"] == Decimal("10000")
