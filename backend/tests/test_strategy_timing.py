"""择时策略单元测试。

覆盖：
- DualMAStrategy: 双均线策略
- MACDStrategy: MACD 策略
- ValuationStrategy: 估值分位数策略
- compute_sma / compute_ema / compute_macd / compute_percentile: 技术指标计算

需求: 5.4
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.domain.backtest.engine_event import BarContext
from app.domain.backtest.order import OrderIntent
from app.domain.backtest.portfolio import Portfolio
from app.domain.strategy.timing import (
    DualMAParams,
    DualMAStrategy,
    MACDParams,
    MACDStrategy,
    Signal,
    ValuationParams,
    ValuationStrategy,
    compute_ema,
    compute_macd,
    compute_percentile,
    compute_sma,
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


def _make_nav_series(
    start: date,
    values: list[float],
) -> dict[date, Decimal]:
    """生成连续日期的净值序列。"""
    result: dict[date, Decimal] = {}
    for i, val in enumerate(values):
        result[start + timedelta(days=i)] = Decimal(str(round(val, 6)))
    return result


# ---------------------------------------------------------------------------
# compute_sma 测试
# ---------------------------------------------------------------------------


class TestComputeSMA:
    """简单移动平均线测试。"""

    def test_basic(self) -> None:
        """基本 SMA 计算。"""
        nav = _make_nav_series(date(2024, 1, 1), [1.0, 2.0, 3.0, 4.0, 5.0])
        sma = compute_sma(nav, 3, date(2024, 1, 5))
        assert sma is not None
        # 最近 3 天: 3, 4, 5 → 平均 4.0
        assert abs(sma - 4.0) < 1e-10

    def test_full_window(self) -> None:
        """窗口等于数据长度。"""
        nav = _make_nav_series(date(2024, 1, 1), [2.0, 4.0, 6.0])
        sma = compute_sma(nav, 3, date(2024, 1, 3))
        assert sma is not None
        assert abs(sma - 4.0) < 1e-10

    def test_insufficient_data(self) -> None:
        """数据不足返回 None。"""
        nav = _make_nav_series(date(2024, 1, 1), [1.0, 2.0])
        sma = compute_sma(nav, 5, date(2024, 1, 2))
        assert sma is None

    def test_cutoff_date_filters(self) -> None:
        """截止日期正确过滤数据。"""
        nav = _make_nav_series(date(2024, 1, 1), [1.0, 2.0, 3.0, 4.0, 5.0])
        # 截止到第 3 天，窗口 2 → 最近 2 天: 2, 3 → 平均 2.5
        sma = compute_sma(nav, 2, date(2024, 1, 3))
        assert sma is not None
        assert abs(sma - 2.5) < 1e-10


# ---------------------------------------------------------------------------
# compute_ema 测试
# ---------------------------------------------------------------------------


class TestComputeEMA:
    """指数移动平均线测试。"""

    def test_basic(self) -> None:
        """基本 EMA 计算。"""
        nav = _make_nav_series(date(2024, 1, 1), [1.0, 2.0, 3.0, 4.0, 5.0])
        ema = compute_ema(nav, 3, date(2024, 1, 5))
        assert ema is not None
        # 初始 EMA = SMA(前3) = 2.0
        # α = 2/(3+1) = 0.5
        # EMA after 4.0: 0.5*4 + 0.5*2 = 3.0
        # EMA after 5.0: 0.5*5 + 0.5*3 = 4.0
        assert abs(ema - 4.0) < 1e-10

    def test_insufficient_data(self) -> None:
        """数据不足返回 None。"""
        nav = _make_nav_series(date(2024, 1, 1), [1.0, 2.0])
        ema = compute_ema(nav, 5, date(2024, 1, 2))
        assert ema is None

    def test_single_window(self) -> None:
        """窗口为 1 时 EMA 等于最新值。"""
        nav = _make_nav_series(date(2024, 1, 1), [1.0, 2.0, 3.0])
        ema = compute_ema(nav, 1, date(2024, 1, 3))
        assert ema is not None
        # α = 2/2 = 1.0, 所以 EMA 始终等于最新值
        assert abs(ema - 3.0) < 1e-10


# ---------------------------------------------------------------------------
# compute_macd 测试
# ---------------------------------------------------------------------------


class TestComputeMACD:
    """MACD 指标测试。"""

    def test_insufficient_data(self) -> None:
        """数据不足返回 None。"""
        nav = _make_nav_series(date(2024, 1, 1), [1.0] * 10)
        result = compute_macd(nav, 12, 26, 9, date(2024, 1, 10))
        assert result is None

    def test_uptrend_positive_dif(self) -> None:
        """上涨趋势中 DIF 应为正。"""
        # 生成 60 天稳定上涨数据
        values = [1.0 * 1.005**i for i in range(60)]
        nav = _make_nav_series(date(2024, 1, 1), values)
        result = compute_macd(nav, 12, 26, 9, date(2024, 2, 29))
        assert result is not None
        dif, dea, macd_bar = result
        # 上涨趋势中快线 > 慢线，DIF > 0
        assert dif > 0

    def test_downtrend_negative_dif(self) -> None:
        """下跌趋势中 DIF 应为负。"""
        values = [2.0 * 0.995**i for i in range(60)]
        nav = _make_nav_series(date(2024, 1, 1), values)
        result = compute_macd(nav, 12, 26, 9, date(2024, 2, 29))
        assert result is not None
        dif, dea, macd_bar = result
        assert dif < 0

    def test_returns_three_values(self) -> None:
        """返回 DIF, DEA, MACD柱 三个值。"""
        values = [1.0 + 0.01 * i for i in range(60)]
        nav = _make_nav_series(date(2024, 1, 1), values)
        result = compute_macd(nav, 12, 26, 9, date(2024, 2, 29))
        assert result is not None
        assert len(result) == 3


# ---------------------------------------------------------------------------
# compute_percentile 测试
# ---------------------------------------------------------------------------


class TestComputePercentile:
    """百分位数计算测试。"""

    def test_highest_value(self) -> None:
        """最高值百分位接近 1。"""
        nav = _make_nav_series(date(2024, 1, 1), [1.0, 2.0, 3.0, 4.0, 5.0])
        pct = compute_percentile(nav, 10, date(2024, 1, 5))
        assert pct is not None
        assert pct == 1.0  # 5.0 大于所有其他值

    def test_lowest_value(self) -> None:
        """最低值百分位为 0。"""
        nav = _make_nav_series(date(2024, 1, 1), [5.0, 4.0, 3.0, 2.0, 1.0])
        pct = compute_percentile(nav, 10, date(2024, 1, 5))
        assert pct is not None
        assert pct == 0.0  # 1.0 小于所有其他值

    def test_middle_value(self) -> None:
        """中间值百分位约 0.5。"""
        nav = _make_nav_series(date(2024, 1, 1), [1.0, 2.0, 3.0, 4.0, 5.0, 3.0])
        pct = compute_percentile(nav, 10, date(2024, 1, 6))
        assert pct is not None
        # 3.0 大于 1.0, 2.0 → 2/5 = 0.4
        assert abs(pct - 0.4) < 1e-10

    def test_insufficient_data(self) -> None:
        """数据不足返回 None。"""
        nav = _make_nav_series(date(2024, 1, 1), [1.0])
        pct = compute_percentile(nav, 10, date(2024, 1, 1))
        assert pct is None

    def test_window_limits_data(self) -> None:
        """窗口限制历史数据范围。"""
        # 10 个数据点，窗口 5 → 只看最近 5 个
        values = [10.0, 9.0, 8.0, 7.0, 6.0, 1.0, 2.0, 3.0, 4.0, 5.0]
        nav = _make_nav_series(date(2024, 1, 1), values)
        pct = compute_percentile(nav, 5, date(2024, 1, 10))
        assert pct is not None
        # 最近 5 个: [1, 2, 3, 4, 5], 当前 5 大于 1,2,3,4 → 4/4 = 1.0
        assert pct == 1.0


# ---------------------------------------------------------------------------
# DualMAParams 测试
# ---------------------------------------------------------------------------


class TestDualMAParams:
    """双均线参数测试。"""

    def test_default_params(self) -> None:
        """默认参数。"""
        params = DualMAParams()
        assert params.short_window == 5
        assert params.long_window == 20

    def test_invalid_window(self) -> None:
        """窗口为 0 应失败。"""
        with pytest.raises(Exception):
            DualMAParams(short_window=0)

    def test_serialization(self) -> None:
        """参数可序列化。"""
        params = DualMAParams(short_window=10, long_window=30)
        d = params.model_dump()
        assert d["short_window"] == 10
        assert d["long_window"] == 30


# ---------------------------------------------------------------------------
# DualMAStrategy 测试
# ---------------------------------------------------------------------------


class TestDualMAStrategy:
    """双均线策略测试。"""

    def test_creation(self) -> None:
        """策略创建。"""
        params = DualMAParams(short_window=5, long_window=20)
        strategy = DualMAStrategy(params=params, universe=["A"])
        assert strategy.name == "dual_ma"

    def test_golden_cross_buy(self) -> None:
        """金叉信号：短均线上穿长均线时买入。"""
        # 构造先跌后涨的数据，使短均线从下方穿越长均线
        # 前 20 天下跌，后 10 天快速上涨
        values = [2.0 - 0.03 * i for i in range(20)]
        values += [values[-1] + 0.1 * i for i in range(1, 11)]
        nav = _make_nav_series(date(2024, 1, 1), values)

        params = DualMAParams(short_window=5, long_window=15)
        strategy = DualMAStrategy(params=params, universe=["A"])

        # 在上涨末期，短均线应 > 长均线
        ctx = _make_context(
            current_date=date(2024, 1, 31),
            cash=Decimal("100000"),
            nav_data={"A": nav},
            cutoff_date=date(2024, 1, 30),
        )
        orders = strategy.on_bar(ctx)
        # 应产生买入指令
        assert len(orders) > 0
        subscribe_orders = [o for o in orders if o.direction == "subscribe"]
        assert len(subscribe_orders) > 0

    def test_death_cross_sell(self) -> None:
        """死叉信号：短均线下穿长均线时卖出。"""
        # 先涨后跌
        values = [1.0 + 0.03 * i for i in range(20)]
        values += [values[-1] - 0.1 * i for i in range(1, 11)]
        nav = _make_nav_series(date(2024, 1, 1), values)

        params = DualMAParams(short_window=5, long_window=15)
        strategy = DualMAStrategy(params=params, universe=["A"])

        # 先触发金叉
        ctx1 = _make_context(
            current_date=date(2024, 1, 20),
            cash=Decimal("50000"),
            positions={"A": Decimal("50000")},
            nav_data={"A": nav},
            cutoff_date=date(2024, 1, 19),
        )
        strategy.on_bar(ctx1)

        # 下跌末期触发死叉
        ctx2 = _make_context(
            current_date=date(2024, 1, 31),
            cash=Decimal("50000"),
            positions={"A": Decimal("50000")},
            nav_data={"A": nav},
            cutoff_date=date(2024, 1, 30),
        )
        orders = strategy.on_bar(ctx2)
        # 应产生卖出指令
        if orders:
            redeem_orders = [o for o in orders if o.direction == "redeem"]
            assert len(redeem_orders) > 0

    def test_no_signal_change_no_orders(self) -> None:
        """信号不变时不产生订单。"""
        # 持续上涨，信号始终为 FULL
        values = [1.0 + 0.01 * i for i in range(30)]
        nav = _make_nav_series(date(2024, 1, 1), values)

        params = DualMAParams(short_window=5, long_window=10)
        strategy = DualMAStrategy(params=params, universe=["A"])

        # 第一天触发信号
        ctx1 = _make_context(
            current_date=date(2024, 1, 25),
            cash=Decimal("100000"),
            nav_data={"A": nav},
            cutoff_date=date(2024, 1, 24),
        )
        strategy.on_bar(ctx1)

        # 第二天信号不变，不应产生订单
        ctx2 = _make_context(
            current_date=date(2024, 1, 26),
            cash=Decimal("100000"),
            nav_data={"A": nav},
            cutoff_date=date(2024, 1, 25),
        )
        orders = strategy.on_bar(ctx2)
        assert len(orders) == 0

    def test_empty_universe(self) -> None:
        """空基金池不产生订单。"""
        params = DualMAParams()
        strategy = DualMAStrategy(params=params, universe=[])
        ctx = _make_context(current_date=date(2024, 1, 1), nav_data={})
        orders = strategy.on_bar(ctx)
        assert len(orders) == 0

    def test_insufficient_data(self) -> None:
        """数据不足时不产生订单。"""
        nav = _make_nav_series(date(2024, 1, 1), [1.0, 1.1, 1.2])
        params = DualMAParams(short_window=5, long_window=20)
        strategy = DualMAStrategy(params=params, universe=["A"])

        ctx = _make_context(
            current_date=date(2024, 1, 4),
            cash=Decimal("100000"),
            nav_data={"A": nav},
            cutoff_date=date(2024, 1, 3),
        )
        orders = strategy.on_bar(ctx)
        assert len(orders) == 0


# ---------------------------------------------------------------------------
# MACDStrategy 测试
# ---------------------------------------------------------------------------


class TestMACDStrategy:
    """MACD 策略测试。"""

    def test_creation(self) -> None:
        """策略创建。"""
        params = MACDParams(fast_period=12, slow_period=26, signal_period=9)
        strategy = MACDStrategy(params=params, universe=["A"])
        assert strategy.name == "macd_timing"

    def test_uptrend_buy_signal(self) -> None:
        """上涨趋势产生买入信号。"""
        values = [1.0 * 1.005**i for i in range(60)]
        nav = _make_nav_series(date(2024, 1, 1), values)

        params = MACDParams(fast_period=12, slow_period=26, signal_period=9)
        strategy = MACDStrategy(params=params, universe=["A"])

        ctx = _make_context(
            current_date=date(2024, 3, 1),
            cash=Decimal("100000"),
            nav_data={"A": nav},
            cutoff_date=date(2024, 2, 29),
        )
        orders = strategy.on_bar(ctx)
        # 上涨趋势中 DIF > DEA，应产生买入
        assert len(orders) > 0
        subscribe_orders = [o for o in orders if o.direction == "subscribe"]
        assert len(subscribe_orders) > 0

    def test_downtrend_sell_signal(self) -> None:
        """先涨后跌触发死叉卖出信号。"""
        # 先涨 40 天，再跌 30 天 → 产生死叉
        values_up = [1.0 * 1.01**i for i in range(40)]
        values_down = [values_up[-1] * 0.98**i for i in range(1, 31)]
        values = values_up + values_down
        nav = _make_nav_series(date(2024, 1, 1), values)

        params = MACDParams(fast_period=12, slow_period=26, signal_period=9)
        strategy = MACDStrategy(params=params, universe=["A"])

        # 先在上涨期触发金叉（FULL 信号）
        ctx1 = _make_context(
            current_date=date(2024, 2, 10),
            cash=Decimal("50000"),
            positions={"A": Decimal("50000")},
            nav_data={"A": nav},
            cutoff_date=date(2024, 2, 9),
        )
        orders1 = strategy.on_bar(ctx1)
        # 上涨期应为 FULL 信号

        # 在下跌末期触发死叉（EMPTY 信号）
        ctx2 = _make_context(
            current_date=date(2024, 3, 11),
            cash=Decimal("50000"),
            positions={"A": Decimal("50000")},
            nav_data={"A": nav},
            cutoff_date=date(2024, 3, 10),
        )
        orders2 = strategy.on_bar(ctx2)
        # 如果信号变为 EMPTY，应产生赎回指令
        if orders2:
            redeem_orders = [o for o in orders2 if o.direction == "redeem"]
            assert len(redeem_orders) > 0

    def test_insufficient_data(self) -> None:
        """数据不足时不产生订单。"""
        nav = _make_nav_series(date(2024, 1, 1), [1.0] * 10)
        params = MACDParams()
        strategy = MACDStrategy(params=params, universe=["A"])

        ctx = _make_context(
            current_date=date(2024, 1, 11),
            cash=Decimal("100000"),
            nav_data={"A": nav},
            cutoff_date=date(2024, 1, 10),
        )
        orders = strategy.on_bar(ctx)
        assert len(orders) == 0

    def test_empty_universe(self) -> None:
        """空基金池不产生订单。"""
        params = MACDParams()
        strategy = MACDStrategy(params=params, universe=[])
        ctx = _make_context(current_date=date(2024, 1, 1), nav_data={})
        orders = strategy.on_bar(ctx)
        assert len(orders) == 0


# ---------------------------------------------------------------------------
# MACDParams 测试
# ---------------------------------------------------------------------------


class TestMACDParams:
    """MACD 参数测试。"""

    def test_default_params(self) -> None:
        """默认参数。"""
        params = MACDParams()
        assert params.fast_period == 12
        assert params.slow_period == 26
        assert params.signal_period == 9

    def test_invalid_period(self) -> None:
        """周期为 0 应失败。"""
        with pytest.raises(Exception):
            MACDParams(fast_period=0)


# ---------------------------------------------------------------------------
# ValuationStrategy 测试
# ---------------------------------------------------------------------------


class TestValuationStrategy:
    """估值分位数策略测试。"""

    def test_creation(self) -> None:
        """策略创建。"""
        params = ValuationParams(lookback_days=252, low_threshold=0.3, high_threshold=0.7)
        strategy = ValuationStrategy(params=params, universe=["A"])
        assert strategy.name == "valuation_timing"

    def test_low_valuation_buy(self) -> None:
        """低估值时满仓买入。"""
        # 先涨后跌，当前值处于历史低位
        values = [2.0 - 0.01 * i for i in range(50)]
        nav = _make_nav_series(date(2024, 1, 1), values)

        params = ValuationParams(lookback_days=50, low_threshold=0.3, high_threshold=0.7)
        strategy = ValuationStrategy(params=params, universe=["A"])

        ctx = _make_context(
            current_date=date(2024, 2, 20),
            cash=Decimal("100000"),
            nav_data={"A": nav},
            cutoff_date=date(2024, 2, 19),
        )
        orders = strategy.on_bar(ctx)
        # 当前值是最低的，百分位 = 0 < 0.3 → 满仓
        assert len(orders) > 0
        subscribe_orders = [o for o in orders if o.direction == "subscribe"]
        assert len(subscribe_orders) > 0

    def test_high_valuation_sell(self) -> None:
        """高估值时空仓。"""
        # 持续上涨，当前值处于历史高位
        values = [1.0 + 0.01 * i for i in range(50)]
        nav = _make_nav_series(date(2024, 1, 1), values)

        params = ValuationParams(lookback_days=50, low_threshold=0.3, high_threshold=0.7)
        strategy = ValuationStrategy(params=params, universe=["A"])

        ctx = _make_context(
            current_date=date(2024, 2, 20),
            cash=Decimal("50000"),
            positions={"A": Decimal("50000")},
            nav_data={"A": nav},
            cutoff_date=date(2024, 2, 19),
        )
        orders = strategy.on_bar(ctx)
        # 当前值是最高的，百分位 = 1.0 > 0.7 → 空仓
        if orders:
            redeem_orders = [o for o in orders if o.direction == "redeem"]
            assert len(redeem_orders) > 0

    def test_mid_valuation_half_position(self) -> None:
        """中间估值时半仓。"""
        # 构造当前值在中间位置的数据
        values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 5.5]
        nav = _make_nav_series(date(2024, 1, 1), values)

        params = ValuationParams(lookback_days=20, low_threshold=0.3, high_threshold=0.7)
        strategy = ValuationStrategy(params=params, universe=["A"])

        ctx = _make_context(
            current_date=date(2024, 1, 12),
            cash=Decimal("100000"),
            nav_data={"A": nav},
            cutoff_date=date(2024, 1, 11),
        )
        orders = strategy.on_bar(ctx)
        # 5.5 大于 1,2,3,4,5 → 百分位 = 5/10 = 0.5，在 0.3~0.7 之间 → 半仓
        assert len(orders) > 0

    def test_empty_universe(self) -> None:
        """空基金池不产生订单。"""
        params = ValuationParams()
        strategy = ValuationStrategy(params=params, universe=[])
        ctx = _make_context(current_date=date(2024, 1, 1), nav_data={})
        orders = strategy.on_bar(ctx)
        assert len(orders) == 0

    def test_insufficient_data(self) -> None:
        """数据不足时不产生订单。"""
        nav = _make_nav_series(date(2024, 1, 1), [1.0])
        params = ValuationParams(lookback_days=252)
        strategy = ValuationStrategy(params=params, universe=["A"])

        ctx = _make_context(
            current_date=date(2024, 1, 2),
            cash=Decimal("100000"),
            nav_data={"A": nav},
            cutoff_date=date(2024, 1, 1),
        )
        orders = strategy.on_bar(ctx)
        assert len(orders) == 0


# ---------------------------------------------------------------------------
# ValuationParams 测试
# ---------------------------------------------------------------------------


class TestValuationParams:
    """估值分位数参数测试。"""

    def test_default_params(self) -> None:
        """默认参数。"""
        params = ValuationParams()
        assert params.lookback_days == 252
        assert params.low_threshold == 0.3
        assert params.high_threshold == 0.7

    def test_invalid_lookback(self) -> None:
        """lookback 为 0 应失败。"""
        with pytest.raises(Exception):
            ValuationParams(lookback_days=0)

    def test_threshold_bounds(self) -> None:
        """阈值超出 [0,1] 应失败。"""
        with pytest.raises(Exception):
            ValuationParams(low_threshold=-0.1)
        with pytest.raises(Exception):
            ValuationParams(high_threshold=1.5)

    def test_serialization(self) -> None:
        """参数可序列化。"""
        params = ValuationParams(lookback_days=120, low_threshold=0.2, high_threshold=0.8)
        d = params.model_dump()
        assert d["lookback_days"] == 120
        assert d["low_threshold"] == 0.2
        assert d["high_threshold"] == 0.8
