"""均值-方差与 Black-Litterman 策略模块。

实现基于现代投资组合理论的资产配置策略：
- MeanVarianceStrategy: 经典 Markowitz 均值-方差优化
- BlackLittermanStrategy: Black-Litterman 模型，融合市场均衡与用户观点

设计要点：
- 继承 BaseStrategy，实现 on_bar 方法
- 使用 scipy.optimize.minimize 求解最优权重
- MV 优化支持最大 Sharpe、最小方差、目标收益三种模式
- BL 模型支持用户观点矩阵（绝对观点与相对观点）
- 在调仓日通过 rebalance_to 生成最小化调仓指令
- 非调仓日返回空列表，保持持仓不变

需求: 5.3
"""

from __future__ import annotations

import math
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Literal

import numpy as np
from pydantic import Field
from scipy.optimize import minimize

from app.domain.backtest.engine_event import BarContext
from app.domain.backtest.order import OrderIntent
from app.domain.strategy.base import BaseStrategy, StrategyParams, rebalance_to


# ---------------------------------------------------------------------------
# 优化目标枚举
# ---------------------------------------------------------------------------


class OptObjective(str, Enum):
    """均值-方差优化目标。"""

    MAX_SHARPE = "max_sharpe"
    MIN_VARIANCE = "min_variance"
    TARGET_RETURN = "target_return"


# ---------------------------------------------------------------------------
# 调仓频率枚举
# ---------------------------------------------------------------------------


class RebalanceFreq(str, Enum):
    """调仓频率。"""

    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"


# ---------------------------------------------------------------------------
# 参数类
# ---------------------------------------------------------------------------


class MeanVarianceParams(StrategyParams):
    """均值-方差策略参数。

    Attributes:
        lookback_days: 回看窗口天数（用于估计收益率和协方差）
        rebalance_freq: 调仓频率
        objective: 优化目标（max_sharpe/min_variance/target_return）
        target_annual_return: 目标年化收益率（仅 target_return 模式使用）
        risk_free_rate: 年化无风险利率
        allow_short: 是否允许做空
    """

    lookback_days: int = Field(default=60, gt=0, description="回看窗口天数")
    rebalance_freq: RebalanceFreq = Field(
        default=RebalanceFreq.MONTHLY, description="调仓频率"
    )
    objective: OptObjective = Field(
        default=OptObjective.MAX_SHARPE, description="优化目标"
    )
    target_annual_return: float = Field(
        default=0.08, description="目标年化收益率"
    )
    risk_free_rate: float = Field(
        default=0.02, description="年化无风险利率"
    )
    allow_short: bool = Field(default=False, description="是否允许做空")
    max_weight: float = Field(default=0.4, gt=0, le=1, description="单基金最大权重")
    min_weight: float = Field(default=0.0, ge=0, lt=1, description="单基金最小权重")
    turnover_limit: float | None = Field(default=None, gt=0, le=1, description="单次调仓最大单边换手率；None 表示不限制")
    min_observations: int = Field(default=60, ge=2, description="最小有效样本数（兼容旧字段）")
    min_history_days: int = Field(default=60, ge=2, description="最小历史收益样本天数")
    max_condition_number: float = Field(default=1e8, gt=0, description="协方差矩阵条件数阈值（兼容旧字段）")
    condition_number_threshold: float = Field(default=1e8, gt=0, description="协方差矩阵条件数门槛")
    fallback_method: Literal["equal_weight", "inverse_vol"] = Field(
        default="equal_weight", description="质量门禁触发时的降级权重方法"
    )
    shrinkage: float = Field(default=1e-6, ge=0, description="协方差矩阵对角稳定化强度（兼容旧字段）")
    cov_shrinkage: float = Field(default=1e-6, ge=0, description="协方差矩阵收缩/稳定化强度")


