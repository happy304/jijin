"""Walk-Forward 验证模块。

实现滚动窗口前推验证，用于评估策略的样本外表现和过拟合风险。

流程：
1. 将回测期分为多个滚动窗口（训练期 + 验证期）
2. 在每个训练期内运行策略（in-sample）
3. 在紧接的验证期内评估策略表现（out-of-sample）
4. 汇总所有 OOS 窗口的绩效，计算 Walk-Forward Efficiency

WFE (Walk-Forward Efficiency):
    WFE = OOS_Sharpe / IS_Sharpe
    WFE > 0.5 通常认为策略具有一定的稳健性
    WFE > 0.7 认为策略较为稳健

需求: 优化计划 6.1
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from app.domain.backtest.calendar import trading_days

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class WalkForwardWindow:
    """单个 Walk-Forward 窗口的结果。

    Attributes:
        window_id: 窗口编号（从 1 开始）
        train_start: 训练期起始日期
        train_end: 训练期结束日期
        test_start: 验证期起始日期
        test_end: 验证期结束日期
        is_sharpe: 训练期（in-sample）Sharpe 比率
        oos_sharpe: 验证期（out-of-sample）Sharpe 比率
        is_return: 训练期总收益率
        oos_return: 验证期总收益率
        is_max_drawdown: 训练期最大回撤
        oos_max_drawdown: 验证期最大回撤
    """

    window_id: int
    train_start: date
    train_end: date
    test_start: date
    test_end: date
    is_sharpe: float = 0.0
    oos_sharpe: float = 0.0
    is_return: float = 0.0
    oos_return: float = 0.0
    is_max_drawdown: float = 0.0
    oos_max_drawdown: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典。"""
        return {
            "window_id": self.window_id,
            "train_start": self.train_start.isoformat(),
            "train_end": self.train_end.isoformat(),
            "test_start": self.test_start.isoformat(),
            "test_end": self.test_end.isoformat(),
            "is_sharpe": round(self.is_sharpe, 4),
            "oos_sharpe": round(self.oos_sharpe, 4),
            "is_return": round(self.is_return, 6),
            "oos_return": round(self.oos_return, 6),
            "is_max_drawdown": round(self.is_max_drawdown, 6),
            "oos_max_drawdown": round(self.oos_max_drawdown, 6),
        }


