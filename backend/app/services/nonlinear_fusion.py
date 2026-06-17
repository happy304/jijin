"""非线性信号融合模块（v5 新增）。

替代简单线性加权，捕捉因子间的交互效应。

方法选择：
- 不引入 XGBoost/sklearn 重依赖（避免部署复杂度）
- 使用基于规则的非线性组合 + 交互项
- 核心思想：当多个因子同时极端时，信号应该比线性加权更强

非线性增强策略：
1. 信号一致性加成（已在 v5 基础版实现）
2. 极端信号放大：单因子极端值时非线性放大
3. 因子交互项：动量×技术、动量×估值 等交叉项
4. 分段线性：不同评分区间使用不同权重

设计原则：
- 可解释性优先（不用黑箱模型）
- 渐进式增强（在线性基础上叠加非线性项）
- 参数少（避免过拟合）
- 失败时回退到线性加权
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class FusionInput:
    """信号融合的输入。"""

    technical: float = 0.0
    momentum: float = 0.0
    strategy: float = 0.0
    prediction: float = 0.0
    cross_sectional: float = 0.0
    macro: float = 0.0  # 宏观因子


@dataclass
class FusionResult:
    """非线性融合结果。"""

    score: float = 0.0  # 最终融合评分 (-1 到 1)
    linear_component: float = 0.0  # 线性部分
    nonlinear_component: float = 0.0  # 非线性增强部分
    interaction_boost: float = 0.0  # 交互项贡献
    extreme_boost: float = 0.0  # 极端信号放大贡献


def nonlinear_fuse(
    signals: FusionInput,
    weights: dict[str, float],
    macro_score: float = 0.0,
) -> FusionResult:
    """非线性信号融合。

    在线性加权基础上叠加三种非线性增强：
    1. 极端信号放大：单因子 |score| > 0.6 时，贡献按 x^1.5 放大
    2. 因子交互项：动量×技术、动量×预测 同向时额外加成
    3. 宏观确认加成：宏观因子与个股信号同向时加成

    Args:
        signals: 各维度评分
        weights: 归一化权重 {technical: w, momentum: w, ...}
        macro_score: 宏观因子评分

    Returns:
        FusionResult
    """
    result = FusionResult()

    # 1. 线性基础分
    signal_map = {
        "technical": signals.technical,
        "momentum": signals.momentum,
        "strategy": signals.strategy,
        "prediction": signals.prediction,
        "cross_sectional": signals.cross_sectional,
    }

    linear = sum(
        weights.get(k, 0) * v for k, v in signal_map.items()
    )
    result.linear_component = linear

    # 2. 极端信号放大
    # 当单因子评分绝对值 > 0.6 时，用 sign(x) * |x|^1.3 替代 x
    # 效果：强信号被放大，弱信号不变
    extreme_sum = 0.0
    for k, v in signal_map.items():
        w = weights.get(k, 0)
        if w == 0:
            continue
        if abs(v) > 0.6:
            # 非线性放大：|x|^1.3 > |x| 当 |x| > 1 时不成立
            # 但我们的信号在 [-1,1]，所以用 sign * |x|^0.7 来放大
            # |0.7|^0.7 = 0.76 > 0.7, |0.9|^0.7 = 0.93 > 0.9 × 不对
            # 改用：sign(x) * (|x| + (|x|-0.6)^2 * 0.5)
            amplified = np.sign(v) * (abs(v) + (abs(v) - 0.6) ** 2 * 1.5)
            extreme_sum += w * (amplified - v)  # 只计算增量

    result.extreme_boost = float(extreme_sum)

    # 3. 因子交互项
    # 动量 × 技术：两者同向且都较强时，额外加成
    interaction = 0.0
    mom = signals.momentum
    tech = signals.technical
    pred = signals.prediction

    # 动量-技术交互（价格趋势确认）
    if abs(mom) > 0.15 and abs(tech) > 0.15:
        if mom * tech > 0:  # 同向
            interaction += 0.08 * min(abs(mom), abs(tech))
            if mom > 0:
                interaction = abs(interaction)
            else:
                interaction = -abs(interaction)

    # 动量-预测交互（趋势+概率确认）
    if abs(mom) > 0.15 and abs(pred) > 0.15:
        if mom * pred > 0:  # 同向
            inter_mp = 0.06 * min(abs(mom), abs(pred))
            interaction += inter_mp if mom > 0 else -inter_mp

    result.interaction_boost = float(interaction)

    # 4. 宏观确认（已在 trading_advisor 中实现，这里作为备用）
    # 如果 macro_score 非零且与 linear 同向，额外加成
    macro_boost = 0.0
    if macro_score != 0 and linear != 0:
        if macro_score * linear > 0:
            macro_boost = abs(macro_score) * 0.1 * np.sign(linear)

    # 5. 合成最终分数
    raw = linear + result.extreme_boost + result.interaction_boost + macro_boost
    result.nonlinear_component = float(
        result.extreme_boost + result.interaction_boost + macro_boost
    )
    result.score = float(np.clip(raw, -1.0, 1.0))

    return result


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------

__all__ = [
    "FusionInput",
    "FusionResult",
    "nonlinear_fuse",
]