class BlackLittermanParams(StrategyParams):
    """Black-Litterman 策略参数。

    Attributes:
        lookback_days: 回看窗口天数
        rebalance_freq: 调仓频率
        tau: 不确定性缩放因子（通常取 0.01~0.1）
        risk_aversion: 风险厌恶系数（δ），默认 2.5
        risk_free_rate: 年化无风险利率
        allow_short: 是否允许做空
    """

    lookback_days: int = Field(default=60, gt=0, description="回看窗口天数")
    rebalance_freq: RebalanceFreq = Field(
        default=RebalanceFreq.MONTHLY, description="调仓频率"
    )
    tau: float = Field(default=0.05, gt=0, description="不确定性缩放因子")
    risk_aversion: float = Field(default=2.5, gt=0, description="风险厌恶系数")
    risk_free_rate: float = Field(default=0.02, description="年化无风险利率")
    allow_short: bool = Field(default=False, description="是否允许做空")
    max_weight: float = Field(default=0.4, gt=0, le=1, description="单基金最大权重")
    min_weight: float = Field(default=0.0, ge=0, lt=1, description="单基金最小权重")
    turnover_limit: float | None = Field(default=None, gt=0, le=1, description="单次调仓最大单边换手率")
    min_history_days: int = Field(default=60, ge=2, description="最小历史收益样本天数")
    condition_number_threshold: float = Field(default=1e8, gt=0, description="协方差矩阵条件数门槛")
    cov_shrinkage: float = Field(default=1e-6, ge=0, description="协方差矩阵稳定化强度")
    fallback_method: Literal["equal_weight", "inverse_vol"] = Field(
        default="equal_weight", description="质量门禁触发时的降级权重方法"
    )


# ---------------------------------------------------------------------------
# 观点定义
# ---------------------------------------------------------------------------


class View:
    """用户观点。

    支持绝对观点和相对观点：
    - 绝对观点：资产 i 的预期收益为 q（如"基金 A 年化收益 10%"）
    - 相对观点：资产 i 相对资产 j 的超额收益为 q（如"基金 A 比基金 B 多涨 5%"）

    Attributes:
        p_row: 观点向量（对应 P 矩阵的一行）
        q: 观点预期收益
        confidence: 观点置信度（0~1），越高表示越确信
    """

    def __init__(
        self,
        p_row: list[float],
        q: float,
        confidence: float = 0.5,
    ) -> None:
        self.p_row = p_row
        self.q = q
        self.confidence = max(0.01, min(confidence, 0.99))


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
    - quarterly: 距上次调仓 >= 84 天

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
    elif freq == RebalanceFreq.MONTHLY:
        return days_since_last >= 28
    else:  # QUARTERLY
        return days_since_last >= 84


# ---------------------------------------------------------------------------
# 均值-方差优化求解
# ---------------------------------------------------------------------------


def _fallback_mv_weights(
    cov_matrix: np.ndarray,
    method: Literal["equal_weight", "inverse_vol"] = "equal_weight",
    max_weight: float = 0.4,
) -> np.ndarray | None:
    """生成保守降级权重，并尽量满足单基金上限。"""
    n = cov_matrix.shape[0]
    if n == 0:
        return None
    if method == "inverse_vol":
        diag = np.diag(cov_matrix) if cov_matrix.ndim == 2 else np.ones(n)
        vols = np.sqrt(np.maximum(diag, 1e-12))
        raw = 1.0 / vols
        weights = raw / raw.sum() if raw.sum() > 0 else np.ones(n) / n
    else:
        weights = np.ones(n) / n
    feasible_cap = max(max_weight, 1.0 / n)
    for _ in range(20):
        excess = weights > feasible_cap
        if not np.any(excess):
            break
        overflow = float(np.sum(weights[excess] - feasible_cap))
        weights[excess] = feasible_cap
        room = ~excess
        room_capacity = feasible_cap - weights[room]
        capacity_sum = float(np.sum(np.maximum(room_capacity, 0)))
        if capacity_sum <= 0:
            break
        weights[room] += overflow * np.maximum(room_capacity, 0) / capacity_sum
    total = float(weights.sum())
    return weights / total if total > 0 else None


