"""FOF（基金中的基金）策略模块。

实现基于多因子打分筛选 + 组合优化的 FOF 策略：
- FOFStrategy: 多因子评分筛选基金池，再通过优化方法确定权重

设计要点：
- 继承 BaseStrategy，实现 on_bar 方法
- 支持多因子加权打分：收益率、Sharpe、波动率、最大回撤等
- 筛选规则：Top-N 或得分阈值
- 优化方法：等权、逆波动率加权、风险平价、得分加权
- 在调仓日通过 rebalance_to 生成最小化调仓指令

需求: 5.5
"""

from __future__ import annotations

import math
from datetime import date
from decimal import Decimal
from enum import Enum

import numpy as np
from pydantic import Field

from app.domain.backtest.engine_event import BarContext
from app.domain.backtest.order import OrderIntent
from app.domain.strategy.base import BaseStrategy, StrategyParams, rebalance_to


# ---------------------------------------------------------------------------
# 因子类型枚举
# ---------------------------------------------------------------------------


class FactorType(str, Enum):
    """可用的评分因子。"""

    RETURN = "return"  # 区间收益率
    SHARPE = "sharpe"  # Sharpe 比率
    VOLATILITY = "volatility"  # 波动率（越低越好）
    MAX_DRAWDOWN = "max_drawdown"  # 最大回撤（越小越好）
    SORTINO = "sortino"  # Sortino 比率


# ---------------------------------------------------------------------------
# 优化方法枚举
# ---------------------------------------------------------------------------


class WeightMethod(str, Enum):
    """权重优化方法。"""

    EQUAL = "equal"  # 等权
    INVERSE_VOL = "inverse_vol"  # 逆波动率加权
    SCORE_WEIGHTED = "score_weighted"  # 得分加权
    RISK_PARITY = "risk_parity"  # 风险平价


# ---------------------------------------------------------------------------
# 调仓频率枚举
# ---------------------------------------------------------------------------


class RebalanceFreq(str, Enum):
    """调仓频率。"""

    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"


# ---------------------------------------------------------------------------
# 因子权重配置
# ---------------------------------------------------------------------------


class FactorWeight:
    """因子权重配置。

    Attributes:
        factor: 因子类型
        weight: 权重（正数表示越大越好，负数表示越小越好）
        lookback_days: 该因子的回看窗口
    """

    def __init__(
        self,
        factor: FactorType,
        weight: float = 1.0,
        lookback_days: int = 60,
    ) -> None:
        self.factor = factor
        self.weight = weight
        self.lookback_days = lookback_days


# ---------------------------------------------------------------------------
# 参数类
# ---------------------------------------------------------------------------


class FOFParams(StrategyParams):
    """FOF 策略参数。

    Attributes:
        lookback_days: 默认回看窗口天数
        rebalance_freq: 调仓频率
        top_n: 选取得分最高的 N 只基金（0 表示使用阈值筛选）
        score_threshold: 得分阈值（仅 top_n=0 时使用）
        weight_method: 权重优化方法
    """

    lookback_days: int = Field(default=60, gt=0, description="默认回看窗口天数")
    rebalance_freq: RebalanceFreq = Field(
        default=RebalanceFreq.MONTHLY, description="调仓频率"
    )
    top_n: int = Field(default=5, ge=0, description="选取 Top-N 基金")
    score_threshold: float = Field(default=0.0, description="得分阈值")
    weight_method: WeightMethod = Field(
        default=WeightMethod.EQUAL, description="权重优化方法"
    )
    max_weight: float = Field(default=0.25, gt=0, le=1, description="单基金最大权重")
    winsorize_enabled: bool = Field(default=True, description="是否对因子做分位截尾")
    winsorize_lower: float = Field(default=0.05, ge=0, lt=1, description="因子截尾下分位")
    winsorize_upper: float = Field(default=0.95, gt=0, le=1, description="因子截尾上分位")
    rank_normalize_enabled: bool = Field(default=True, description="是否使用排名归一化")
    factor_validation_enabled: bool = Field(default=True, description="是否计算因子 IC/分组收益验证诊断")
    validation_forward_days: int = Field(default=20, ge=2, description="因子验证前瞻收益天数")
    correlation_penalty_enabled: bool = Field(default=True, description="是否对高相关入选基金做权重惩罚")
    correlation_threshold: float = Field(default=0.85, ge=-1, le=1, description="相关性惩罚阈值")
    correlation_penalty_strength: float = Field(default=0.5, ge=0, le=1, description="高相关基金权重惩罚强度")
    type_concentration_penalty_enabled: bool = Field(default=True, description="是否启用类型集中度诊断")
    max_type_weight: float = Field(default=0.7, gt=0, le=1, description="单类型基金目标权重上限")


