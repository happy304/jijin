"""回测策略对比服务。

提供多个回测结果的并排对比分析：
1. 绩效指标对比 — 多个回测的 Sharpe/回撤/收益等指标并排
2. 净值曲线对比 — 归一化后的净值曲线叠加
3. 风险收益散点 — 各策略在风险-收益坐标系中的位置
4. 优劣排名 — 按各指标对策略进行排名

灵感来源：fund-strategy 的策略对比功能
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass
class BacktestCompareItem:
    """单个回测的对比数据。"""

    run_id: int
    strategy_name: str | None = None
    start_date: date | None = None
    end_date: date | None = None

    # 核心指标
    total_return: float = 0.0
    annualized_return: float = 0.0
    sharpe: float = 0.0
    max_drawdown: float = 0.0
    volatility: float = 0.0
    sortino: float = 0.0
    calmar: float = 0.0
    win_rate: float = 0.0
    var_95: float = 0.0

    # 基准相对指标
    alpha: float | None = None
    beta: float | None = None
    information_ratio: float | None = None

    # 归一化净值曲线（起始为1）
    normalized_equity: list[tuple[str, float]] = field(default_factory=list)


@dataclass
class CompareResult:
    """策略对比结果。"""

    items: list[BacktestCompareItem] = field(default_factory=list)
    # 各指标的排名（run_id -> rank）
    rankings: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # 最佳策略推荐
    best_sharpe_run_id: int | None = None
    best_return_run_id: int | None = None
    lowest_drawdown_run_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """序列化。"""
        return {
            "items": [
                {
                    "run_id": item.run_id,
                    "strategy_name": item.strategy_name,
                    "start_date": item.start_date.isoformat() if item.start_date else None,
                    "end_date": item.end_date.isoformat() if item.end_date else None,
                    "total_return": round(item.total_return, 6),
                    "annualized_return": round(item.annualized_return, 6),
                    "sharpe": round(item.sharpe, 4),
                    "max_drawdown": round(item.max_drawdown, 6),
                    "volatility": round(item.volatility, 6),
                    "sortino": round(item.sortino, 4),
                    "calmar": round(item.calmar, 4),
                    "win_rate": round(item.win_rate, 4),
                    "var_95": round(item.var_95, 6),
                    "alpha": round(item.alpha, 4) if item.alpha is not None else None,
                    "beta": round(item.beta, 4) if item.beta is not None else None,
                    "information_ratio": round(item.information_ratio, 4) if item.information_ratio is not None else None,
                    "normalized_equity": item.normalized_equity,
                }
                for item in self.items
            ],
            "rankings": self.rankings,
            "best_sharpe_run_id": self.best_sharpe_run_id,
            "best_return_run_id": self.best_return_run_id,
            "lowest_drawdown_run_id": self.lowest_drawdown_run_id,
        }


def compare_backtests(items: list[BacktestCompareItem]) -> CompareResult:
    """对比多个回测结果。

    Args:
        items: 各回测的对比数据列表

    Returns:
        CompareResult 对比结果
    """
    if not items:
        return CompareResult()

    result = CompareResult(items=items)

    # 计算各指标排名
    metrics_to_rank = [
        ("sharpe", True),           # 越大越好
        ("annualized_return", True),
        ("max_drawdown", False),    # 越小（绝对值越小）越好 → 值越大越好（因为是负数）
        ("volatility", False),      # 越小越好 → 反向排
        ("sortino", True),
        ("calmar", True),
        ("win_rate", True),
    ]

    for metric_name, higher_is_better in metrics_to_rank:
        sorted_items = sorted(
            items,
            key=lambda x: getattr(x, metric_name, 0) or 0,
            reverse=higher_is_better,
        )
        result.rankings[metric_name] = [
            {"run_id": item.run_id, "value": round(getattr(item, metric_name, 0) or 0, 4), "rank": idx + 1}
            for idx, item in enumerate(sorted_items)
        ]

    # 最佳策略
    if items:
        best_sharpe = max(items, key=lambda x: x.sharpe)
        result.best_sharpe_run_id = best_sharpe.run_id

        best_return = max(items, key=lambda x: x.annualized_return)
        result.best_return_run_id = best_return.run_id

        # max_drawdown 是负数，绝对值最小的最好
        lowest_dd = max(items, key=lambda x: x.max_drawdown)  # 最接近0的
        result.lowest_drawdown_run_id = lowest_dd.run_id

    return result