def _stabilize_covariance(
    cov_matrix: np.ndarray,
    shrinkage: float,
) -> tuple[np.ndarray | None, float | None, bool]:
    """清理并轻量稳定化协方差矩阵。"""
    cov = np.asarray(cov_matrix, dtype=float)
    if cov.ndim != 2 or cov.shape[0] != cov.shape[1] or not np.all(np.isfinite(cov)):
        return None, None, False
    cov = (cov + cov.T) / 2.0
    ridge_applied = False
    if shrinkage > 0:
        avg_var = float(np.nanmean(np.diag(cov))) if cov.shape[0] else 0.0
        ridge = max(avg_var, 1e-12) * shrinkage
        cov = cov + np.eye(cov.shape[0]) * ridge
        ridge_applied = True
    try:
        condition_number = float(np.linalg.cond(cov))
    except np.linalg.LinAlgError:
        return None, None, ridge_applied
    return cov, condition_number, ridge_applied


def mv_optimize_with_diagnostics(
    expected_returns: np.ndarray,
    cov_matrix: np.ndarray,
    objective: OptObjective,
    risk_free_rate: float = 0.02,
    target_return: float = 0.08,
    allow_short: bool = False,
    max_weight: float = 1.0,
    min_weight: float = 0.0,
    min_observations: int | None = None,
    n_observations: int | None = None,
    max_condition_number: float = 1e8,
    fallback_method: Literal["equal_weight", "inverse_vol"] = "equal_weight",
    shrinkage: float = 1e-6,
) -> tuple[np.ndarray | None, dict[str, object]]:
    """求解均值-方差权重，并返回约束、质量门禁与 fallback 诊断。"""
    expected_returns = np.asarray(expected_returns, dtype=float)
    n = cov_matrix.shape[0]
    diagnostics: dict[str, object] = {
        "objective": objective.value,
        "n_assets": n,
        "n_observations": n_observations,
        "min_observations": min_observations,
        "max_condition_number": max_condition_number,
        "fallback": False,
        "fallback_method": None,
        "warnings": [],
    }
    if n == 0 or expected_returns.shape[0] != n:
        diagnostics["fallback"] = True
        diagnostics["warnings"] = ["输入资产数量为空或收益向量维度不匹配"]
        return None, diagnostics
    if n == 1:
        diagnostics["constraints"] = {"max_weight": 1.0, "min_weight": 1.0, "allow_short": allow_short}
        return np.array([1.0]), diagnostics

    feasible_max = max(max_weight, 1.0 / n)
    feasible_min = min(min_weight, 1.0 / n) if not allow_short else min_weight
    warnings: list[str] = []
    if feasible_max > max_weight:
        warnings.append(f"单基金上限 {max_weight:.2%} 对 {n} 只资产不可行，已放宽至 {feasible_max:.2%}")
    diagnostics["constraints"] = {
        "max_weight": max_weight,
        "effective_max_weight": feasible_max,
        "min_weight": min_weight,
        "effective_min_weight": feasible_min,
        "allow_short": allow_short,
    }

    def fallback(reason: str, matrix: np.ndarray | None = None) -> tuple[np.ndarray | None, dict[str, object]]:
        weights = _fallback_mv_weights(matrix if matrix is not None else np.eye(n), fallback_method, max_weight)
        diagnostics["fallback"] = True
        diagnostics["fallback_method"] = fallback_method
        diagnostics["validation_status"] = "research_only"
        diagnostics["warnings"] = warnings + [reason]
        if weights is not None:
            diagnostics["weight_diagnostics"] = {
                "top1_weight": round(float(np.max(weights)), 4),
                "hhi": round(float(np.sum(weights * weights)), 4),
            }
        return weights, diagnostics

    cov_stable, condition_number, ridge_applied = _stabilize_covariance(cov_matrix, shrinkage)
    diagnostics["condition_number"] = condition_number
    diagnostics["ridge_applied"] = ridge_applied
    if cov_stable is None or not np.all(np.isfinite(expected_returns)):
        return fallback("协方差矩阵或预期收益包含无效值")
    cov_matrix = cov_stable

    if min_observations is not None and n_observations is not None and n_observations < min_observations:
        return fallback(f"样本数 {n_observations} 低于门槛 {min_observations}", cov_matrix)
    if condition_number is None or condition_number > max_condition_number:
        return fallback(f"协方差矩阵条件数 {condition_number} 超过阈值 {max_condition_number}", cov_matrix)

    if allow_short:
        bounds = [(-feasible_max, feasible_max)] * n
    else:
        bounds = [(feasible_min, feasible_max)] * n

    constraints: list[dict] = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]
    w0 = np.ones(n) / n

    if objective == OptObjective.MAX_SHARPE:
        def neg_sharpe(w: np.ndarray) -> float:
            port_return = w @ expected_returns
            port_var = w @ cov_matrix @ w
            if port_var <= 1e-12 or not np.isfinite(port_var):
                return 1e10
            port_vol = np.sqrt(port_var)
            sharpe = (port_return - risk_free_rate) / port_vol
            return -float(sharpe) if np.isfinite(sharpe) else 1e10

        result = minimize(
            neg_sharpe,
            w0,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"maxiter": 1000, "ftol": 1e-12},
        )

    elif objective == OptObjective.MIN_VARIANCE:
        def portfolio_variance(w: np.ndarray) -> float:
            return float(w @ cov_matrix @ w)

        result = minimize(
            portfolio_variance,
            w0,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"maxiter": 1000, "ftol": 1e-12},
        )

    elif objective == OptObjective.TARGET_RETURN:
        def portfolio_variance_tr(w: np.ndarray) -> float:
            return float(w @ cov_matrix @ w)

        constraints.append(
            {"type": "ineq", "fun": lambda w: w @ expected_returns - target_return}
        )
        result = minimize(
            portfolio_variance_tr,
            w0,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"maxiter": 1000, "ftol": 1e-12},
        )
    else:
        return None, diagnostics

    if not result.success:
        return fallback(f"优化器未收敛：{getattr(result, 'message', 'unknown')}", cov_matrix)

    weights = result.x
    if not allow_short:
        weights = np.maximum(weights, 0)
    weight_sum = weights.sum()
    if weight_sum > 0:
        weights = weights / weight_sum
    else:
        return fallback("优化结果权重和无效", cov_matrix)

    if not allow_short and np.max(weights) > feasible_max + 1e-5:
        return fallback("优化结果超过单基金权重上限", cov_matrix)
    diagnostics["validation_status"] = "available" if not warnings else "research_only"
    diagnostics["warnings"] = warnings
    diagnostics["weight_diagnostics"] = {
        "top1_weight": round(float(np.max(weights)), 4),
        "hhi": round(float(np.sum(weights * weights)), 4),
    }
    return weights, diagnostics


