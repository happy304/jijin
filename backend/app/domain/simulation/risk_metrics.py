"""Additional risk metrics computation for simulation results.

Provides utility functions for:
- Probability of ruin (capital falling below threshold)
- Sharpe ratio prediction
- Information ratio prediction
- Sortino ratio prediction
- Calmar ratio prediction
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray


@dataclass
class ExtendedRiskMetrics:
    """Extended risk metrics computed from simulation paths."""

    # Predicted ratios
    predicted_sharpe: float
    predicted_sortino: float
    predicted_calmar: float

    # Probability metrics
    prob_positive_return: float  # 正收益概率
    prob_loss_gt_10pct: float  # 亏损超过 10% 的概率
    prob_loss_gt_20pct: float  # 亏损超过 20% 的概率
    prob_ruin: float  # 资金低于 50% 的概率（破产概率）

    # Distribution shape
    skewness: float  # 收益分布偏度
    kurtosis: float  # 收益分布峰度（超额）

    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict."""
        return {
            "predicted_sharpe": round(self.predicted_sharpe, 4),
            "predicted_sortino": round(self.predicted_sortino, 4),
            "predicted_calmar": round(self.predicted_calmar, 4),
            "prob_positive_return": round(self.prob_positive_return, 4),
            "prob_loss_gt_10pct": round(self.prob_loss_gt_10pct, 4),
            "prob_loss_gt_20pct": round(self.prob_loss_gt_20pct, 4),
            "prob_ruin": round(self.prob_ruin, 4),
            "skewness": round(self.skewness, 4),
            "kurtosis": round(self.kurtosis, 4),
        }


def compute_extended_metrics(
    paths: NDArray[np.float64],
    risk_free_rate: float = 0.02,
) -> ExtendedRiskMetrics:
    """Compute extended risk metrics from simulation paths.

    Args:
        paths: Shape (n_sims, horizon_days+1), price paths.
        risk_free_rate: Annual risk-free rate (default 2% for China).

    Returns:
        ExtendedRiskMetrics with predicted ratios and probabilities.
    """
    n_sims = paths.shape[0]
    n_steps = paths.shape[1] - 1
    initial = paths[0, 0]  # All paths start at same initial value

    # Terminal returns
    terminal_returns = (paths[:, -1] - initial) / initial

    # Daily returns for each path
    daily_returns = np.diff(paths, axis=1) / paths[:, :-1]

    # Annualized metrics per path
    years = n_steps / 252.0
    daily_rf = risk_free_rate / 252.0

    # Predicted Sharpe (average across paths)
    excess_daily = daily_returns - daily_rf
    mean_excess_daily = np.mean(excess_daily, axis=1)
    daily_std = np.std(daily_returns, axis=1, ddof=1)
    path_sharpes = np.divide(
        mean_excess_daily,
        daily_std,
        out=np.zeros_like(mean_excess_daily, dtype=np.float64),
        where=daily_std > 1e-12,
    ) * np.sqrt(252)
    # Filter out inf/nan
    valid_sharpes = path_sharpes[np.isfinite(path_sharpes)]
    predicted_sharpe = float(np.median(valid_sharpes)) if len(valid_sharpes) > 0 else 0.0

    # Predicted Sortino
    downside_returns = np.where(daily_returns < 0, daily_returns, 0)
    downside_std = np.sqrt(np.mean(downside_returns**2, axis=1))
    mean_daily = np.mean(daily_returns, axis=1)
    path_sortinos = np.divide(
        mean_daily - daily_rf,
        downside_std,
        out=np.zeros_like(downside_std, dtype=np.float64),
        where=downside_std > 1e-12,
    ) * np.sqrt(252)
    valid_sortinos = path_sortinos[np.isfinite(path_sortinos)]
    predicted_sortino = float(np.median(valid_sortinos)) if len(valid_sortinos) > 0 else 0.0

    # Predicted Calmar (return / max drawdown)
    max_drawdowns = np.zeros(n_sims)
    for i in range(n_sims):
        running_max = np.maximum.accumulate(paths[i])
        drawdowns = (paths[i] - running_max) / running_max
        max_drawdowns[i] = abs(float(np.min(drawdowns)))

    annualized_returns = (1 + terminal_returns) ** (1 / years) - 1
    valid_dd_mask = max_drawdowns > 1e-10
    path_calmars = np.divide(
        annualized_returns,
        max_drawdowns,
        out=np.zeros_like(annualized_returns, dtype=np.float64),
        where=valid_dd_mask,
    )
    valid_calmars = path_calmars[np.isfinite(path_calmars)]
    predicted_calmar = float(np.median(valid_calmars)) if len(valid_calmars) > 0 else 0.0

    # Probability metrics
    prob_positive = float(np.mean(terminal_returns > 0))
    prob_loss_10 = float(np.mean(terminal_returns < -0.10))
    prob_loss_20 = float(np.mean(terminal_returns < -0.20))
    prob_ruin = float(np.mean(terminal_returns < -0.50))

    # Distribution shape
    skewness = float(_skewness(terminal_returns))
    kurtosis = float(_excess_kurtosis(terminal_returns))

    return ExtendedRiskMetrics(
        predicted_sharpe=predicted_sharpe,
        predicted_sortino=predicted_sortino,
        predicted_calmar=predicted_calmar,
        prob_positive_return=prob_positive,
        prob_loss_gt_10pct=prob_loss_10,
        prob_loss_gt_20pct=prob_loss_20,
        prob_ruin=prob_ruin,
        skewness=skewness,
        kurtosis=kurtosis,
    )


def _skewness(x: NDArray[np.float64]) -> float:
    """Compute adjusted sample skewness (unbiased estimator).

    公式: g1 = [n^2 / ((n-1)(n-2))] * (1/n) * Σ[(x_i - mean) / std]^3
    等价于 scipy.stats.skew(x, bias=False)
    """
    n = len(x)
    if n < 3:
        return 0.0
    mean = np.mean(x)
    std = np.std(x, ddof=1)
    if std < 1e-12:
        return 0.0
    m3 = float(np.mean(((x - mean) / std) ** 3))
    # 无偏修正: g1 = [n / ((n-1)(n-2))] * Σ[(xi-mean)/s]^3
    #         = [n / ((n-1)(n-2))] * n * m3
    #         = [n^2 / ((n-1)(n-2))] * m3
    adjustment = (n * n) / ((n - 1) * (n - 2))
    return float(m3 * adjustment)


def _excess_kurtosis(x: NDArray[np.float64]) -> float:
    """Compute excess kurtosis (Fisher's definition, bias-corrected).

    使用简化的超额峰度估计: E[(x-mean)^4]/std^4 - 3
    注意：这是有偏估计（与 scipy.stats.kurtosis(x, bias=True) 一致）。
    对于大样本（n > 100，模拟场景通常 n=10000），偏差可忽略。
    """
    n = len(x)
    if n < 4:
        return 0.0
    mean = np.mean(x)
    std = np.std(x, ddof=1)
    if std < 1e-12:
        return 0.0
    return float(np.mean(((x - mean) / std) ** 4) - 3.0)
