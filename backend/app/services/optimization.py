"""参数优化服务模块。

提供策略参数优化功能：
- GridSearchOptimizer: 网格搜索，遍历所有参数组合
- SobolSearchOptimizer: 基于 Sobol 准随机序列的随机搜索
- ParallelOptimizer: 支持通过 Celery 任务池并行回测

多目标优化默认惩罚回撤、换手、费用、过拟合和数据质量不足；baseline
对照进一步要求复杂候选策略跑赢定投、风险平价、简单动量等简单模型。
"""

from __future__ import annotations

import itertools
import logging
import math
from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Literal, Sequence

import numpy as np
from scipy.stats import qmc

from app.domain.backtest.engine_event import (
    BacktestResult,
    DividendInfo,
    EventDrivenEngine,
    FundMeta,
)
from app.domain.strategy.base import StrategyParams

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Parameter Space Definition
# ---------------------------------------------------------------------------


class ParamType(str, Enum):
    """参数类型枚举。"""

    CONTINUOUS = "continuous"
    DISCRETE = "discrete"
    CATEGORICAL = "categorical"


@dataclass
class ParamDimension:
    """单个参数维度定义。"""

    name: str
    param_type: ParamType
    low: float | None = None
    high: float | None = None
    step: float | None = None
    choices: list[Any] | None = None

    def __post_init__(self) -> None:
        if self.param_type in (ParamType.CONTINUOUS, ParamType.DISCRETE):
            if self.low is None or self.high is None:
                raise ValueError(
                    f"Parameter '{self.name}': continuous/discrete types require low and high"
                )
            if self.low > self.high:
                raise ValueError(
                    f"Parameter '{self.name}': low ({self.low}) must be <= high ({self.high})"
                )
        elif self.param_type == ParamType.CATEGORICAL:
            if not self.choices:
                raise ValueError(
                    f"Parameter '{self.name}': categorical type requires non-empty choices"
                )


@dataclass
class ParamSpace:
    """参数搜索空间。"""

    dimensions: list[ParamDimension]

    def grid_points(self) -> list[dict[str, Any]]:
        axes: list[list[Any]] = []
        names: list[str] = []

        for dim in self.dimensions:
            names.append(dim.name)
            if dim.param_type == ParamType.CATEGORICAL:
                axes.append(list(dim.choices))  # type: ignore[arg-type]
            elif dim.param_type == ParamType.DISCRETE:
                step = dim.step if dim.step is not None else 1
                values = list(
                    range(int(dim.low), int(dim.high) + 1, int(step))  # type: ignore[arg-type]
                )
                if values and values[-1] != int(dim.high):  # type: ignore[arg-type]
                    values.append(int(dim.high))  # type: ignore[arg-type]
                axes.append(values)
            else:
                step = dim.step if dim.step is not None else (dim.high - dim.low) / 10  # type: ignore[operator]
                values_f = []
                v = dim.low  # type: ignore[assignment]
                while v <= dim.high + step * 0.01:  # type: ignore[operator]
                    values_f.append(round(v, 10))
                    v += step  # type: ignore[operator]
                axes.append(values_f)

        points: list[dict[str, Any]] = []
        for combo in itertools.product(*axes):
            points.append(dict(zip(names, combo)))
        return points

    def sobol_sample(self, n_samples: int, seed: int | None = None) -> list[dict[str, Any]]:
        n_dims = len(self.dimensions)
        if n_dims == 0:
            return [{} for _ in range(n_samples)]

        sampler = qmc.Sobol(d=n_dims, scramble=True, seed=seed)
        m = int(np.ceil(np.log2(max(n_samples, 1))))
        m = max(m, 1)
        raw_samples = sampler.random_base2(m)[:n_samples]

        points: list[dict[str, Any]] = []
        for sample in raw_samples:
            params: dict[str, Any] = {}
            for i, dim in enumerate(self.dimensions):
                u = sample[i]
                if dim.param_type == ParamType.CONTINUOUS:
                    value = dim.low + u * (dim.high - dim.low)  # type: ignore[operator]
                    params[dim.name] = round(value, 6)
                elif dim.param_type == ParamType.DISCRETE:
                    value = dim.low + u * (dim.high - dim.low)  # type: ignore[operator]
                    params[dim.name] = int(round(value))
                else:
                    idx = int(u * len(dim.choices))  # type: ignore[arg-type]
                    idx = min(idx, len(dim.choices) - 1)  # type: ignore[arg-type]
                    params[dim.name] = dim.choices[idx]  # type: ignore[index]
            points.append(params)
        return points


# ---------------------------------------------------------------------------
# Multi-objective and baseline scoring
# ---------------------------------------------------------------------------


@dataclass
class MultiObjectiveConfig:
    """多目标优化评分配置。"""

    weight_return: float = 0.25
    weight_risk_adjusted: float = 0.25
    weight_hit_rate: float = 0.15
    weight_drawdown_penalty: float = 0.15
    weight_turnover_penalty: float = 0.08
    weight_overfit_penalty: float = 0.17
    weight_data_quality_penalty: float = 0.10
    return_scale: float = 0.20
    sharpe_scale: float = 1.50
    max_drawdown_limit: float = 0.30
    max_turnover_limit: float = 3.0
    max_fee_drag_limit: float = 0.03
    max_pbo: float = 0.50
    min_ic_degradation: float = 0.45
    min_oos_sharpe: float = -0.10
    min_oos_return: float = -0.05
    min_data_quality_score: float = 0.55
    min_sample_count: int = 60
    elimination_score_floor: float = -0.35
    hard_eliminate_on_poor_data: bool = True