def mv_optimize(
    expected_returns: np.ndarray,
    cov_matrix: np.ndarray,
    objective: OptObjective,
    risk_free_rate: float = 0.02,
    target_return: float = 0.08,
    allow_short: bool = False,
    max_weight: float = 1.0,
    min_weight: float = 0.0,
    min_observations: int | None = None,
    n_observations: int | None = None,
    max_condition_number: float = 1e8,
    fallback_method: Literal["equal_weight", "inverse_vol"] = "equal_weight",
    shrinkage: float = 1e-6,
) -> np.ndarray | None:
    """兼容旧接口：仅返回均值-方差权重。"""
    weights, _ = mv_optimize_with_diagnostics(
        expected_returns,
        cov_matrix,
        objective,
        risk_free_rate=risk_free_rate,
        target_return=target_return,
        allow_short=allow_short,
        max_weight=max_weight,
        min_weight=min_weight,
        min_observations=min_observations,
        n_observations=n_observations,
        max_condition_number=max_condition_number,
        fallback_method=fallback_method,
        shrinkage=shrinkage,
    )
    return weights


# ---------------------------------------------------------------------------
# Black-Litterman 模型
# ---------------------------------------------------------------------------


def compute_equilibrium_returns(
    cov_matrix: np.ndarray,
    market_weights: np.ndarray,
    risk_aversion: float = 2.5,
    risk_free_rate: float = 0.02,
) -> np.ndarray:
    """计算市场均衡隐含收益率（Π）。

    Π = δ × Σ × w_mkt

    其中：
    - δ: 风险厌恶系数
    - Σ: 协方差矩阵
    - w_mkt: 市场权重（等权或市值加权）

    Args:
        cov_matrix: 协方差矩阵，shape=(N, N)
        market_weights: 市场均衡权重，shape=(N,)
        risk_aversion: 风险厌恶系数
        risk_free_rate: 无风险利率

    Returns:
        隐含均衡收益率向量，shape=(N,)
    """
    return risk_aversion * cov_matrix @ market_weights


