"""截面因子选基引擎 — 基于学术研究和 FOF 行业实践的量化基金评价模型。

核心理念：
- 从"单基金时序预测"转向"多基金截面排序"
- 不预测绝对涨跌，只预测相对优劣
- 使用有学术 OOS 证据的因子，而非纯净值衍生信号

因子体系（基于可获取数据）：
1. Alpha 持续性因子：近期风险调整后超额收益的稳定性
2. 规模因子（负向）：小规模基金灵活性优势
3. 费率因子（负向）：低费率基金长期跑赢
4. 波动率调整收益因子：Sharpe/Sortino 比率的持续性
5. 最大回撤恢复因子：回撤后恢复能力
6. 收益一致性因子：月度正收益比例

学术依据：
- Carhart (1997): 基金业绩持续性
- Berk & Green (2004): 规模与业绩的负相关
- Fama & French (2010): 费率对净收益的侵蚀
- Amihud & Goyenko (2013): R² 与选股能力
- 中国市场: Liu, Stambaugh & Yuan (2019) A股因子模型

设计原则：
- 所有因子在截面上计算（同类基金间比较）
- 使用 percentile rank 而非原始值（消除量纲差异）
- 因子权重可通过 IC 加权自适应调整
- 内置 OOS 验证：滚动截面 IC 监控
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


@dataclass
class CrossSectionalConfig:
    """截面因子选基配置。"""

    # 因子权重（默认等权，可通过 IC 加权自适应）
    weight_alpha_persistence: float = 0.25
    weight_sharpe_persistence: float = 0.20
    weight_size: float = 0.15
    weight_fee: float = 0.15
    weight_drawdown_recovery: float = 0.10
    weight_consistency: float = 0.15

    # 因子计算参数
    alpha_lookback_months: int = 12  # Alpha 持续性回看月数
    sharpe_lookback_days: int = 252  # Sharpe 计算回看天数
    min_history_days: int = 252  # 最少需要的历史天数
    risk_free_rate: float = 0.02  # 无风险利率（年化）

    # 截面排序参数
    min_funds_for_ranking: int = 10  # 截面排序最少需要的基金数
    top_percentile: float = 0.20  # Top 组的百分位阈值
    bottom_percentile: float = 0.20  # Bottom 组的百分位阈值

    # IC 自适应权重
    use_ic_weighted: bool = True  # 是否使用 IC 加权
    ic_lookback_periods: int = 12  # IC 计算回看期数
    ic_min_for_inclusion: float = 0.0  # IC 低于此值的因子权重归零

    # 安全阈值
    oos_ic_safety_threshold: float = 0.0  # OOS IC 低于此值时输出 hold


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class FundFactorValues:
    """单只基金的因子原始值。"""

    fund_code: str
    fund_name: str | None = None
    fund_type: str | None = None

    # 因子原始值
    alpha_persistence: float | None = None
    sharpe_persistence: float | None = None
    size_factor: float | None = None  # log(AUM)，负向因子
    fee_factor: float | None = None  # 管理费率，负向因子
    drawdown_recovery: float | None = None
    consistency: float | None = None  # 月度正收益比例

    # 截面百分位排名 (0~1, 1=最好)
    rank_alpha: float | None = None
    rank_sharpe: float | None = None
    rank_size: float | None = None
    rank_fee: float | None = None
    rank_drawdown: float | None = None
    rank_consistency: float | None = None

    # 综合得分
    composite_rank: float = 0.0  # 0~1, 1=最好
    composite_zscore: float = 0.0  # 标准化综合分


@dataclass
class CrossSectionalResult:
    """截面因子选基结果。"""

    eval_date: str = ""
    fund_type_filter: str | None = None
    n_funds_evaluated: int = 0
    n_funds_qualified: int = 0  # 数据充足的基金数

    # 各基金的因子值和排名
    fund_scores: list[FundFactorValues] = field(default_factory=list)

    # 推荐结果
    top_funds: list[str] = field(default_factory=list)  # Top 组基金代码
    bottom_funds: list[str] = field(default_factory=list)  # Bottom 组基金代码

    # 因子 IC 诊断
    factor_ics: dict[str, float | None] = field(default_factory=dict)
    avg_ic: float | None = None

    # 警告
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "eval_date": self.eval_date,
            "fund_type_filter": self.fund_type_filter,
            "n_funds_evaluated": self.n_funds_evaluated,
            "n_funds_qualified": self.n_funds_qualified,
            "top_funds": self.top_funds,
            "bottom_funds": self.bottom_funds,
            "factor_ics": self.factor_ics,
            "avg_ic": self.avg_ic,
            "fund_scores": [
                {
                    "fund_code": f.fund_code,
                    "fund_name": f.fund_name,
                    "composite_rank": round(f.composite_rank, 4),
                    "factors": {
                        "alpha_persistence": f.alpha_persistence,
                        "sharpe_persistence": f.sharpe_persistence,
                        "size_factor": f.size_factor,
                        "fee_factor": f.fee_factor,
                        "drawdown_recovery": f.drawdown_recovery,
                        "consistency": f.consistency,
                    },
                    "ranks": {
                        "alpha": f.rank_alpha,
                        "sharpe": f.rank_sharpe,
                        "size": f.rank_size,
                        "fee": f.rank_fee,
                        "drawdown": f.rank_drawdown,
                        "consistency": f.rank_consistency,
                    },
                }
                for f in self.fund_scores[:20]  # 只返回前20
            ],
            "warnings": self.warnings,
            "methodology": (
                "截面因子排序模型：在同类基金中按多因子综合排名，"
                "选择相对最优的基金。不预测绝对涨跌，只预测相对优劣。"
            ),
        }


# ---------------------------------------------------------------------------
# 因子计算函数
# ---------------------------------------------------------------------------


def compute_alpha_persistence(
    nav_values: list[float],
    risk_free_rate: float = 0.02,
    window_months: int = 12,
) -> float | None:
    """计算 Alpha 持续性因子。

    方法：
    - 将历史数据分为多个非重叠月度窗口
    - 每个窗口计算超额收益（相对无风险利率）
    - Alpha 持续性 = 正超额收益月份数 / 总月份数 × 平均超额收益的 t 统计量

    学术依据：
    - Carhart (1997) 发现短期（1年内）业绩有一定持续性
    - 持续性因子在截面上有 IC ≈ 0.03~0.06

    Args:
        nav_values: 净值序列（日频）
        risk_free_rate: 年化无风险利率
        window_months: 回看月数

    Returns:
        Alpha 持续性得分，None 表示数据不足
    """
    n = len(nav_values)
    days_per_month = 21  # 约21个交易日/月
    min_days = days_per_month * max(6, window_months)

    if n < min_days:
        return None

    arr = np.array(nav_values, dtype=np.float64)
    monthly_rf = risk_free_rate / 12  # 月度无风险利率

    # 计算月度收益率
    monthly_returns: list[float] = []
    for i in range(window_months):
        end_idx = n - 1 - i * days_per_month
        start_idx = end_idx - days_per_month
        if start_idx < 0:
            break
        month_ret = (arr[end_idx] / arr[start_idx]) - 1
        monthly_returns.append(month_ret)

    if len(monthly_returns) < 6:
        return None

    monthly_returns.reverse()  # 时间正序
    excess_returns = [r - monthly_rf for r in monthly_returns]

    # 正超额收益比例
    positive_ratio = sum(1 for r in excess_returns if r > 0) / len(excess_returns)

    # 超额收益的 t 统计量（衡量显著性）
    mean_excess = np.mean(excess_returns)
    std_excess = np.std(excess_returns, ddof=1)

    if std_excess == 0:
        return positive_ratio  # 无波动时只看正比例

    t_stat = mean_excess / (std_excess / math.sqrt(len(excess_returns)))

    # 综合得分：正比例 × t统计量的 sigmoid 变换
    # t_stat > 2 → 显著正alpha，得分高
    # t_stat < 0 → 负alpha，得分低
    t_score = 2.0 / (1.0 + math.exp(-t_stat)) - 1.0  # 映射到 (-1, 1)

    # 最终得分 = 正比例权重 0.4 + t_score 权重 0.6
    score = 0.4 * (positive_ratio * 2 - 1) + 0.6 * t_score

    return float(score)


def compute_sharpe_persistence(
    nav_values: list[float],
    risk_free_rate: float = 0.02,
    lookback_days: int = 252,
) -> float | None:
    """计算 Sharpe 比率持续性因子。

    方法：
    - 计算前半段和后半段的 Sharpe 比率
    - 持续性 = 两段 Sharpe 的调和平均（惩罚不一致性）
    - 如果两段方向相反（一正一负），持续性为负

    学术依据：
    - Sharpe ratio 在截面上有短期持续性（1-3年）
    - 但长期持续性弱（>5年几乎消失）
    - 使用调和平均而非算术平均，惩罚"前好后差"的基金

    Args:
        nav_values: 净值序列
        risk_free_rate: 年化无风险利率
        lookback_days: 回看天数

    Returns:
        Sharpe 持续性得分，None 表示数据不足
    """
    n = len(nav_values)
    if n < lookback_days:
        return None

    arr = np.array(nav_values[-lookback_days:], dtype=np.float64)
    returns = np.diff(arr) / arr[:-1]

    if len(returns) < 120:
        return None

    daily_rf = risk_free_rate / 252
    half = len(returns) // 2

    # 前半段 Sharpe
    r1 = returns[:half]
    mean1 = np.mean(r1) - daily_rf
    std1 = np.std(r1, ddof=1)
    sharpe1 = (mean1 / std1 * math.sqrt(252)) if std1 > 0 else 0.0

    # 后半段 Sharpe
    r2 = returns[half:]
    mean2 = np.mean(r2) - daily_rf
    std2 = np.std(r2, ddof=1)
    sharpe2 = (mean2 / std2 * math.sqrt(252)) if std2 > 0 else 0.0

    # 方向一致性检查
    if sharpe1 * sharpe2 < 0:
        # 方向相反：持续性为负
        return float(-(abs(sharpe1) + abs(sharpe2)) / 4)

    # 调和平均（两者都为正时）
    if sharpe1 > 0 and sharpe2 > 0:
        harmonic = 2 * sharpe1 * sharpe2 / (sharpe1 + sharpe2)
        return float(harmonic)
    elif sharpe1 < 0 and sharpe2 < 0:
        # 两段都为负：持续性差
        return float((sharpe1 + sharpe2) / 2)
    else:
        # 其中一个为零
        return float((sharpe1 + sharpe2) / 2)


def compute_size_factor(fund_size: float | None) -> float | None:
    """计算规模因子（负向：小规模更好）。

    学术依据：
    - Chen, Hong, Huang & Kubik (2004): 基金规模与业绩负相关
    - 原因：大基金面临流动性约束、冲击成本高、被迫分散
    - A股市场：小基金（2-20亿）灵活性优势更明显

    方法：
    - 使用 log(AUM) 作为原始值
    - 截面排序时反向（小规模排名靠前）
    - 排除极小规模（<0.5亿，清盘风险）

    Args:
        fund_size: 基金规模（亿元）

    Returns:
        log(规模) 的负值，None 表示数据缺失
    """
    if fund_size is None or fund_size <= 0:
        return None

    # 排除清盘风险基金（<0.5亿）
    if fund_size < 0.5:
        return None

    # 使用 -log(size) 使得小规模得分高
    return float(-math.log(fund_size))


def compute_fee_factor(management_fee: float | None) -> float | None:
    """计算费率因子（负向：低费率更好）。

    学术依据：
    - Sharpe (1991): 费前收益为零和博弈，费后低费率基金系统性跑赢
    - Fama & French (2010): 费率是基金业绩最强的截面预测因子之一
    - Morningstar 研究：费率比星级评分更能预测未来业绩

    Args:
        management_fee: 年管理费率（小数形式，如 0.015 = 1.5%）

    Returns:
        费率的负值（低费率得分高），None 表示数据缺失
    """
    if management_fee is None or management_fee < 0:
        return None

    # 负值使得低费率排名靠前
    return float(-management_fee)


def compute_drawdown_recovery(nav_values: list[float]) -> float | None:
    """计算最大回撤恢复因子。

    方法：
    - 计算近1年最大回撤
    - 计算从最大回撤点到当前的恢复比例
    - 恢复因子 = 恢复比例 / (1 + 回撤深度)
    - 快速恢复 + 浅回撤 = 高分

    逻辑：
    - 回撤恢复能力反映基金经理的风控和择时能力
    - 在截面上，恢复快的基金往往后续表现也更好

    Args:
        nav_values: 净值序列

    Returns:
        回撤恢复得分，None 表示数据不足
    """
    n = len(nav_values)
    if n < 120:
        return None

    arr = np.array(nav_values[-252:] if n > 252 else nav_values,
                   dtype=np.float64)

    # 计算回撤序列
    running_max = np.maximum.accumulate(arr)
    drawdowns = (arr - running_max) / running_max

    max_dd = float(np.min(drawdowns))  # 最大回撤（负值）
    max_dd_idx = int(np.argmin(drawdowns))  # 最大回撤发生位置

    if max_dd >= 0:
        # 无回撤，满分
        return 1.0

    # 从最大回撤点到当前的恢复比例
    if max_dd_idx < len(arr) - 1:
        trough_value = arr[max_dd_idx]
        peak_before = running_max[max_dd_idx]
        current_value = arr[-1]

        # 恢复比例：0 = 还在底部，1 = 完全恢复
        loss_amount = peak_before - trough_value
        if loss_amount > 0:
            recovery = min(1.0, (current_value - trough_value) / loss_amount)
        else:
            recovery = 1.0
    else:
        recovery = 0.0

    # 综合得分：恢复比例 / (1 + 回撤深度)
    # 浅回撤 + 快恢复 = 高分
    dd_depth = abs(max_dd)
    score = recovery / (1.0 + dd_depth * 2)

    return float(score)


def compute_consistency_factor(nav_values: list[float]) -> float | None:
    """计算收益一致性因子。

    方法：
    - 计算近12个月中正收益月份的比例
    - 加权：近期月份权重更高（指数衰减）
    - 一致性高的基金 = 稳定创造正收益 = 基金经理能力的体现

    学术依据：
    - 收益一致性（hit rate）是基金经理选股能力的代理变量
    - 在截面上，高一致性基金后续表现更好（IC ≈ 0.03~0.05）
    - 比单纯看收益率更稳健（不受单月极端值影响）

    Args:
        nav_values: 净值序列

    Returns:
        一致性得分 (0~1)，None 表示数据不足
    """
    n = len(nav_values)
    days_per_month = 21
    min_months = 6

    if n < days_per_month * min_months:
        return None

    arr = np.array(nav_values, dtype=np.float64)

    # 计算月度收益率（最近12个月）
    n_months = min(12, (n - 1) // days_per_month)
    monthly_returns: list[float] = []

    for i in range(n_months):
        end_idx = n - 1 - i * days_per_month
        start_idx = end_idx - days_per_month
        if start_idx < 0:
            break
        month_ret = (arr[end_idx] / arr[start_idx]) - 1
        monthly_returns.append(month_ret)

    if len(monthly_returns) < min_months:
        return None

    monthly_returns.reverse()  # 时间正序

    # 指数衰减权重（近期权重更高）
    n_m = len(monthly_returns)
    half_life = 6.0  # 半衰期6个月
    weights = [math.exp(-math.log(2) * (n_m - 1 - i) / half_life)
               for i in range(n_m)]
    total_weight = sum(weights)

    # 加权正收益比例
    weighted_positive = sum(
        w for r, w in zip(monthly_returns, weights) if r > 0
    )
    consistency = weighted_positive / total_weight if total_weight > 0 else 0.0

    return float(consistency)


# ---------------------------------------------------------------------------
# 截面排序引擎
# ---------------------------------------------------------------------------


def percentile_rank(values: list[float | None]) -> list[float | None]:
    """计算百分位排名（0~1，1=最好）。

    处理 None 值：None 不参与排名，结果也为 None。
    处理并列：使用平均排名。
    """
    # 收集有效值及其索引
    valid_pairs: list[tuple[int, float]] = [
        (i, v) for i, v in enumerate(values) if v is not None
    ]

    if not valid_pairs:
        return [None] * len(values)

    n_valid = len(valid_pairs)
    if n_valid == 1:
        result: list[float | None] = [None] * len(values)
        result[valid_pairs[0][0]] = 0.5  # 只有一个值，排名0.5
        return result

    # 按值排序
    sorted_pairs = sorted(valid_pairs, key=lambda x: x[1])

    # 分配排名（处理并列）
    result = [None] * len(values)  # type: ignore[assignment]
    i = 0
    while i < n_valid:
        # 找到所有相同值的范围
        j = i
        while j < n_valid and sorted_pairs[j][1] == sorted_pairs[i][1]:
            j += 1
        # 平均排名
        avg_rank = (i + j - 1) / 2.0
        # 转换为百分位 (0~1)
        pct = avg_rank / (n_valid - 1) if n_valid > 1 else 0.5
        for k in range(i, j):
            result[sorted_pairs[k][0]] = pct
        i = j

    return result


def run_cross_sectional_scoring(
    fund_data: list[dict[str, Any]],
    config: CrossSectionalConfig | None = None,
) -> CrossSectionalResult:
    """运行截面因子选基评分。

    这是核心函数：对一组同类基金计算截面因子，排序，输出推荐。

    Args:
        fund_data: 基金数据列表，每条包含：
            - fund_code: str
            - fund_name: str | None
            - fund_type: str | None
            - nav_values: list[float] (日频净值序列，升序)
            - fund_size: float | None (亿元)
            - management_fee: float | None (年管理费率)
        config: 配置参数

    Returns:
        CrossSectionalResult
    """
    if not config:
        config = CrossSectionalConfig()

    result = CrossSectionalResult(
        eval_date=date.today().isoformat(),
        n_funds_evaluated=len(fund_data),
    )

    if len(fund_data) < config.min_funds_for_ranking:
        result.warnings.append(
            f"基金数量不足（{len(fund_data)}/{config.min_funds_for_ranking}），"
            f"截面排序需要至少 {config.min_funds_for_ranking} 只同类基金"
        )
        return result

    # 1. 计算每只基金的因子原始值
    fund_factors: list[FundFactorValues] = []

    for fd in fund_data:
        nav_values = fd.get("nav_values", [])
        fv = FundFactorValues(
            fund_code=fd["fund_code"],
            fund_name=fd.get("fund_name"),
            fund_type=fd.get("fund_type"),
        )

        if len(nav_values) < config.min_history_days:
            # 数据不足，跳过
            continue

        # 计算各因子
        fv.alpha_persistence = compute_alpha_persistence(
            nav_values, config.risk_free_rate, config.alpha_lookback_months
        )
        fv.sharpe_persistence = compute_sharpe_persistence(
            nav_values, config.risk_free_rate, config.sharpe_lookback_days
        )
        fv.size_factor = compute_size_factor(fd.get("fund_size"))
        fv.fee_factor = compute_fee_factor(fd.get("management_fee"))
        fv.drawdown_recovery = compute_drawdown_recovery(nav_values)
        fv.consistency = compute_consistency_factor(nav_values)

        fund_factors.append(fv)

    result.n_funds_qualified = len(fund_factors)

    if len(fund_factors) < config.min_funds_for_ranking:
        result.warnings.append(
            f"数据充足的基金不足（{len(fund_factors)}/{config.min_funds_for_ranking}）"
        )
        return result

    # 2. 截面百分位排名
    alpha_vals = [f.alpha_persistence for f in fund_factors]
    sharpe_vals = [f.sharpe_persistence for f in fund_factors]
    size_vals = [f.size_factor for f in fund_factors]
    fee_vals = [f.fee_factor for f in fund_factors]
    dd_vals = [f.drawdown_recovery for f in fund_factors]
    consist_vals = [f.consistency for f in fund_factors]

    ranks_alpha = percentile_rank(alpha_vals)
    ranks_sharpe = percentile_rank(sharpe_vals)
    ranks_size = percentile_rank(size_vals)
    ranks_fee = percentile_rank(fee_vals)
    ranks_dd = percentile_rank(dd_vals)
    ranks_consist = percentile_rank(consist_vals)

    for i, fv in enumerate(fund_factors):
        fv.rank_alpha = ranks_alpha[i]
        fv.rank_sharpe = ranks_sharpe[i]
        fv.rank_size = ranks_size[i]
        fv.rank_fee = ranks_fee[i]
        fv.rank_drawdown = ranks_dd[i]
        fv.rank_consistency = ranks_consist[i]

    # 3. 加权综合排名
    weights = {
        "alpha": config.weight_alpha_persistence,
        "sharpe": config.weight_sharpe_persistence,
        "size": config.weight_size,
        "fee": config.weight_fee,
        "drawdown": config.weight_drawdown_recovery,
        "consistency": config.weight_consistency,
    }

    for fv in fund_factors:
        # 收集有效的排名值
        rank_values = {
            "alpha": fv.rank_alpha,
            "sharpe": fv.rank_sharpe,
            "size": fv.rank_size,
            "fee": fv.rank_fee,
            "drawdown": fv.rank_drawdown,
            "consistency": fv.rank_consistency,
        }

        # 加权平均（跳过 None 值，重新归一化权重）
        weighted_sum = 0.0
        total_weight = 0.0
        for key, rank_val in rank_values.items():
            if rank_val is not None:
                w = weights[key]
                weighted_sum += rank_val * w
                total_weight += w

        if total_weight > 0:
            fv.composite_rank = weighted_sum / total_weight
        else:
            fv.composite_rank = 0.5  # 无有效因子时给中间值

    # 4. 按综合排名排序
    fund_factors.sort(key=lambda f: f.composite_rank, reverse=True)

    # 5. 计算 z-score（用于与原有引擎对接）
    composite_ranks = [f.composite_rank for f in fund_factors]
    mean_rank = np.mean(composite_ranks)
    std_rank = np.std(composite_ranks, ddof=1)
    if std_rank > 0:
        for fv in fund_factors:
            fv.composite_zscore = float(
                (fv.composite_rank - mean_rank) / std_rank
            )
    else:
        for fv in fund_factors:
            fv.composite_zscore = 0.0

    # 6. 确定 Top/Bottom 组
    n_top = max(1, int(len(fund_factors) * config.top_percentile))
    n_bottom = max(1, int(len(fund_factors) * config.bottom_percentile))

    result.top_funds = [f.fund_code for f in fund_factors[:n_top]]
    result.bottom_funds = [f.fund_code for f in fund_factors[-n_bottom:]]
    result.fund_scores = fund_factors

    return result


# ---------------------------------------------------------------------------
# 截面 IC 验证（多期滚动版本）
# ---------------------------------------------------------------------------


def compute_cross_sectional_ic(
    fund_data: list[dict[str, Any]],
    forward_days: int = 20,
    config: CrossSectionalConfig | None = None,
    n_periods: int = 12,
    period_step_days: int = 21,
) -> dict[str, float | None]:
    """计算各因子的多期滚动截面 IC（专业版本）。

    方法（Rolling Cross-Sectional IC）：
    - 在过去 n_periods 个时间截面上，分别计算因子值和前瞻收益
    - 每个截面：IC = Spearman(因子排序, 未来收益排序)
    - 最终报告：IC_mean, IC_std, IC_IR, 正IC比例

    与单期 IC 的区别：
    - 单期 IC：只看一个时间点，受噪声影响极大，可能 ±0.7
    - 多期 IC：看 12 个月的平均，IC_mean=0.03~0.08 才是真正有效

    行业标准：
    - IC_mean > 0.03 → 因子有效
    - IC_IR > 0.5 → 因子稳定有效
    - 正 IC 比例 > 60% → 因子方向一致

    Args:
        fund_data: 基金数据（需要包含足够长的 nav_values）
        forward_days: 前瞻天数（默认20个交易日≈1个月）
        config: 配置
        n_periods: 滚动期数（默认12期≈12个月）
        period_step_days: 每期间隔天数（默认21≈1个月）

    Returns:
        {factor_name: IC_mean} 字典，附带诊断信息
    """
    if not config:
        config = CrossSectionalConfig()

    from scipy.stats import spearmanr

    # 需要的最小数据长度：
    # min_history_days（因子计算）+ n_periods * period_step_days + forward_days
    min_len = config.min_history_days + n_periods * period_step_days + forward_days

    # 过滤数据充足的基金
    valid_funds = [
        fd for fd in fund_data
        if len(fd.get("nav_values", [])) >= min_len
    ]

    if len(valid_funds) < config.min_funds_for_ranking:
        return {"error": None, "n_funds": len(valid_funds)}

    # 确定所有基金的最短长度（用于对齐时间截面）
    min_nav_len = min(len(fd["nav_values"]) for fd in valid_funds)

    # 收集每期的 IC
    factor_ic_series: dict[str, list[float]] = {
        "alpha": [], "sharpe": [], "size": [],
        "fee": [], "drawdown": [], "consistency": [],
        "composite": [],
    }

    for period_idx in range(n_periods):
        # 时间截面位置：从最新往回推
        # eval_end = 数据末尾 - period_idx * step - forward_days
        # 即：在 eval_end 时点计算因子，看 eval_end + forward_days 的收益
        offset = period_idx * period_step_days + forward_days
        eval_end = min_nav_len - offset

        if eval_end < config.min_history_days:
            break  # 数据不够了

        # 在该截面计算所有基金的因子值和前瞻收益
        factor_values: dict[str, list[float]] = {
            "alpha": [], "sharpe": [], "size": [],
            "fee": [], "drawdown": [], "consistency": [],
        }
        future_returns: list[float] = []

        for fd in valid_funds:
            nav_values = fd["nav_values"]

            # 用 eval_end 之前的数据计算因子（严格无前视）
            hist_navs = nav_values[:eval_end]

            # 前瞻收益：eval_end 到 eval_end + forward_days
            future_end = eval_end + forward_days
            if future_end > len(nav_values):
                continue
            future_ret = (nav_values[future_end - 1] / nav_values[eval_end - 1]) - 1

            # 计算因子
            alpha = compute_alpha_persistence(
                hist_navs, config.risk_free_rate, config.alpha_lookback_months
            )
            sharpe = compute_sharpe_persistence(
                hist_navs, config.risk_free_rate, config.sharpe_lookback_days
            )
            size = compute_size_factor(fd.get("fund_size"))
            fee = compute_fee_factor(fd.get("management_fee"))
            dd = compute_drawdown_recovery(hist_navs)
            consist = compute_consistency_factor(hist_navs)

            if alpha is not None and sharpe is not None:
                factor_values["alpha"].append(alpha)
                factor_values["sharpe"].append(sharpe)
                factor_values["size"].append(size if size is not None else 0.0)
                factor_values["fee"].append(fee if fee is not None else 0.0)
                factor_values["drawdown"].append(dd if dd is not None else 0.0)
                factor_values["consistency"].append(
                    consist if consist is not None else 0.0
                )
                future_returns.append(future_ret)

        # 该截面基金数不足，跳过
        if len(future_returns) < config.min_funds_for_ranking:
            continue

        returns_arr = np.array(future_returns)

        # 计算该截面各因子的 IC
        for factor_name, values in factor_values.items():
            vals_arr = np.array(values)
            if np.std(vals_arr) > 0 and np.std(returns_arr) > 0:
                ic_val, _ = spearmanr(vals_arr, returns_arr)
                if not np.isnan(ic_val):
                    factor_ic_series[factor_name].append(float(ic_val))

        # 综合因子 IC
        composite_scores = np.zeros(len(future_returns))
        n_factors_used = 0
        for values in factor_values.values():
            vals_arr = np.array(values)
            if np.std(vals_arr) > 0:
                z = (vals_arr - np.mean(vals_arr)) / np.std(vals_arr)
                composite_scores += z
                n_factors_used += 1

        if n_factors_used > 0 and np.std(composite_scores) > 0:
            ic_val, _ = spearmanr(composite_scores, returns_arr)
            if not np.isnan(ic_val):
                factor_ic_series["composite"].append(float(ic_val))

    # 汇总多期 IC 统计
    result: dict[str, float | None] = {}

    for factor_name, ic_list in factor_ic_series.items():
        if len(ic_list) >= 3:
            ic_arr = np.array(ic_list)
            ic_mean = float(np.mean(ic_arr))
            ic_std = float(np.std(ic_arr, ddof=1))
            ic_ir = ic_mean / ic_std if ic_std > 0 else 0.0
            positive_ratio = float(np.mean(ic_arr > 0))

            result[factor_name] = round(ic_mean, 4)
            result[f"{factor_name}_std"] = round(ic_std, 4)
            result[f"{factor_name}_ir"] = round(ic_ir, 4)
            result[f"{factor_name}_positive_pct"] = round(positive_ratio, 4)
            result[f"{factor_name}_n_periods"] = len(ic_list)
        else:
            result[factor_name] = None

    result["n_funds"] = len(valid_funds)  # type: ignore[assignment]
    result["n_periods_computed"] = max(  # type: ignore[assignment]
        (len(v) for v in factor_ic_series.values()), default=0
    )
    return result


# ---------------------------------------------------------------------------
# 与原有引擎对接：将截面排名转换为交易信号
# ---------------------------------------------------------------------------


def cross_sectional_to_signal(
    fund_code: str,
    cs_result: CrossSectionalResult,
    config: CrossSectionalConfig | None = None,
) -> float:
    """将截面排名转换为交易信号分数 (-1 到 1)。

    用于替代原有引擎中的 Bootstrap 预测分数。

    映射规则：
    - Top 20% → 正分 (0.3 ~ 1.0)
    - Middle 60% → 接近 0 (-0.2 ~ 0.2)
    - Bottom 20% → 负分 (-1.0 ~ -0.3)

    Args:
        fund_code: 基金代码
        cs_result: 截面评分结果
        config: 配置

    Returns:
        信号分数 (-1 到 1)
    """
    if not config:
        config = CrossSectionalConfig()

    # 找到该基金的排名
    fund_score = None
    for fs in cs_result.fund_scores:
        if fs.fund_code == fund_code:
            fund_score = fs
            break

    if fund_score is None:
        return 0.0  # 未参与排名，返回中性

    rank = fund_score.composite_rank  # 0~1, 1=最好

    # 非线性映射：强化头尾相对优劣信号
    # rank > 0.8 → 增配观察候选信号
    # rank < 0.2 → 减配观察候选信号
    # 0.2 ~ 0.8 → 弱信号
    if rank >= 0.8:
        # Top 20%: 映射到 [0.3, 1.0]
        signal = 0.3 + (rank - 0.8) / 0.2 * 0.7
    elif rank <= 0.2:
        # Bottom 20%: 映射到 [-1.0, -0.3]
        signal = -1.0 + rank / 0.2 * 0.7
    else:
        # Middle 60%: 映射到 [-0.2, 0.2]
        signal = (rank - 0.5) / 0.3 * 0.2

    return float(np.clip(signal, -1.0, 1.0))


# ---------------------------------------------------------------------------
# 数据库加载辅助
# ---------------------------------------------------------------------------


async def load_fund_data_for_scoring(
    session: Any,
    fund_type: str | None = None,
    min_history_days: int = 252,
    as_of_date: date | None = None,
) -> list[dict[str, Any]]:
    """从数据库加载截面评分所需的基金数据。

    Args:
        session: AsyncSession
        fund_type: 基金类型过滤（None=全部）
        min_history_days: 最少历史天数

    Returns:
        基金数据列表
    """
    from sqlalchemy import text

    cutoff_date = as_of_date or date.today()

    # 1. 获取 as_of_date 当时可申购且处于 active 的基金列表
    fund_query = text(
        "SELECT f.code, f.name, f.fund_type, "
        "       COALESCE(pit.management_fee, f.management_fee) AS management_fee "
        "FROM funds f "
        "LEFT JOIN ("
        "  SELECT fund_code, status, is_purchasable, management_fee, "
        "         ROW_NUMBER() OVER (PARTITION BY fund_code ORDER BY effective_date DESC) AS rn "
        "  FROM fund_meta_history "
        "  WHERE effective_date <= :cutoff_date"
        ") pit ON pit.fund_code = f.code AND pit.rn = 1 "
        "WHERE COALESCE(pit.status, f.status) = 'active' "
        "AND COALESCE(pit.is_purchasable, f.is_purchasable) = true "
        + ("AND f.fund_type = :fund_type " if fund_type else "")
        + "ORDER BY f.code"
    )
    params: dict[str, Any] = {"cutoff_date": cutoff_date}
    if fund_type:
        params["fund_type"] = fund_type

    result = await session.execute(fund_query, params)
    funds_meta = [(row[0], row[1], row[2], row[3]) for row in result]

    if not funds_meta:
        return []

    # 2. 获取 as_of_date 当时已知的最新基金规模数据
    size_query = text(
        "SELECT fund_code, effective_date, fund_size "
        "FROM fund_meta_history "
        "WHERE fund_size IS NOT NULL "
        "AND effective_date <= :cutoff_date "
        "ORDER BY fund_code, effective_date DESC"
    )
    size_result = await session.execute(size_query, {"cutoff_date": cutoff_date})
    size_map: dict[str, float] = {}
    for row in size_result:
        code = row[0]
        if code in size_map or row[2] is None:
            continue
        # fund_size 存储为元（转换为亿元）
        size_map[code] = float(row[2]) / 1e8

    # 3. 批量获取截至 as_of_date 的净值数据
    fund_codes = [f[0] for f in funds_meta]
    fund_data: list[dict[str, Any]] = []

    # 分批查询避免过大 SQL
    batch_size = 50
    for batch_start in range(0, len(fund_codes), batch_size):
        batch_codes = fund_codes[batch_start:batch_start + batch_size]
        placeholders = ", ".join([f":c{i}" for i in range(len(batch_codes))])

        nav_query = text(
            f"SELECT fund_code, trade_date, "
            f"COALESCE(adj_nav, unit_nav) as nav "
            f"FROM fund_nav "
            f"WHERE fund_code IN ({placeholders}) "
            f"AND trade_date <= :cutoff_date "
            f"AND (adj_nav IS NOT NULL OR unit_nav IS NOT NULL) "
            f"ORDER BY fund_code, trade_date"
        )
        nav_params = {f"c{i}": code for i, code in enumerate(batch_codes)}
        nav_params["cutoff_date"] = cutoff_date
        nav_result = await session.execute(nav_query, nav_params)

        # 按基金分组
        nav_by_fund: dict[str, list[float]] = {}
        for row in nav_result:
            code = row[0]
            if code not in nav_by_fund:
                nav_by_fund[code] = []
            nav_by_fund[code].append(float(row[2]))

        # 组装数据
        for code, name, ft, mgmt_fee in funds_meta:
            if code not in batch_codes:
                continue
            navs = nav_by_fund.get(code, [])
            if len(navs) >= min_history_days:
                fund_data.append({
                    "fund_code": code,
                    "fund_name": name,
                    "fund_type": ft,
                    "nav_values": navs,
                    "fund_size": size_map.get(code),
                    "management_fee": float(mgmt_fee) if mgmt_fee else None,
                })

    return fund_data


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------

__all__ = [
    "CrossSectionalConfig",
    "CrossSectionalResult",
    "FundFactorValues",
    "compute_alpha_persistence",
    "compute_sharpe_persistence",
    "compute_size_factor",
    "compute_fee_factor",
    "compute_drawdown_recovery",
    "compute_consistency_factor",
    "compute_cross_sectional_ic",
    "cross_sectional_to_signal",
    "load_fund_data_for_scoring",
    "percentile_rank",
    "run_cross_sectional_scoring",
]
