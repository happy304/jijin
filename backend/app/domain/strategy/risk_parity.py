"""风险平价策略模块。

实现基于等风险贡献（Equal Risk Contribution）的资产配置策略：
- RiskParityStrategy: 优化权重使每只资产对组合风险的贡献相等

设计要点：
- 继承 BaseStrategy，实现 on_bar 方法
- 使用 scipy.optimize.minimize 求解等风险贡献权重
- 支持三种协方差估计方法：样本协方差、指数加权、Ledoit-Wolf 收缩估计
- 在调仓日通过 rebalance_to 生成最小化调仓指令
- 非调仓日返回空列表，保持持仓不变

优化目标：min Σ(w_i × (Σ_w)_i - TargetRC)²
其中 TargetRC = σ_p / N（每只资产的目标风险贡献相等）

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
# 协方差估计方法枚举
# ---------------------------------------------------------------------------


class CovMethod(str, Enum):
    """协方差矩阵估计方法。"""

    SAMPLE = "sample"
    EWM = "ewm"
    SHRINKAGE = "shrinkage"


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


class RiskParityParams(StrategyParams):
    """风险平价策略参数。

    Attributes:
        lookback_days: 回看窗口天数（用于计算协方差矩阵的历史数据长度）
        rebalance_freq: 调仓频率（weekly/monthly/quarterly）
        cov_method: 协方差估计方法（sample/ewm/shrinkage）
    """

    lookback_days: int = Field(default=60, gt=0, description="回看窗口天数")
    rebalance_freq: RebalanceFreq = Field(
        default=RebalanceFreq.MONTHLY, description="调仓频率"
    )
    cov_method: CovMethod = Field(
        default=CovMethod.SAMPLE, description="协方差估计方法"
    )


# ---------------------------------------------------------------------------
# 协方差估计函数
# ---------------------------------------------------------------------------


def estimate_covariance_sample(returns: np.ndarray) -> np.ndarray:
    """计算样本协方差矩阵。

    Args:
        returns: 收益率矩阵，shape=(T, N)，T 为时间步数，N 为资产数

    Returns:
        协方差矩阵，shape=(N, N)
    """
    # 使用无偏估计（ddof=1）
    return np.cov(returns, rowvar=False, ddof=1)


def estimate_covariance_ewm(returns: np.ndarray, halflife: float | None = None) -> np.ndarray:
    """计算指数加权协方差矩阵。

    使用指数加权移动平均（EWMA）方法，近期数据权重更高。
    halflife 默认为 lookback/2。

    Args:
        returns: 收益率矩阵，shape=(T, N)
        halflife: 半衰期（天数），默认为 T/2

    Returns:
        协方差矩阵，shape=(N, N)
    """
    t, n = returns.shape
    if halflife is None:
        halflife = t / 2.0

    # 计算衰减因子
    decay = 1 - np.exp(-np.log(2) / halflife)

    # 计算权重（从最旧到最新递增）
    weights = np.array([(1 - decay) ** (t - 1 - i) for i in range(t)])
    weights = weights / weights.sum()

    # 加权均值
    weighted_mean = (weights[:, np.newaxis] * returns).sum(axis=0)

    # 加权协方差
    demeaned = returns - weighted_mean
    cov_matrix = (weights[:, np.newaxis] * demeaned).T @ demeaned

    return cov_matrix


def estimate_covariance_shrinkage(returns: np.ndarray) -> np.ndarray:
    """计算 Ledoit-Wolf 收缩协方差矩阵。

    将样本协方差向单位矩阵（缩放后）收缩，减少估计误差。
    收缩目标为 μI，其中 μ = trace(S) / N。

    实现 Ledoit & Wolf (2004) 的解析收缩强度公式。

    Args:
        returns: 收益率矩阵，shape=(T, N)

    Returns:
        收缩后的协方差矩阵，shape=(N, N)
    """
    t, n = returns.shape

    # 样本协方差
    sample_cov = np.cov(returns, rowvar=False, ddof=1)

    # 收缩目标：缩放的单位矩阵
    mu = np.trace(sample_cov) / n
    target = mu * np.eye(n)

    # 计算最优收缩强度（Ledoit-Wolf 解析公式简化版）
    # δ² = ||S - F||² (样本协方差与目标的距离)
    delta = sample_cov - target
    delta_sq_sum = np.sum(delta**2)

    # 计算 β（样本协方差估计的方差）
    # 使用简化的估计方法
    x = returns - returns.mean(axis=0)
    beta_sum = 0.0
    for k in range(t):
        xk = x[k : k + 1, :]  # (1, N)
        m_k = xk.T @ xk  # (N, N)
        beta_sum += np.sum((m_k - sample_cov) ** 2)
    beta = beta_sum / (t**2)

    # 收缩强度 α = β / δ²
    if delta_sq_sum > 0:
        alpha = min(beta / delta_sq_sum, 1.0)
    else:
        alpha = 1.0

    # 收缩后的协方差矩阵
    shrunk_cov = alpha * target + (1 - alpha) * sample_cov

    return shrunk_cov


def estimate_covariance(
    returns: np.ndarray,
    method: CovMethod,
    halflife: float | None = None,
) -> np.ndarray:
    """根据指定方法估计协方差矩阵。

    Args:
        returns: 收益率矩阵，shape=(T, N)
        method: 协方差估计方法
        halflife: EWM 方法的半衰期（仅 ewm 方法使用）

    Returns:
        协方差矩阵，shape=(N, N)
    """
    if method == CovMethod.SAMPLE:
        return estimate_covariance_sample(returns)
    elif method == CovMethod.EWM:
        return estimate_covariance_ewm(returns, halflife=halflife)
    elif method == CovMethod.SHRINKAGE:
        return estimate_covariance_shrinkage(returns)
    else:
        return estimate_covariance_sample(returns)


# ---------------------------------------------------------------------------
# 风险平价优化求解
# ---------------------------------------------------------------------------


def risk_parity_weights(cov_matrix: np.ndarray) -> np.ndarray | None:
    """求解风险平价权重。

    优化目标：最小化各资产风险贡献与目标风险贡献的偏差平方和。
    目标风险贡献 = 组合总风险 / N（等风险贡献）。

    具体优化问题：
        min Σ_i (w_i × (Σw)_i - σ_p / N)²
        s.t. Σ w_i = 1, w_i >= 0

    其中：
    - w_i: 第 i 只资产的权重
    - (Σw)_i: 协方差矩阵乘以权重向量的第 i 个分量（边际风险贡献）
    - σ_p = sqrt(w' Σ w): 组合波动率
    - N: 资产数量

    Args:
        cov_matrix: 协方差矩阵，shape=(N, N)，必须为正半定

    Returns:
        最优权重向量，shape=(N,)，如果优化失败返回 None
    """
    n = cov_matrix.shape[0]

    if n == 0:
        return None

    if n == 1:
        return np.array([1.0])

    def objective(w: np.ndarray) -> float:
        """风险平价目标函数。"""
        # 组合方差
        port_var = w @ cov_matrix @ w
        if port_var <= 0:
            return 1e10

        # 组合波动率
        port_vol = np.sqrt(port_var)

        # 各资产的边际风险贡献
        marginal_risk = cov_matrix @ w

        # 各资产的风险贡献 = w_i × marginal_risk_i
        risk_contrib = w * marginal_risk

        # 目标风险贡献（等风险）
        target_rc = port_vol**2 / n  # 使用方差形式避免开方

        # 目标函数：各资产风险贡献与目标的偏差平方和
        return float(np.sum((risk_contrib - target_rc) ** 2))

    # 初始权重：等权
    w0 = np.ones(n) / n

    # 约束：权重和为 1
    constraints = {"type": "eq", "fun": lambda w: np.sum(w) - 1.0}

    # 边界：权重非负
    bounds = [(0.0, 1.0)] * n

    # 求解
    result = minimize(
        objective,
        w0,
        method="SLSQP",
        bounds=bounds,
        constraints=constraints,
        options={"maxiter": 1000, "ftol": 1e-12},
    )

    if not result.success:
        # 尝试使用不同的初始点
        # 使用逆方差权重作为初始点
        diag = np.diag(cov_matrix)
        if np.all(diag > 0):
            inv_var = 1.0 / diag
            w0_alt = inv_var / inv_var.sum()
            result = minimize(
                objective,
                w0_alt,
                method="SLSQP",
                bounds=bounds,
                constraints=constraints,
                options={"maxiter": 1000, "ftol": 1e-12},
            )

    if not result.success:
        return None

    # 归一化权重（确保和为 1）
    weights = result.x
    weights = np.maximum(weights, 0)  # 确保非负
    weight_sum = weights.sum()
    if weight_sum > 0:
        weights = weights / weight_sum
    else:
        return None

    return weights


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
# 风险平价策略
# ---------------------------------------------------------------------------


class RiskParityStrategy(BaseStrategy):
    """风险平价策略。

    优化权重使每只资产对组合风险的贡献相等。
    在调仓日根据历史收益率计算协方差矩阵，求解等风险贡献权重。

    协方差估计方法：
    - sample: 标准样本协方差
    - ewm: 指数加权移动平均（halflife = lookback_days / 2）
    - shrinkage: Ledoit-Wolf 收缩估计

    调仓逻辑：
    1. 判断是否为调仓日（基于 rebalance_freq）
    2. 从 context 获取基金池中各基金的历史净值
    3. 计算日收益率矩阵
    4. 使用指定方法估计协方差矩阵
    5. 求解风险平价权重
    6. 通过 rebalance_to 生成最小化调仓指令

    Example::

        strategy = RiskParityStrategy(
            params=RiskParityParams(
                lookback_days=60,
                rebalance_freq=RebalanceFreq.MONTHLY,
                cov_method=CovMethod.SAMPLE,
            ),
            universe=["000001", "000002", "000003"],
        )
    """

    name = "risk_parity"

    def __init__(
        self,
        params: RiskParityParams | None = None,
        universe: list[str] | None = None,
    ) -> None:
        super().__init__(params=params, universe=universe)
        self._last_rebalance_date: date | None = None
        # 最近一次调仓的诊断信息（包含 MRC/CRC/百分比贡献）
        self._last_risk_contributions: dict[str, object] | None = None

    @property
    def risk_parity_params(self) -> RiskParityParams:
        """获取类型化的参数。"""
        assert isinstance(self.params, RiskParityParams)
        return self.params

    @property
    def last_risk_contributions(self) -> dict[str, object] | None:
        """返回上次调仓时的风险贡献诊断（None 表示尚未调仓）。

        包含字段：
        - portfolio_volatility, portfolio_variance
        - per_asset: list of {asset, weight, marginal_risk, component_risk, pct_contribution}
        - risk_concentration_hhi, diversification_ratio
        """
        return self._last_risk_contributions

    def on_bar(self, context: BarContext) -> list[OrderIntent]:
        """每个交易日判断是否调仓。

        如果到达调仓日，计算协方差矩阵并求解风险平价权重，
        通过 rebalance_to 生成调仓指令。

        Args:
            context: 当日策略上下文（只能看到 T-1 及之前数据）

        Returns:
            OrderIntent 列表，非调仓日返回空列表
        """
        if not is_rebalance_day(
            context.date,
            self._last_rebalance_date,
            self.risk_parity_params.rebalance_freq,
        ):
            return []

        # 获取各基金的历史净值并计算收益率矩阵
        returns_matrix, valid_codes = self._compute_returns_matrix(context)

        if returns_matrix is None or len(valid_codes) < 2:
            # 数据不足，无法计算协方差矩阵（至少需要 2 只基金）
            return []

        # 估计协方差矩阵
        halflife = self.risk_parity_params.lookback_days / 2.0
        cov_matrix = estimate_covariance(
            returns_matrix,
            self.risk_parity_params.cov_method,
            halflife=halflife,
        )

        # 检查协方差矩阵有效性
        if not np.all(np.isfinite(cov_matrix)):
            return []

        # 求解风险平价权重
        weights = risk_parity_weights(cov_matrix)

        if weights is None:
            return []

        # 构建目标权重字典
        target_weights: dict[str, float] = {}
        for i, code in enumerate(valid_codes):
            w = float(weights[i])
            if w > 1e-6:  # 忽略极小权重
                target_weights[code] = w

        if not target_weights:
            return []

        # 记录调仓日
        self._last_rebalance_date = context.date

        # 计算并记录风险贡献诊断（不影响调仓逻辑，纯报告用途）
        try:
            from app.domain.performance.risk_contribution import (
                compute_risk_contributions,
            )

            rc = compute_risk_contributions(
                weights=weights,
                cov_matrix=cov_matrix,
                asset_names=valid_codes,
            )
            if rc is not None:
                self._last_risk_contributions = rc.to_dict()
        except Exception:
            # 诊断失败不阻塞调仓
            self._last_risk_contributions = None

        # 生成调仓指令
        return rebalance_to(context, target_weights)

    def _compute_returns_matrix(
        self, context: BarContext
    ) -> tuple[np.ndarray | None, list[str]]:
        """从 context 中提取收益率矩阵。

        获取基金池中各基金的历史净值，计算日收益率，
        对齐日期后返回收益率矩阵。

        Args:
            context: 策略上下文

        Returns:
            (returns_matrix, valid_codes) 元组：
            - returns_matrix: 收益率矩阵 shape=(T, N)，如果数据不足返回 None
            - valid_codes: 有效基金代码列表
        """
        lookback = self.risk_parity_params.lookback_days

        # 收集各基金的净值序列
        nav_series_list: list[dict[date, Decimal]] = []
        valid_codes: list[str] = []

        for code in self.universe:
            nav_series = context.nav_series(code)
            if len(nav_series) >= lookback // 2:  # 至少需要一半窗口的数据
                nav_series_list.append(nav_series)
                valid_codes.append(code)

        if len(valid_codes) < 2:
            return None, valid_codes

        # 找到所有基金共同的日期
        common_dates = set(nav_series_list[0].keys())
        for nav_series in nav_series_list[1:]:
            common_dates &= set(nav_series.keys())

        sorted_dates = sorted(common_dates)

        # 取最近 lookback_days 个交易日
        if len(sorted_dates) > lookback:
            sorted_dates = sorted_dates[-lookback:]

        if len(sorted_dates) < 10:  # 至少需要 10 个数据点
            return None, []

        # 构建净值矩阵并计算收益率
        n_assets = len(valid_codes)
        n_dates = len(sorted_dates)

        nav_matrix = np.zeros((n_dates, n_assets))
        for j, nav_series in enumerate(nav_series_list):
            for i, d in enumerate(sorted_dates):
                nav_matrix[i, j] = float(nav_series[d])

        # 计算日收益率
        # returns[t] = nav[t] / nav[t-1] - 1
        returns_matrix = nav_matrix[1:] / nav_matrix[:-1] - 1.0

        # 检查有效性（无 NaN 或 Inf）
        if not np.all(np.isfinite(returns_matrix)):
            return None, []

        return returns_matrix, valid_codes