# ---------------------------------------------------------------------------
# 因子计算辅助函数
# ---------------------------------------------------------------------------


def compute_return(nav_series: dict[date, Decimal], lookback: int) -> float | None:
    """计算区间收益率。"""
    sorted_dates = sorted(nav_series.keys())
    if len(sorted_dates) < 2:
        return None

    if len(sorted_dates) <= lookback:
        start_nav = float(nav_series[sorted_dates[0]])
    else:
        idx = len(sorted_dates) - lookback - 1
        if idx < 0:
            idx = 0
        start_nav = float(nav_series[sorted_dates[idx]])

    end_nav = float(nav_series[sorted_dates[-1]])
    if start_nav <= 0:
        return None
    return (end_nav / start_nav) - 1.0


def compute_volatility(nav_series: dict[date, Decimal], lookback: int) -> float | None:
    """计算年化波动率。"""
    sorted_dates = sorted(nav_series.keys())
    if len(sorted_dates) <= lookback:
        window_dates = sorted_dates
    else:
        window_dates = sorted_dates[-lookback:]

    if len(window_dates) < 3:
        return None

    returns: list[float] = []
    for i in range(1, len(window_dates)):
        prev = float(nav_series[window_dates[i - 1]])
        curr = float(nav_series[window_dates[i]])
        if prev <= 0:
            continue
        returns.append(curr / prev - 1.0)

    if len(returns) < 2:
        return None

    mean_r = sum(returns) / len(returns)
    var = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(var) * math.sqrt(252)


def compute_sharpe(nav_series: dict[date, Decimal], lookback: int) -> float | None:
    """计算年化 Sharpe 比率（无风险利率为 0）。"""
    sorted_dates = sorted(nav_series.keys())
    if len(sorted_dates) <= lookback:
        window_dates = sorted_dates
    else:
        window_dates = sorted_dates[-lookback:]

    if len(window_dates) < 3:
        return None

    returns: list[float] = []
    for i in range(1, len(window_dates)):
        prev = float(nav_series[window_dates[i - 1]])
        curr = float(nav_series[window_dates[i]])
        if prev <= 0:
            continue
        returns.append(curr / prev - 1.0)

    if len(returns) < 2:
        return None

    mean_r = sum(returns) / len(returns)
    var = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
    std_r = math.sqrt(var)

    if std_r == 0:
        return None

    return (mean_r / std_r) * math.sqrt(252)


def compute_max_drawdown(nav_series: dict[date, Decimal], lookback: int) -> float | None:
    """计算最大回撤（返回正数，越小越好）。"""
    sorted_dates = sorted(nav_series.keys())
    if len(sorted_dates) <= lookback:
        window_dates = sorted_dates
    else:
        window_dates = sorted_dates[-lookback:]

    if len(window_dates) < 2:
        return None

    values = [float(nav_series[d]) for d in window_dates]
    peak = values[0]
    max_dd = 0.0

    for v in values:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    return max_dd


def compute_sortino(nav_series: dict[date, Decimal], lookback: int) -> float | None:
    """计算年化 Sortino 比率。"""
    sorted_dates = sorted(nav_series.keys())
    if len(sorted_dates) <= lookback:
        window_dates = sorted_dates
    else:
        window_dates = sorted_dates[-lookback:]

    if len(window_dates) < 3:
        return None

    returns: list[float] = []
    for i in range(1, len(window_dates)):
        prev = float(nav_series[window_dates[i - 1]])
        curr = float(nav_series[window_dates[i]])
        if prev <= 0:
            continue
        returns.append(curr / prev - 1.0)

    if len(returns) < 2:
        return None

    mean_r = sum(returns) / len(returns)
    downside = [r for r in returns if r < 0]

    if not downside:
        # 无下行收益，Sortino 无穷大，返回一个大值
        return 100.0 if mean_r > 0 else 0.0

    downside_var = sum(r**2 for r in downside) / len(downside)
    downside_std = math.sqrt(downside_var)

    if downside_std == 0:
        return None

    return (mean_r / downside_std) * math.sqrt(252)


