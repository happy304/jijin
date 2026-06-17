"""Monte Carlo 滚动回测服务模块。

实现基于收益序列的 Monte Carlo 模拟，评估策略稳健性：
- MonteCarloSimulator: 核心模拟器
- MonteCarloConfig: 模拟配置
- MonteCarloResult: 模拟结果（含分布统计）

支持两种 bootstrap 方法：
1. IID Bootstrap: 独立同分布重采样，适用于无自相关的收益序列
2. Block Bootstrap: 区块重采样，保留收益序列的自相关结构

流程：
1. 从原始策略回测中提取日收益率序列
2. 使用 bootstrap 方法生成 N 条模拟收益路径
3. 对每条路径计算绩效指标
4. 输出指标的分布统计（百分位数、置信区间）

需求: 5.8
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class MonteCarloConfig:
    """Monte Carlo 模拟配置。

    Attributes:
        n_simulations: 模拟次数（路径数量）
        method: bootstrap 方法 ("iid" 或 "block")
        block_size: 区块大小（仅 block 方法使用），None 时自动计算
        confidence_level: 置信区间水平（如 0.95 表示 95% 置信区间）
        seed: 随机种子，用于可重复性
        percentiles: 输出的百分位数列表
    """

    n_simulations: int = 1000
    method: str = "iid"  # "iid" 或 "block"
    block_size: int | None = None  # None 时自动计算为 sqrt(n)
    confidence_level: float = 0.95
    seed: int | None = None
    percentiles: list[float] = field(
        default_factory=lambda: [5.0, 10.0, 25.0, 50.0, 75.0, 90.0, 95.0]
    )

    def __post_init__(self) -> None:
        if self.n_simulations <= 0:
            raise ValueError(
                f"n_simulations must be positive, got {self.n_simulations}"
            )
        if self.method not in ("iid", "block"):
            raise ValueError(
                f"method must be 'iid' or 'block', got '{self.method}'"
            )
        if self.block_size is not None and self.block_size <= 0:
            raise ValueError(
                f"block_size must be positive, got {self.block_size}"
            )
        if not (0.0 < self.confidence_level < 1.0):
            raise ValueError(
                f"confidence_level must be in (0, 1), got {self.confidence_level}"
            )


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class SimulationPath:
    """单条模拟路径的结果。

    Attributes:
        path_index: 路径序号
        cumulative_returns: 累计收益序列
        metrics: 该路径的绩效指标
    """

    path_index: int
    cumulative_returns: NDArray[np.float64]
    metrics: dict[str, float]


@dataclass
class DistributionStats:
    """单个指标的分布统计。

    Attributes:
        mean: 均值
        std: 标准差
        median: 中位数
        percentiles: 百分位数字典 {百分位: 值}
        ci_lower: 置信区间下界
        ci_upper: 置信区间上界
        min_value: 最小值
        max_value: 最大值
    """

    mean: float
    std: float
    median: float
    percentiles: dict[float, float]
    ci_lower: float
    ci_upper: float
    min_value: float
    max_value: float


@dataclass
class MonteCarloResult:
    """Monte Carlo 模拟结果。

    Attributes:
        config: 使用的配置
        n_original_returns: 原始收益序列长度
        n_simulations: 实际模拟次数
        metric_distributions: 各指标的分布统计
        equity_percentiles: 权益曲线百分位数 {百分位: 曲线}
        all_metrics: 所有路径的指标列表（用于自定义分析）
    """

    config: MonteCarloConfig
    n_original_returns: int
    n_simulations: int
    metric_distributions: dict[str, DistributionStats]
    equity_percentiles: dict[float, NDArray[np.float64]]
    all_metrics: list[dict[str, float]]


# ---------------------------------------------------------------------------
# Bootstrap Methods
# ---------------------------------------------------------------------------


def iid_bootstrap(
    returns: NDArray[np.float64],
    n_simulations: int,
    rng: np.random.Generator,
) -> NDArray[np.float64]:
    """IID Bootstrap 重采样。

    从原始收益序列中独立同分布地随机抽样（有放回），
    生成 n_simulations 条与原始序列等长的模拟路径。

    Args:
        returns: 原始日收益率序列，shape (n_days,)
        n_simulations: 模拟次数
        rng: numpy 随机数生成器

    Returns:
        模拟收益矩阵，shape (n_simulations, n_days)
    """
    n_days = len(returns)
    # 生成随机索引矩阵
    indices = rng.integers(0, n_days, size=(n_simulations, n_days))
    return returns[indices]


def block_bootstrap(
    returns: NDArray[np.float64],
    n_simulations: int,
    block_size: int,
    rng: np.random.Generator,
) -> NDArray[np.float64]:
    """Block Bootstrap（区块 Bootstrap）重采样。

    将原始收益序列按固定长度的区块进行重采样，
    保留区块内的自相关结构。使用非重叠区块的随机拼接。

    Args:
        returns: 原始日收益率序列，shape (n_days,)
        n_simulations: 模拟次数
        block_size: 区块大小
        rng: numpy 随机数生成器

    Returns:
        模拟收益矩阵，shape (n_simulations, n_days)
    """
    n_days = len(returns)
    # 计算需要多少个区块来填满一条路径
    n_blocks = int(np.ceil(n_days / block_size))

    # 可选的区块起始位置：[0, n_days - block_size]
    max_start = n_days - block_size
    if max_start < 0:
        # 如果 block_size > n_days，退化为 iid bootstrap
        return iid_bootstrap(returns, n_simulations, rng)

    simulated = np.empty((n_simulations, n_days), dtype=np.float64)

    for i in range(n_simulations):
        # 随机选择 n_blocks 个区块起始位置
        starts = rng.integers(0, max_start + 1, size=n_blocks)
        # 拼接区块
        path_parts = []
        for start in starts:
            path_parts.append(returns[start : start + block_size])
        path = np.concatenate(path_parts)[:n_days]
        simulated[i] = path

    return simulated


# ---------------------------------------------------------------------------
# Metrics Calculation
# ---------------------------------------------------------------------------


def compute_path_metrics(cumulative_returns: NDArray[np.float64]) -> dict[str, float]:
    """计算单条模拟路径的绩效指标。

    Args:
        cumulative_returns: 累计收益曲线（从 1.0 开始）

    Returns:
        指标字典
    """
    if len(cumulative_returns) < 2:
        return {
            "total_return": 0.0,
            "annualized_return": 0.0,
            "volatility": 0.0,
            "sharpe": 0.0,
            "max_drawdown": 0.0,
            "calmar": 0.0,
            "sortino": 0.0,
        }

    # 总收益
    total_return = float(cumulative_returns[-1] / cumulative_returns[0] - 1.0)

    # 日收益率
    daily_returns = np.diff(cumulative_returns) / cumulative_returns[:-1]
    n_days = len(daily_returns)

    # 年化收益
    years = n_days / 252.0
    if years > 0 and total_return > -1.0:
        annualized_return = (1.0 + total_return) ** (1.0 / years) - 1.0
    else:
        annualized_return = -1.0

    # 波动率（年化）
    volatility = float(np.std(daily_returns, ddof=1) * np.sqrt(252)) if n_days > 1 else 0.0

    # Sharpe（假设无风险利率为 0）
    sharpe = annualized_return / volatility if volatility > 0 else 0.0

    # 最大回撤
    running_max = np.maximum.accumulate(cumulative_returns)
    drawdowns = (cumulative_returns - running_max) / running_max
    max_drawdown = float(np.min(drawdowns))  # 负值

    # Calmar
    calmar = (
        annualized_return / abs(max_drawdown) if max_drawdown < 0 else 0.0
    )

    # Sortino（下行波动率）
    negative_returns = daily_returns[daily_returns < 0]
    if len(negative_returns) > 1:
        downside_vol = float(np.std(negative_returns, ddof=1) * np.sqrt(252))
    else:
        downside_vol = 0.0
    sortino = annualized_return / downside_vol if downside_vol > 0 else 0.0

    return {
        "total_return": total_return,
        "annualized_return": annualized_return,
        "volatility": volatility,
        "sharpe": sharpe,
        "max_drawdown": max_drawdown,
        "calmar": calmar,
        "sortino": sortino,
    }


# ---------------------------------------------------------------------------
# Monte Carlo Simulator
# ---------------------------------------------------------------------------


class MonteCarloSimulator:
    """Monte Carlo 滚动回测模拟器。

    基于策略的历史收益序列，通过 bootstrap 重采样生成大量模拟路径，
    评估策略在不同市场情景下的稳健性。

    支持两种 bootstrap 方法：
    - IID Bootstrap: 独立重采样，破坏时间依赖性
    - Block Bootstrap: 区块重采样，保留短期自相关

    Example::

        simulator = MonteCarloSimulator(
            returns=daily_returns_array,
            config=MonteCarloConfig(n_simulations=1000, method="block"),
        )
        result = simulator.run()

        # 查看 Sharpe 的分布
        sharpe_dist = result.metric_distributions["sharpe"]
        print(f"Sharpe 95% CI: [{sharpe_dist.ci_lower:.3f}, {sharpe_dist.ci_upper:.3f}]")
    """

    def __init__(
        self,
        returns: NDArray[np.float64],
        config: MonteCarloConfig | None = None,
    ) -> None:
        """初始化 Monte Carlo 模拟器。

        Args:
            returns: 日收益率序列（如 [0.01, -0.005, 0.003, ...]）
            config: 模拟配置，None 时使用默认配置

        Raises:
            ValueError: 如果收益序列为空或长度不足
        """
        if len(returns) < 2:
            raise ValueError(
                f"Returns series must have at least 2 elements, got {len(returns)}"
            )

        self.returns = np.asarray(returns, dtype=np.float64)
        self.config = config or MonteCarloConfig()

    def run(self) -> MonteCarloResult:
        """执行 Monte Carlo 模拟。

        Returns:
            MonteCarloResult 包含分布统计和所有路径指标
        """
        rng = np.random.default_rng(self.config.seed)
        n_days = len(self.returns)

        logger.info(
            "Monte Carlo simulation: %d paths, method=%s, n_days=%d",
            self.config.n_simulations,
            self.config.method,
            n_days,
        )

        # 1. 生成模拟收益矩阵
        if self.config.method == "iid":
            simulated_returns = iid_bootstrap(
                self.returns, self.config.n_simulations, rng
            )
        else:
            block_size = self.config.block_size
            if block_size is None:
                # 自动计算区块大小：sqrt(n) 是常用启发式
                block_size = max(1, int(np.sqrt(n_days)))
            simulated_returns = block_bootstrap(
                self.returns, self.config.n_simulations, block_size, rng
            )

        # 2. 计算每条路径的累计收益曲线
        # cumulative_returns[i] 从 1.0 开始
        cumulative_matrix = np.cumprod(1.0 + simulated_returns, axis=1)
        # 在前面插入初始值 1.0
        ones_col = np.ones((self.config.n_simulations, 1), dtype=np.float64)
        cumulative_matrix = np.hstack([ones_col, cumulative_matrix])

        # 3. 计算每条路径的指标
        all_metrics: list[dict[str, float]] = []
        for i in range(self.config.n_simulations):
            metrics = compute_path_metrics(cumulative_matrix[i])
            all_metrics.append(metrics)

        # 4. 计算分布统计
        metric_distributions = self._compute_distributions(all_metrics)

        # 5. 计算权益曲线百分位数
        equity_percentiles = self._compute_equity_percentiles(cumulative_matrix)

        logger.info(
            "Monte Carlo simulation complete: %d paths generated",
            self.config.n_simulations,
        )

        return MonteCarloResult(
            config=self.config,
            n_original_returns=n_days,
            n_simulations=self.config.n_simulations,
            metric_distributions=metric_distributions,
            equity_percentiles=equity_percentiles,
            all_metrics=all_metrics,
        )

    def _compute_distributions(
        self, all_metrics: list[dict[str, float]]
    ) -> dict[str, DistributionStats]:
        """计算各指标的分布统计。

        Args:
            all_metrics: 所有路径的指标列表

        Returns:
            各指标的分布统计字典
        """
        if not all_metrics:
            return {}

        # 收集所有指标名称
        metric_names = list(all_metrics[0].keys())
        distributions: dict[str, DistributionStats] = {}

        alpha = 1.0 - self.config.confidence_level

        for name in metric_names:
            values = np.array([m[name] for m in all_metrics], dtype=np.float64)

            # 过滤掉 NaN 和 Inf
            valid_mask = np.isfinite(values)
            valid_values = values[valid_mask]

            if len(valid_values) == 0:
                distributions[name] = DistributionStats(
                    mean=0.0,
                    std=0.0,
                    median=0.0,
                    percentiles={p: 0.0 for p in self.config.percentiles},
                    ci_lower=0.0,
                    ci_upper=0.0,
                    min_value=0.0,
                    max_value=0.0,
                )
                continue

            mean = float(np.mean(valid_values))
            std = float(np.std(valid_values, ddof=1)) if len(valid_values) > 1 else 0.0
            median = float(np.median(valid_values))

            # 百分位数
            percentile_values = {}
            for p in self.config.percentiles:
                percentile_values[p] = float(np.percentile(valid_values, p))

            # 置信区间
            ci_lower = float(np.percentile(valid_values, alpha / 2 * 100))
            ci_upper = float(np.percentile(valid_values, (1 - alpha / 2) * 100))

            distributions[name] = DistributionStats(
                mean=mean,
                std=std,
                median=median,
                percentiles=percentile_values,
                ci_lower=ci_lower,
                ci_upper=ci_upper,
                min_value=float(np.min(valid_values)),
                max_value=float(np.max(valid_values)),
            )

        return distributions

    def _compute_equity_percentiles(
        self, cumulative_matrix: NDArray[np.float64]
    ) -> dict[float, NDArray[np.float64]]:
        """计算权益曲线的百分位数。

        对每个时间点，计算所有路径在该时间点的百分位数，
        形成百分位数权益曲线（用于绘制稳健性分布图）。

        Args:
            cumulative_matrix: 累计收益矩阵，shape (n_simulations, n_days+1)

        Returns:
            百分位数权益曲线字典 {百分位: 曲线}
        """
        equity_percentiles: dict[float, NDArray[np.float64]] = {}

        for p in self.config.percentiles:
            curve = np.percentile(cumulative_matrix, p, axis=0)
            equity_percentiles[p] = curve

        return equity_percentiles


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------


def run_monte_carlo_from_equity(
    equity_curve: list[float],
    config: MonteCarloConfig | None = None,
) -> MonteCarloResult:
    """从权益曲线运行 Monte Carlo 模拟的便捷函数。

    将权益曲线转换为日收益率序列，然后执行 Monte Carlo 模拟。

    Args:
        equity_curve: 权益曲线（如 [100000, 100500, 99800, ...]）
        config: 模拟配置

    Returns:
        MonteCarloResult

    Raises:
        ValueError: 如果权益曲线长度不足或包含非正值
    """
    if len(equity_curve) < 3:
        raise ValueError(
            f"Equity curve must have at least 3 points, got {len(equity_curve)}"
        )

    equity_arr = np.array(equity_curve, dtype=np.float64)

    # 检查非正值
    if np.any(equity_arr <= 0):
        raise ValueError("Equity curve must contain only positive values")

    # 计算日收益率
    returns = np.diff(equity_arr) / equity_arr[:-1]

    simulator = MonteCarloSimulator(returns=returns, config=config)
    return simulator.run()
