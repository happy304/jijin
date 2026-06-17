"""基金轮动策略模块。

实现基于动量/Sharpe/信息比率等因子的 Top-N 轮动策略：
- MomentumRotation: 定期从基金池中选出得分最高的 N 只基金，等权配置

设计要点：
- 继承 BaseStrategy，实现 on_bar 方法
- 支持多种评分方法：简单收益率 (return)、Sharpe 比率 (sharpe)
- 使用 context.nav_series 获取历史净值，基于 lookback 窗口计算得分
- 在调仓日通过 rebalance_to 生成最小化调仓指令
- 非调仓日返回空列表，保持持仓不变

需求: 5.2
"""

from __future__ import annotations

import math
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import Field

from app.domain.backtest.engine_event import BarContext
from app.domain.backtest.order import OrderIntent
from app.domain.strategy.base import BaseStrategy, StrategyParams, rebalance_to


# ---------------------------------------------------------------------------
# 评分方法枚举
# ---------------------------------------------------------------------------


class ScoreMethod(str, Enum):
    """评分方法。"""

    RETURN = "return"
    SHARPE = "sharpe"


# ---------------------------------------------------------------------------
# 调仓频率枚举
# ---------------------------------------------------------------------------


class RebalanceFreq(str, Enum):
    """调仓频率。"""

    WEEKLY = "weekly"
    MONTHLY = "monthly"


# ---------------------------------------------------------------------------
# 参数类
# ---------------------------------------------------------------------------


class MomentumParams(StrategyParams):
    """动量轮动策略参数。

    Attributes:
        lookback_days: 回看窗口天数（用于计算评分的历史数据长度）
        top_n: 选取得分最高的 N 只基金
        rebalance_freq: 调仓频率（weekly/monthly）
        score_method: 评分方法（return=简单收益率, sharpe=Sharpe 比率）
    """

    lookback_days: int = Field(default=120, gt=0, description="回看窗口天数")
    top_n: int = Field(default=3, gt=0, description="选取 Top-N 基金")
    rebalance_freq: RebalanceFreq = Field(
        default=RebalanceFreq.MONTHLY, description="调仓频率"
    )
    score_method: ScoreMethod = Field(
        default=ScoreMethod.RETURN, description="评分方法"
    )


# ---------------------------------------------------------------------------
# 评分计算辅助函数
# ---------------------------------------------------------------------------


def compute_return_score(nav_series: dict[date, Decimal], lookback_days: int) -> float | None:
    """计算简单收益率得分。

    基于 lookback 窗口内的净值变化计算收益率：
    score = (最新净值 / lookback 天前净值) - 1

    Args:
        nav_series: 历史净值序列 {date: nav}
        lookback_days: 回看窗口天数

    Returns:
        收益率（float），数据不足完整 lookback 窗口时返回 None
    """
    if not nav_series:
        return None

    sorted_dates = sorted(nav_series.keys())
    if len(sorted_dates) < 2:
        return None

    # 数据不足完整 lookback 窗口时返回 None，避免不同基金评分窗口不一致
    if len(sorted_dates) <= lookback_days:
        return None

    latest_date = sorted_dates[-1]
    latest_nav = float(nav_series[latest_date])

    # 找到 lookback_days 天前的数据点
    start_idx = len(sorted_dates) - lookback_days - 1
    start_nav = float(nav_series[sorted_dates[start_idx]])

    if start_nav <= 0:
        return None

    return (latest_nav / start_nav) - 1.0


