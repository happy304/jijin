"""均值回归策略模块。

实现基于均值回归原理的基金投资策略：
- 当基金净值偏离均线超过阈值时，逆向操作
- 净值低于均线（超卖）时加仓，高于均线（超买）时减仓
- 适用于震荡市场中的基金轮动

设计要点：
- 继承 BaseStrategy，实现 on_bar 方法
- 使用移动平均线作为"均值"参考
- Z-Score 衡量偏离程度，避免绝对值比较的局限
- 支持多基金池，按偏离程度排序分配权重
- 非调仓日返回空列表

需求: 扩展开发 - 新增策略
"""

from __future__ import annotations

import math
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import Field

from app.domain.backtest.engine_event import BarContext
from app.domain.backtest.order import OrderIntent
from app.domain.strategy.base import BaseStrategy, StrategyParams, rebalance_to


# ---------------------------------------------------------------------------
# 参数类
# ---------------------------------------------------------------------------


class MeanReversionParams(StrategyParams):
    """均值回归策略参数。

    Attributes:
        ma_window: 移动平均窗口天数（用于计算均值）
        zscore_window: Z-Score 计算窗口（用于标准化偏离度）
        entry_threshold: 入场阈值（Z-Score 绝对值超过此值时触发）
        exit_threshold: 出场阈值（Z-Score 回归到此值以内时平仓）
        max_positions: 最大同时持仓基金数
        rebalance_days: 调仓间隔天数
    """

    ma_window: int = Field(
        default=60, gt=5, le=252, description="移动平均窗口天数"
    )
    zscore_window: int = Field(
        default=60, gt=5, le=252, description="Z-Score 计算窗口"
    )
    entry_threshold: float = Field(
        default=-1.5, le=0, description="入场阈值（负值表示超卖时买入）"
    )
    exit_threshold: float = Field(
        default=0.5, ge=0, description="出场阈值（Z-Score 回归到此值时卖出）"
    )
    max_positions: int = Field(
        default=5, gt=0, le=20, description="最大持仓基金数"
    )
    rebalance_days: int = Field(
        default=5, gt=0, le=30, description="最小调仓间隔天数"
    )


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def compute_zscore(
    nav_series: dict[date, Decimal],
    ma_window: int,
    zscore_window: int,
) -> float | None:
    """计算当前净值相对移动平均线的 Z-Score。

    Z-Score = (当前净值 - MA) / 标准差

    Args:
        nav_series: 历史净值序列 {date: nav}
        ma_window: 移动平均窗口
        zscore_window: 标准差计算窗口

    Returns:
        Z-Score 值，数据不足时返回 None
    """
    if not nav_series:
        return None

    sorted_dates = sorted(nav_series.keys())
    required_length = max(ma_window, zscore_window) + 1

    if len(sorted_dates) < required_length:
        return None

    # 提取净值数组
    navs = [float(nav_series[d]) for d in sorted_dates]

    # 计算移动平均
    ma_slice = navs[-ma_window:]
    ma = sum(ma_slice) / len(ma_slice)

    if ma <= 0:
        return None

    # 计算偏离度序列（最近 zscore_window 天的 NAV/MA - 1）
    deviations: list[float] = []
    for i in range(len(navs) - zscore_window, len(navs)):
        if i < ma_window:
            continue
        # 每天的 MA
        day_ma = sum(navs[i - ma_window + 1 : i + 1]) / ma_window
        if day_ma > 0:
            deviations.append(navs[i] / day_ma - 1.0)

    if len(deviations) < 3:
        return None

    # 当前偏离度
    current_deviation = navs[-1] / ma - 1.0

    # 计算标准差
    mean_dev = sum(deviations) / len(deviations)
    variance = sum((d - mean_dev) ** 2 for d in deviations) / (len(deviations) - 1)
    std_dev = math.sqrt(variance)

    if std_dev < 1e-10:
        return None

    # Z-Score
    zscore = (current_deviation - mean_dev) / std_dev
    return zscore


# ---------------------------------------------------------------------------
# 均值回归策略
# ---------------------------------------------------------------------------


