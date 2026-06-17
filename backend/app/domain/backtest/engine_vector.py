"""向量化回测引擎模块。

纯 pandas 向量化实现，不处理 T+1 和精确费率，速度快 100 倍。
适用于研究阶段快速迭代，支持信号矩阵 → 权重归一化 → 扣除简化成本 → 权益曲线。

核心流程：
1. 接收信号矩阵（目标权重）和收益率矩阵
2. 归一化权重（使权重和 ≤ 1）
3. 应用 1 日滞后（shift(1)）避免前视偏差
4. 计算换手率并扣除简化交易成本
5. 生成组合收益序列和权益曲线

需求: 4.12
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class VectorBacktestResult:
    """向量化回测结果。

    Attributes:
        equity: 权益曲线（pd.Series，索引为日期）
        returns: 组合日收益率序列
        turnover: 每日换手率序列
        total_return: 总收益率
        annualized_return: 年化收益率
        max_drawdown: 最大回撤（正数表示）
    """

    equity: pd.Series
    returns: pd.Series
    turnover: pd.Series
    total_return: float
    annualized_return: float
    max_drawdown: float


class VectorBacktest:
    """纯 pandas 向量化回测引擎。

    不处理 T+1 结算和精确费率，使用简化的固定基点成本。
    适用于研究阶段快速策略迭代。

    Args:
        initial_capital: 初始资金，默认 1,000,000
        cost_bps: 简化交易成本（基点），默认 10bps（单边）

    用法示例::

        import pandas as pd
        import numpy as np

        dates = pd.date_range("2020-01-01", periods=252, freq="B")
        funds = ["fund_a", "fund_b", "fund_c"]

        # 信号矩阵：目标权重
        signals = pd.DataFrame(
            np.random.dirichlet([1, 1, 1], size=252),
            index=dates, columns=funds,
        )

        # 收益率矩阵
        returns = pd.DataFrame(
            np.random.randn(252, 3) * 0.01,
            index=dates, columns=funds,
        )

        engine = VectorBacktest(initial_capital=1_000_000, cost_bps=10)
        result = engine.run(signals, returns)
        print(f"Total return: {result.total_return:.2%}")
        print(f"Max drawdown: {result.max_drawdown:.2%}")
    """

    def __init__(
        self,
        initial_capital: float = 1_000_000.0,
        cost_bps: float = 10.0,
    ) -> None:
        if initial_capital <= 0:
            raise ValueError("initial_capital must be positive")
        if cost_bps < 0:
            raise ValueError("cost_bps must be non-negative")
        self.initial_capital = initial_capital
        self.cost_bps = cost_bps

    def run(
        self,
        signals: pd.DataFrame,
        returns: pd.DataFrame,
    ) -> VectorBacktestResult:
        """运行向量化回测。

        Args:
            signals: 信号矩阵（目标权重），index=日期，columns=基金代码，值在 [0, 1]
            returns: 日收益率矩阵，index=日期，columns=基金代码

        Returns:
            VectorBacktestResult 包含权益曲线、收益序列、换手率等

        Raises:
            ValueError: 如果输入数据为空或格式不正确
        """
        self._validate_inputs(signals, returns)

        # 对齐索引和列
        common_cols = signals.columns.intersection(returns.columns)
        common_idx = signals.index.intersection(returns.index)

        if len(common_cols) == 0:
            raise ValueError("signals and returns have no common columns (fund codes)")
        if len(common_idx) == 0:
            raise ValueError("signals and returns have no common index (dates)")

        signals_aligned = signals.loc[common_idx, common_cols]
        returns_aligned = returns.loc[common_idx, common_cols]

        # 1. 归一化权重
        weights = self._normalize_weights(signals_aligned)

        # 2. 计算换手率（权重变化的绝对值之和）
        turnover = weights.diff().abs().sum(axis=1)
        # 第一天的换手率为初始建仓的权重之和
        turnover.iloc[0] = weights.iloc[0].abs().sum()

        # 3. 计算交易成本
        costs = turnover * self.cost_bps / 10000.0

        # 4. 计算组合收益（使用 shift(1) 避免前视偏差）
        # 使用前一日权重乘以当日收益
        lagged_weights = weights.shift(1)
        # 第一天无前一日权重，组合收益为 0（减去建仓成本）
        port_returns = (lagged_weights * returns_aligned).sum(axis=1) - costs
        # 第一天 lagged_weights 为 NaN，sum 为 0，只扣除建仓成本
        port_returns.iloc[0] = -costs.iloc[0]

        # 5. 计算权益曲线
        equity = (1 + port_returns).cumprod() * self.initial_capital

        # 6. 计算汇总指标
        total_return = self._calc_total_return(equity)
        annualized_return = self._calc_annualized_return(equity)
        max_drawdown = self._calc_max_drawdown(equity)

        return VectorBacktestResult(
            equity=equity,
            returns=port_returns,
            turnover=turnover,
            total_return=total_return,
            annualized_return=annualized_return,
            max_drawdown=max_drawdown,
        )

    def _validate_inputs(
        self,
        signals: pd.DataFrame,
        returns: pd.DataFrame,
    ) -> None:
        """校验输入数据。"""
        if not isinstance(signals, pd.DataFrame):
            raise TypeError("signals must be a pandas DataFrame")
        if not isinstance(returns, pd.DataFrame):
            raise TypeError("returns must be a pandas DataFrame")
        if signals.empty:
            raise ValueError("signals DataFrame is empty")
        if returns.empty:
            raise ValueError("returns DataFrame is empty")

    def _normalize_weights(self, signals: pd.DataFrame) -> pd.DataFrame:
        """归一化权重，使每行权重之和 ≤ 1。

        如果权重之和 > 1，按比例缩放到 1。
        如果权重之和 ≤ 1，保持不变（差额视为现金配置）。
        负权重被裁剪为 0。
        """
        # 裁剪负值
        weights = signals.clip(lower=0)

        # 按行归一化：如果和 > 1 则缩放
        row_sums = weights.sum(axis=1)
        # 只对和 > 1 的行进行缩放
        scale_mask = row_sums > 1.0
        if scale_mask.any():
            scale_factors = row_sums.copy()
            scale_factors[~scale_mask] = 1.0
            weights = weights.div(scale_factors, axis=0)

        return weights

    @staticmethod
    def _calc_total_return(equity: pd.Series) -> float:
        """计算总收益率。"""
        if len(equity) < 2:
            return 0.0
        return float(equity.iloc[-1] / equity.iloc[0] - 1)

    @staticmethod
    def _calc_annualized_return(equity: pd.Series) -> float:
        """计算年化收益率（假设 252 个交易日/年）。"""
        if len(equity) < 2:
            return 0.0
        total_return = equity.iloc[-1] / equity.iloc[0]
        # n 个权益点只有 n-1 个收益区间；与事件驱动回测和因子库口径保持一致。
        n_periods = len(equity) - 1
        years = n_periods / 252.0
        if years <= 0:
            return 0.0
        # 处理负总收益的情况
        if total_return <= 0:
            return -1.0
        return float(total_return ** (1.0 / years) - 1)

    @staticmethod
    def _calc_max_drawdown(equity: pd.Series) -> float:
        """计算最大回撤（返回正数）。"""
        if len(equity) < 2:
            return 0.0
        cummax = equity.cummax()
        drawdown = (equity - cummax) / cummax
        max_dd = drawdown.min()
        return float(abs(max_dd))
