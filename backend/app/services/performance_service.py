"""绩效分析服务 — 汇总所有绩效指标并输出结构化 JSON。

聚合以下模块的计算结果：
- 收益类因子（total_return, annualized_return, excess_return, jensen_alpha）
- 风险类因子（volatility, max_drawdown, downside_deviation, var, cvar）
- 风险调整因子（sharpe, sortino, information_ratio, treynor）
- 基准对比因子（beta, tracking_error, r_squared, up_capture, down_capture）
- Fama-French 归因（3 因子 / 5 因子）
- Brinson 归因（配置效应、选股效应、交互效应）

输出结构化 JSON，供 API 响应和 AI 归因报告使用。
支持单策略分析和多策略对比。

需求: 6.4, 6.5, 6.6
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
import pandas as pd

from app.domain.factors.benchmark import (
    beta,
    down_capture,
    r_squared,
    tracking_error,
    up_capture,
)
from app.domain.factors.returns import (
    annualized_return,
    excess_return,
    jensen_alpha,
    total_return,
)
from app.domain.factors.risk import (
    calmar,
    cvar,
    downside_deviation,
    max_drawdown,
    var,
    volatility,
)
from app.domain.factors.risk_adjusted import (
    information_ratio,
    sharpe,
    sortino,
    treynor,
)
from app.domain.performance.brinson import BrinsonResult, brinson_attribution
from app.domain.performance.fama_french import (
    FamaFrenchResult,
    fama_french_3factor,
    fama_french_5factor,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ReturnMetrics:
    """收益类指标。"""

    total_return: float = np.nan
    annualized_return: float = np.nan
    excess_return: float = np.nan
    jensen_alpha: float = np.nan

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_return": _safe_float(self.total_return),
            "annualized_return": _safe_float(self.annualized_return),
            "excess_return": _safe_float(self.excess_return),
            "jensen_alpha": _safe_float(self.jensen_alpha),
        }


@dataclass
class RiskMetrics:
    """风险类指标。"""

    volatility: float = np.nan
    max_drawdown: float = np.nan
    downside_deviation: float = np.nan
    var_95: float = np.nan
    cvar_95: float = np.nan
    calmar: float = np.nan

    def to_dict(self) -> dict[str, Any]:
        return {
            "volatility": _safe_float(self.volatility),
            "max_drawdown": _safe_float(self.max_drawdown),
            "downside_deviation": _safe_float(self.downside_deviation),
            "var_95": _safe_float(self.var_95),
            "cvar_95": _safe_float(self.cvar_95),
            "calmar": _safe_float(self.calmar),
        }


@dataclass
class RiskAdjustedMetrics:
    """风险调整收益指标。"""

    sharpe: float = np.nan
    sortino: float = np.nan
    information_ratio: float = np.nan
    treynor: float = np.nan

    def to_dict(self) -> dict[str, Any]:
        return {
            "sharpe": _safe_float(self.sharpe),
            "sortino": _safe_float(self.sortino),
            "information_ratio": _safe_float(self.information_ratio),
            "treynor": _safe_float(self.treynor),
        }


@dataclass
class BenchmarkMetrics:
    """基准对比指标。"""

    beta: float = np.nan
    tracking_error: float = np.nan
    r_squared: float = np.nan
    up_capture: float = np.nan
    down_capture: float = np.nan

    def to_dict(self) -> dict[str, Any]:
        return {
            "beta": _safe_float(self.beta),
            "tracking_error": _safe_float(self.tracking_error),
            "r_squared": _safe_float(self.r_squared),
            "up_capture": _safe_float(self.up_capture),
            "down_capture": _safe_float(self.down_capture),
        }


@dataclass
class AttributionResult:
    """归因分析结果（Fama-French + Brinson）。"""

    fama_french: Optional[FamaFrenchResult] = None
    brinson: Optional[BrinsonResult] = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}

        if self.fama_french is not None:
            result["fama_french"] = {
                "alpha": _safe_float(self.fama_french.alpha),
                "alpha_daily": _safe_float(self.fama_french.alpha_daily),
                "betas": {
                    k: _safe_float(v) for k, v in self.fama_french.betas.items()
                },
                "r_squared": _safe_float(self.fama_french.r_squared),
                "adj_r_squared": _safe_float(self.fama_french.adj_r_squared),
                "residual_std": _safe_float(self.fama_french.residual_std),
                "t_stats": {
                    k: _safe_float(v) for k, v in self.fama_french.t_stats.items()
                },
                "n_obs": self.fama_french.n_obs,
                "model_type": self.fama_french.model_type,
            }
        else:
            result["fama_french"] = None

        if self.brinson is not None:
            result["brinson"] = {
                "allocation_effect": {
                    k: _safe_float(v)
                    for k, v in self.brinson.allocation_effect.items()
                },
                "selection_effect": {
                    k: _safe_float(v)
                    for k, v in self.brinson.selection_effect.items()
                },
                "interaction_effect": {
                    k: _safe_float(v)
                    for k, v in self.brinson.interaction_effect.items()
                },
                "total_excess_return": _safe_float(
                    self.brinson.total_excess_return
                ),
                "sectors": self.brinson.sectors,
            }
        else:
            result["brinson"] = None

        return result


@dataclass
class PerformanceReport:
    """完整绩效分析报告。

    包含收益、风险、风险调整、基准对比和归因分析五大模块。
    """

    strategy_name: str = ""
    returns: ReturnMetrics = field(default_factory=ReturnMetrics)
    risk: RiskMetrics = field(default_factory=RiskMetrics)
    risk_adjusted: RiskAdjustedMetrics = field(default_factory=RiskAdjustedMetrics)
    benchmark: BenchmarkMetrics = field(default_factory=BenchmarkMetrics)
    attribution: AttributionResult = field(default_factory=AttributionResult)
    sharpe_inference: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        """序列化为结构化 JSON 字典，供 API 和 AI 归因使用。"""
        return {
            "strategy_name": self.strategy_name,
            "returns": self.returns.to_dict(),
            "risk": self.risk.to_dict(),
            "risk_adjusted": self.risk_adjusted.to_dict(),
            "benchmark": self.benchmark.to_dict(),
            "attribution": self.attribution.to_dict(),
            "sharpe_inference": self.sharpe_inference,
        }


@dataclass
class ComparisonReport:
    """多策略对比报告。"""

    strategies: list[PerformanceReport] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """序列化为结构化 JSON 字典。"""
        return {
            "strategies": [s.to_dict() for s in self.strategies],
            "comparison_table": self._build_comparison_table(),
        }

    def _build_comparison_table(self) -> dict[str, Any]:
        """构建关键指标对比表。"""
        if not self.strategies:
            return {}

        table: dict[str, dict[str, Any]] = {}
        for report in self.strategies:
            name = report.strategy_name or "unnamed"
            table[name] = {
                "total_return": _safe_float(report.returns.total_return),
                "annualized_return": _safe_float(report.returns.annualized_return),
                "volatility": _safe_float(report.risk.volatility),
                "max_drawdown": _safe_float(report.risk.max_drawdown),
                "sharpe": _safe_float(report.risk_adjusted.sharpe),
                "sortino": _safe_float(report.risk_adjusted.sortino),
                "calmar": _safe_float(report.risk.calmar),
                "beta": _safe_float(report.benchmark.beta),
                "information_ratio": _safe_float(
                    report.risk_adjusted.information_ratio
                ),
            }
        return table


# ---------------------------------------------------------------------------
# Performance Service
# ---------------------------------------------------------------------------


class PerformanceService:
    """绩效分析服务。

    汇总所有绩效指标（收益、风险、风险调整、基准对比、归因），
    输出结构化 JSON 供 API 和 AI 归因报告使用。

    支持单策略分析和多策略对比。
    """

    def __init__(self, risk_free_rate: float = 0.0) -> None:
        """初始化绩效分析服务。

        Parameters:
            risk_free_rate: 年化无风险利率，默认 0。
        """
        self.risk_free_rate = risk_free_rate

    def analyze(
        self,
        nav: pd.Series,
        benchmark_nav: Optional[pd.Series] = None,
        strategy_name: str = "",
        factor_returns: Optional[pd.DataFrame] = None,
        risk_free_series: Optional[pd.Series] = None,
        brinson_data: Optional[dict[str, dict[str, float]]] = None,
        use_5factor: bool = False,
        compute_inference: bool = False,
        n_trials: int = 1,
        variance_of_trials: Optional[float] = None,
    ) -> PerformanceReport:
        """执行单策略完整绩效分析。

        Parameters:
            nav: 策略净值序列（日期索引）。
            benchmark_nav: 基准净值序列（可选）。
            strategy_name: 策略名称。
            factor_returns: Fama-French 因子收益 DataFrame
                （列: MKT, SMB, HML [, RMW, CMA]）。
            risk_free_series: 日频无风险利率序列（用于 Fama-French）。
            brinson_data: Brinson 归因输入数据，格式:
                {
                    "portfolio_weights": {sector: weight},
                    "benchmark_weights": {sector: weight},
                    "portfolio_returns": {sector: return},
                    "benchmark_returns": {sector: return},
                }
            use_5factor: 是否使用 Fama-French 五因子模型（默认三因子）。
            compute_inference: 是否计算 Sharpe 的统计推断（PSR / DSR / CI）。
                打开后 ``report.sharpe_inference`` 会被填充。
            n_trials: 多重检验试验次数（用于 DSR）。从优化结果传入。
            variance_of_trials: 试验 Sharpe 方差（DSR 阈值估计用）。

        Returns:
            PerformanceReport 包含所有绩效指标。
        """
        report = PerformanceReport(strategy_name=strategy_name)

        # 1. 收益类指标
        report.returns = self._compute_return_metrics(nav, benchmark_nav)

        # 2. 风险类指标
        report.risk = self._compute_risk_metrics(nav)

        # 3. 风险调整指标
        report.risk_adjusted = self._compute_risk_adjusted_metrics(
            nav, benchmark_nav
        )

        # 4. 基准对比指标
        report.benchmark = self._compute_benchmark_metrics(nav, benchmark_nav)

        # 5. 归因分析
        report.attribution = self._compute_attribution(
            nav=nav,
            benchmark_nav=benchmark_nav,
            factor_returns=factor_returns,
            risk_free_series=risk_free_series,
            brinson_data=brinson_data,
            use_5factor=use_5factor,
        )

        # 6. 可选：Sharpe 统计推断（PSR / DSR / 95% CI）
        if compute_inference:
            report.sharpe_inference = self._compute_sharpe_inference(
                nav=nav,
                n_trials=n_trials,
                variance_of_trials=variance_of_trials,
            )

        return report

    def compare(
        self,
        strategies: dict[str, pd.Series],
        benchmark_nav: Optional[pd.Series] = None,
        factor_returns: Optional[pd.DataFrame] = None,
        risk_free_series: Optional[pd.Series] = None,
        use_5factor: bool = False,
    ) -> ComparisonReport:
        """执行多策略对比分析。

        Parameters:
            strategies: 策略名称 → 净值序列的映射。
            benchmark_nav: 基准净值序列（可选，所有策略共用）。
            factor_returns: Fama-French 因子收益 DataFrame（可选）。
            risk_free_series: 日频无风险利率序列（可选）。
            use_5factor: 是否使用 Fama-French 五因子模型。

        Returns:
            ComparisonReport 包含所有策略的绩效报告和对比表。
        """
        reports: list[PerformanceReport] = []

        for name, nav in strategies.items():
            report = self.analyze(
                nav=nav,
                benchmark_nav=benchmark_nav,
                strategy_name=name,
                factor_returns=factor_returns,
                risk_free_series=risk_free_series,
                use_5factor=use_5factor,
            )
            reports.append(report)

        return ComparisonReport(strategies=reports)

    # -----------------------------------------------------------------------
    # Private computation methods
    # -----------------------------------------------------------------------

    def _compute_return_metrics(
        self,
        nav: pd.Series,
        benchmark_nav: Optional[pd.Series],
    ) -> ReturnMetrics:
        """计算收益类指标。"""
        metrics = ReturnMetrics()

        metrics.total_return = total_return(nav)
        metrics.annualized_return = annualized_return(nav)
        metrics.excess_return = excess_return(nav, benchmark_nav)
        metrics.jensen_alpha = jensen_alpha(
            nav, benchmark_nav, risk_free_rate=self.risk_free_rate
        )

        return metrics

    def _compute_risk_metrics(self, nav: pd.Series) -> RiskMetrics:
        """计算风险类指标。"""
        metrics = RiskMetrics()

        metrics.volatility = volatility(nav)
        metrics.max_drawdown = max_drawdown(nav)
        metrics.downside_deviation = downside_deviation(nav)
        metrics.var_95 = var(nav, confidence=0.95)
        metrics.cvar_95 = cvar(nav, confidence=0.95)
        metrics.calmar = calmar(nav)

        return metrics

    def _compute_risk_adjusted_metrics(
        self,
        nav: pd.Series,
        benchmark_nav: Optional[pd.Series],
    ) -> RiskAdjustedMetrics:
        """计算风险调整收益指标。"""
        metrics = RiskAdjustedMetrics()

        metrics.sharpe = sharpe(nav, risk_free_rate=self.risk_free_rate)
        metrics.sortino = sortino(nav, risk_free_rate=self.risk_free_rate)
        metrics.information_ratio = information_ratio(nav, benchmark_nav)
        metrics.treynor = treynor(
            nav, benchmark_nav, risk_free_rate=self.risk_free_rate
        )

        return metrics

    def _compute_benchmark_metrics(
        self,
        nav: pd.Series,
        benchmark_nav: Optional[pd.Series],
    ) -> BenchmarkMetrics:
        """计算基准对比指标。"""
        metrics = BenchmarkMetrics()

        if benchmark_nav is None:
            return metrics

        metrics.beta = beta(nav, benchmark_nav)
        metrics.tracking_error = tracking_error(nav, benchmark_nav)
        metrics.r_squared = r_squared(nav, benchmark_nav)
        metrics.up_capture = up_capture(nav, benchmark_nav)
        metrics.down_capture = down_capture(nav, benchmark_nav)

        return metrics

    def _compute_attribution(
        self,
        nav: pd.Series,
        benchmark_nav: Optional[pd.Series],
        factor_returns: Optional[pd.DataFrame],
        risk_free_series: Optional[pd.Series],
        brinson_data: Optional[dict[str, dict[str, float]]],
        use_5factor: bool,
    ) -> AttributionResult:
        """计算归因分析（Fama-French + Brinson）。"""
        result = AttributionResult()

        # Fama-French 归因
        if factor_returns is not None and nav is not None and len(nav) >= 2:
            try:
                # 将 NAV 转为日收益率
                nav_clean = nav.dropna()
                fund_returns = nav_clean.pct_change().dropna()

                if use_5factor:
                    ff_result = fama_french_5factor(
                        fund_returns=fund_returns,
                        factor_returns=factor_returns,
                        risk_free_rate=risk_free_series,
                    )
                else:
                    ff_result = fama_french_3factor(
                        fund_returns=fund_returns,
                        factor_returns=factor_returns,
                        risk_free_rate=risk_free_series,
                    )
                result.fama_french = ff_result
            except Exception as e:
                logger.warning("Fama-French attribution failed: %s", str(e))

        # Brinson 归因
        if brinson_data is not None:
            try:
                brinson_result = brinson_attribution(
                    portfolio_weights=brinson_data.get("portfolio_weights", {}),
                    benchmark_weights=brinson_data.get("benchmark_weights", {}),
                    portfolio_returns=brinson_data.get("portfolio_returns", {}),
                    benchmark_returns=brinson_data.get("benchmark_returns", {}),
                )
                result.brinson = brinson_result
            except Exception as e:
                logger.warning("Brinson attribution failed: %s", str(e))

        return result

    def _compute_sharpe_inference(
        self,
        nav: pd.Series,
        n_trials: int = 1,
        variance_of_trials: Optional[float] = None,
    ) -> Optional[dict[str, Any]]:
        """计算 Sharpe 的统计推断（PSR / DSR / 95% CI）。

        基于已观测净值序列推断真实 Sharpe 是否显著为零。

        Args:
            nav: 净值序列
            n_trials: 多重检验试验次数（来自参数优化结果）
            variance_of_trials: 试验 Sharpe 方差

        Returns:
            包含 PSR / DSR / 置信区间等的 dict，数据不足时返回 None
        """
        if nav is None or len(nav) < 30:
            return None
        try:
            from app.domain.performance.sharpe_inference import sharpe_inference

            nav_clean = nav.dropna()
            returns = nav_clean.pct_change().dropna().values

            inference = sharpe_inference(
                returns=returns,
                n_trials=n_trials,
                variance_of_trials=variance_of_trials,
                freq=252,
            )
            if inference is None:
                return None
            return inference.to_dict()
        except Exception as e:
            logger.warning("Sharpe inference failed: %s", str(e))
            return None


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _safe_float(value: Any) -> Optional[float]:
    """将值转为安全的 JSON 可序列化浮点数。

    NaN 和 Inf 转为 None，其余保留浮点数。
    """
    if value is None:
        return None
    try:
        f = float(value)
        if np.isnan(f) or np.isinf(f):
            return None
        return round(f, 6)
    except (TypeError, ValueError):
        return None