def compute_factor_score(
    nav_series: dict[date, Decimal],
    factor: FactorType,
    lookback: int,
) -> float | None:
    """计算单个因子得分。

    Args:
        nav_series: 净值序列
        factor: 因子类型
        lookback: 回看窗口

    Returns:
        因子原始值，None 表示数据不足
    """
    if factor == FactorType.RETURN:
        return compute_return(nav_series, lookback)
    elif factor == FactorType.SHARPE:
        return compute_sharpe(nav_series, lookback)
    elif factor == FactorType.VOLATILITY:
        return compute_volatility(nav_series, lookback)
    elif factor == FactorType.MAX_DRAWDOWN:
        return compute_max_drawdown(nav_series, lookback)
    elif factor == FactorType.SORTINO:
        return compute_sortino(nav_series, lookback)
    else:
        return None


# ---------------------------------------------------------------------------
# 多因子打分
# ---------------------------------------------------------------------------


def winsorize_values(
    values: list[float | None],
    lower: float = 0.05,
    upper: float = 0.95,
) -> list[float | None]:
    """对有效因子值做分位截尾，None 保持不变。"""
    valid = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if len(valid) < 2:
        return values[:]
    lo = float(np.quantile(valid, lower))
    hi = float(np.quantile(valid, upper))
    return [None if v is None else float(min(max(float(v), lo), hi)) for v in values]


def rank_normalize(values: list[float | None]) -> list[float | None]:
    """将原始值转为排名百分位（0~1）。

    None 值保持为 None，有效值按排名归一化。

    Args:
        values: 原始因子值列表

    Returns:
        归一化后的排名百分位列表
    """
    valid_indices = [i for i, v in enumerate(values) if v is not None]
    if len(valid_indices) <= 1:
        return [0.5 if v is not None else None for v in values]

    valid_values = [(values[i], i) for i in valid_indices]  # type: ignore
    sorted_vals = sorted(valid_values, key=lambda x: x[0])  # type: ignore

    result: list[float | None] = [None] * len(values)
    n = len(sorted_vals)
    for rank, (_, orig_idx) in enumerate(sorted_vals):
        result[orig_idx] = rank / (n - 1) if n > 1 else 0.5

    return result