def compute_sharpe_score(nav_series: dict[date, Decimal], lookback_days: int) -> float | None:
    """计算 Sharpe 比率得分。

    基于 lookback 窗口内的日收益率计算年化 Sharpe 比率：
    sharpe = mean(daily_returns) / std(daily_returns) * sqrt(252)

    假设无风险利率为 0（简化处理）。

    Args:
        nav_series: 历史净值序列 {date: nav}
        lookback_days: 回看窗口天数

    Returns:
        Sharpe 比率（float），数据不足完整 lookback 窗口时返回 None
    """
    if not nav_series:
        return None

    sorted_dates = sorted(nav_series.keys())

    # 数据不足完整 lookback 窗口时返回 None，避免不同基金评分窗口不一致
    if len(sorted_dates) <= lookback_days:
        return None

    # 取 lookback 窗口内的数据
    window_dates = sorted_dates[-(lookback_days + 1):]

    if len(window_dates) < 3:
        # 至少需要 3 个数据点才能计算有意义的 Sharpe
        return None

    # 计算日收益率
    daily_returns: list[float] = []
    for i in range(1, len(window_dates)):
        prev_nav = float(nav_series[window_dates[i - 1]])
        curr_nav = float(nav_series[window_dates[i]])
        if prev_nav <= 0:
            continue
        daily_returns.append(curr_nav / prev_nav - 1.0)

    if len(daily_returns) < 2:
        return None

    # 计算均值和标准差
    mean_return = sum(daily_returns) / len(daily_returns)
    variance = sum((r - mean_return) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
    std_return = math.sqrt(variance)

    if std_return == 0:
        return None

    # 年化 Sharpe（假设 252 个交易日）
    sharpe = (mean_return / std_return) * math.sqrt(252)
    return sharpe


def compute_score(
    nav_series: dict[date, Decimal],
    lookback_days: int,
    method: ScoreMethod,
) -> float | None:
    """根据指定方法计算基金得分。

    Args:
        nav_series: 历史净值序列 {date: nav}
        lookback_days: 回看窗口天数
        method: 评分方法

    Returns:
        得分（float），数据不足时返回 None
    """
    if method == ScoreMethod.RETURN:
        return compute_return_score(nav_series, lookback_days)
    elif method == ScoreMethod.SHARPE:
        return compute_sharpe_score(nav_series, lookback_days)
    else:
        return None


# ---------------------------------------------------------------------------
# 调仓日判断辅助函数
# ---------------------------------------------------------------------------


def is_rebalance_day(
    current_date: date,
    last_rebalance_date: date | None,
    freq: RebalanceFreq,
) -> bool:
    """判断当前日期是否为调仓日。

    规则：
    - weekly: 距上次调仓 >= 7 天
    - monthly: 距上次调仓 >= 28 天

    首次调仓（last_rebalance_date 为 None）时直接返回 True。

    Args:
        current_date: 当前交易日
        last_rebalance_date: 上次调仓日期
        freq: 调仓频率

    Returns:
        是否应该在当日调仓
    """
    if last_rebalance_date is None:
        return True

    days_since_last = (current_date - last_rebalance_date).days

    if freq == RebalanceFreq.WEEKLY:
        return days_since_last >= 7
    else:  # MONTHLY
        return days_since_last >= 28


# ---------------------------------------------------------------------------
# 动量轮动策略
# ---------------------------------------------------------------------------


class MomentumRotation(BaseStrategy):
    """基金动量轮动策略。

    定期从基金池中选出评分最高的 Top-N 只基金，等权配置。
    非调仓日保持持仓不变。

    评分方法：
    - return: 简单收益率（lookback 窗口内的涨幅）
    - sharpe: Sharpe 比率（lookback 窗口内的风险调整收益）

    调仓逻辑：
    1. 判断是否为调仓日（基于 rebalance_freq）
    2. 对基金池中每只基金计算评分
    3. 选取得分最高的 top_n 只基金
    4. 等权配置（每只基金权重 = 1/top_n）
    5. 通过 rebalance_to 生成最小化调仓指令

    Example::

        strategy = MomentumRotation(
            params=MomentumParams(
                lookback_days=120,
                top_n=3,
                rebalance_freq=RebalanceFreq.MONTHLY,
                score_method=ScoreMethod.RETURN,
            ),
            universe=["000001", "000002", "000003", "000004", "000005"],
        )
    """

    name = "momentum_rotation"

    def __init__(
        self,
        params: MomentumParams | None = None,
        universe: list[str] | None = None,
    ) -> None:
        super().__init__(params=params, universe=universe)
        self._last_rebalance_date: date | None = None

    @property
    def momentum_params(self) -> MomentumParams:
        """获取类型化的参数。"""
        assert isinstance(self.params, MomentumParams)
        return self.params

    def on_bar(self, context: BarContext) -> list[OrderIntent]:
        """每个交易日判断是否调仓。

        如果到达调仓日，计算基金池中每只基金的评分，
        选取 Top-N 等权配置，通过 rebalance_to 生成调仓指令。

        Args:
            context: 当日策略上下文（只能看到 T-1 及之前数据）

        Returns:
            OrderIntent 列表，非调仓日返回空列表
        """
        if not is_rebalance_day(
            context.date,
            self._last_rebalance_date,
            self.momentum_params.rebalance_freq,
        ):
            return []

        # 计算每只基金的评分
        scores: dict[str, float] = {}
        for code in self.universe:
            nav_series = context.nav_series(code)
            score = compute_score(
                nav_series,
                self.momentum_params.lookback_days,
                self.momentum_params.score_method,
            )
            if score is not None:
                scores[code] = score

        # 如果没有任何基金有有效评分，不调仓
        if not scores:
            return []

        # 选取 Top-N
        top_n = min(self.momentum_params.top_n, len(scores))
        top_codes = sorted(scores, key=scores.get, reverse=True)[:top_n]  # type: ignore[arg-type]

        # 等权配置
        weight = 1.0 / len(top_codes)
        target_weights = {code: weight for code in top_codes}

        # 记录调仓日
        self._last_rebalance_date = context.date

        # 生成调仓指令
        return rebalance_to(context, target_weights)
