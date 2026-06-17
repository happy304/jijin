"""未来函数专项测试。

验证 BarContext 严格防止策略访问 T 日及未来数据：
1. 访问 T 日 NAV 抛出 LookaheadError
2. 访问未来日期 NAV 抛出 LookaheadError
3. 访问 T-1 及更早 NAV 正常返回
4. nav_series() 排除 T 日及未来数据

覆盖 nav、factor、holding 三类数据场景。

需求: 4.10
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.domain.backtest.engine_event import (
    BarContext,
    EventDrivenEngine,
    FundMeta,
    LookaheadError,
)
from app.domain.backtest.order import OrderIntent
from app.domain.backtest.portfolio import Portfolio


# ---------------------------------------------------------------------------
# Fixtures / Helpers
# ---------------------------------------------------------------------------


def _make_nav_data() -> dict[str, dict[date, Decimal]]:
    """构建测试用净值数据（2024-01-02 到 2024-01-12）。"""
    return {
        "000001": {
            date(2024, 1, 2): Decimal("1.5000"),
            date(2024, 1, 3): Decimal("1.5100"),
            date(2024, 1, 4): Decimal("1.5200"),
            date(2024, 1, 5): Decimal("1.5300"),
            date(2024, 1, 8): Decimal("1.5400"),
            date(2024, 1, 9): Decimal("1.5500"),
            date(2024, 1, 10): Decimal("1.5600"),
            date(2024, 1, 11): Decimal("1.5700"),
            date(2024, 1, 12): Decimal("1.5800"),
        },
        "110011": {
            date(2024, 1, 2): Decimal("2.1000"),
            date(2024, 1, 3): Decimal("2.1100"),
            date(2024, 1, 4): Decimal("2.1200"),
            date(2024, 1, 5): Decimal("2.1300"),
            date(2024, 1, 8): Decimal("2.1400"),
            date(2024, 1, 9): Decimal("2.1500"),
            date(2024, 1, 10): Decimal("2.1600"),
            date(2024, 1, 11): Decimal("2.1700"),
            date(2024, 1, 12): Decimal("2.1800"),
        },
    }


def _make_context(
    current_date: date,
    cutoff_date: date,
    nav_data: dict[str, dict[date, Decimal]] | None = None,
) -> BarContext:
    """构建 BarContext 实例。"""
    return BarContext(
        current_date=current_date,
        portfolio=Portfolio(cash=Decimal("100000")),
        nav_history=nav_data or _make_nav_data(),
        _cutoff_date=cutoff_date,
    )


# ---------------------------------------------------------------------------
# NAV 数据未来函数测试
# ---------------------------------------------------------------------------


class TestNavLookahead:
    """NAV 净值数据的未来函数防护测试。"""

    def test_access_t_day_nav_raises_lookahead_error(self) -> None:
        """访问 T 日（当天）NAV 应抛出 LookaheadError。"""
        # T 日 = 2024-01-04, cutoff = 2024-01-03 (T-1)
        ctx = _make_context(
            current_date=date(2024, 1, 4),
            cutoff_date=date(2024, 1, 3),
        )

        with pytest.raises(LookaheadError):
            ctx.nav("000001", date(2024, 1, 4))

    def test_access_future_nav_raises_lookahead_error(self) -> None:
        """访问未来日期（T+1, T+2 等）NAV 应抛出 LookaheadError。"""
        # T 日 = 2024-01-04, cutoff = 2024-01-03
        ctx = _make_context(
            current_date=date(2024, 1, 4),
            cutoff_date=date(2024, 1, 3),
        )

        # T+1
        with pytest.raises(LookaheadError):
            ctx.nav("000001", date(2024, 1, 5))

        # T+2
        with pytest.raises(LookaheadError):
            ctx.nav("000001", date(2024, 1, 8))

        # 更远的未来
        with pytest.raises(LookaheadError):
            ctx.nav("000001", date(2024, 1, 12))

    def test_access_t_minus_1_nav_works(self) -> None:
        """访问 T-1 日 NAV 正常返回。"""
        ctx = _make_context(
            current_date=date(2024, 1, 4),
            cutoff_date=date(2024, 1, 3),
        )

        result = ctx.nav("000001", date(2024, 1, 3))
        assert result == Decimal("1.5100")

    def test_access_earlier_nav_works(self) -> None:
        """访问 T-2 及更早的 NAV 正常返回。"""
        ctx = _make_context(
            current_date=date(2024, 1, 4),
            cutoff_date=date(2024, 1, 3),
        )

        # T-2
        result = ctx.nav("000001", date(2024, 1, 2))
        assert result == Decimal("1.5000")

    def test_default_nav_returns_cutoff_date(self) -> None:
        """不指定日期时，nav() 返回 cutoff_date（T-1）的净值。"""
        ctx = _make_context(
            current_date=date(2024, 1, 4),
            cutoff_date=date(2024, 1, 3),
        )

        result = ctx.nav("000001")
        assert result == Decimal("1.5100")  # 2024-01-03 的净值

    def test_nav_series_excludes_t_day_and_future(self) -> None:
        """nav_series() 只返回 cutoff_date 及之前的数据。"""
        ctx = _make_context(
            current_date=date(2024, 1, 5),
            cutoff_date=date(2024, 1, 4),
        )

        series = ctx.nav_series("000001")

        # 应包含 cutoff 及之前
        assert date(2024, 1, 2) in series
        assert date(2024, 1, 3) in series
        assert date(2024, 1, 4) in series

        # 不应包含 T 日及之后
        assert date(2024, 1, 5) not in series
        assert date(2024, 1, 8) not in series
        assert date(2024, 1, 9) not in series

    def test_nav_series_values_correct(self) -> None:
        """nav_series() 返回的值正确。"""
        ctx = _make_context(
            current_date=date(2024, 1, 4),
            cutoff_date=date(2024, 1, 3),
        )

        series = ctx.nav_series("000001")

        assert series[date(2024, 1, 2)] == Decimal("1.5000")
        assert series[date(2024, 1, 3)] == Decimal("1.5100")
        assert len(series) == 2

    def test_nav_nonexistent_fund_returns_none(self) -> None:
        """查询不存在的基金返回 None。"""
        ctx = _make_context(
            current_date=date(2024, 1, 4),
            cutoff_date=date(2024, 1, 3),
        )

        result = ctx.nav("999999", date(2024, 1, 2))
        assert result is None

    def test_nav_series_nonexistent_fund_returns_empty(self) -> None:
        """查询不存在的基金的 nav_series 返回空字典。"""
        ctx = _make_context(
            current_date=date(2024, 1, 4),
            cutoff_date=date(2024, 1, 3),
        )

        series = ctx.nav_series("999999")
        assert series == {}

    def test_multiple_funds_lookahead_protection(self) -> None:
        """多只基金的未来函数防护同样有效。"""
        ctx = _make_context(
            current_date=date(2024, 1, 4),
            cutoff_date=date(2024, 1, 3),
        )

        # 第二只基金也不能访问 T 日数据
        with pytest.raises(LookaheadError):
            ctx.nav("110011", date(2024, 1, 4))

        # 但可以访问 T-1
        result = ctx.nav("110011", date(2024, 1, 3))
        assert result == Decimal("2.1100")


# ---------------------------------------------------------------------------
# 引擎集成测试：策略中的未来函数检测
# ---------------------------------------------------------------------------


class TestEngineLookaheadIntegration:
    """通过引擎运行策略，验证未来函数防护在实际回测中生效。"""

    def test_strategy_accessing_t_day_nav_raises(self) -> None:
        """策略在 on_bar 中访问 T 日 NAV 时引擎抛出 LookaheadError。"""

        class BadStrategy:
            """试图访问当天净值的策略。"""

            def on_bar(self, context: BarContext) -> list[OrderIntent]:
                # 试图访问 T 日净值 - 应该失败
                context.nav("000001", context.date)
                return []

        engine = EventDrivenEngine()
        nav_data = _make_nav_data()

        with pytest.raises(LookaheadError):
            engine.run(
                start=date(2024, 1, 2),
                end=date(2024, 1, 5),
                strategy=BadStrategy(),
                nav_data=nav_data,
                initial_capital=Decimal("100000"),
                fund_meta={"000001": FundMeta(code="000001", fund_type="stock")},
            )

    def test_strategy_accessing_future_nav_raises(self) -> None:
        """策略在 on_bar 中访问未来日期 NAV 时引擎抛出 LookaheadError。"""

        class FuturePeekStrategy:
            """试图偷看未来净值的策略。"""

            def on_bar(self, context: BarContext) -> list[OrderIntent]:
                # 试图访问 T+5 日净值
                from datetime import timedelta

                future_date = context.date + timedelta(days=5)
                context.nav("000001", future_date)
                return []

        engine = EventDrivenEngine()
        nav_data = _make_nav_data()

        with pytest.raises(LookaheadError):
            engine.run(
                start=date(2024, 1, 2),
                end=date(2024, 1, 5),
                strategy=FuturePeekStrategy(),
                nav_data=nav_data,
                initial_capital=Decimal("100000"),
                fund_meta={"000001": FundMeta(code="000001", fund_type="stock")},
            )

    def test_strategy_using_nav_series_safe(self) -> None:
        """策略使用 nav_series() 不会泄露未来数据。"""

        collected_series: list[dict[date, Decimal]] = []

        class SeriesCollectorStrategy:
            """收集 nav_series 结果的策略。"""

            def on_bar(self, context: BarContext) -> list[OrderIntent]:
                series = context.nav_series("000001")
                collected_series.append(series)
                # 验证 series 中没有 T 日及之后的数据
                for d in series:
                    assert d < context.date, (
                        f"nav_series leaked future data: {d} >= {context.date}"
                    )
                return []

        engine = EventDrivenEngine()
        nav_data = _make_nav_data()

        engine.run(
            start=date(2024, 1, 2),
            end=date(2024, 1, 5),
            strategy=SeriesCollectorStrategy(),
            nav_data=nav_data,
            initial_capital=Decimal("100000"),
            fund_meta={"000001": FundMeta(code="000001", fund_type="stock")},
        )

        # 应该收集了 4 个交易日的 series
        assert len(collected_series) == 4

    def test_strategy_accessing_t_minus_1_nav_works(self) -> None:
        """策略正常访问 T-1 日 NAV 不报错。"""

        nav_values: list[Decimal | None] = []

        class SafeStrategy:
            """只访问 T-1 净值的安全策略。"""

            def on_bar(self, context: BarContext) -> list[OrderIntent]:
                # 默认 nav() 返回 T-1 日净值
                val = context.nav("000001")
                nav_values.append(val)
                return []

        engine = EventDrivenEngine()
        nav_data = _make_nav_data()

        result = engine.run(
            start=date(2024, 1, 2),
            end=date(2024, 1, 5),
            strategy=SafeStrategy(),
            nav_data=nav_data,
            initial_capital=Decimal("100000"),
            fund_meta={"000001": FundMeta(code="000001", fund_type="stock")},
        )

        # 引擎正常完成
        assert len(result.equity_curve) == 4
        # 每天都能获取到 T-1 净值（第一天可能为 None 因为没有前一天数据）
        assert len(nav_values) == 4


# ---------------------------------------------------------------------------
# 因子数据未来函数测试（通过 nav_series 间接验证）
# ---------------------------------------------------------------------------


class TestFactorLookahead:
    """因子计算依赖 nav_series，验证因子不会使用未来数据。

    因子计算基于 nav_series() 返回的数据，因此只要 nav_series()
    正确排除了未来数据，因子计算就不会有未来函数问题。
    """

    def test_factor_calculation_uses_only_past_data(self) -> None:
        """模拟因子计算：只使用 nav_series 中的历史数据。"""
        ctx = _make_context(
            current_date=date(2024, 1, 8),
            cutoff_date=date(2024, 1, 5),
        )

        series = ctx.nav_series("000001")

        # 模拟计算收益率因子
        dates_sorted = sorted(series.keys())
        assert len(dates_sorted) >= 2

        # 所有日期都应 <= cutoff
        for d in dates_sorted:
            assert d <= date(2024, 1, 5)

        # 计算简单收益率
        first_nav = series[dates_sorted[0]]
        last_nav = series[dates_sorted[-1]]
        ret = (last_nav - first_nav) / first_nav

        # 收益率应基于 1.5000 -> 1.5300 (1/2 -> 1/5)
        expected = (Decimal("1.5300") - Decimal("1.5000")) / Decimal("1.5000")
        assert ret == expected

    def test_rolling_factor_window_respects_cutoff(self) -> None:
        """滚动窗口因子计算不会超出 cutoff 边界。"""
        ctx = _make_context(
            current_date=date(2024, 1, 9),
            cutoff_date=date(2024, 1, 8),
        )

        series = ctx.nav_series("000001")
        dates_sorted = sorted(series.keys())

        # 最后一个可用日期应该是 cutoff_date
        assert dates_sorted[-1] <= date(2024, 1, 8)

        # 不应包含 T 日（1/9）及之后
        assert date(2024, 1, 9) not in series
        assert date(2024, 1, 10) not in series


# ---------------------------------------------------------------------------
# 持仓数据未来函数测试（通过 BarContext 属性验证）
# ---------------------------------------------------------------------------


class TestHoldingLookahead:
    """持仓数据的未来函数防护测试。

    BarContext 的 positions 属性反映的是当前时刻的持仓状态，
    不会泄露未来的持仓变动。验证策略只能看到已确认的持仓。
    """

    def test_positions_reflect_confirmed_only(self) -> None:
        """positions 只反映已确认的持仓，不包含 pending 订单的预期结果。"""
        portfolio = Portfolio(cash=Decimal("100000"))
        # 手动添加已确认持仓
        portfolio.positions["000001"] = Decimal("10000")

        ctx = BarContext(
            current_date=date(2024, 1, 4),
            portfolio=portfolio,
            nav_history=_make_nav_data(),
            _cutoff_date=date(2024, 1, 3),
        )

        # 能看到已确认的持仓
        assert ctx.positions["000001"] == Decimal("10000")

    def test_pending_orders_not_in_positions(self) -> None:
        """pending 订单不会提前反映在 positions 中。"""
        from app.domain.backtest.order import Order

        portfolio = Portfolio(cash=Decimal("50000"))
        portfolio.positions["000001"] = Decimal("5000")

        # 添加一个 pending 订单（尚未确认）
        pending_order = Order(
            order_id="ORD-001",
            fund_code="110011",
            direction="subscribe",
            amount=Decimal("30000"),
            shares=None,
            order_date=date(2024, 1, 3),
        )
        portfolio.add_pending_order(pending_order)

        ctx = BarContext(
            current_date=date(2024, 1, 4),
            portfolio=portfolio,
            nav_history=_make_nav_data(),
            _cutoff_date=date(2024, 1, 3),
        )

        # 110011 的持仓应该为 0（pending 订单未确认）
        assert "110011" not in ctx.positions
        # 已确认的 000001 持仓正常
        assert ctx.positions["000001"] == Decimal("5000")

    def test_engine_positions_update_after_confirmation(self) -> None:
        """引擎中持仓在订单确认后才更新。"""

        position_snapshots: list[dict[str, Decimal]] = []

        class PositionTrackerStrategy:
            """记录每天持仓状态的策略。"""

            def __init__(self) -> None:
                self._bought = False

            def on_bar(self, context: BarContext) -> list[OrderIntent]:
                position_snapshots.append(dict(context.positions))
                if not self._bought:
                    self._bought = True
                    return [
                        OrderIntent(
                            fund_code="000001",
                            direction="subscribe",
                            amount=Decimal("50000"),
                        )
                    ]
                return []

        engine = EventDrivenEngine()
        nav_data = _make_nav_data()

        engine.run(
            start=date(2024, 1, 2),
            end=date(2024, 1, 5),
            strategy=PositionTrackerStrategy(),
            nav_data=nav_data,
            initial_capital=Decimal("100000"),
            fund_meta={"000001": FundMeta(code="000001", fund_type="stock")},
        )

        # Day 1 (1/2): 下单前无持仓
        assert position_snapshots[0] == {}

        # Day 2 (1/3): T+1 确认后有持仓（确认在 on_bar 之前执行）
        assert "000001" in position_snapshots[1]
        assert position_snapshots[1]["000001"] > Decimal("0")

    def test_cash_reflects_frozen_amount(self) -> None:
        """下单后现金被冻结，策略能看到正确的可用现金。"""

        cash_snapshots: list[Decimal] = []

        class CashTrackerStrategy:
            """记录每天现金的策略。"""

            def __init__(self) -> None:
                self._bought = False

            def on_bar(self, context: BarContext) -> list[OrderIntent]:
                cash_snapshots.append(context.cash)
                if not self._bought:
                    self._bought = True
                    return [
                        OrderIntent(
                            fund_code="000001",
                            direction="subscribe",
                            amount=Decimal("60000"),
                        )
                    ]
                return []

        engine = EventDrivenEngine()
        nav_data = _make_nav_data()

        engine.run(
            start=date(2024, 1, 2),
            end=date(2024, 1, 5),
            strategy=CashTrackerStrategy(),
            nav_data=nav_data,
            initial_capital=Decimal("100000"),
            fund_meta={"000001": FundMeta(code="000001", fund_type="stock")},
        )

        # Day 1 (1/2): 下单前全部现金可用
        assert cash_snapshots[0] == Decimal("100000")

        # Day 2 (1/3): 现金已被冻结 60000
        assert cash_snapshots[1] == Decimal("40000")


# ---------------------------------------------------------------------------
# 边界条件测试
# ---------------------------------------------------------------------------


class TestLookaheadEdgeCases:
    """未来函数防护的边界条件测试。"""

    def test_cutoff_date_boundary_exact(self) -> None:
        """cutoff_date 当天的数据可以访问。"""
        ctx = _make_context(
            current_date=date(2024, 1, 4),
            cutoff_date=date(2024, 1, 3),
        )

        # cutoff_date 本身可以访问
        result = ctx.nav("000001", date(2024, 1, 3))
        assert result == Decimal("1.5100")

    def test_one_day_after_cutoff_raises(self) -> None:
        """cutoff_date + 1 天不可访问。"""
        ctx = _make_context(
            current_date=date(2024, 1, 4),
            cutoff_date=date(2024, 1, 3),
        )

        with pytest.raises(LookaheadError):
            ctx.nav("000001", date(2024, 1, 4))

    def test_lookahead_error_message_contains_details(self) -> None:
        """LookaheadError 的错误信息包含有用的调试信息。"""
        ctx = _make_context(
            current_date=date(2024, 1, 4),
            cutoff_date=date(2024, 1, 3),
        )

        with pytest.raises(LookaheadError, match="000001"):
            ctx.nav("000001", date(2024, 1, 5))

    def test_lookahead_error_message_contains_dates(self) -> None:
        """LookaheadError 的错误信息包含日期信息。"""
        ctx = _make_context(
            current_date=date(2024, 1, 4),
            cutoff_date=date(2024, 1, 3),
        )

        with pytest.raises(LookaheadError, match="2024-01-04"):
            ctx.nav("000001", date(2024, 1, 5))

    def test_nav_series_empty_when_no_historical_data(self) -> None:
        """当 cutoff 之前没有数据时，nav_series 返回空。"""
        nav_data: dict[str, dict[date, Decimal]] = {
            "000001": {
                date(2024, 1, 8): Decimal("1.5400"),
                date(2024, 1, 9): Decimal("1.5500"),
            }
        }

        ctx = BarContext(
            current_date=date(2024, 1, 4),
            portfolio=Portfolio(cash=Decimal("100000")),
            nav_history=nav_data,
            _cutoff_date=date(2024, 1, 3),
        )

        series = ctx.nav_series("000001")
        assert series == {}