class MeanReversionStrategy(BaseStrategy):
    """均值回归策略。

    核心逻辑：
    1. 计算基金池中每只基金的 Z-Score（净值偏离均线的标准化程度）
    2. Z-Score < entry_threshold 的基金视为超卖，纳入买入候选
    3. 已持仓基金 Z-Score > exit_threshold 时卖出（均值回归完成）
    4. 按 Z-Score 从低到高排序，选取最超卖的 max_positions 只基金
    5. 等权配置买入候选基金

    适用场景：
    - 震荡市场中的基金轮动
    - 均值回归特征明显的宽基指数基金
    - 与动量策略形成互补（动量适合趋势市，均值回归适合震荡市）

    Example::

        strategy = MeanReversionStrategy(
            params=MeanReversionParams(
                ma_window=60,
                zscore_window=60,
                entry_threshold=-1.5,
                exit_threshold=0.5,
                max_positions=3,
            ),
            universe=["000001", "000002", "000003", "000004", "000005"],
        )
    """

    name = "mean_reversion"

    def __init__(
        self,
        params: MeanReversionParams | None = None,
        universe: list[str] | None = None,
    ) -> None:
        super().__init__(params=params, universe=universe)
        self._last_rebalance_date: date | None = None

    @property
    def mr_params(self) -> MeanReversionParams:
        """获取类型化的参数。"""
        assert isinstance(self.params, MeanReversionParams)
        return self.params

    def on_bar(self, context: BarContext) -> list[OrderIntent]:
        """每个交易日判断是否调仓。

        逻辑：
        1. 检查是否满足最小调仓间隔
        2. 计算所有基金的 Z-Score
        3. 确定买入候选（超卖）和卖出候选（回归）
        4. 生成目标权重，通过 rebalance_to 产生调仓指令

        Args:
            context: 当日策略上下文

        Returns:
            OrderIntent 列表
        """
        # 检查调仓间隔
        if self._last_rebalance_date is not None:
            days_since = (context.date - self._last_rebalance_date).days
            if days_since < self.mr_params.rebalance_days:
                return []

        # 计算所有基金的 Z-Score
        zscores: dict[str, float] = {}
        for code in self.universe:
            nav_series = context.nav_series(code)
            zscore = compute_zscore(
                nav_series,
                self.mr_params.ma_window,
                self.mr_params.zscore_window,
            )
            if zscore is not None:
                zscores[code] = zscore

        if not zscores:
            return []

        # 确定目标持仓
        target_weights: dict[str, float] = {}

        # 找出超卖基金（Z-Score < entry_threshold）
        oversold = {
            code: z for code, z in zscores.items()
            if z < self.mr_params.entry_threshold
        }

        # 已持仓但需要卖出的基金（Z-Score > exit_threshold）
        current_positions = context.positions

        # 保留仍在超卖区间的已持仓基金
        for code in current_positions:
            if code in zscores and zscores[code] <= self.mr_params.exit_threshold:
                oversold[code] = zscores.get(code, 0.0)

        if not oversold:
            # 没有超卖基金，如果有持仓且都已回归，则清仓
            if current_positions:
                all_reverted = all(
                    zscores.get(code, 0.0) > self.mr_params.exit_threshold
                    for code in current_positions
                )
                if all_reverted:
                    self._last_rebalance_date = context.date
                    return rebalance_to(context, {})
            return []

        # 按 Z-Score 从低到高排序（最超卖的优先）
        sorted_candidates = sorted(oversold.items(), key=lambda x: x[1])
        selected = sorted_candidates[: self.mr_params.max_positions]

        # 等权配置
        weight = 1.0 / len(selected)
        target_weights = {code: weight for code, _ in selected}

        # 检查是否需要调仓（目标与当前是否有差异）
        current_codes = set(current_positions.keys())
        target_codes = set(target_weights.keys())

        if current_codes == target_codes and current_positions:
            # 持仓基金没变，不调仓
            return []

        self._last_rebalance_date = context.date
        return rebalance_to(context, target_weights)