def black_litterman_posterior(
    cov_matrix: np.ndarray,
    equilibrium_returns: np.ndarray,
    P: np.ndarray,
    Q: np.ndarray,
    omega: np.ndarray,
    tau: float = 0.05,
) -> tuple[np.ndarray, np.ndarray]:
    """计算 Black-Litterman 后验收益率和协方差。

    后验公式：
    E[R] = [(τΣ)^{-1} + P'Ω^{-1}P]^{-1} × [(τΣ)^{-1}Π + P'Ω^{-1}Q]
    后验协方差 = [(τΣ)^{-1} + P'Ω^{-1}P]^{-1}

    Args:
        cov_matrix: 协方差矩阵 Σ，shape=(N, N)
        equilibrium_returns: 均衡收益率 Π，shape=(N,)
        P: 观点矩阵，shape=(K, N)，K 为观点数
        Q: 观点收益率向量，shape=(K,)
        omega: 观点不确定性矩阵 Ω，shape=(K, K)，对角矩阵
        tau: 不确定性缩放因子

    Returns:
        (posterior_returns, posterior_cov) 元组
    """
    n = cov_matrix.shape[0]

    # τΣ
    tau_sigma = tau * cov_matrix

    # (τΣ)^{-1}
    tau_sigma_inv = np.linalg.inv(tau_sigma)

    # Ω^{-1}
    omega_inv = np.linalg.inv(omega)

    # 后验精度矩阵
    # M = (τΣ)^{-1} + P'Ω^{-1}P
    M = tau_sigma_inv + P.T @ omega_inv @ P

    # 后验协方差
    posterior_cov = np.linalg.inv(M)

    # 后验收益率
    # E[R] = posterior_cov × [(τΣ)^{-1}Π + P'Ω^{-1}Q]
    posterior_returns = posterior_cov @ (tau_sigma_inv @ equilibrium_returns + P.T @ omega_inv @ Q)

    return posterior_returns, posterior_cov


def build_omega_from_views(
    views: list[View],
    P: np.ndarray,
    cov_matrix: np.ndarray,
    tau: float,
) -> np.ndarray:
    """根据观点置信度构建 Ω 矩阵。

    Ω 为对角矩阵，对角元素 ω_k = (1/c_k - 1) × p_k' × (τΣ) × p_k
    其中 c_k 为第 k 个观点的置信度。

    置信度越高，ω_k 越小，观点对后验的影响越大。

    Args:
        views: 观点列表
        P: 观点矩阵，shape=(K, N)
        cov_matrix: 协方差矩阵
        tau: 不确定性缩放因子

    Returns:
        Ω 矩阵，shape=(K, K)
    """
    k = len(views)
    omega = np.zeros((k, k))
    tau_sigma = tau * cov_matrix

    for i, view in enumerate(views):
        p_i = P[i]
        # ω_i = (1/confidence - 1) × p_i' × τΣ × p_i
        view_var = p_i @ tau_sigma @ p_i
        omega[i, i] = ((1.0 / view.confidence) - 1.0) * view_var

    return omega


# ---------------------------------------------------------------------------
# 收益率矩阵计算辅助
# ---------------------------------------------------------------------------


