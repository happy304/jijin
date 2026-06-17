"""择时策略模块。

实现基于技术指标的择时策略：
- DualMAStrategy: 双均线策略（短期均线上穿/下穿长期均线）
- MACDStrategy: MACD 策略（DIF 上穿/下穿 DEA）
- ValuationStrategy: 估值分位数策略（低估加仓、高估减仓）

设计要点：
- 继承 BaseStrategy，实现 on_bar 方法
- 信号生成与仓位管理分离
- 支持全仓/半仓/空仓三档仓位
- 在信号变化时通过 rebalance_to 生成调仓指令
- 无信号变化时保持持仓不变

需求: 5.4
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import Field

from app.domain.backtest.engine_event import BarContext
from app.domain.backtest.order import OrderIntent
from app.domain.strategy.base import BaseStrategy, StrategyParams, rebalance_to


# ---------------------------------------------------------------------------
# 仓位信号枚举
# ---------------------------------------------------------------------------


class Signal(str, Enum):
    """择时信号。"""

    FULL = "full"  # 满仓
    HALF = "half"  # 半仓
    EMPTY = "empty"  # 空仓


# ---------------------------------------------------------------------------
# 技术指标计算辅助函数
# ---------------------------------------------------------------------------


def compute_sma(
    nav_series: dict[date, Decimal],
    window: int,
    cutoff_date: date,
) -> float | None:
    """计算简单移动平均线。

    Args:
        nav_series: 净值序列 {date: nav}
        window: 均线窗口天数
        cutoff_date: 截止日期

    Returns:
        均线值，数据不足返回 None
    """
    sorted_dates = sorted(d for d in nav_series.keys() if d <= cutoff_date)
    if len(sorted_dates) < window:
        return None

    recent = sorted_dates[-window:]
    values = [float(nav_series[d]) for d in recent]
    return sum(values) / len(values)


def compute_ema(
    nav_series: dict[date, Decimal],
    window: int,
    cutoff_date: date,
) -> float | None:
    """计算指数移动平均线。

    EMA_t = α × P_t + (1-α) × EMA_{t-1}
    α = 2 / (window + 1)

    Args:
        nav_series: 净值序列 {date: nav}
        window: EMA 窗口天数
        cutoff_date: 截止日期

    Returns:
        EMA 值，数据不足返回 None
    """
    sorted_dates = sorted(d for d in nav_series.keys() if d <= cutoff_date)
    if len(sorted_dates) < window:
        return None

    alpha = 2.0 / (window + 1)
    values = [float(nav_series[d]) for d in sorted_dates]

    # 使用前 window 个数据的 SMA 作为初始 EMA
    ema = sum(values[:window]) / window
    for v in values[window:]:
        ema = alpha * v + (1 - alpha) * ema

    return ema


def compute_macd(
    nav_series: dict[date, Decimal],
    fast: int,
    slow: int,
    signal: int,
    cutoff_date: date,
) -> tuple[float, float, float] | None:
    """计算 MACD 指标。

    DIF = EMA(fast) - EMA(slow)
    DEA = EMA(DIF, signal)
    MACD柱 = 2 × (DIF - DEA)

    Args:
        nav_series: 净值序列
        fast: 快线周期（默认 12）
        slow: 慢线周期（默认 26）
        signal: 信号线周期（默认 9）
        cutoff_date: 截止日期

    Returns:
        (DIF, DEA, MACD柱) 元组，数据不足返回 None
    """
    sorted_dates = sorted(d for d in nav_series.keys() if d <= cutoff_date)
    # 需要至少 slow + signal 个数据点
    min_required = slow + signal
    if len(sorted_dates) < min_required:
        return None

    values = [float(nav_series[d]) for d in sorted_dates]

    # 计算快线 EMA
    alpha_fast = 2.0 / (fast + 1)
    ema_fast = sum(values[:fast]) / fast
    fast_series: list[float] = []
    for v in values[fast:]:
        ema_fast = alpha_fast * v + (1 - alpha_fast) * ema_fast
        fast_series.append(ema_fast)

    # 计算慢线 EMA
    alpha_slow = 2.0 / (slow + 1)
    ema_slow = sum(values[:slow]) / slow
    slow_series: list[float] = []
    for v in values[slow:]:
        ema_slow = alpha_slow * v + (1 - alpha_slow) * ema_slow
        slow_series.append(ema_slow)

    # 对齐 DIF 序列（从 slow 开始有效）
    # fast_series 从 index=fast 开始，slow_series 从 index=slow 开始
    # DIF 从 index=slow 开始有效
    offset = slow - fast
    if offset > len(fast_series):
        return None

    dif_series: list[float] = []
    for i in range(len(slow_series)):
        fast_idx = i + offset
        if fast_idx < len(fast_series):
            dif_series.append(fast_series[fast_idx] - slow_series[i])

    if len(dif_series) < signal:
        return None

    # 计算 DEA（DIF 的 EMA）
    alpha_signal = 2.0 / (signal + 1)
    dea = sum(dif_series[:signal]) / signal
    for d in dif_series[signal:]:
        dea = alpha_signal * d + (1 - alpha_signal) * dea

    dif = dif_series[-1]
    macd_bar = 2.0 * (dif - dea)

    return dif, dea, macd_bar


def compute_percentile(
    nav_series: dict[date, Decimal],
    window: int,
    cutoff_date: date,
) -> float | None:
    """计算当前净值在历史窗口中的百分位数。

    用于估值分位数策略：当前值在过去 window 天中的排名百分比。

    Args:
        nav_series: 净值序列
        window: 历史窗口天数
        cutoff_date: 截止日期

    Returns:
        百分位数（0~1），数据不足返回 None
    """
    sorted_dates = sorted(d for d in nav_series.keys() if d <= cutoff_date)
    if len(sorted_dates) < 2:
        return None

    # 取最近 window 天
    if len(sorted_dates) > window:
        recent_dates = sorted_dates[-window:]
    else:
        recent_dates = sorted_dates

    values = [float(nav_series[d]) for d in recent_dates]
    current = values[-1]

    # 计算百分位：小于当前值的比例
    count_below = sum(1 for v in values if v < current)
    percentile = count_below / (len(values) - 1) if len(values) > 1 else 0.5

    return percentile


# ---------------------------------------------------------------------------
# 双均线策略参数
# ---------------------------------------------------------------------------


class DualMAParams(StrategyParams):
    """双均线策略参数。

    Attributes:
        short_window: 短期均线窗口（天）
        long_window: 长期均线窗口（天）
        fund_code: 跟踪的基金代码（基金池中第一只，或指定）
    """

    short_window: int = Field(default=5, gt=0, description="短期均线窗口")
    long_window: int = Field(default=20, gt=0, description="长期均线窗口")


# ---------------------------------------------------------------------------
# MACD 策略参数
# ---------------------------------------------------------------------------


class MACDParams(StrategyParams):
    """MACD 策略参数。

    Attributes:
        fast_period: 快线周期
        slow_period: 慢线周期
        signal_period: 信号线周期
    """

    fast_period: int = Field(default=12, gt=0, description="快线周期")
    slow_period: int = Field(default=26, gt=0, description="慢线周期")
    signal_period: int = Field(default=9, gt=0, description="信号线周期")


# ---------------------------------------------------------------------------
# 估值分位数策略参数
# ---------------------------------------------------------------------------


class ValuationParams(StrategyParams):
    """估值分位数策略参数。

    Attributes:
        lookback_days: 历史窗口天数（用于计算百分位）
        low_threshold: 低估阈值（百分位 <= 此值时满仓）
        high_threshold: 高估阈值（百分位 >= 此值时空仓）
    """

    lookback_days: int = Field(default=252, gt=0, description="历史窗口天数")
    low_threshold: float = Field(default=0.3, ge=0, le=1, description="低估阈值")
    high_threshold: float = Field(default=0.7, ge=0, le=1, description="高估阈值")


# ---------------------------------------------------------------------------
# 双均线策略
# ---------------------------------------------------------------------------


class DualMAStrategy(BaseStrategy):
    """双均线择时策略。

    当短期均线上穿长期均线时满仓买入（金叉），
    当短期均线下穿长期均线时清仓卖出（死叉）。

    信号逻辑：
    - 短均线 > 长均线 → 满仓
    - 短均线 <= 长均线 → 空仓

    对基金池中所有基金等权配置（满仓时），或全部清仓（空仓时）。

    Example::

        strategy = DualMAStrategy(
            params=DualMAParams(short_window=5, long_window=20),
            universe=["000001"],
        )
    """

    name = "dual_ma"

    def __init__(
        self,
        params: DualMAParams | None = None,
        universe: list[str] | None = None,
    ) -> None:
        super().__init__(params=params, universe=universe)
        self._last_signal: Signal | None = None

    @property
    def ma_params(self) -> DualMAParams:
        """获取类型化的参数。"""
        assert isinstance(self.params, DualMAParams)
        return self.params

    def on_bar(self, context: BarContext) -> list[OrderIntent]:
        """每个交易日计算均线信号。

        仅在信号变化时产生调仓指令。

        Args:
            context: 当日策略上下文

        Returns:
            OrderIntent 列表
        """
        if not self.universe:
            return []

        # 使用第一只基金的净值计算均线
        target_code = self.universe[0]
        nav_series = context.nav_series(target_code)

        if not nav_series:
            return []

        # 计算短期和长期均线
        short_ma = compute_sma(nav_series, self.ma_params.short_window, context._cutoff_date)
        long_ma = compute_sma(nav_series, self.ma_params.long_window, context._cutoff_date)

        if short_ma is None or long_ma is None:
            return []

        # 生成信号
        if short_ma > long_ma:
            current_signal = Signal.FULL
        else:
            current_signal = Signal.EMPTY

        # 仅在信号变化时调仓
        if current_signal == self._last_signal:
            return []

        self._last_signal = current_signal

        # 生成目标权重
        if current_signal == Signal.FULL:
            weight = 1.0 / len(self.universe)
            target_weights = {code: weight for code in self.universe}
        else:
            # 空仓：所有基金权重为 0
            target_weights = {code: 0.0 for code in self.universe}

        return rebalance_to(context, target_weights)


# ---------------------------------------------------------------------------
# MACD 策略
# ---------------------------------------------------------------------------


class MACDStrategy(BaseStrategy):
    """MACD 择时策略。

    基于 MACD 指标的金叉/死叉信号进行择时：
    - DIF 上穿 DEA（金叉）→ 满仓
    - DIF 下穿 DEA（死叉）→ 空仓

    Example::

        strategy = MACDStrategy(
            params=MACDParams(fast_period=12, slow_period=26, signal_period=9),
            universe=["000001"],
        )
    """

    name = "macd_timing"

    def __init__(
        self,
        params: MACDParams | None = None,
        universe: list[str] | None = None,
    ) -> None:
        super().__init__(params=params, universe=universe)
        self._last_signal: Signal | None = None

    @property
    def macd_params(self) -> MACDParams:
        """获取类型化的参数。"""
        assert isinstance(self.params, MACDParams)
        return self.params

    def on_bar(self, context: BarContext) -> list[OrderIntent]:
        """每个交易日计算 MACD 信号。

        Args:
            context: 当日策略上下文

        Returns:
            OrderIntent 列表
        """
        if not self.universe:
            return []

        target_code = self.universe[0]
        nav_series = context.nav_series(target_code)

        if not nav_series:
            return []

        # 计算 MACD
        result = compute_macd(
            nav_series,
            self.macd_params.fast_period,
            self.macd_params.slow_period,
            self.macd_params.signal_period,
            context._cutoff_date,
        )

        if result is None:
            return []

        dif, dea, macd_bar = result

        # 生成信号：DIF > DEA 为金叉（满仓），DIF <= DEA 为死叉（空仓）
        if dif > dea:
            current_signal = Signal.FULL
        else:
            current_signal = Signal.EMPTY

        # 仅在信号变化时调仓
        if current_signal == self._last_signal:
            return []

        self._last_signal = current_signal

        if current_signal == Signal.FULL:
            weight = 1.0 / len(self.universe)
            target_weights = {code: weight for code in self.universe}
        else:
            target_weights = {code: 0.0 for code in self.universe}

        return rebalance_to(context, target_weights)


# ---------------------------------------------------------------------------
# 估值分位数策略
# ---------------------------------------------------------------------------


class ValuationStrategy(BaseStrategy):
    """估值分位数择时策略。

    基于净值在历史窗口中的百分位数进行择时：
    - 百分位 <= low_threshold → 低估，满仓
    - 百分位 >= high_threshold → 高估，空仓
    - 中间区域 → 半仓

    可接入指数估值数据（PE/PB 分位数），此处使用净值分位数作为简化实现。

    Example::

        strategy = ValuationStrategy(
            params=ValuationParams(
                lookback_days=252,
                low_threshold=0.3,
                high_threshold=0.7,
            ),
            universe=["000001"],
        )
    """

    name = "valuation_timing"

    def __init__(
        self,
        params: ValuationParams | None = None,
        universe: list[str] | None = None,
    ) -> None:
        super().__init__(params=params, universe=universe)
        self._last_signal: Signal | None = None

    @property
    def val_params(self) -> ValuationParams:
        """获取类型化的参数。"""
        assert isinstance(self.params, ValuationParams)
        return self.params

    def on_bar(self, context: BarContext) -> list[OrderIntent]:
        """每个交易日计算估值分位数信号。

        Args:
            context: 当日策略上下文

        Returns:
            OrderIntent 列表
        """
        if not self.universe:
            return []

        target_code = self.universe[0]
        nav_series = context.nav_series(target_code)

        if not nav_series:
            return []

        # 计算百分位
        percentile = compute_percentile(
            nav_series, self.val_params.lookback_days, context._cutoff_date
        )

        if percentile is None:
            return []

        # 生成信号
        if percentile <= self.val_params.low_threshold:
            current_signal = Signal.FULL
        elif percentile >= self.val_params.high_threshold:
            current_signal = Signal.EMPTY
        else:
            current_signal = Signal.HALF

        # 仅在信号变化时调仓
        if current_signal == self._last_signal:
            return []

        self._last_signal = current_signal

        if current_signal == Signal.FULL:
            weight = 1.0 / len(self.universe)
            target_weights = {code: weight for code in self.universe}
        elif current_signal == Signal.HALF:
            weight = 0.5 / len(self.universe)
            target_weights = {code: weight for code in self.universe}
        else:
            target_weights = {code: 0.0 for code in self.universe}

        return rebalance_to(context, target_weights)
