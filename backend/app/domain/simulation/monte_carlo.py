"""Monte Carlo simulation engine for fund portfolio prediction.

Implements three simulation methods:
1. GBM (Geometric Brownian Motion) — assumes log-normal returns
2. Bootstrap — resamples historical returns preserving fat tails
3. Hybrid — GBM with empirical distribution correction

The engine handles both single-fund and multi-fund (portfolio) scenarios,
using Cholesky decomposition to preserve correlation structure in the
multi-fund case.

References:
- Geometric Brownian Motion for asset price modeling
- Cholesky decomposition for correlated random variables
- Bootstrap methods for financial time series
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd
from numpy.typing import NDArray


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class SimulationConfig:
    """Configuration for a Monte Carlo simulation run."""

    horizon_days: int = 252
    num_simulations: int = 10000
    method: Literal["gbm", "bootstrap", "hybrid"] = "gbm"
    confidence_levels: list[float] = field(default_factory=lambda: [0.95, 0.99])
    initial_capital: float = 100000.0
    target_return: float | None = None
    random_seed: int | None = None


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class SimulationResult:
    """Result of a Monte Carlo simulation."""

    # Percentile paths for fan chart (shape: [num_percentiles, horizon_days+1])
    # Keys: "p5", "p10", "p25", "p50", "p75", "p90", "p95"
    percentile_paths: dict[str, list[float]]

    # Risk metrics
    expected_return: float  # 预期年化收益率（均值）
    median_return: float  # 中位数年化收益率
    volatility: float  # 预测波动率

    # VaR / CVaR at each confidence level
    var: dict[str, float]  # e.g. {"95": -0.12, "99": -0.18}
    cvar: dict[str, float]  # e.g. {"95": -0.15, "99": -0.22}

    # Drawdown distribution
    max_drawdown_median: float  # 最大回撤中位数
    max_drawdown_p95: float  # 最大回撤 95 分位

    # Target probability
    target_return: float | None = None
    target_probability: float | None = None  # 达成目标收益的概率

    # Terminal wealth distribution
    terminal_wealth_mean: float = 0.0
    terminal_wealth_median: float = 0.0
    terminal_wealth_p5: float = 0.0
    terminal_wealth_p95: float = 0.0

    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict."""
        return {
            "percentile_paths": self.percentile_paths,
            "expected_return": round(self.expected_return, 6),
            "median_return": round(self.median_return, 6),
            "volatility": round(self.volatility, 6),
            "var": {k: round(v, 6) for k, v in self.var.items()},
            "cvar": {k: round(v, 6) for k, v in self.cvar.items()},
            "max_drawdown_median": round(self.max_drawdown_median, 6),
            "max_drawdown_p95": round(self.max_drawdown_p95, 6),
            "target_return": self.target_return,
            "target_probability": (
                round(self.target_probability, 4)
                if self.target_probability is not None
                else None
            ),
            "terminal_wealth_mean": round(self.terminal_wealth_mean, 2),
            "terminal_wealth_median": round(self.terminal_wealth_median, 2),
            "terminal_wealth_p5": round(self.terminal_wealth_p5, 2),
            "terminal_wealth_p95": round(self.terminal_wealth_p95, 2),
        }


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class MonteCarloEngine:
    """Monte Carlo simulation engine for fund portfolios.

    Usage:
        engine = MonteCarloEngine(config)
        result = engine.run(historical_returns, weights)
    """

    def __init__(self, config: SimulationConfig) -> None:
        self.config = config
        self._rng = np.random.default_rng(config.random_seed)

    def run(
        self,
        historical_returns: pd.DataFrame | pd.Series,
        weights: NDArray[np.float64] | None = None,
        progress_callback: callable | None = None,
    ) -> SimulationResult:
        """Run Monte Carlo simulation.

        Args:
            historical_returns: Historical daily returns.
                - Series: single fund returns
                - DataFrame: multi-fund returns (columns = fund codes)
            weights: Portfolio weights (only for DataFrame input).
                If None, equal weights are used.
            progress_callback: Optional callback(progress_pct, message)

        Returns:
            SimulationResult with percentile paths and risk metrics.
        """
        paths = self.simulate_paths(historical_returns, weights, progress_callback)

        if progress_callback:
            progress_callback(80, "计算风险指标")

        # Compute results from paths
        result = self._compute_results(paths)

        if progress_callback:
            progress_callback(95, "生成分位数路径")

        return result

    def simulate_paths(
        self,
        historical_returns: pd.DataFrame | pd.Series,
        weights: NDArray[np.float64] | None = None,
        progress_callback: callable | None = None,
    ) -> NDArray[np.float64]:
        """Generate Monte Carlo paths without post-processing metrics."""
        if isinstance(historical_returns, pd.Series):
            returns = historical_returns.dropna().values
        else:
            # Multi-fund: compute portfolio returns
            df = historical_returns.dropna()
            if weights is None:
                weights = np.ones(df.shape[1]) / df.shape[1]
            weights = np.asarray(weights, dtype=np.float64)
            weights = weights / weights.sum()  # normalize
            returns = (df.values @ weights)

        if len(returns) < 30:
            raise ValueError(
                f"历史数据不足：需要至少 30 个交易日的收益率数据，当前仅有 {len(returns)} 个"
            )

        if progress_callback:
            progress_callback(10, "参数估计中")

        # Generate simulation paths based on method
        if self.config.method == "gbm":
            return self._simulate_gbm(returns, progress_callback)
        if self.config.method == "bootstrap":
            return self._simulate_bootstrap(returns, progress_callback)
        if self.config.method == "hybrid":
            return self._simulate_hybrid(returns, progress_callback)
        raise ValueError(f"未知模拟方法: {self.config.method}")

    # ------------------------------------------------------------------
    # Simulation methods
    # ------------------------------------------------------------------

    def _simulate_gbm(
        self,
        returns: NDArray[np.float64],
        progress_callback: callable | None = None,
    ) -> NDArray[np.float64]:
        """Geometric Brownian Motion simulation.

        Model: S(t+dt) = S(t) * exp((mu - sigma^2/2)*dt + sigma*sqrt(dt)*Z)
        where Z ~ N(0,1)

        This ensures prices remain positive and returns are log-normally
        distributed, which is the standard assumption in quantitative finance.

        参数估计说明：
        - 从对数收益率 ln(1+r) 估计 μ 和 σ，确保与 GBM 模型假设一致
        - 简单收益率 r 与对数收益率 ln(1+r) 在小值时近似相等，
          但在高波动率场景下差异显著
        """
        # 从对数收益率估计参数（与 GBM 模型假设一致）
        log_returns_hist = np.log(1 + returns)
        mu = np.mean(log_returns_hist)  # 对数收益率均值
        sigma = np.std(log_returns_hist, ddof=1)  # 对数收益率标准差

        dt = 1.0  # daily steps
        n_steps = self.config.horizon_days
        n_sims = self.config.num_simulations

        # Generate random shocks
        Z = self._rng.standard_normal((n_sims, n_steps))

        # Build price paths using GBM formula
        # 注意：mu 已经是对数收益率均值，GBM 公式中的 Itô 修正项
        # 使得 E[S(t)] = S(0) * exp(mu * t)
        # 单步对数收益 = (mu - 0.5*sigma^2)*dt + sigma*sqrt(dt)*Z
        log_returns_sim = (mu - 0.5 * sigma**2) * dt + sigma * np.sqrt(dt) * Z

        # Cumulative sum of log returns, prepend 0 for initial value
        cum_log_returns = np.zeros((n_sims, n_steps + 1))
        cum_log_returns[:, 1:] = np.cumsum(log_returns_sim, axis=1)

        # Convert to price paths (normalized to 1.0 at start)
        paths = np.exp(cum_log_returns) * self.config.initial_capital

        if progress_callback:
            progress_callback(60, "GBM 路径生成完成")

        return paths

    def _simulate_bootstrap(
        self,
        returns: NDArray[np.float64],
        progress_callback: callable | None = None,
    ) -> NDArray[np.float64]:
        """Bootstrap resampling simulation.

        Randomly samples (with replacement) from historical returns.
        Preserves the empirical distribution including fat tails and
        skewness that GBM's normality assumption misses.
        """
        n_steps = self.config.horizon_days
        n_sims = self.config.num_simulations
        n_hist = len(returns)

        # Random indices for resampling
        indices = self._rng.integers(0, n_hist, size=(n_sims, n_steps))

        # Build return matrix from resampled indices
        sampled_returns = returns[indices]

        # Convert returns to price paths
        # price(t) = price(0) * prod(1 + r_i)
        cum_returns = np.cumprod(1 + sampled_returns, axis=1)

        # Prepend 1.0 for initial value
        paths = np.ones((n_sims, n_steps + 1))
        paths[:, 1:] = cum_returns
        paths *= self.config.initial_capital

        if progress_callback:
            progress_callback(60, "Bootstrap 路径生成完成")

        return paths

    def _simulate_hybrid(
        self,
        returns: NDArray[np.float64],
        progress_callback: callable | None = None,
    ) -> NDArray[np.float64]:
        """Hybrid simulation: GBM with empirical tail correction.

        Uses GBM as the base model but replaces extreme quantiles
        with values drawn from the empirical distribution. This
        captures the drift/volatility structure of GBM while
        preserving fat-tail behavior observed in real markets.

        修正说明（v2）：
        - 统一在对数收益率空间操作，避免混淆简单收益率和对数收益率
        - 阈值判断和尾部替换均在对数空间进行
        - 经验尾部值也转换为对数收益率后再替换
        """
        # 统一在对数收益率空间操作
        log_returns_hist = np.log(1 + returns)
        mu_log = np.mean(log_returns_hist)
        sigma_log = np.std(log_returns_hist, ddof=1)

        dt = 1.0
        n_steps = self.config.horizon_days
        n_sims = self.config.num_simulations

        Z = self._rng.standard_normal((n_sims, n_steps))
        # GBM 对数收益率
        gbm_log_returns = (mu_log - 0.5 * sigma_log**2) * dt + sigma_log * np.sqrt(dt) * Z

        # 在对数收益率空间识别极端值（超过 2 sigma）
        threshold = 2.0 * sigma_log
        extreme_mask = np.abs(gbm_log_returns - (mu_log - 0.5 * sigma_log**2)) > threshold

        # 用经验分布的尾部值替换极端事件（也在对数空间）
        n_extreme = extreme_mask.sum()
        if n_extreme > 0:
            # 经验尾部：对数收益率中超过 2 sigma 的值
            tail_log_values = log_returns_hist[
                np.abs(log_returns_hist - mu_log) > threshold
            ]
            if len(tail_log_values) > 0:
                tail_samples = self._rng.choice(tail_log_values, size=n_extreme)
                gbm_log_returns[extreme_mask] = tail_samples
            # If no empirical tail data, keep GBM values

        # Build paths from log returns
        cum_log_returns = np.zeros((n_sims, n_steps + 1))
        cum_log_returns[:, 1:] = np.cumsum(gbm_log_returns, axis=1)
        paths = np.exp(cum_log_returns) * self.config.initial_capital

        if progress_callback:
            progress_callback(60, "Hybrid 路径生成完成")

        return paths

    # ------------------------------------------------------------------
    # Result computation
    # ------------------------------------------------------------------

    def _compute_results(
        self, paths: NDArray[np.float64]
    ) -> SimulationResult:
        """Compute risk metrics and percentile paths from simulation paths.

        Args:
            paths: Shape (n_sims, horizon_days+1), each row is a price path.
        """
        n_sims = paths.shape[0]
        n_steps = paths.shape[1] - 1
        initial = self.config.initial_capital

        # Terminal values
        terminal_values = paths[:, -1]

        # Total returns
        total_returns = (terminal_values - initial) / initial

        # Annualized returns (assuming 252 trading days/year)
        # 保护：当 total_return <= -1 时（理论上 GBM 不会出现，但 Bootstrap 可能），
        # 年化收益率设为 -1（即亏损 100%）
        years = n_steps / 252.0
        annualized_returns = np.where(
            total_returns <= -1.0,
            -1.0,
            (1 + total_returns) ** (1 / years) - 1,
        )

        # Percentile paths for fan chart
        percentiles = [5, 10, 25, 50, 75, 90, 95]
        percentile_paths = {}
        for p in percentiles:
            path_values = np.percentile(paths, p, axis=0)
            percentile_paths[f"p{p}"] = [round(float(v), 2) for v in path_values]

        # VaR and CVaR
        var_dict = {}
        cvar_dict = {}
        for cl in self.config.confidence_levels:
            alpha = 1 - cl
            var_quantile = np.percentile(total_returns, alpha * 100)
            var_dict[str(int(cl * 100))] = float(var_quantile)

            # CVaR = expected loss given loss exceeds VaR
            tail_returns = total_returns[total_returns <= var_quantile]
            cvar_dict[str(int(cl * 100))] = (
                float(np.mean(tail_returns)) if len(tail_returns) > 0 else float(var_quantile)
            )

        # Maximum drawdown distribution
        max_drawdowns = np.zeros(n_sims)
        for i in range(n_sims):
            path = paths[i]
            running_max = np.maximum.accumulate(path)
            drawdowns = (path - running_max) / running_max
            max_drawdowns[i] = float(np.min(drawdowns))

        # Target probability
        target_prob = None
        if self.config.target_return is not None:
            target_prob = float(
                np.mean(total_returns >= self.config.target_return)
            )

        # Volatility: annualized from daily path returns
        daily_returns_all = np.diff(paths, axis=1) / paths[:, :-1]
        avg_daily_vol = np.mean(np.std(daily_returns_all, axis=1, ddof=1))
        annualized_vol = avg_daily_vol * np.sqrt(252)

        return SimulationResult(
            percentile_paths=percentile_paths,
            expected_return=float(np.mean(annualized_returns)),
            median_return=float(np.median(annualized_returns)),
            volatility=float(annualized_vol),
            var=var_dict,
            cvar=cvar_dict,
            max_drawdown_median=float(np.median(max_drawdowns)),
            max_drawdown_p95=float(np.percentile(max_drawdowns, 5)),  # 5th percentile = worst 95%
            target_return=self.config.target_return,
            target_probability=target_prob,
            terminal_wealth_mean=float(np.mean(terminal_values)),
            terminal_wealth_median=float(np.median(terminal_values)),
            terminal_wealth_p5=float(np.percentile(terminal_values, 5)),
            terminal_wealth_p95=float(np.percentile(terminal_values, 95)),
        )