def compute_returns_matrix(
    context: BarContext,
    universe: list[str],
    lookback_days: int,
) -> tuple[np.ndarray | None, list[str]]:
    """从 context 中提取收益率矩阵。

    Args:
        context: 策略上下文
        universe: 基金池
        lookback_days: 回看窗口天数

    Returns:
        (returns_matrix, valid_codes) 元组
    """
    nav_series_list: list[dict[date, Decimal]] = []
    valid_codes: list[str] = []

    for code in universe:
        nav_series = context.nav_series(code)
        if len(nav_series) >= lookback_days // 2:
            nav_series_list.append(nav_series)
            valid_codes.append(code)

    if len(valid_codes) < 2:
        return None, valid_codes

    # 找到共同日期
    common_dates = set(nav_series_list[0].keys())
    for nav_series in nav_series_list[1:]:
        common_dates &= set(nav_series.keys())

    sorted_dates = sorted(common_dates)

    if len(sorted_dates) > lookback_days:
        sorted_dates = sorted_dates[-lookback_days:]

    if len(sorted_dates) < 10:
        return None, []

    # 构建净值矩阵
    n_assets = len(valid_codes)
    n_dates = len(sorted_dates)
    nav_matrix = np.zeros((n_dates, n_assets))
    for j, nav_series in enumerate(nav_series_list):
        for i, d in enumerate(sorted_dates):
            nav_matrix[i, j] = float(nav_series[d])

    # 日收益率
    returns_matrix = nav_matrix[1:] / nav_matrix[:-1] - 1.0

    if not np.all(np.isfinite(returns_matrix)):
        return None, []

    return returns_matrix, valid_codes


# ---------------------------------------------------------------------------
# 均值-方差策略
# ---------------------------------------------------------------------------


class MeanVarianceStrategy(BaseStrategy):
    """均值-方差优化策略。"""

    name = "mean_variance"

    def __init__(
        self,
        params: MeanVarianceParams | None = None,
        universe: list[str] | None = None,
    ) -> None:
        super().__init__(params=params, universe=universe)
        self._last_rebalance_date: date | None = None
        self.last_diagnostics: dict[str, object] = {}

    @property
    def mv_params(self) -> MeanVarianceParams:
        """获取类型化的参数。"""
        assert isinstance(self.params, MeanVarianceParams)
        return self.params

    def on_bar(self, context: BarContext) -> list[OrderIntent]:
        """每个交易日判断是否调仓。"""
        if not is_rebalance_day(
            context.date,
            self._last_rebalance_date,
            self.mv_params.rebalance_freq,
        ):
            return []

        returns_matrix, valid_codes = compute_returns_matrix(
            context, self.universe, self.mv_params.lookback_days
        )
        if returns_matrix is None or len(valid_codes) < 2:
            return []

        daily_mean = returns_matrix.mean(axis=0)
        expected_returns = daily_mean * 252
        cov_matrix = np.cov(returns_matrix, rowvar=False, ddof=1) * 252
        if not np.all(np.isfinite(cov_matrix)):
            return []

        weights, diagnostics = mv_optimize_with_diagnostics(
            expected_returns=expected_returns,
            cov_matrix=cov_matrix,
            objective=self.mv_params.objective,
            risk_free_rate=self.mv_params.risk_free_rate,
            target_return=self.mv_params.target_annual_return,
            allow_short=self.mv_params.allow_short,
            max_weight=self.mv_params.max_weight,
            min_weight=self.mv_params.min_weight,
            min_observations=max(self.mv_params.min_observations, self.mv_params.min_history_days),
            n_observations=returns_matrix.shape[0],
            max_condition_number=min(self.mv_params.max_condition_number, self.mv_params.condition_number_threshold),
            fallback_method=self.mv_params.fallback_method,
            shrinkage=max(self.mv_params.shrinkage, self.mv_params.cov_shrinkage),
        )
        self.last_diagnostics = diagnostics
        if weights is None:
            return []

        target_weights: dict[str, float] = {}
        for i, code in enumerate(valid_codes):
            w = float(weights[i])
            if abs(w) > 1e-6:
                target_weights[code] = w
        if not target_weights:
            return []

        self._last_rebalance_date = context.date
        if self.mv_params.turnover_limit is not None:
            self.last_diagnostics["turnover_limit"] = self.mv_params.turnover_limit
        return rebalance_to(context, target_weights, turnover_limit=self.mv_params.turnover_limit)