def compute_composite_scores_with_diagnostics(
    nav_data: dict[str, dict[date, Decimal]],
    codes: list[str],
    factor_weights: list[FactorWeight],
    winsorize_enabled: bool = True,
    winsorize_limits: tuple[float, float] = (0.05, 0.95),
    rank_normalize_enabled: bool = True,
) -> tuple[dict[str, float], dict[str, object]]:
    """计算多因子综合得分。

    流程：
    1. 对每个因子，计算所有基金的原始值
    2. 排名归一化到 [0, 1]
    3. 对反向因子（波动率、最大回撤）取 1 - rank
    4. 加权求和得到综合得分

    Args:
        nav_data: 各基金净值数据
        codes: 基金代码列表
        factor_weights: 因子权重配置列表

    Returns:
        {fund_code: composite_score} 字典
    """
    # 反向因子（越小越好）
    inverse_factors = {FactorType.VOLATILITY, FactorType.MAX_DRAWDOWN}

    # 初始化得分与诊断
    scores: dict[str, float] = {code: 0.0 for code in codes}
    total_weight = 0.0
    factor_diagnostics: dict[str, dict[str, object]] = {}
    quality_warnings: list[str] = []

    for fw in factor_weights:
        # 计算每只基金的因子原始值
        raw_values: list[float | None] = []
        for code in codes:
            nav_series = nav_data.get(code, {})
            raw_values.append(compute_factor_score(nav_series, fw.factor, fw.lookback_days))

        valid_count = sum(1 for v in raw_values if v is not None)
        coverage_ratio = valid_count / len(codes) if codes else 0.0
        missing_count = len(codes) - valid_count
        if coverage_ratio < 0.7:
            quality_warnings.append(
                f"因子 {fw.factor.value} 覆盖率 {coverage_ratio:.0%}，低于 70% 门槛"
            )

        winsorized = False
        if winsorize_enabled:
            before = raw_values[:]
            raw_values = winsorize_values(raw_values, winsorize_limits[0], winsorize_limits[1])
            winsorized = any(
                a is not None and b is not None and abs(float(a) - float(b)) > 1e-12
                for a, b in zip(before, raw_values, strict=False)
            )

        ranked = rank_normalize(raw_values) if rank_normalize_enabled else raw_values
        factor_diagnostics[fw.factor.value] = {
            "coverage_ratio": round(coverage_ratio, 4),
            "valid_count": valid_count,
            "missing_count": missing_count,
            "winsorized": winsorized,
            "rank_normalized": rank_normalize_enabled,
        }

        # 加权累加
        abs_weight = abs(fw.weight)
        total_weight += abs_weight

        for i, code in enumerate(codes):
            if ranked[i] is not None:
                rank_val = ranked[i]
                # 反向因子取反
                if fw.factor in inverse_factors:
                    rank_val = 1.0 - rank_val  # type: ignore
                # 负权重也取反
                if fw.weight < 0:
                    rank_val = 1.0 - rank_val  # type: ignore
                scores[code] += abs_weight * rank_val  # type: ignore

    # 归一化总分
    if total_weight > 0:
        for code in codes:
            scores[code] /= total_weight

    diagnostics: dict[str, object] = {
        "factors": factor_diagnostics,
        "effective_fund_count": len(codes),
        "oos_status": "not_available",
        "validation_status": "research_only" if quality_warnings else "insufficient_validation",
        "quality_warnings": quality_warnings,
        "winsorize_enabled": winsorize_enabled,
        "rank_normalize_enabled": rank_normalize_enabled,
    }
    if not quality_warnings:
        diagnostics["quality_warnings"] = ["OOS/IC 验证暂不可用，结果仅作研究与复核参考"]
    return scores, diagnostics


def compute_composite_scores(
    nav_data: dict[str, dict[date, Decimal]],
    codes: list[str],
    factor_weights: list[FactorWeight],
    winsorize_enabled: bool = True,
    winsorize_limits: tuple[float, float] = (0.05, 0.95),
    rank_normalize_enabled: bool = True,
) -> dict[str, float]:
    """兼容旧接口：仅返回多因子综合得分。"""
    scores, _ = compute_composite_scores_with_diagnostics(
        nav_data,
        codes,
        factor_weights,
        winsorize_enabled=winsorize_enabled,
        winsorize_limits=winsorize_limits,
        rank_normalize_enabled=rank_normalize_enabled,
    )
    return scores


def _safe_corr(x: list[float], y: list[float]) -> float | None:
    """计算稳健相关系数；样本或方差不足返回 None。"""
    if len(x) < 3 or len(x) != len(y):
        return None
    xa = np.asarray(x, dtype=float)
    ya = np.asarray(y, dtype=float)
    if not np.all(np.isfinite(xa)) or not np.all(np.isfinite(ya)):
        return None
    if float(np.std(xa)) <= 1e-12 or float(np.std(ya)) <= 1e-12:
        return None
    return float(np.corrcoef(xa, ya)[0, 1])


def _rank_ic(values: list[float], returns: list[float]) -> float | None:
    """以排名归一化近似 Spearman Rank IC。"""
    ranked_values = rank_normalize(values)
    ranked_returns = rank_normalize(returns)
    pairs = [
        (float(a), float(b))
        for a, b in zip(ranked_values, ranked_returns, strict=False)
        if a is not None and b is not None
    ]
    if len(pairs) < 3:
        return None
    return _safe_corr([p[0] for p in pairs], [p[1] for p in pairs])