DEFAULT_MULTI_OBJECTIVE_CONFIG = MultiObjectiveConfig()


@dataclass
class MultiObjectiveScore:
    """一次多目标评分诊断结果。"""

    score: float
    components: dict[str, float] = field(default_factory=dict)
    eliminated: bool = False
    reasons: list[str] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)

    def to_metrics(self) -> dict[str, Any]:
        return {
            "multi_objective_score": round(float(self.score), 6),
            "multi_objective_components": self.components,
            "multi_objective_eliminated": self.eliminated,
            "multi_objective_reasons": list(self.reasons),
            "multi_objective_config": self.config,
        }


@dataclass
class BaselineComparisonConfig:
    """Baseline 对照门槛配置。"""

    baseline_names: tuple[str, ...] = ("dca", "risk_parity", "simple_momentum")
    min_multi_objective_uplift: float = 0.02
    min_sharpe_uplift: float = 0.0
    min_return_uplift: float = 0.0
    max_drawdown_worsening: float = 0.03
    complexity_penalty: float = 0.03
    underperformance_penalty_multiplier: float = 2.0
    require_baseline: bool = False


DEFAULT_BASELINE_COMPARISON_CONFIG = BaselineComparisonConfig()


@dataclass
class BaselineMetrics:
    """单个 baseline 的归一化指标。"""

    name: str
    metrics: dict[str, Any]
    multi_objective_score: float
    sharpe: float | None = None
    total_return: float | None = None
    max_drawdown: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "multi_objective_score": round(float(self.multi_objective_score), 6),
            "sharpe": self.sharpe,
            "total_return": self.total_return,
            "max_drawdown": self.max_drawdown,
            "metrics": self.metrics,
        }


@dataclass
class BaselineComparisonResult:
    """候选策略相对简单 baseline 的对照结果。"""

    passed: bool
    adjusted_score: float
    best_baseline: BaselineMetrics | None = None
    comparisons: dict[str, dict[str, Any]] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)

    def to_metrics(self) -> dict[str, Any]:
        return {
            "baseline_adjusted_score": round(float(self.adjusted_score), 6),
            "baseline_passed": bool(self.passed),
            "baseline_best": self.best_baseline.to_dict() if self.best_baseline else None,
            "baseline_comparison": self.comparisons,
            "baseline_reasons": list(self.reasons),
            "baseline_config": self.config,
        }


def _finite_float(value: Any, default: float | None = None) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(result) or math.isinf(result):
        return default
    return result


def _first_metric(metrics: dict[str, Any], names: Sequence[str], default: float | None = None) -> float | None:
    for name in names:
        if name in metrics:
            value = _finite_float(metrics.get(name))
            if value is not None:
                return value
    return default


def _clip_unit(value: float) -> float:
    return float(np.clip(value, -1.0, 1.0))


def _positive_clip(value: float) -> float:
    return float(np.clip(value, 0.0, 1.0))