# ---------------------------------------------------------------------------
# Black-Litterman 策略
# ---------------------------------------------------------------------------


class BlackLittermanStrategy(BaseStrategy):
    """Black-Litterman 策略。"""

    name = "black_litterman"

    def __init__(
        self,
        params: BlackLittermanParams | None = None,
        universe: list[str] | None = None,
    ) -> None:
        super().__init__(params=params, universe=universe)
        self._last_rebalance_date: date | None = None
        self._views: list[View] = []
        self.last_diagnostics: dict[str, object] = {}

    @property
    def bl_params(self) -> BlackLittermanParams:
        """获取类型化的参数。"""
        assert isinstance(self.params, BlackLittermanParams)
        return self.params

    def set_views(self, views: list[View]) -> None:
        """设置用户观点。"""
        self._views = views

    def on_bar(self, context: BarContext) -> list[OrderIntent]:
        """每个交易日判断是否调仓。"""
        if not is_rebalance_day(
            context.date,
            self._last_rebalance_date,
            self.bl_params.rebalance_freq,
        ):
            return []

        returns_matrix, valid_codes = compute_returns_matrix(
            context, self.universe, self.bl_params.lookback_days
        )
        if returns_matrix is None or len(valid_codes) < 2:
            return []

        n = len(valid_codes)
        cov_matrix = np.cov(returns_matrix, rowvar=False, ddof=1) * 252
        if not np.all(np.isfinite(cov_matrix)):
            return []

        market_weights = np.ones(n) / n
        equilibrium_returns = compute_equilibrium_returns(
            cov_matrix=cov_matrix,
            market_weights=market_weights,
            risk_aversion=self.bl_params.risk_aversion,
            risk_free_rate=self.bl_params.risk_free_rate,
        )

        posterior_returns = equilibrium_returns
        if self._views:
            valid_views = [v for v in self._views if len(v.p_row) == n]
            if valid_views:
                P = np.array([v.p_row for v in valid_views])
                Q = np.array([v.q for v in valid_views])
                omega = build_omega_from_views(valid_views, P, cov_matrix, self.bl_params.tau)
                diag_omega = np.diag(omega)
                if np.all(diag_omega > 0):
                    posterior_returns, _ = black_litterman_posterior(
                        cov_matrix=cov_matrix,
                        equilibrium_returns=equilibrium_returns,
                        P=P,
                        Q=Q,
                        omega=omega,
                        tau=self.bl_params.tau,
                    )

        weights, diagnostics = mv_optimize_with_diagnostics(
            expected_returns=posterior_returns,
            cov_matrix=cov_matrix,
            objective=OptObjective.MAX_SHARPE,
            risk_free_rate=self.bl_params.risk_free_rate,
            allow_short=self.bl_params.allow_short,
            max_weight=self.bl_params.max_weight,
            min_weight=self.bl_params.min_weight,
            min_observations=max(self.bl_params.lookback_days, self.bl_params.min_history_days),
            n_observations=returns_matrix.shape[0],
            max_condition_number=self.bl_params.condition_number_threshold,
            fallback_method=self.bl_params.fallback_method,
            shrinkage=self.bl_params.cov_shrinkage,
        )
        self.last_diagnostics = diagnostics
        if weights is None:
            return []

        target_weights: dict[str, float] = {}
        for i, code in enumerate(valid_codes):
            w = float(weights[i])
            if abs(w) > 1e-6:
                target_weights[code] = w
        if not target_weights:
            return []

        self._last_rebalance_date = context.date
        if self.bl_params.turnover_limit is not None:
            self.last_diagnostics["turnover_limit"] = self.bl_params.turnover_limit
        return rebalance_to(context, target_weights, turnover_limit=self.bl_params.turnover_limit)