def validate_factor_oos(
    nav_data: dict[str, dict[date, Decimal]],
    codes: list[str],
    factor_weights: list[FactorWeight],
    forward_days: int = 20,
) -> dict[str, object]:
    """用历史切片做轻量 OOS/IC 诊断。

    对每只基金使用 ``T-forward_days`` 之前的数据计算因子，并观察随后
    ``forward_days`` 的收益；该诊断不参与当前因子值计算，仅用于标记
    因子是否具备可复核的样本外迹象。
    """
    diagnostics: dict[str, object] = {
        "status": "not_available",
        "factors": {},
        "warnings": [],
    }
    if len(codes) < 3:
        diagnostics["warnings"] = ["可验证基金数量少于 3，只输出研究近似"]
        return diagnostics

    factor_results: dict[str, dict[str, object]] = {}
    valid_factor_count = 0
    weak_factor_count = 0
    for fw in factor_weights:
        factor_values: list[float] = []
        future_returns: list[float] = []
        for code in codes:
            series = nav_data.get(code, {})
            sorted_dates = sorted(series)
            if len(sorted_dates) <= max(fw.lookback_days // 2, forward_days + 3):
                continue
            split_idx = len(sorted_dates) - forward_days
            if split_idx <= 1 or split_idx >= len(sorted_dates):
                continue
            hist_dates = sorted_dates[:split_idx]
            hist_series = {d: series[d] for d in hist_dates}
            value = compute_factor_score(hist_series, fw.factor, fw.lookback_days)
            start_nav = float(series[sorted_dates[split_idx - 1]])
            end_nav = float(series[sorted_dates[-1]])
            if value is None or start_nav <= 0:
                continue
            factor_values.append(float(value))
            future_returns.append(end_nav / start_nav - 1.0)

        ic = _safe_corr(factor_values, future_returns)
        ric = _rank_ic(factor_values, future_returns)
        group_returns: dict[str, float | None] = {"top": None, "bottom": None, "spread": None}
        if len(future_returns) >= 4:
            pairs = sorted(zip(factor_values, future_returns, strict=False), key=lambda x: x[0], reverse=True)
            n_group = max(1, len(pairs) // 3)
            top_ret = float(np.mean([p[1] for p in pairs[:n_group]]))
            bottom_ret = float(np.mean([p[1] for p in pairs[-n_group:]]))
            group_returns = {"top": round(top_ret, 6), "bottom": round(bottom_ret, 6), "spread": round(top_ret - bottom_ret, 6)}

        status = "not_available"
        if ric is not None:
            if ric >= 0.03:
                status = "effective"
                valid_factor_count += 1
            elif ric >= 0.0:
                status = "weak"
                weak_factor_count += 1
            else:
                status = "invalid"
        factor_results[fw.factor.value] = {
            "sample_count": len(future_returns),
            "ic": round(ic, 4) if ic is not None else None,
            "rank_ic": round(ric, 4) if ric is not None else None,
            "group_returns": group_returns,
            "status": status,
        }

    if valid_factor_count > 0:
        diagnostics["status"] = "available"
    elif weak_factor_count > 0:
        diagnostics["status"] = "weak"
    diagnostics["factors"] = factor_results
    if diagnostics["status"] != "available":
        diagnostics["warnings"] = ["因子样本外验证不足或偏弱，结果仅作研究与复核参考"]
    return diagnostics


# ---------------------------------------------------------------------------
# 权重优化
# ---------------------------------------------------------------------------


def _apply_weight_cap(weights: dict[str, float], max_weight: float) -> dict[str, float]:
    """统一应用单基金权重上限；不可行时自动放宽到等权可行上限。"""
    if not weights:
        return {}
    n = len(weights)
    cap = max(max_weight, 1.0 / n)
    values = {code: max(0.0, float(w)) for code, w in weights.items()}
    total = sum(values.values())
    if total <= 0:
        values = {code: 1.0 / n for code in weights}
    else:
        values = {code: w / total for code, w in values.items()}
    for _ in range(20):
        capped = {code for code, w in values.items() if w > cap}
        if not capped:
            break
        overflow = sum(values[code] - cap for code in capped)
        for code in capped:
            values[code] = cap
        room = [code for code in values if code not in capped]
        capacity = sum(max(cap - values[code], 0.0) for code in room)
        if capacity <= 0:
            break
        for code in room:
            values[code] += overflow * max(cap - values[code], 0.0) / capacity
    total = sum(values.values())
    return {code: w / total for code, w in values.items()} if total > 0 else {}


def compute_weights(
    selected_codes: list[str],
    scores: dict[str, float],
    nav_data: dict[str, dict[date, Decimal]],
    method: WeightMethod,
    lookback: int,
    max_weight: float = 1.0,
) -> dict[str, float]:
    """根据优化方法计算权重。

    Args:
        selected_codes: 入选基金代码列表
        scores: 综合得分字典
        nav_data: 净值数据
        method: 权重方法
        lookback: 回看窗口

    Returns:
        {fund_code: weight} 字典，权重和为 1
    """
    n = len(selected_codes)
    if n == 0:
        return {}

    if method == WeightMethod.EQUAL:
        w = 1.0 / n
        return _apply_weight_cap({code: w for code in selected_codes}, max_weight)

    elif method == WeightMethod.SCORE_WEIGHTED:
        total_score = sum(scores.get(code, 0) for code in selected_codes)
        if total_score <= 0:
            w = 1.0 / n
            return _apply_weight_cap({code: w for code in selected_codes}, max_weight)
        return _apply_weight_cap(
            {code: scores.get(code, 0) / total_score for code in selected_codes},
            max_weight,
        )

    elif method == WeightMethod.INVERSE_VOL:
        vols: dict[str, float] = {}
        for code in selected_codes:
            nav_series = nav_data.get(code, {})
            vol = compute_volatility(nav_series, lookback)
            if vol is not None and vol > 0:
                vols[code] = vol

        if not vols:
            w = 1.0 / n
            return _apply_weight_cap({code: w for code in selected_codes}, max_weight)

        inv_vols = {code: 1.0 / v for code, v in vols.items()}
        total_inv = sum(inv_vols.values())
        if total_inv <= 0:
            w = 1.0 / n
            return _apply_weight_cap({code: w for code in selected_codes}, max_weight)

        weights = {code: inv_vols.get(code, 0) / total_inv for code in selected_codes}
        # 归一化（可能有些基金没有波动率数据）
        w_sum = sum(weights.values())
        if w_sum > 0:
            return _apply_weight_cap({code: w / w_sum for code, w in weights.items()}, max_weight)
        return _apply_weight_cap({code: 1.0 / n for code in selected_codes}, max_weight)

    elif method == WeightMethod.RISK_PARITY:
        # 简化版风险平价：逆波动率权重（与 inverse_vol 类似但概念不同）
        # 完整版在 risk_parity.py 中实现
        vols: dict[str, float] = {}
        for code in selected_codes:
            nav_series = nav_data.get(code, {})
            vol = compute_volatility(nav_series, lookback)
            if vol is not None and vol > 0:
                vols[code] = vol

        if not vols:
            w = 1.0 / n
            return _apply_weight_cap({code: w for code in selected_codes}, max_weight)

        inv_vols = {code: 1.0 / v for code, v in vols.items()}
        total_inv = sum(inv_vols.values())
        weights = {code: inv_vols.get(code, 0) / total_inv for code in selected_codes}
        w_sum = sum(weights.values())
        if w_sum > 0:
            return _apply_weight_cap({code: w / w_sum for code, w in weights.items()}, max_weight)
        return _apply_weight_cap({code: 1.0 / n for code in selected_codes}, max_weight)

    else:
        w = 1.0 / n
        return _apply_weight_cap({code: w for code in selected_codes}, max_weight)


# ---------------------------------------------------------------------------
# 调仓日判断
# ---------------------------------------------------------------------------


def apply_correlation_penalty(
    weights: dict[str, float],
    nav_data: dict[str, dict[date, Decimal]],
    threshold: float = 0.85,
    penalty_strength: float = 0.5,
    max_weight: float = 1.0,
) -> tuple[dict[str, float], dict[str, object]]:
    """对高相关基金权重做温和惩罚并返回审计诊断。"""
    if len(weights) < 2 or penalty_strength <= 0:
        return weights, {"applied": False, "high_correlation_pairs": []}
    returns_by_code: dict[str, np.ndarray] = {}
    for code in weights:
        series = nav_data.get(code, {})
        dates = sorted(series)[-90:]
        if len(dates) < 21:
            continue
        values = np.asarray([float(series[d]) for d in dates], dtype=float)
        if np.all(values[:-1] > 0):
            returns_by_code[code] = values[1:] / values[:-1] - 1.0

    adjusted = dict(weights)
    pairs: list[dict[str, object]] = []
    codes = list(weights)
    for i, a in enumerate(codes):
        for b in codes[i + 1:]:
            ra = returns_by_code.get(a)
            rb = returns_by_code.get(b)
            if ra is None or rb is None:
                continue
            n = min(len(ra), len(rb))
            corr = _safe_corr(ra[-n:].tolist(), rb[-n:].tolist())
            if corr is not None and corr >= threshold:
                lower = a if adjusted.get(a, 0.0) <= adjusted.get(b, 0.0) else b
                adjusted[lower] *= max(0.0, 1.0 - penalty_strength)
                pairs.append({"fund_a": a, "fund_b": b, "correlation": round(corr, 4), "penalized": lower})

    if pairs:
        adjusted = _apply_weight_cap(adjusted, max_weight)
    return adjusted, {"applied": bool(pairs), "high_correlation_pairs": pairs}


def is_rebalance_day(
    current_date: date,
    last_rebalance_date: date | None,
    freq: RebalanceFreq,
) -> bool:
    """判断当前日期是否为调仓日。"""
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
# FOF 策略
# ---------------------------------------------------------------------------


class FOFStrategy(BaseStrategy):
    """FOF 多因子策略。

    流程：
    1. 判断是否为调仓日
    2. 对基金池中每只基金计算多因子综合得分
    3. 按 Top-N 或得分阈值筛选入选基金
    4. 使用指定优化方法计算权重
    5. 通过 rebalance_to 生成调仓指令

    Example::

        strategy = FOFStrategy(
            params=FOFParams(
                lookback_days=60,
                top_n=5,
                weight_method=WeightMethod.SCORE_WEIGHTED,
            ),
            universe=["000001", "000002", ...],
            factor_weights=[
                FactorWeight(FactorType.SHARPE, weight=2.0, lookback_days=60),
                FactorWeight(FactorType.MAX_DRAWDOWN, weight=1.0, lookback_days=60),
                FactorWeight(FactorType.RETURN, weight=1.5, lookback_days=120),
            ],
        )
    """

    name = "fof"

    def __init__(
        self,
        params: FOFParams | None = None,
        universe: list[str] | None = None,
        factor_weights: list[FactorWeight] | None = None,
    ) -> None:
        super().__init__(params=params, universe=universe)
        self._last_rebalance_date: date | None = None
        self._factor_weights = factor_weights or [
            FactorWeight(FactorType.SHARPE, weight=1.0, lookback_days=60),
            FactorWeight(FactorType.RETURN, weight=1.0, lookback_days=60),
            FactorWeight(FactorType.MAX_DRAWDOWN, weight=1.0, lookback_days=60),
        ]
        self.last_diagnostics: dict[str, object] = {}

    @property
    def fof_params(self) -> FOFParams:
        """获取类型化的参数。"""
        assert isinstance(self.params, FOFParams)
        return self.params

    def on_bar(self, context: BarContext) -> list[OrderIntent]:
        """每个交易日判断是否调仓。

        Args:
            context: 当日策略上下文

        Returns:
            OrderIntent 列表，非调仓日返回空列表
        """
        if not is_rebalance_day(
            context.date,
            self._last_rebalance_date,
            self.fof_params.rebalance_freq,
        ):
            return []

        if not self.universe:
            return []

        # 获取各基金净值数据（截止到 T-1）
        nav_data: dict[str, dict[date, Decimal]] = {}
        for code in self.universe:
            nav_series = context.nav_series(code)
            if nav_series:
                nav_data[code] = nav_series

        valid_codes = list(nav_data.keys())
        if not valid_codes:
            return []

        # 计算多因子综合得分与诊断
        scores, diagnostics = compute_composite_scores_with_diagnostics(
            nav_data,
            valid_codes,
            self._factor_weights,
            winsorize_enabled=self.fof_params.winsorize_enabled,
            winsorize_limits=(self.fof_params.winsorize_lower, self.fof_params.winsorize_upper),
            rank_normalize_enabled=self.fof_params.rank_normalize_enabled,
        )
        if self.fof_params.factor_validation_enabled:
            validation = validate_factor_oos(
                nav_data,
                valid_codes,
                self._factor_weights,
                forward_days=self.fof_params.validation_forward_days,
            )
            diagnostics["factor_validation"] = validation
            diagnostics["oos_status"] = validation.get("status", "not_available")
            validation_warnings = validation.get("warnings", [])
            if isinstance(validation_warnings, list):
                diagnostics.setdefault("quality_warnings", [])
                diagnostics["quality_warnings"] = list(diagnostics.get("quality_warnings", [])) + validation_warnings
        self.last_diagnostics = diagnostics

        # 筛选基金
        if self.fof_params.top_n > 0:
            # Top-N 筛选
            sorted_codes = sorted(valid_codes, key=lambda c: scores.get(c, 0), reverse=True)
            n = min(self.fof_params.top_n, len(sorted_codes))
            selected = sorted_codes[:n]
        else:
            # 阈值筛选
            selected = [c for c in valid_codes if scores.get(c, 0) >= self.fof_params.score_threshold]

        if not selected:
            return []

        # 计算权重
        target_weights = compute_weights(
            selected, scores, nav_data,
            self.fof_params.weight_method,
            self.fof_params.lookback_days,
            max_weight=self.fof_params.max_weight,
        )

        if not target_weights:
            return []

        correlation_diagnostics: dict[str, object] = {"applied": False, "high_correlation_pairs": []}
        if self.fof_params.correlation_penalty_enabled:
            target_weights, correlation_diagnostics = apply_correlation_penalty(
                target_weights,
                nav_data,
                threshold=self.fof_params.correlation_threshold,
                penalty_strength=self.fof_params.correlation_penalty_strength,
                max_weight=self.fof_params.max_weight,
            )
            if not target_weights:
                return []

        weight_values = sorted(target_weights.values(), reverse=True)
        top1_weight = weight_values[0] if weight_values else 0.0
        top3_weight = sum(weight_values[:3])
        hhi = sum(w * w for w in target_weights.values())
        concentration_warnings = list(self.last_diagnostics.get("quality_warnings", []))
        if top1_weight > self.fof_params.max_weight + 1e-6:
            concentration_warnings.append("单基金权重超过配置上限，已触发可行性放宽")
        if hhi > 0.35:
            concentration_warnings.append("组合集中度偏高，需人工复核")
        if correlation_diagnostics.get("applied"):
            concentration_warnings.append("高相关基金已触发权重惩罚，需复核组合分散度")
        if self.fof_params.type_concentration_penalty_enabled:
            concentration_warnings.append("当前策略上下文未提供基金类型/行业暴露，类型集中度仅能人工复核")
        self.last_diagnostics.update({
            "selected_count": len(selected),
            "correlation_diagnostics": correlation_diagnostics,
            "type_concentration": {
                "modelled": False,
                "max_type_weight": self.fof_params.max_type_weight,
                "warning": "策略上下文未提供基金类型/行业暴露，暂不自动惩罚类型集中度",
            },
            "weight_diagnostics": {
                "max_weight": self.fof_params.max_weight,
                "top1_weight": round(top1_weight, 4),
                "top3_weight": round(top3_weight, 4),
                "hhi": round(hhi, 4),
            },
            "quality_warnings": concentration_warnings,
            "validation_status": (
                "research_only"
                if concentration_warnings or self.last_diagnostics.get("oos_status") != "available"
                else "available"
            ),
        })

        # 记录调仓日
        self._last_rebalance_date = context.date

        return rebalance_to(context, target_weights)