def compute_multi_objective_score(
    metrics: dict[str, Any],
    config: MultiObjectiveConfig | None = None,
) -> MultiObjectiveScore:
    """把回测/Walk-Forward/OOS 指标合成为稳健多目标分数。"""

    cfg = config or DEFAULT_MULTI_OBJECTIVE_CONFIG
    reasons: list[str] = []

    oos_return = _first_metric(
        metrics,
        [
            "oos_return",
            "avg_oos_return",
            "total_oos_return",
            "total_return",
            "annualized_return",
            "simulated_total_return",
        ],
        0.0,
    ) or 0.0
    risk_adjusted = _first_metric(
        metrics,
        [
            "oos_sharpe",
            "avg_oos_sharpe",
            "sharpe",
            "information_ratio",
            "sortino",
            "calmar",
            "simulated_sharpe",
        ],
        0.0,
    ) or 0.0
    hit_rate = _first_metric(
        metrics,
        [
            "oos_hit_rate",
            "hit_rate",
            "win_rate",
            "avg_oos_buy_hit_rate",
            "buy_hit_rate_20d",
        ],
        None,
    )
    sell_hit_rate = _first_metric(metrics, ["avg_oos_sell_hit_rate", "sell_hit_rate_20d"], None)
    if hit_rate is not None and sell_hit_rate is not None:
        hit_rate = (hit_rate + sell_hit_rate) / 2.0
    elif hit_rate is None and sell_hit_rate is not None:
        hit_rate = sell_hit_rate

    max_drawdown = abs(_first_metric(metrics, ["max_drawdown", "oos_max_drawdown", "simulated_max_drawdown"], 0.0) or 0.0)
    turnover = abs(_first_metric(metrics, ["turnover", "annual_turnover", "turnover_rate"], 0.0) or 0.0)
    fee_drag = abs(_first_metric(metrics, ["fee_drag", "fee_drag_pct", "fees_drag_pct"], 0.0) or 0.0)
    pbo = _first_metric(metrics, ["pbo", "oos_pbo", "avg_pbo"], None)
    ic_degradation = _first_metric(metrics, ["ic_degradation", "oos_ic_degradation"], None)
    data_quality_score = _first_metric(metrics, ["data_quality_score", "avg_data_quality_score"], None)
    sample_count = int(_first_metric(metrics, ["sample_count", "oos_signal_count", "total_oos_signals", "n_samples"], 0.0) or 0)

    return_component = math.tanh(oos_return / max(cfg.return_scale, 1e-9))
    risk_adjusted_component = math.tanh(risk_adjusted / max(cfg.sharpe_scale, 1e-9))
    hit_component = 0.0 if hit_rate is None else _clip_unit((hit_rate - 0.5) * 2.0)
    drawdown_penalty = _positive_clip(max_drawdown / max(cfg.max_drawdown_limit, 1e-9))
    turnover_penalty = _positive_clip(turnover / max(cfg.max_turnover_limit, 1e-9))
    fee_penalty = _positive_clip(fee_drag / max(cfg.max_fee_drag_limit, 1e-9))

    overfit_penalty = 0.0
    if pbo is not None:
        overfit_penalty = max(overfit_penalty, _positive_clip((pbo - 0.20) / max(cfg.max_pbo - 0.20, 1e-9)))
        if pbo > cfg.max_pbo:
            reasons.append(f"PBO {pbo:.0%} 高于阈值 {cfg.max_pbo:.0%}")
    if ic_degradation is not None:
        degradation_penalty = _positive_clip((cfg.min_ic_degradation - ic_degradation) / max(cfg.min_ic_degradation, 1e-9))
        overfit_penalty = max(overfit_penalty, degradation_penalty)
        if ic_degradation < cfg.min_ic_degradation:
            reasons.append(f"IS/OOS 衰减比 {ic_degradation:.2f} 低于 {cfg.min_ic_degradation:.2f}")

    data_quality_penalty = 0.0
    if data_quality_score is not None:
        data_quality_penalty = _positive_clip((cfg.min_data_quality_score - data_quality_score) / max(cfg.min_data_quality_score, 1e-9))
        if data_quality_score < cfg.min_data_quality_score:
            reasons.append(f"数据质量分 {data_quality_score:.2f} 低于训练阈值 {cfg.min_data_quality_score:.2f}")
    if sample_count and sample_count < cfg.min_sample_count:
        sample_penalty = _positive_clip((cfg.min_sample_count - sample_count) / max(cfg.min_sample_count, 1))
        data_quality_penalty = max(data_quality_penalty, sample_penalty)
        reasons.append(f"样本数 {sample_count} 少于多目标优化阈值 {cfg.min_sample_count}")

    score = (
        cfg.weight_return * return_component
        + cfg.weight_risk_adjusted * risk_adjusted_component
        + cfg.weight_hit_rate * hit_component
        - cfg.weight_drawdown_penalty * drawdown_penalty
        - cfg.weight_turnover_penalty * max(turnover_penalty, fee_penalty)
        - cfg.weight_overfit_penalty * overfit_penalty
        - cfg.weight_data_quality_penalty * data_quality_penalty
    )
    score = _clip_unit(score)

    eliminated = False
    if oos_return < cfg.min_oos_return:
        eliminated = True
        reasons.append(f"样本外收益 {oos_return:.2%} 低于阈值 {cfg.min_oos_return:.2%}")
    if risk_adjusted < cfg.min_oos_sharpe:
        eliminated = True
        reasons.append(f"样本外风险调整收益 {risk_adjusted:.2f} 低于阈值 {cfg.min_oos_sharpe:.2f}")
    if max_drawdown > cfg.max_drawdown_limit:
        eliminated = True
        reasons.append(f"最大回撤 {max_drawdown:.2%} 超过上限 {cfg.max_drawdown_limit:.2%}")
    if turnover > cfg.max_turnover_limit:
        eliminated = True
        reasons.append(f"换手率 {turnover:.2f} 超过上限 {cfg.max_turnover_limit:.2f}")
    if fee_drag > cfg.max_fee_drag_limit:
        eliminated = True
        reasons.append(f"费用拖累 {fee_drag:.2%} 超过上限 {cfg.max_fee_drag_limit:.2%}")
    if pbo is not None and pbo > cfg.max_pbo:
        eliminated = True
    if cfg.hard_eliminate_on_poor_data and data_quality_penalty >= 0.80:
        eliminated = True
        reasons.append("数据质量不足，禁止参与默认参数训练/发布")
    if score < cfg.elimination_score_floor:
        eliminated = True
        reasons.append(f"多目标综合分 {score:.2f} 低于淘汰线 {cfg.elimination_score_floor:.2f}")

    components = {
        "return": round(float(return_component), 6),
        "risk_adjusted": round(float(risk_adjusted_component), 6),
        "hit_rate": round(float(hit_component), 6),
        "drawdown_penalty": round(float(drawdown_penalty), 6),
        "turnover_penalty": round(float(turnover_penalty), 6),
        "fee_penalty": round(float(fee_penalty), 6),
        "overfit_penalty": round(float(overfit_penalty), 6),
        "data_quality_penalty": round(float(data_quality_penalty), 6),
        "raw_oos_return": round(float(oos_return), 6),
        "raw_risk_adjusted": round(float(risk_adjusted), 6),
        "raw_hit_rate": round(float(hit_rate), 6) if hit_rate is not None else 0.0,
        "raw_max_drawdown": round(float(max_drawdown), 6),
    }

    return MultiObjectiveScore(
        score=score,
        components=components,
        eliminated=eliminated,
        reasons=list(dict.fromkeys(reasons)),
        config=asdict(cfg),
    )


def enrich_metrics_with_multi_objective(
    metrics: dict[str, Any],
    config: MultiObjectiveConfig | None = None,
) -> dict[str, Any]:
    """返回带多目标评分字段的 metrics 副本。"""

    enriched = dict(metrics or {})
    if "multi_objective_score" in enriched:
        return enriched
    score = compute_multi_objective_score(enriched, config=config)
    enriched.update(score.to_metrics())
    return enriched


