"""定投策略单元测试。

覆盖：
- FixedAmountDCA: 定额定投
- ValueAveragingDCA: 价值平均定投
- SmartDCA: 智能定投（均线偏离加倍）
- 辅助函数: _is_investment_day, _compute_moving_average

需求: 5.1
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.domain.backtest.engine_event import BarContext
from app.domain.backtest.order import OrderIntent
from app.domain.backtest.portfolio import Portfolio
from app.domain.strategy.dca import (
    DCAParams,
    FixedAmountDCA,
    Frequency,
    SmartDCA,
    SmartDCAParams,
    ValueAveragingDCA,
    ValueAveragingParams,
    _compute_moving_average,
    _is_investment_day,
)


# ---------------------------------------------------------------------------
# 辅助工具
# ---------------------------------------------------------------------------


def _make_context(
    current_date: date,
    cash: Decimal = Decimal("100000"),
    positions: dict[str, Decimal] | None = None,
    nav_data: dict[str, dict[date, Decimal]] | None = None,
    cutoff_date: date | None = None,
) -> BarContext:
    """构建测试用 BarContext。"""
    positions = positions or {}
    nav_data = nav_data or {}
    if cutoff_date is None:
        cutoff_date = current_date - timedelta(days=1)
    portfolio = Portfolio(cash=cash, positions=positions)
    return BarContext(
        current_date=current_date,
        portfolio=portfolio,
        nav_history=nav_data,
        _cutoff_date=cutoff_date,
    )


# ---------------------------------------------------------------------------
# _is_investment_day 测试
# ---------------------------------------------------------------------------


class TestIsInvestmentDay:
    """投资日判断逻辑测试。"""

    def test_first_day_always_invest(self) -> None:
        """首次投资（无上次日期）应返回 True。"""
        assert _is_investment_day(date(2024, 1, 2), None, Frequency.WEEKLY) is True
        assert _is_investment_day(date(2024, 1, 2), None, Frequency.BIWEEKLY) is True
        assert _is_investment_day(date(2024, 1, 2), None, Frequency.MONTHLY) is True

    def test_weekly_frequency(self) -> None:
        """周频：距上次 >= 7 天才投资。"""
        last = date(2024, 1, 1)
        assert _is_investment_day(date(2024, 1, 7), last, Frequency.WEEKLY) is False
        assert _is_investment_day(date(2024, 1, 8), last, Frequency.WEEKLY) is True
        assert _is_investment_day(date(2024, 1, 15), last, Frequency.WEEKLY) is True

    def test_biweekly_frequency(self) -> None:
        """双周频：距上次 >= 14 天才投资。"""
        last = date(2024, 1, 1)
        assert _is_investment_day(date(2024, 1, 14), last, Frequency.BIWEEKLY) is False
        assert _is_investment_day(date(2024, 1, 15), last, Frequency.BIWEEKLY) is True

    def test_monthly_frequency(self) -> None:
        """月频：距上次 >= 28 天才投资。"""
        last = date(2024, 1, 1)
        assert _is_investment_day(date(2024, 1, 28), last, Frequency.MONTHLY) is False
        assert _is_investment_day(date(2024, 1, 29), last, Frequency.MONTHLY) is True


# ---------------------------------------------------------------------------
# _compute_moving_average 测试
# ---------------------------------------------------------------------------


class TestComputeMovingAverage:
    """移动平均计算测试。"""

    def test_empty_series(self) -> None:
        """空序列返回 None。"""
        assert _compute_moving_average({}, 5) is None

    def test_insufficient_data(self) -> None:
        """数据不足窗口长度返回 None。"""
        nav_series = {
            date(2024, 1, 1): Decimal("1.0"),
            date(2024, 1, 2): Decimal("1.1"),
            date(2024, 1, 3): Decimal("1.2"),
        }
        assert _compute_moving_average(nav_series, 5) is None

    def test_exact_window(self) -> None:
        """数据恰好等于窗口长度。"""
        nav_series = {
            date(2024, 1, 1): Decimal("1.0"),
            date(2024, 1, 2): Decimal("2.0"),
            date(2024, 1, 3): Decimal("3.0"),
        }
        result = _compute_moving_average(nav_series, 3)
        assert result == Decimal("2.0")

    def test_uses_most_recent_data(self) -> None:
        """使用最近的数据计算均线。"""
        nav_series = {
            date(2024, 1, 1): Decimal("1.0"),
            date(2024, 1, 2): Decimal("2.0"),
            date(2024, 1, 3): Decimal("3.0"),
            date(2024, 1, 4): Decimal("4.0"),
            date(2024, 1, 5): Decimal("5.0"),
        }
        # window=3, 最近 3 天: 5.0, 4.0, 3.0 → 平均 4.0
        result = _compute_moving_average(nav_series, 3)
        assert result == Decimal("4.0")


# ---------------------------------------------------------------------------
# FixedAmountDCA 测试
# ---------------------------------------------------------------------------


class TestFixedAmountDCA:
    """定额定投策略测试。"""

    def test_creation(self) -> None:
        """策略创建。"""
        params = DCAParams(
            amount=Decimal("1000"),
            frequency=Frequency.MONTHLY,
            fund_code="000001",
        )
        strategy = FixedAmountDCA(params=params, universe=["000001"])
        assert strategy.name == "fixed_amount_dca"
        assert strategy.dca_params.amount == Decimal("1000")
        assert strategy.dca_params.frequency == Frequency.MONTHLY

    def test_first_day_invests(self) -> None:
        """第一个交易日应投资。"""
        params = DCAParams(
            amount=Decimal("1000"),
            frequency=Frequency.MONTHLY,
            fund_code="000001",
        )
        strategy = FixedAmountDCA(params=params, universe=["000001"])
        ctx = _make_context(
            current_date=date(2024, 1, 2),
            cash=Decimal("100000"),
            nav_data={"000001": {date(2024, 1, 1): Decimal("1.5")}},
        )
        orders = strategy.on_bar(ctx)
        assert len(orders) == 1
        assert orders[0].fund_code == "000001"
        assert orders[0].direction == "subscribe"
        assert orders[0].amount == Decimal("1000")

    def test_respects_frequency(self) -> None:
        """按频率间隔投资。"""
        params = DCAParams(
            amount=Decimal("1000"),
            frequency=Frequency.WEEKLY,
            fund_code="000001",
        )
        strategy = FixedAmountDCA(params=params, universe=["000001"])

        # 第一天投资
        ctx1 = _make_context(current_date=date(2024, 1, 2))
        orders1 = strategy.on_bar(ctx1)
        assert len(orders1) == 1

        # 3 天后不投资
        ctx2 = _make_context(current_date=date(2024, 1, 5))
        orders2 = strategy.on_bar(ctx2)
        assert len(orders2) == 0

        # 7 天后投资
        ctx3 = _make_context(current_date=date(2024, 1, 9))
        orders3 = strategy.on_bar(ctx3)
        assert len(orders3) == 1

    def test_insufficient_cash(self) -> None:
        """现金不足时不投资。"""
        params = DCAParams(
            amount=Decimal("1000"),
            frequency=Frequency.MONTHLY,
            fund_code="000001",
        )
        strategy = FixedAmountDCA(params=params, universe=["000001"])
        ctx = _make_context(
            current_date=date(2024, 1, 2),
            cash=Decimal("500"),  # 不足 1000
        )
        orders = strategy.on_bar(ctx)
        assert len(orders) == 0

    def test_multiple_periods(self) -> None:
        """多期定投正确执行。"""
        params = DCAParams(
            amount=Decimal("2000"),
            frequency=Frequency.MONTHLY,
            fund_code="110011",
        )
        strategy = FixedAmountDCA(params=params, universe=["110011"])

        # 第 1 期
        ctx1 = _make_context(current_date=date(2024, 1, 2))
        orders1 = strategy.on_bar(ctx1)
        assert len(orders1) == 1
        assert orders1[0].amount == Decimal("2000")

        # 第 2 期（28 天后）
        ctx2 = _make_context(current_date=date(2024, 1, 30))
        orders2 = strategy.on_bar(ctx2)
        assert len(orders2) == 1
        assert orders2[0].amount == Decimal("2000")

        # 第 3 期（再 28 天后）
        ctx3 = _make_context(current_date=date(2024, 2, 27))
        orders3 = strategy.on_bar(ctx3)
        assert len(orders3) == 1


# ---------------------------------------------------------------------------
# ValueAveragingDCA 测试
# ---------------------------------------------------------------------------


class TestValueAveragingDCA:
    """价值平均定投策略测试。"""

    def test_creation(self) -> None:
        """策略创建。"""
        params = ValueAveragingParams(
            amount=Decimal("1000"),
            frequency=Frequency.MONTHLY,
            fund_code="000001",
            target_monthly_growth=Decimal("1000"),
        )
        strategy = ValueAveragingDCA(params=params, universe=["000001"])
        assert strategy.name == "value_averaging_dca"
        assert strategy.va_params.target_monthly_growth == Decimal("1000")

    def test_first_period_invests_target(self) -> None:
        """第一期投入目标增长金额。"""
        params = ValueAveragingParams(
            amount=Decimal("1000"),
            frequency=Frequency.MONTHLY,
            fund_code="000001",
            target_monthly_growth=Decimal("1000"),
        )
        strategy = ValueAveragingDCA(params=params, universe=["000001"])
        ctx = _make_context(
            current_date=date(2024, 1, 2),
            cash=Decimal("100000"),
            nav_data={"000001": {date(2024, 1, 1): Decimal("1.5")}},
        )
        orders = strategy.on_bar(ctx)
        assert len(orders) == 1
        # 第 1 期：目标 1000, 当前持仓 0 → 投入 1000
        assert orders[0].amount == Decimal("1000")

    def test_adjusts_for_existing_value(self) -> None:
        """根据现有持仓价值调整投入。"""
        params = ValueAveragingParams(
            amount=Decimal("1000"),
            frequency=Frequency.MONTHLY,
            fund_code="000001",
            target_monthly_growth=Decimal("1000"),
        )
        strategy = ValueAveragingDCA(params=params, universe=["000001"])

        # 第一期
        ctx1 = _make_context(
            current_date=date(2024, 1, 2),
            cash=Decimal("100000"),
            nav_data={"000001": {date(2024, 1, 1): Decimal("1.0")}},
        )
        strategy.on_bar(ctx1)

        # 第二期：持仓 500 份 × 1.2 = 600，目标 2000 → 投入 1400
        ctx2 = _make_context(
            current_date=date(2024, 1, 30),
            cash=Decimal("100000"),
            positions={"000001": Decimal("500")},
            nav_data={"000001": {date(2024, 1, 29): Decimal("1.2")}},
            cutoff_date=date(2024, 1, 29),
        )
        orders = strategy.on_bar(ctx2)
        assert len(orders) == 1
        assert orders[0].amount == Decimal("1400")

    def test_no_invest_when_above_target(self) -> None:
        """持仓价值超过目标时不投资。"""
        params = ValueAveragingParams(
            amount=Decimal("1000"),
            frequency=Frequency.MONTHLY,
            fund_code="000001",
            target_monthly_growth=Decimal("1000"),
        )
        strategy = ValueAveragingDCA(params=params, universe=["000001"])

        # 第一期
        ctx1 = _make_context(
            current_date=date(2024, 1, 2),
            cash=Decimal("100000"),
            nav_data={"000001": {date(2024, 1, 1): Decimal("1.0")}},
        )
        strategy.on_bar(ctx1)

        # 第二期：持仓 2000 份 × 1.5 = 3000，目标 2000 → 不投资
        ctx2 = _make_context(
            current_date=date(2024, 1, 30),
            cash=Decimal("100000"),
            positions={"000001": Decimal("2000")},
            nav_data={"000001": {date(2024, 1, 29): Decimal("1.5")}},
            cutoff_date=date(2024, 1, 29),
        )
        orders = strategy.on_bar(ctx2)
        assert len(orders) == 0

    def test_limited_by_cash(self) -> None:
        """投入金额受现金限制。"""
        params = ValueAveragingParams(
            amount=Decimal("1000"),
            frequency=Frequency.MONTHLY,
            fund_code="000001",
            target_monthly_growth=Decimal("5000"),
        )
        strategy = ValueAveragingDCA(params=params, universe=["000001"])

        ctx = _make_context(
            current_date=date(2024, 1, 2),
            cash=Decimal("3000"),  # 现金只有 3000
            nav_data={"000001": {date(2024, 1, 1): Decimal("1.0")}},
        )
        orders = strategy.on_bar(ctx)
        assert len(orders) == 1
        # 目标 5000，现金只有 3000 → 投入 3000
        assert orders[0].amount == Decimal("3000")


# ---------------------------------------------------------------------------
# SmartDCA 测试
# ---------------------------------------------------------------------------


class TestSmartDCA:
    """智能定投策略测试。"""

    def test_creation(self) -> None:
        """策略创建。"""
        params = SmartDCAParams(
            amount=Decimal("1000"),
            frequency=Frequency.MONTHLY,
            fund_code="000001",
            ma_window=5,
            multiplier_below_ma=Decimal("2.0"),
        )
        strategy = SmartDCA(params=params, universe=["000001"])
        assert strategy.name == "smart_dca"
        assert strategy.smart_params.ma_window == 5
        assert strategy.smart_params.multiplier_below_ma == Decimal("2.0")

    def test_normal_amount_when_above_ma(self) -> None:
        """NAV 高于均线时投入基础金额。"""
        # 构造 NAV 序列：均线 = (1.0+1.1+1.2+1.3+1.4)/5 = 1.2
        # 当前 NAV = 1.4 > 1.2 → 正常投入
        nav_data: dict[date, Decimal] = {
            date(2024, 1, 1): Decimal("1.0"),
            date(2024, 1, 2): Decimal("1.1"),
            date(2024, 1, 3): Decimal("1.2"),
            date(2024, 1, 4): Decimal("1.3"),
            date(2024, 1, 5): Decimal("1.4"),
        }
        params = SmartDCAParams(
            amount=Decimal("1000"),
            frequency=Frequency.MONTHLY,
            fund_code="000001",
            ma_window=5,
            multiplier_below_ma=Decimal("2.0"),
        )
        strategy = SmartDCA(params=params, universe=["000001"])
        ctx = _make_context(
            current_date=date(2024, 1, 8),
            cash=Decimal("100000"),
            nav_data={"000001": nav_data},
            cutoff_date=date(2024, 1, 5),
        )
        orders = strategy.on_bar(ctx)
        assert len(orders) == 1
        assert orders[0].amount == Decimal("1000")

    def test_double_amount_when_below_ma(self) -> None:
        """NAV 低于均线时加倍投入。"""
        # 构造 NAV 序列：均线 = (2.0+1.8+1.6+1.4+1.2)/5 = 1.6
        # 当前 NAV = 1.2 < 1.6 → 加倍投入
        nav_data: dict[date, Decimal] = {
            date(2024, 1, 1): Decimal("2.0"),
            date(2024, 1, 2): Decimal("1.8"),
            date(2024, 1, 3): Decimal("1.6"),
            date(2024, 1, 4): Decimal("1.4"),
            date(2024, 1, 5): Decimal("1.2"),
        }
        params = SmartDCAParams(
            amount=Decimal("1000"),
            frequency=Frequency.MONTHLY,
            fund_code="000001",
            ma_window=5,
            multiplier_below_ma=Decimal("2.0"),
        )
        strategy = SmartDCA(params=params, universe=["000001"])
        ctx = _make_context(
            current_date=date(2024, 1, 8),
            cash=Decimal("100000"),
            nav_data={"000001": nav_data},
            cutoff_date=date(2024, 1, 5),
        )
        orders = strategy.on_bar(ctx)
        assert len(orders) == 1
        assert orders[0].amount == Decimal("2000")

    def test_normal_amount_when_insufficient_data(self) -> None:
        """数据不足以计算均线时使用基础金额。"""
        nav_data: dict[date, Decimal] = {
            date(2024, 1, 1): Decimal("1.0"),
            date(2024, 1, 2): Decimal("0.8"),  # 只有 2 天数据
        }
        params = SmartDCAParams(
            amount=Decimal("1000"),
            frequency=Frequency.MONTHLY,
            fund_code="000001",
            ma_window=5,  # 需要 5 天
            multiplier_below_ma=Decimal("2.0"),
        )
        strategy = SmartDCA(params=params, universe=["000001"])
        ctx = _make_context(
            current_date=date(2024, 1, 3),
            cash=Decimal("100000"),
            nav_data={"000001": nav_data},
            cutoff_date=date(2024, 1, 2),
        )
        orders = strategy.on_bar(ctx)
        assert len(orders) == 1
        assert orders[0].amount == Decimal("1000")

    def test_fallback_to_base_when_cash_insufficient_for_double(self) -> None:
        """加倍金额超过现金但基础金额够时，使用基础金额。"""
        nav_data: dict[date, Decimal] = {
            date(2024, 1, 1): Decimal("2.0"),
            date(2024, 1, 2): Decimal("1.8"),
            date(2024, 1, 3): Decimal("1.6"),
            date(2024, 1, 4): Decimal("1.4"),
            date(2024, 1, 5): Decimal("1.2"),
        }
        params = SmartDCAParams(
            amount=Decimal("1000"),
            frequency=Frequency.MONTHLY,
            fund_code="000001",
            ma_window=5,
            multiplier_below_ma=Decimal("2.0"),
        )
        strategy = SmartDCA(params=params, universe=["000001"])
        ctx = _make_context(
            current_date=date(2024, 1, 8),
            cash=Decimal("1500"),  # 不够 2000 但够 1000
            nav_data={"000001": nav_data},
            cutoff_date=date(2024, 1, 5),
        )
        orders = strategy.on_bar(ctx)
        assert len(orders) == 1
        assert orders[0].amount == Decimal("1000")

    def test_no_invest_when_cash_insufficient(self) -> None:
        """现金不足基础金额时不投资。"""
        nav_data: dict[date, Decimal] = {
            date(2024, 1, 1): Decimal("1.0"),
        }
        params = SmartDCAParams(
            amount=Decimal("1000"),
            frequency=Frequency.MONTHLY,
            fund_code="000001",
            ma_window=5,
            multiplier_below_ma=Decimal("2.0"),
        )
        strategy = SmartDCA(params=params, universe=["000001"])
        ctx = _make_context(
            current_date=date(2024, 1, 2),
            cash=Decimal("500"),
            nav_data={"000001": nav_data},
            cutoff_date=date(2024, 1, 1),
        )
        orders = strategy.on_bar(ctx)
        assert len(orders) == 0

    def test_custom_multiplier(self) -> None:
        """自定义倍数生效。"""
        nav_data: dict[date, Decimal] = {
            date(2024, 1, 1): Decimal("2.0"),
            date(2024, 1, 2): Decimal("1.8"),
            date(2024, 1, 3): Decimal("1.6"),
            date(2024, 1, 4): Decimal("1.4"),
            date(2024, 1, 5): Decimal("1.2"),
        }
        params = SmartDCAParams(
            amount=Decimal("1000"),
            frequency=Frequency.MONTHLY,
            fund_code="000001",
            ma_window=5,
            multiplier_below_ma=Decimal("3.0"),  # 3 倍
        )
        strategy = SmartDCA(params=params, universe=["000001"])
        ctx = _make_context(
            current_date=date(2024, 1, 8),
            cash=Decimal("100000"),
            nav_data={"000001": nav_data},
            cutoff_date=date(2024, 1, 5),
        )
        orders = strategy.on_bar(ctx)
        assert len(orders) == 1
        assert orders[0].amount == Decimal("3000")

    def test_respects_frequency(self) -> None:
        """按频率间隔投资。"""
        nav_data: dict[date, Decimal] = {
            date(2024, 1, d): Decimal("1.5") for d in range(1, 20)
        }
        params = SmartDCAParams(
            amount=Decimal("1000"),
            frequency=Frequency.WEEKLY,
            fund_code="000001",
            ma_window=5,
            multiplier_below_ma=Decimal("2.0"),
        )
        strategy = SmartDCA(params=params, universe=["000001"])

        # 第一天投资
        ctx1 = _make_context(
            current_date=date(2024, 1, 2),
            cash=Decimal("100000"),
            nav_data={"000001": nav_data},
            cutoff_date=date(2024, 1, 1),
        )
        orders1 = strategy.on_bar(ctx1)
        assert len(orders1) == 1

        # 5 天后不投资
        ctx2 = _make_context(
            current_date=date(2024, 1, 7),
            cash=Decimal("100000"),
            nav_data={"000001": nav_data},
            cutoff_date=date(2024, 1, 6),
        )
        orders2 = strategy.on_bar(ctx2)
        assert len(orders2) == 0

        # 7 天后投资
        ctx3 = _make_context(
            current_date=date(2024, 1, 9),
            cash=Decimal("100000"),
            nav_data={"000001": nav_data},
            cutoff_date=date(2024, 1, 8),
        )
        orders3 = strategy.on_bar(ctx3)
        assert len(orders3) == 1


# ---------------------------------------------------------------------------
# 参数验证测试
# ---------------------------------------------------------------------------


class TestDCAParams:
    """DCA 参数验证测试。"""

    def test_valid_params(self) -> None:
        """有效参数创建成功。"""
        params = DCAParams(
            amount=Decimal("500"),
            frequency=Frequency.WEEKLY,
            fund_code="110011",
        )
        assert params.amount == Decimal("500")
        assert params.frequency == Frequency.WEEKLY
        assert params.fund_code == "110011"

    def test_invalid_amount_zero(self) -> None:
        """金额为 0 应失败。"""
        with pytest.raises(Exception):
            DCAParams(amount=Decimal("0"), fund_code="000001")

    def test_invalid_amount_negative(self) -> None:
        """金额为负应失败。"""
        with pytest.raises(Exception):
            DCAParams(amount=Decimal("-100"), fund_code="000001")

    def test_value_averaging_params(self) -> None:
        """价值平均参数。"""
        params = ValueAveragingParams(
            amount=Decimal("1000"),
            frequency=Frequency.MONTHLY,
            fund_code="000001",
            target_monthly_growth=Decimal("2000"),
        )
        assert params.target_monthly_growth == Decimal("2000")

    def test_smart_dca_params(self) -> None:
        """智能定投参数。"""
        params = SmartDCAParams(
            amount=Decimal("1000"),
            frequency=Frequency.MONTHLY,
            fund_code="000001",
            ma_window=60,
            multiplier_below_ma=Decimal("1.5"),
        )
        assert params.ma_window == 60
        assert params.multiplier_below_ma == Decimal("1.5")

    def test_serialization(self) -> None:
        """参数可序列化。"""
        params = SmartDCAParams(
            amount=Decimal("1000"),
            frequency=Frequency.MONTHLY,
            fund_code="000001",
            ma_window=20,
            multiplier_below_ma=Decimal("2.0"),
        )
        d = params.model_dump()
        assert d["amount"] == Decimal("1000")
        assert d["frequency"] == "monthly"
        assert d["ma_window"] == 20