@dataclass
class WalkForwardResult:
    """Walk-Forward 验证整体结果。

    Attributes:
        windows: 各窗口结果列表
        wfe: Walk-Forward Efficiency (OOS_Sharpe_avg / IS_Sharpe_avg)
        avg_oos_sharpe: 平均 OOS Sharpe
        avg_is_sharpe: 平均 IS Sharpe
        avg_oos_return: 平均 OOS 收益率
        oos_win_rate: OOS 窗口中收益为正的比例
        total_oos_return: 所有 OOS 窗口的累计收益率
        is_robust: 策略是否稳健（WFE > 0.5 且 OOS 胜率 > 50%）
    """

    windows: list[WalkForwardWindow] = field(default_factory=list)
    wfe: float = 0.0
    avg_oos_sharpe: float = 0.0
    avg_is_sharpe: float = 0.0
    avg_oos_return: float = 0.0
    oos_win_rate: float = 0.0
    total_oos_return: float = 0.0
    is_robust: bool = False

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典。"""
        return {
            "wfe": round(self.wfe, 4),
            "avg_oos_sharpe": round(self.avg_oos_sharpe, 4),
            "avg_is_sharpe": round(self.avg_is_sharpe, 4),
            "avg_oos_return": round(self.avg_oos_return, 6),
            "oos_win_rate": round(self.oos_win_rate, 4),
            "total_oos_return": round(self.total_oos_return, 6),
            "is_robust": self.is_robust,
            "windows": [w.to_dict() for w in self.windows],
        }


# ---------------------------------------------------------------------------
# 窗口生成
# ---------------------------------------------------------------------------


def generate_walk_forward_windows(
    start: date,
    end: date,
    train_months: int = 12,
    test_months: int = 3,
    step_months: int = 3,
) -> list[tuple[date, date, date, date]]:
    """生成 Walk-Forward 滚动窗口。

    Args:
        start: 回测起始日期
        end: 回测结束日期
        train_months: 训练窗口月数
        test_months: 验证窗口月数
        step_months: 步进月数

    Returns:
        窗口列表 [(train_start, train_end, test_start, test_end), ...]
    """
    windows: list[tuple[date, date, date, date]] = []

    current_start = start
    window_id = 0

    while True:
        # 训练期
        train_start = current_start
        train_end = _add_months(train_start, train_months) - timedelta(days=1)

        # 验证期
        test_start = train_end + timedelta(days=1)
        test_end = _add_months(test_start, test_months) - timedelta(days=1)

        # 如果验证期超出回测结束日期，截断
        if test_start > end:
            break

        if test_end > end:
            test_end = end

        # 确保验证期至少有 20 个交易日
        test_trading_days = trading_days(test_start, test_end)
        if len(test_trading_days) < 20:
            break

        windows.append((train_start, train_end, test_start, test_end))
        window_id += 1

        # 步进
        current_start = _add_months(current_start, step_months)

    return windows


def compute_walk_forward_metrics(
    windows: list[WalkForwardWindow],
) -> WalkForwardResult:
    """汇总 Walk-Forward 窗口结果。

    Args:
        windows: 各窗口的结果列表

    Returns:
        WalkForwardResult 汇总结果
    """
    if not windows:
        return WalkForwardResult()

    n = len(windows)

    # 平均 IS/OOS Sharpe
    avg_is_sharpe = sum(w.is_sharpe for w in windows) / n
    avg_oos_sharpe = sum(w.oos_sharpe for w in windows) / n

    # WFE
    wfe = avg_oos_sharpe / avg_is_sharpe if abs(avg_is_sharpe) > 1e-8 else 0.0

    # 平均 OOS 收益率
    avg_oos_return = sum(w.oos_return for w in windows) / n

    # OOS 胜率
    oos_wins = sum(1 for w in windows if w.oos_return > 0)
    oos_win_rate = oos_wins / n

    # 累计 OOS 收益率
    total_oos = 1.0
    for w in windows:
        total_oos *= (1 + w.oos_return)
    total_oos_return = total_oos - 1.0

    # 稳健性判断
    is_robust = wfe > 0.5 and oos_win_rate > 0.5

    return WalkForwardResult(
        windows=windows,
        wfe=wfe,
        avg_oos_sharpe=avg_oos_sharpe,
        avg_is_sharpe=avg_is_sharpe,
        avg_oos_return=avg_oos_return,
        oos_win_rate=oos_win_rate,
        total_oos_return=total_oos_return,
        is_robust=is_robust,
    )


def compute_period_metrics(
    equity_values: list[float],
) -> tuple[float, float, float]:
    """计算一段时期的 Sharpe、收益率和最大回撤。

    Args:
        equity_values: 权益值序列

    Returns:
        (sharpe, total_return, max_drawdown) 元组
    """
    if len(equity_values) < 2:
        return 0.0, 0.0, 0.0

    # 日收益率
    daily_returns = [
        (equity_values[i] - equity_values[i - 1]) / equity_values[i - 1]
        for i in range(1, len(equity_values))
        if equity_values[i - 1] != 0
    ]

    if not daily_returns:
        return 0.0, 0.0, 0.0

    # 总收益率
    total_return = (equity_values[-1] - equity_values[0]) / equity_values[0]

    # Sharpe
    n = len(daily_returns)
    mean_r = sum(daily_returns) / n
    variance = sum((r - mean_r) ** 2 for r in daily_returns) / max(n - 1, 1)
    std_r = math.sqrt(variance)
    sharpe = (mean_r / std_r) * math.sqrt(252) if std_r > 0 else 0.0

    # 最大回撤
    peak = equity_values[0]
    max_dd = 0.0
    for v in equity_values:
        if v > peak:
            peak = v
        dd = (v - peak) / peak if peak > 0 else 0.0
        if dd < max_dd:
            max_dd = dd

    return sharpe, total_return, max_dd


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _add_months(d: date, months: int) -> date:
    """给日期加上指定月数。"""
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    # 处理月末日期（如 1月31日 + 1个月 = 2月28日）
    import calendar
    max_day = calendar.monthrange(year, month)[1]
    day = min(d.day, max_day)
    return date(year, month, day)