def _extract_baseline_payload(metrics: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(metrics, dict):
        return {}
    payload = metrics.get("baseline_metrics") or metrics.get("baselines") or metrics.get("baseline_comparison_inputs")
    return dict(payload) if isinstance(payload, dict) else {}


def _normalize_baseline_metrics(
    name: str,
    metrics: dict[str, Any],
    *,
    multi_objective_config: MultiObjectiveConfig | None = None,
) -> BaselineMetrics:
    enriched = enrich_metrics_with_multi_objective(metrics, config=multi_objective_config)
    return BaselineMetrics(
        name=name,
        metrics=enriched,
        multi_objective_score=float(enriched.get("multi_objective_score") or 0.0),
        sharpe=_first_metric(enriched, ["oos_sharpe", "avg_oos_sharpe", "sharpe", "simulated_sharpe"], None),
        total_return=_first_metric(enriched, ["oos_return", "avg_oos_return", "total_return", "simulated_total_return"], None),
        max_drawdown=abs(_first_metric(enriched, ["max_drawdown", "oos_max_drawdown", "simulated_max_drawdown"], 0.0) or 0.0),
    )


def compare_against_baselines(
    candidate_metrics: dict[str, Any],
    baseline_metrics: dict[str, dict[str, Any]] | None = None,
    *,
    config: BaselineComparisonConfig | None = None,
    multi_objective_config: MultiObjectiveConfig | None = None,
) -> BaselineComparisonResult:
    """把候选策略与定投、风险平价、简单动量等 baseline 做上线对照。"""

    cfg = config or DEFAULT_BASELINE_COMPARISON_CONFIG
    candidate = enrich_metrics_with_multi_objective(candidate_metrics, config=multi_objective_config)
    candidate_score = float(candidate.get("multi_objective_score") or 0.0)
    candidate_sharpe = _first_metric(candidate, ["oos_sharpe", "avg_oos_sharpe", "sharpe", "simulated_sharpe"], None)
    candidate_return = _first_metric(candidate, ["oos_return", "avg_oos_return", "total_return", "simulated_total_return"], None)
    candidate_drawdown = abs(_first_metric(candidate, ["max_drawdown", "oos_max_drawdown", "simulated_max_drawdown"], 0.0) or 0.0)

    raw_baselines = dict(baseline_metrics or _extract_baseline_payload(candidate))
    normalized: list[BaselineMetrics] = []
    for name, payload in raw_baselines.items():
        if isinstance(payload, dict):
            normalized.append(
                _normalize_baseline_metrics(
                    str(name),
                    payload,
                    multi_objective_config=multi_objective_config,
                )
            )

    configured_order = {name: idx for idx, name in enumerate(cfg.baseline_names)}
    normalized.sort(
        key=lambda item: (
            item.multi_objective_score,
            -configured_order.get(item.name, len(configured_order)),
        ),
        reverse=True,
    )

    reasons: list[str] = []
    comparisons: dict[str, dict[str, Any]] = {}
    best = normalized[0] if normalized else None
    passed = True
    underperformance = 0.0

    if best is None:
        passed = not cfg.require_baseline
        reason = "未提供定投/风险平价/简单动量 baseline 指标"
        reasons.append(reason if passed else f"{reason}，禁止通过 baseline 发布门槛")
    else:
        for baseline in normalized:
            score_uplift = candidate_score - baseline.multi_objective_score
            sharpe_uplift = (
                candidate_sharpe - baseline.sharpe
                if candidate_sharpe is not None and baseline.sharpe is not None
                else None
            )
            return_uplift = (
                candidate_return - baseline.total_return
                if candidate_return is not None and baseline.total_return is not None
                else None
            )
            drawdown_delta = (
                candidate_drawdown - baseline.max_drawdown
                if baseline.max_drawdown is not None
                else None
            )
            comparisons[baseline.name] = {
                "baseline_score": round(float(baseline.multi_objective_score), 6),
                "score_uplift": round(float(score_uplift), 6),
                "sharpe_uplift": round(float(sharpe_uplift), 6) if sharpe_uplift is not None else None,
                "return_uplift": round(float(return_uplift), 6) if return_uplift is not None else None,
                "drawdown_delta": round(float(drawdown_delta), 6) if drawdown_delta is not None else None,
                "baseline_metrics": baseline.metrics,
            }

        best_comparison = comparisons[best.name]
        score_uplift = float(best_comparison["score_uplift"])
        if score_uplift < cfg.min_multi_objective_uplift:
            passed = False
            shortfall = cfg.min_multi_objective_uplift - score_uplift
            underperformance += shortfall
            reasons.append(
                f"多目标分数较最佳 baseline({best.name})仅提升 {score_uplift:.4f}，低于 {cfg.min_multi_objective_uplift:.4f}"
            )

        sharpe_uplift = best_comparison.get("sharpe_uplift")
        if sharpe_uplift is not None and sharpe_uplift < cfg.min_sharpe_uplift:
            passed = False
            shortfall = cfg.min_sharpe_uplift - float(sharpe_uplift)
            underperformance += shortfall * 0.25
            reasons.append(
                f"Sharpe 较最佳 baseline({best.name})提升 {float(sharpe_uplift):.4f}，低于 {cfg.min_sharpe_uplift:.4f}"
            )

        return_uplift = best_comparison.get("return_uplift")
        if return_uplift is not None and return_uplift < cfg.min_return_uplift:
            passed = False
            shortfall = cfg.min_return_uplift - float(return_uplift)
            underperformance += shortfall
            reasons.append(
                f"收益较最佳 baseline({best.name})提升 {float(return_uplift):.2%}，低于 {cfg.min_return_uplift:.2%}"
            )

        drawdown_delta = best_comparison.get("drawdown_delta")
        if drawdown_delta is not None and drawdown_delta > cfg.max_drawdown_worsening:
            passed = False
            excess = float(drawdown_delta) - cfg.max_drawdown_worsening
            underperformance += excess
            reasons.append(
                f"回撤较最佳 baseline({best.name})恶化 {float(drawdown_delta):.2%}，超过 {cfg.max_drawdown_worsening:.2%}"
            )

    adjusted_score = candidate_score - cfg.complexity_penalty
    if not passed:
        adjusted_score -= cfg.underperformance_penalty_multiplier * underperformance
    adjusted_score = _clip_unit(adjusted_score)

    if passed:
        if best is None:
            reasons.append("baseline 指标缺失，本次不阻塞但已扣复杂度惩罚")
        else:
            reasons.append(f"候选策略通过 baseline 对照门槛，最佳 baseline 为 {best.name}")

    return BaselineComparisonResult(
        passed=passed,
        adjusted_score=adjusted_score,
        best_baseline=best,
        comparisons=comparisons,
        reasons=list(dict.fromkeys(reasons)),
        config=asdict(cfg),
    )


def enrich_metrics_with_baseline_comparison(
    metrics: dict[str, Any],
    baseline_metrics: dict[str, dict[str, Any]] | None = None,
    *,
    config: BaselineComparisonConfig | None = None,
    multi_objective_config: MultiObjectiveConfig | None = None,
) -> dict[str, Any]:
    """返回带 baseline 对照字段的 metrics 副本。"""

    enriched = enrich_metrics_with_multi_objective(metrics, config=multi_objective_config)
    if "baseline_adjusted_score" in enriched:
        return enriched
    comparison = compare_against_baselines(
        enriched,
        baseline_metrics=baseline_metrics,
        config=config,
        multi_objective_config=multi_objective_config,
    )
    enriched.update(comparison.to_metrics())
    return enriched


def _objective_metric_key(objective: str) -> str:
    if objective == "multi_objective":
        return "multi_objective_score"
    if objective in {"baseline_adjusted", "baseline_adjusted_score"}:
        return "baseline_adjusted_score"
    return objective


# ---------------------------------------------------------------------------
# Optimization Result
# ---------------------------------------------------------------------------


@dataclass
class OptimizationTrial:
    """单次优化试验结果。"""

    params: dict[str, Any]
    metric_value: float
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class OptimizationResult:
    """优化结果汇总。"""

    best_params: dict[str, Any]
    best_metric: float
    trials: list[OptimizationTrial]
    objective: str
    method: str
    total_trials: int
    deflation_warning: str | None = None
    inflation_diagnostics: dict[str, Any] | None = None


def compute_inflation_diagnostics(
    result: OptimizationResult,
    best_returns: list[float] | None = None,
    freq: int = 252,
) -> dict[str, Any]:
    """计算优化结果的多重检验校正诊断。"""

    metrics = [
        t.metric_value for t in result.trials
        if t.metric_value not in (float("inf"), float("-inf"))
        and not (isinstance(t.metric_value, float) and math.isnan(t.metric_value))
    ]
    n_trials = max(len(metrics), 1)

    diagnostics: dict[str, Any] = {
        "n_trials": n_trials,
        "objective": result.objective,
    }

    if result.objective == "sharpe" and len(metrics) >= 2:
        per_period = [m / math.sqrt(freq) for m in metrics]
        var_trials = float(np.var(per_period, ddof=1))
        diagnostics["variance_of_trials"] = var_trials

        from app.domain.performance.sharpe_inference import expected_max_sharpe

        expected_max = expected_max_sharpe(n_trials, var_trials)
        diagnostics["expected_max_sharpe_per_period"] = expected_max
        diagnostics["expected_max_sharpe_annualized"] = expected_max * math.sqrt(freq)

    if best_returns is not None and result.objective == "sharpe":
        from app.domain.performance.sharpe_inference import sharpe_inference

        var_trials = diagnostics.get("variance_of_trials")
        inference = sharpe_inference(
            returns=best_returns,
            n_trials=n_trials,
            variance_of_trials=var_trials,
            freq=freq,
        )
        if inference is not None:
            diagnostics["dsr"] = inference.dsr
            diagnostics["psr"] = inference.psr
            diagnostics["dsr_significant"] = inference.dsr_significant
            diagnostics["psr_significant"] = inference.psr_significant
            diagnostics["ci_lower_annualized"] = inference.ci_lower
            diagnostics["ci_upper_annualized"] = inference.ci_upper

    return diagnostics


# ---------------------------------------------------------------------------
# Backtest Runner (callable for optimization)
# ---------------------------------------------------------------------------


BacktestRunner = Callable[[dict[str, Any]], dict[str, float]]


# ---------------------------------------------------------------------------
# Optimizers
# ---------------------------------------------------------------------------


class GridSearchOptimizer:
    """网格搜索优化器。"""

    def __init__(
        self,
        param_space: ParamSpace,
        objective: str = "sharpe",
        maximize: bool = True,
        multi_objective_config: MultiObjectiveConfig | None = None,
        baseline_metrics: dict[str, dict[str, Any]] | None = None,
        baseline_config: BaselineComparisonConfig | None = None,
    ) -> None:
        self.param_space = param_space
        self.objective = objective
        self.maximize = maximize
        self.multi_objective_config = multi_objective_config
        self.baseline_metrics = baseline_metrics
        self.baseline_config = baseline_config

    def _enrich_metrics(self, metrics: dict[str, Any]) -> dict[str, Any]:
        enriched = enrich_metrics_with_multi_objective(metrics, config=self.multi_objective_config)
        if self.baseline_metrics is not None or _extract_baseline_payload(enriched) or self.objective in {"baseline_adjusted", "baseline_adjusted_score"}:
            enriched = enrich_metrics_with_baseline_comparison(
                enriched,
                baseline_metrics=self.baseline_metrics,
                config=self.baseline_config,
                multi_objective_config=self.multi_objective_config,
            )
        return enriched

    def optimize(self, runner: BacktestRunner) -> OptimizationResult:
        points = self.param_space.grid_points()
        logger.info("Grid search: %d parameter combinations", len(points))

        trials: list[OptimizationTrial] = []
        objective_key = _objective_metric_key(self.objective)
        failed_value = float("-inf") if self.maximize else float("inf")

        for i, params in enumerate(points):
            try:
                metrics = self._enrich_metrics(runner(params))
                metric_value = metrics.get(objective_key, failed_value)
                trials.append(
                    OptimizationTrial(
                        params=params,
                        metric_value=float(metric_value),
                        metrics=metrics,
                    )
                )
                logger.debug(
                    "Grid search trial %d/%d: params=%s, %s=%.6f",
                    i + 1, len(points), params, self.objective, float(metric_value),
                )
            except Exception as e:
                logger.warning(
                    "Grid search trial %d/%d failed: params=%s, error=%s",
                    i + 1, len(points), params, str(e),
                )
                trials.append(
                    OptimizationTrial(
                        params=params,
                        metric_value=failed_value,
                        metrics={},
                    )
                )

        return self._build_result(trials, method="grid")

    def _build_result(
        self, trials: list[OptimizationTrial], method: str
    ) -> OptimizationResult:
        sorted_trials = sorted(
            trials,
            key=lambda t: t.metric_value,
            reverse=self.maximize,
        )

        best = sorted_trials[0] if sorted_trials else OptimizationTrial(
            params={}, metric_value=float("-inf") if self.maximize else float("inf")
        )

        warning: str | None = None
        if self.objective in {"multi_objective", "multi_objective_score", "baseline_adjusted", "baseline_adjusted_score"} and len(trials) >= 2:
            eliminated_count = sum(
                1 for trial in trials
                if bool(trial.metrics.get("multi_objective_eliminated"))
            )
            baseline_failed_count = sum(
                1 for trial in trials
                if trial.metrics and trial.metrics.get("baseline_passed") is False
            )
            warning = (
                f"多目标优化已评估 {len(trials)} 组参数，其中 {eliminated_count} 组被规则淘汰，"
                f"{baseline_failed_count} 组未跑赢简单 baseline。"
            )
        if self.objective == "sharpe" and len(trials) >= 10:
            warning = (
                f"网格搜索做了 {len(trials)} 次试验。最优 Sharpe = "
                f"{best.metric_value:.3f} 可能受选择偏差影响而被高估。"
                "建议调用 compute_inflation_diagnostics() 计算 DSR/PSR 校验显著性。"
            )

        return OptimizationResult(
            best_params=best.params,
            best_metric=best.metric_value,
            trials=sorted_trials,
            objective=self.objective,
            method=method,
            total_trials=len(trials),
            deflation_warning=warning,
        )


class SobolSearchOptimizer:
    """基于 Sobol 准随机序列的随机搜索优化器。"""

    def __init__(
        self,
        param_space: ParamSpace,
        n_samples: int = 64,
        objective: str = "sharpe",
        maximize: bool = True,
        seed: int | None = None,
        multi_objective_config: MultiObjectiveConfig | None = None,
        baseline_metrics: dict[str, dict[str, Any]] | None = None,
        baseline_config: BaselineComparisonConfig | None = None,
    ) -> None:
        self.param_space = param_space
        self.n_samples = n_samples
        self.objective = objective
        self.maximize = maximize
        self.seed = seed
        self.multi_objective_config = multi_objective_config
        self.baseline_metrics = baseline_metrics
        self.baseline_config = baseline_config

    def _enrich_metrics(self, metrics: dict[str, Any]) -> dict[str, Any]:
        return GridSearchOptimizer(
            self.param_space,
            objective=self.objective,
            maximize=self.maximize,
            multi_objective_config=self.multi_objective_config,
            baseline_metrics=self.baseline_metrics,
            baseline_config=self.baseline_config,
        )._enrich_metrics(metrics)

    def optimize(self, runner: BacktestRunner) -> OptimizationResult:
        points = self.param_space.sobol_sample(self.n_samples, seed=self.seed)
        logger.info("Sobol search: %d samples", len(points))

        trials: list[OptimizationTrial] = []
        objective_key = _objective_metric_key(self.objective)
        failed_value = float("-inf") if self.maximize else float("inf")

        for i, params in enumerate(points):
            try:
                metrics = self._enrich_metrics(runner(params))
                metric_value = metrics.get(objective_key, failed_value)
                trials.append(
                    OptimizationTrial(
                        params=params,
                        metric_value=float(metric_value),
                        metrics=metrics,
                    )
                )
                logger.debug(
                    "Sobol search trial %d/%d: params=%s, %s=%.6f",
                    i + 1, len(points), params, self.objective, float(metric_value),
                )
            except Exception as e:
                logger.warning(
                    "Sobol search trial %d/%d failed: params=%s, error=%s",
                    i + 1, len(points), params, str(e),
                )
                trials.append(
                    OptimizationTrial(
                        params=params,
                        metric_value=failed_value,
                        metrics={},
                    )
                )

        builder = GridSearchOptimizer(
            self.param_space,
            objective=self.objective,
            maximize=self.maximize,
            multi_objective_config=self.multi_objective_config,
            baseline_metrics=self.baseline_metrics,
            baseline_config=self.baseline_config,
        )
        return builder._build_result(trials, method="sobol")


# ---------------------------------------------------------------------------
# Parallel Optimizer (Celery task pool)
# ---------------------------------------------------------------------------


def _run_single_backtest_task(
    strategy_class_path: str,
    params: dict[str, Any],
    nav_data_serialized: dict[str, dict[str, str]],
    start_date: str,
    end_date: str,
    initial_capital: str,
    fund_meta_serialized: dict[str, dict[str, Any]] | None,
    objective: str,
) -> dict[str, Any]:
    """Celery 任务：运行单次回测并返回指标。"""

    import importlib
    from datetime import date as date_type

    nav_data: dict[str, dict[date_type, Decimal]] = {}
    for fund_code, date_navs in nav_data_serialized.items():
        nav_data[fund_code] = {
            date_type.fromisoformat(d): Decimal(n) for d, n in date_navs.items()
        }

    fund_meta: dict[str, FundMeta] | None = None
    if fund_meta_serialized:
        fund_meta = {}
        for code, meta_dict in fund_meta_serialized.items():
            fund_meta[code] = FundMeta(
                code=code,
                fund_type=meta_dict.get("fund_type", "stock"),
            )

    module_path, class_name = strategy_class_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    strategy_class = getattr(module, class_name)

    if hasattr(strategy_class, "params_class"):
        strategy_params = strategy_class.params_class(**params)
    else:
        strategy_params = StrategyParams()

    universe = list(nav_data.keys())
    strategy = strategy_class(params=strategy_params, universe=universe)

    engine = EventDrivenEngine()
    result = engine.run(
        start=date_type.fromisoformat(start_date),
        end=date_type.fromisoformat(end_date),
        strategy=strategy,
        nav_data=nav_data,
        initial_capital=Decimal(initial_capital),
        fund_meta=fund_meta,
    )

    from app.domain.backtest.result import BacktestResult as EnhancedResult

    enhanced = EnhancedResult.from_engine_result(result)
    metrics_obj = enhanced.metrics
    metrics: dict[str, float] = {}
    if metrics_obj:
        metrics = {
            "total_return": metrics_obj.total_return,
            "annualized_return": metrics_obj.annualized_return,
            "max_drawdown": metrics_obj.max_drawdown,
            "sharpe": metrics_obj.sharpe,
            "sortino": metrics_obj.sortino,
            "volatility": metrics_obj.volatility,
            "calmar": metrics_obj.calmar,
            "win_rate": metrics_obj.win_rate,
            "profit_factor": metrics_obj.profit_factor,
        }

    initial = float(Decimal(initial_capital) or 0)
    traded_amount = sum(float(getattr(trade, "amount", 0) or 0) for trade in result.trades)
    total_fees = sum(float(getattr(trade, "fee", 0) or 0) for trade in result.trades)
    if initial > 0:
        metrics["turnover"] = traded_amount / initial
        metrics["fee_drag"] = total_fees / initial
    metrics["sample_count"] = max(0, len(result.equity_curve) - 1)

    metrics = enrich_metrics_with_multi_objective(metrics)
    objective_key = _objective_metric_key(objective)
    return {
        "params": params,
        "metrics": metrics,
        "objective_value": metrics.get(objective_key, float("-inf")),
    }


class ParallelOptimizer:
    """并行参数优化器（基于 Celery 任务池）。"""

    def __init__(
        self,
        param_space: ParamSpace,
        strategy_class_path: str,
        nav_data: dict[str, dict[date, Decimal]],
        start_date: date,
        end_date: date,
        initial_capital: Decimal = Decimal("100000"),
        fund_meta: dict[str, FundMeta] | None = None,
        objective: str = "sharpe",
        maximize: bool = True,
        method: Literal["grid", "sobol"] = "sobol",
        n_samples: int = 64,
        seed: int | None = None,
        timeout: int = 600,
        multi_objective_config: MultiObjectiveConfig | None = None,
        baseline_metrics: dict[str, dict[str, Any]] | None = None,
        baseline_config: BaselineComparisonConfig | None = None,
    ) -> None:
        self.param_space = param_space
        self.strategy_class_path = strategy_class_path
        self.nav_data = nav_data
        self.start_date = start_date
        self.end_date = end_date
        self.initial_capital = initial_capital
        self.fund_meta = fund_meta
        self.objective = objective
        self.maximize = maximize
        self.method = method
        self.n_samples = n_samples
        self.seed = seed
        self.timeout = timeout
        self.multi_objective_config = multi_objective_config
        self.baseline_metrics = baseline_metrics
        self.baseline_config = baseline_config

    def _generate_points(self) -> list[dict[str, Any]]:
        if self.method == "grid":
            return self.param_space.grid_points()
        return self.param_space.sobol_sample(self.n_samples, seed=self.seed)

    def _serialize_nav_data(self) -> dict[str, dict[str, str]]:
        serialized: dict[str, dict[str, str]] = {}
        for fund_code, date_navs in self.nav_data.items():
            serialized[fund_code] = {
                d.isoformat(): str(nav) for d, nav in date_navs.items()
            }
        return serialized

    def _serialize_fund_meta(self) -> dict[str, dict[str, Any]] | None:
        if self.fund_meta is None:
            return None
        serialized: dict[str, dict[str, Any]] = {}
        for code, meta in self.fund_meta.items():
            serialized[code] = {"fund_type": meta.fund_type}
        return serialized

    def _enrich_metrics(self, metrics: dict[str, Any]) -> dict[str, Any]:
        return GridSearchOptimizer(
            self.param_space,
            objective=self.objective,
            maximize=self.maximize,
            multi_objective_config=self.multi_objective_config,
            baseline_metrics=self.baseline_metrics,
            baseline_config=self.baseline_config,
        )._enrich_metrics(metrics)

    def optimize(self) -> OptimizationResult:
        from app.tasks.celery_app import celery_app as _celery_app

        points = self._generate_points()
        logger.info(
            "Parallel optimization (%s): dispatching %d tasks",
            self.method, len(points),
        )

        nav_serialized = self._serialize_nav_data()
        meta_serialized = self._serialize_fund_meta()
        tasks = []
        for params in points:
            task = _celery_app.send_task(
                "app.tasks.optimization.run_optimization_backtest",
                kwargs={
                    "strategy_class_path": self.strategy_class_path,
                    "params": params,
                    "nav_data_serialized": nav_serialized,
                    "start_date": self.start_date.isoformat(),
                    "end_date": self.end_date.isoformat(),
                    "initial_capital": str(self.initial_capital),
                    "fund_meta_serialized": meta_serialized,
                    "objective": self.objective,
                },
                queue="backtest",
            )
            tasks.append(task)

        trials: list[OptimizationTrial] = []
        failed_value = float("-inf") if self.maximize else float("inf")
        objective_key = _objective_metric_key(self.objective)
        for i, async_result in enumerate(tasks):
            try:
                result = async_result.get(timeout=self.timeout)
                metrics = self._enrich_metrics(result["metrics"])
                trials.append(
                    OptimizationTrial(
                        params=result["params"],
                        metric_value=float(metrics.get(objective_key, result["objective_value"])),
                        metrics=metrics,
                    )
                )
            except Exception as e:
                logger.warning(
                    "Parallel task %d/%d failed: %s", i + 1, len(tasks), str(e)
                )
                trials.append(
                    OptimizationTrial(
                        params=points[i],
                        metric_value=failed_value,
                        metrics={},
                    )
                )

        return self._build_result(trials)

    def optimize_local(self) -> OptimizationResult:
        points = self._generate_points()
        logger.info(
            "Local optimization (%s): %d parameter combinations",
            self.method, len(points),
        )

        trials: list[OptimizationTrial] = []
        failed_value = float("-inf") if self.maximize else float("inf")
        objective_key = _objective_metric_key(self.objective)
        for i, params in enumerate(points):
            try:
                result = _run_single_backtest_task(
                    strategy_class_path=self.strategy_class_path,
                    params=params,
                    nav_data_serialized=self._serialize_nav_data(),
                    start_date=self.start_date.isoformat(),
                    end_date=self.end_date.isoformat(),
                    initial_capital=str(self.initial_capital),
                    fund_meta_serialized=self._serialize_fund_meta(),
                    objective=self.objective,
                )
                metrics = self._enrich_metrics(result["metrics"])
                trials.append(
                    OptimizationTrial(
                        params=result["params"],
                        metric_value=float(metrics.get(objective_key, result["objective_value"])),
                        metrics=metrics,
                    )
                )
            except Exception as e:
                logger.warning(
                    "Local trial %d/%d failed: params=%s, error=%s",
                    i + 1, len(points), params, str(e),
                )
                trials.append(
                    OptimizationTrial(
                        params=params,
                        metric_value=failed_value,
                        metrics={},
                    )
                )

        return self._build_result(trials)

    def _build_result(self, trials: list[OptimizationTrial]) -> OptimizationResult:
        return GridSearchOptimizer(
            self.param_space,
            objective=self.objective,
            maximize=self.maximize,
            multi_objective_config=self.multi_objective_config,
            baseline_metrics=self.baseline_metrics,
            baseline_config=self.baseline_config,
        )._build_result(trials, method=self.method)


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------


def create_optimizer(
    param_space: ParamSpace,
    method: Literal["grid", "sobol"] = "grid",
    objective: str = "sharpe",
    maximize: bool = True,
    n_samples: int = 64,
    seed: int | None = None,
    multi_objective_config: MultiObjectiveConfig | None = None,
    baseline_metrics: dict[str, dict[str, Any]] | None = None,
    baseline_config: BaselineComparisonConfig | None = None,
) -> GridSearchOptimizer | SobolSearchOptimizer:
    """创建优化器的便捷工厂函数。"""

    if method == "grid":
        return GridSearchOptimizer(
            param_space=param_space,
            objective=objective,
            maximize=maximize,
            multi_objective_config=multi_objective_config,
            baseline_metrics=baseline_metrics,
            baseline_config=baseline_config,
        )
    return SobolSearchOptimizer(
        param_space=param_space,
        n_samples=n_samples,
        objective=objective,
        maximize=maximize,
        seed=seed,
        multi_objective_config=multi_objective_config,
        baseline_metrics=baseline_metrics,
        baseline_config=baseline_config,
    )
