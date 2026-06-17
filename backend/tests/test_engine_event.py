"""事件驱动回测引擎核心测试。

使用简单的买入持有策略进行 smoke test，验证：
1. 引擎基本运行流程
2. BarContext 防未来函数
3. 订单 T+1 确认
4. 分红处理
5. 权益曲线记录
6. 进度回调
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.domain.backtest.engine_event import (
    BacktestResult,
    BarContext,
    DividendInfo,
    EquitySnapshot,
    EventDrivenEngine,
    FundMeta,
    LookaheadError,
    NoOpRiskEngine,
)
from app.domain.backtest.fees import FeeTier
from app.domain.backtest.order import OrderIntent


# ---------------------------------------------------------------------------
# Test Strategies
# ---------------------------------------------------------------------------


class BuyAndHoldStrategy:
    """买入持有策略：第一天全仓买入，之后不操作。"""

    def __init__(self, fund_code: str, amount: Decimal) -> None:
        self._fund_code = fund_code
        self._amount = amount
        self._bought = False

    def on_bar(self, context: BarContext) -> list[OrderIntent]:
        if not self._bought:
            self._bought = True
            return [
                OrderIntent(
                    fund_code=self._fund_code,
                    direction="subscribe",
                    amount=self._amount,
                )
            ]
        return []


class SellStrategy:
    """赎回策略：在指定日期赎回指定份额。"""

    def __init__(self, fund_code: str, shares: Decimal, sell_date: date) -> None:
        self._fund_code = fund_code
        self._shares = shares
        self._sell_date = sell_date

    def on_bar(self, context: BarContext) -> list[OrderIntent]:
        if context.date == self._sell_date:
            return [
                OrderIntent(
                    fund_code=self._fund_code,
                    direction="redeem",
                    shares=self._shares,
                )
            ]
        return []


class LookaheadStrategy:
    """试图访问未来数据的策略（用于测试防未来函数）。"""

    def __init__(self, fund_code: str) -> None:
        self._fund_code = fund_code

    def on_bar(self, context: BarContext) -> list[OrderIntent]:
        # 试图访问当天（T 日）的净值 - 应该抛出 LookaheadError
        context.nav(self._fund_code, context.date)
        return []


class DoNothingStrategy:
    """什么都不做的策略。"""

    def on_bar(self, context: BarContext) -> list[OrderIntent]:
        return []


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _build_nav_data() -> dict[str, dict[date, Decimal]]:
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
        }
    }


def _build_fund_meta_no_fee() -> dict[str, FundMeta]:
    """无费率的基金元数据。"""
    return {
        "000001": FundMeta(code="000001", fund_type="stock"),
    }


def _build_fund_meta_with_fee() -> dict[str, FundMeta]:
    """带费率的基金元数据。"""
    return {
        "000001": FundMeta(
            code="000001",
            fund_type="stock",
            subscribe_fee_tiers=[
                FeeTier(
                    min_amount=Decimal("0"),
                    max_amount=None,
                    rate=Decimal("0.015"),
                ),
            ],
            redeem_fee_tiers=[
                FeeTier(
                    min_holding_days=0,
                    max_holding_days=7,
                    rate=Decimal("0.015"),
                ),
                FeeTier(
                    min_holding_days=7,
                    max_holding_days=None,
                    rate=Decimal("0.005"),
                ),
            ],
        ),
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEventDrivenEngineBasic:
    """基本功能测试。"""

    def test_engine_runs_and_returns_result(self) -> None:
        """引擎能正常运行并返回结果。"""
        engine = EventDrivenEngine()
        nav_data = _build_nav_data()
        strategy = DoNothingStrategy()

        result = engine.run(
            start=date(2024, 1, 2),
            end=date(2024, 1, 5),
            strategy=strategy,
            nav_data=nav_data,
            initial_capital=Decimal("100000"),
            fund_meta=_build_fund_meta_no_fee(),
        )

        assert isinstance(result, BacktestResult)
        assert result.initial_capital == Decimal("100000")
        assert result.start_date == date(2024, 1, 2)
        assert result.end_date == date(2024, 1, 5)
        assert len(result.equity_curve) > 0
        assert result.trades == []

    def test_equity_curve_length_matches_trading_days(self) -> None:
        """权益曲线长度等于交易日数。"""
        engine = EventDrivenEngine()
        nav_data = _build_nav_data()
        strategy = DoNothingStrategy()

        result = engine.run(
            start=date(2024, 1, 2),
            end=date(2024, 1, 5),
            strategy=strategy,
            nav_data=nav_data,
            initial_capital=Decimal("100000"),
            fund_meta=_build_fund_meta_no_fee(),
        )

        # 2024-01-02 to 2024-01-05: Tue, Wed, Thu, Fri = 4 trading days
        assert len(result.equity_curve) == 4

    def test_no_trade_equity_stays_constant(self) -> None:
        """不交易时权益保持不变（全部为现金）。"""
        engine = EventDrivenEngine()
        nav_data = _build_nav_data()
        strategy = DoNothingStrategy()

        result = engine.run(
            start=date(2024, 1, 2),
            end=date(2024, 1, 5),
            strategy=strategy,
            nav_data=nav_data,
            initial_capital=Decimal("100000"),
            fund_meta=_build_fund_meta_no_fee(),
        )

        for snapshot in result.equity_curve:
            assert snapshot.equity == Decimal("100000")
            assert snapshot.cash == Decimal("100000")
            assert snapshot.position_value == Decimal("0")


class TestBuyAndHold:
    """买入持有策略测试。"""

    def test_buy_and_hold_no_fee(self) -> None:
        """无费率买入持有：T 日下单，T+1 确认。"""
        engine = EventDrivenEngine()
        nav_data = _build_nav_data()
        strategy = BuyAndHoldStrategy("000001", Decimal("50000"))

        result = engine.run(
            start=date(2024, 1, 2),
            end=date(2024, 1, 12),
            strategy=strategy,
            nav_data=nav_data,
            initial_capital=Decimal("100000"),
            fund_meta=_build_fund_meta_no_fee(),
        )

        # 应该有 1 笔成交
        assert len(result.trades) == 1
        fill = result.trades[0]
        assert fill.direction == "subscribe"
        assert fill.fund_code == "000001"
        assert fill.amount == Decimal("50000")
        # 使用 T 日（2024-01-02）净值 1.5000 确认
        assert fill.nav == Decimal("1.5000")
        # 无费率：shares = 50000 / 1.5 = 33333.33
        expected_shares = (Decimal("50000") / Decimal("1.5000")).quantize(Decimal("0.01"))
        assert fill.shares == expected_shares
        assert fill.fee == Decimal("0")

        # 最终组合应有持仓
        assert "000001" in result.final_portfolio.positions
        assert result.final_portfolio.positions["000001"] == expected_shares
        # 剩余现金 = 100000 - 50000 = 50000
        assert result.final_portfolio.cash == Decimal("50000")

    def test_buy_and_hold_with_fee(self) -> None:
        """带费率买入持有。"""
        engine = EventDrivenEngine()
        nav_data = _build_nav_data()
        strategy = BuyAndHoldStrategy("000001", Decimal("50000"))

        result = engine.run(
            start=date(2024, 1, 2),
            end=date(2024, 1, 12),
            strategy=strategy,
            nav_data=nav_data,
            initial_capital=Decimal("100000"),
            fund_meta=_build_fund_meta_with_fee(),
        )

        assert len(result.trades) == 1
        fill = result.trades[0]
        assert fill.fee > Decimal("0")
        # 外扣法：fee = 50000 * 0.015 / (1 + 0.015) = 739.66
        expected_fee = (Decimal("50000") * Decimal("0.015") / Decimal("1.015")).quantize(
            Decimal("0.01")
        )
        assert fill.fee == expected_fee
        # net_amount = 50000 - fee
        net_amount = Decimal("50000") - expected_fee
        expected_shares = (net_amount / Decimal("1.5000")).quantize(Decimal("0.01"))
        assert fill.shares == expected_shares

    def test_equity_increases_with_nav(self) -> None:
        """净值上涨时权益增加。"""
        engine = EventDrivenEngine()
        nav_data = _build_nav_data()
        # 全仓买入
        strategy = BuyAndHoldStrategy("000001", Decimal("100000"))

        result = engine.run(
            start=date(2024, 1, 2),
            end=date(2024, 1, 12),
            strategy=strategy,
            nav_data=nav_data,
            initial_capital=Decimal("100000"),
            fund_meta=_build_fund_meta_no_fee(),
        )

        # 确认后（T+1 = 1/3），权益应随净值上涨
        # 找到确认后的快照
        confirmed_snapshots = [
            s for s in result.equity_curve if s.trade_date >= date(2024, 1, 3)
        ]
        # 权益应该递增（因为净值在涨）
        for i in range(1, len(confirmed_snapshots)):
            assert confirmed_snapshots[i].equity >= confirmed_snapshots[i - 1].equity


class TestOrderConfirmation:
    """订单确认测试。"""

    def test_order_confirmed_on_t_plus_1(self) -> None:
        """股票型基金 T+1 确认。"""
        engine = EventDrivenEngine()
        nav_data = _build_nav_data()
        strategy = BuyAndHoldStrategy("000001", Decimal("50000"))

        result = engine.run(
            start=date(2024, 1, 2),
            end=date(2024, 1, 5),
            strategy=strategy,
            nav_data=nav_data,
            initial_capital=Decimal("100000"),
            fund_meta=_build_fund_meta_no_fee(),
        )

        # 1/2 下单，1/3 确认
        assert len(result.trades) == 1
        fill = result.trades[0]
        assert fill.confirm_date == date(2024, 1, 3)

    def test_insufficient_cash_order_rejected(self) -> None:
        """现金不足时订单不入队。"""
        engine = EventDrivenEngine()
        nav_data = _build_nav_data()
        # 试图买入超过初始资金的金额
        strategy = BuyAndHoldStrategy("000001", Decimal("200000"))

        result = engine.run(
            start=date(2024, 1, 2),
            end=date(2024, 1, 5),
            strategy=strategy,
            nav_data=nav_data,
            initial_capital=Decimal("100000"),
            fund_meta=_build_fund_meta_no_fee(),
        )

        # 订单应该被拒绝（现金不足）
        assert len(result.trades) == 0


class TestBarContextLookahead:
    """防未来函数测试。"""

    def test_lookahead_raises_error(self) -> None:
        """策略试图访问 T 日数据时抛出 LookaheadError。"""
        engine = EventDrivenEngine()
        nav_data = _build_nav_data()
        strategy = LookaheadStrategy("000001")

        with pytest.raises(LookaheadError):
            engine.run(
                start=date(2024, 1, 2),
                end=date(2024, 1, 5),
                strategy=strategy,
                nav_data=nav_data,
                initial_capital=Decimal("100000"),
                fund_meta=_build_fund_meta_no_fee(),
            )

    def test_bar_context_nav_returns_t_minus_1(self) -> None:
        """BarContext.nav() 默认返回 T-1 日净值。"""
        nav_data = _build_nav_data()
        context = BarContext(
            current_date=date(2024, 1, 3),
            portfolio=Portfolio(cash=Decimal("100000")),
            nav_history=nav_data,
            _cutoff_date=date(2024, 1, 2),
        )

        # T-1 = 2024-01-02, nav = 1.5000
        assert context.nav("000001") == Decimal("1.5000")

    def test_bar_context_nav_series_excludes_future(self) -> None:
        """BarContext.nav_series() 不包含 T 日及之后数据。"""
        nav_data = _build_nav_data()
        context = BarContext(
            current_date=date(2024, 1, 4),
            portfolio=Portfolio(cash=Decimal("100000")),
            nav_history=nav_data,
            _cutoff_date=date(2024, 1, 3),
        )

        series = context.nav_series("000001")
        # 只应包含 1/2 和 1/3 的数据
        assert date(2024, 1, 2) in series
        assert date(2024, 1, 3) in series
        assert date(2024, 1, 4) not in series
        assert date(2024, 1, 5) not in series


class TestDividendProcessing:
    """分红处理测试。"""

    def test_cash_dividend(self) -> None:
        """现金分红增加现金。"""
        engine = EventDrivenEngine()
        nav_data = _build_nav_data()

        # 先买入，然后在 1/8 分红
        class BuyThenDividendStrategy:
            def __init__(self) -> None:
                self._bought = False

            def on_bar(self, context: BarContext) -> list[OrderIntent]:
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

        strategy = BuyThenDividendStrategy()
        dividends = [
            DividendInfo(
                fund_code="000001",
                ex_date=date(2024, 1, 8),
                dividend_per_share=Decimal("0.10"),
                reinvest=False,
            ),
        ]

        result = engine.run(
            start=date(2024, 1, 2),
            end=date(2024, 1, 12),
            strategy=strategy,
            nav_data=nav_data,
            initial_capital=Decimal("100000"),
            fund_meta=_build_fund_meta_no_fee(),
            dividends=dividends,
        )

        # 确认买入后有持仓
        assert len(result.trades) == 1
        shares = result.trades[0].shares

        # 分红后现金应增加 shares * 0.10
        expected_dividend_cash = shares * Decimal("0.10")
        # 最终现金 = 50000（剩余）+ dividend
        assert result.final_portfolio.cash == Decimal("50000") + expected_dividend_cash

    def test_reinvest_dividend(self) -> None:
        """红利再投增加份额。"""
        engine = EventDrivenEngine()
        nav_data = _build_nav_data()

        class BuyFirstDayStrategy:
            def __init__(self) -> None:
                self._bought = False

            def on_bar(self, context: BarContext) -> list[OrderIntent]:
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

        strategy = BuyFirstDayStrategy()
        dividends = [
            DividendInfo(
                fund_code="000001",
                ex_date=date(2024, 1, 8),
                dividend_per_share=Decimal("0.10"),
                reinvest=True,
            ),
        ]

        result = engine.run(
            start=date(2024, 1, 2),
            end=date(2024, 1, 12),
            strategy=strategy,
            nav_data=nav_data,
            initial_capital=Decimal("100000"),
            fund_meta=_build_fund_meta_no_fee(),
            dividends=dividends,
        )

        # 确认买入后的份额
        original_shares = result.trades[0].shares
        # 红利再投后份额应增加
        # additional = original_shares * 0.10 / nav_on_1_8(1.5400)
        additional_shares = original_shares * Decimal("0.10") / Decimal("1.5400")
        expected_total = original_shares + additional_shares

        assert result.final_portfolio.positions["000001"] == expected_total
        # 现金不变（红利再投不增加现金）
        assert result.final_portfolio.cash == Decimal("50000")


class TestProgressCallback:
    """进度回调测试。"""

    def test_progress_callback_called(self) -> None:
        """进度回调被正确调用。"""
        engine = EventDrivenEngine()
        nav_data = _build_nav_data()
        strategy = DoNothingStrategy()

        progress_calls: list[tuple[int, int, date]] = []

        def on_progress(current: int, total: int, d: date) -> None:
            progress_calls.append((current, total, d))

        result = engine.run(
            start=date(2024, 1, 2),
            end=date(2024, 1, 5),
            strategy=strategy,
            nav_data=nav_data,
            initial_capital=Decimal("100000"),
            fund_meta=_build_fund_meta_no_fee(),
            progress_callback=on_progress,
        )

        # 4 个交易日，应该调用 4 次
        assert len(progress_calls) == 4
        assert progress_calls[0] == (1, 4, date(2024, 1, 2))
        assert progress_calls[-1] == (4, 4, date(2024, 1, 5))


class TestRedemption:
    """赎回测试。"""

    def test_redeem_after_buy(self) -> None:
        """买入后赎回。"""
        engine = EventDrivenEngine()
        nav_data = _build_nav_data()

        class BuyThenSellStrategy:
            """先买后卖策略。"""

            def __init__(self) -> None:
                self._state = "buy"

            def on_bar(self, context: BarContext) -> list[OrderIntent]:
                if self._state == "buy":
                    self._state = "wait"
                    return [
                        OrderIntent(
                            fund_code="000001",
                            direction="subscribe",
                            amount=Decimal("50000"),
                        )
                    ]
                # 在 1/8 赎回全部
                if context.date == date(2024, 1, 8):
                    shares = context.positions.get("000001", Decimal("0"))
                    if shares > Decimal("0"):
                        return [
                            OrderIntent(
                                fund_code="000001",
                                direction="redeem",
                                shares=shares,
                            )
                        ]
                return []

        strategy = BuyThenSellStrategy()

        result = engine.run(
            start=date(2024, 1, 2),
            end=date(2024, 1, 12),
            strategy=strategy,
            nav_data=nav_data,
            initial_capital=Decimal("100000"),
            fund_meta=_build_fund_meta_no_fee(),
        )

        # 应该有 2 笔成交：1 笔申购 + 1 笔赎回
        assert len(result.trades) == 2
        assert result.trades[0].direction == "subscribe"
        assert result.trades[1].direction == "redeem"

        # 赎回后应无持仓
        assert "000001" not in result.final_portfolio.positions


# Need to import Portfolio for BarContext tests
from app.domain.backtest.portfolio import Portfolio
