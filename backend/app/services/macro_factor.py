"""宏观/情绪因子评分模块（v5 新增）。

基于已有的市场级数据（benchmark_nav + index_valuation），
计算宏观环境评分，作为交易建议引擎的新信号维度。

信号维度：
1. 市场动量因子：宽基指数（沪深300/中证500）的中短期动量
2. 估值因子：指数 PE/PB 百分位 → 低估买入/高估卖出
3. 波动率因子：市场波动率状态 → 高波动时降低风险偏好
4. 趋势一致性因子：多指数趋势方向是否一致

数据来源（已有，无需新增采集）：
- benchmark_nav 表：沪深300/中证500/上证50 日收益率
- index_valuation 表：PE/PB 百分位

设计原则：
- 不预测市场方向，只描述当前宏观环境状态
- 输出 -1 到 1 的评分，正值利于买入，负值利于卖出
- 数据不足时返回 0（中性），不影响其他信号
- 可独立于基金类型使用（宏观环境对所有基金都有影响）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class MacroFactorScore:
    """宏观因子综合评分结果。"""

    # 各子因子评分 (-1 到 1)
    market_momentum_score: float = 0.0  # 市场动量
    valuation_score: float = 0.0  # 估值因子
    volatility_score: float = 0.0  # 波动率因子
    trend_consensus_score: float = 0.0  # 趋势一致性

    # 综合评分
    composite_score: float = 0.0  # 加权综合 (-1 到 1)

    # 诊断信息
    data_available: bool = False  # 是否有足够数据
    market_state: str = "unknown"  # bullish/bearish/neutral/volatile
    valuation_state: str = "unknown"  # cheap/fair/expensive
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "composite_score": round(self.composite_score, 4),
            "sub_scores": {
                "market_momentum": round(self.market_momentum_score, 4),
                "valuation": round(self.valuation_score, 4),
                "volatility": round(self.volatility_score, 4),
                "trend_consensus": round(self.trend_consensus_score, 4),
            },
            "market_state": self.market_state,
            "valuation_state": self.valuation_state,
            "data_available": self.data_available,
        }


# ---------------------------------------------------------------------------
# 核心计算
# ---------------------------------------------------------------------------


def compute_macro_score(
    benchmark_returns: dict[str, list[float]],
    valuation_data: dict[str, dict[str, float | None]] | None = None,
) -> MacroFactorScore:
    """计算宏观因子综合评分。

    Args:
        benchmark_returns: 基准指数日收益率
            {index_code: [daily_return_1, daily_return_2, ...]}
            至少需要 60 天数据
        valuation_data: 最新估值数据
            {index_code: {pe_percentile, pb_percentile}}

    Returns:
        MacroFactorScore
    """
    result = MacroFactorScore()

    # 检查数据可用性
    if not benchmark_returns:
        return result

    # 取主要指数（沪深300 优先，回退到任何可用指数）
    primary_code = None
    primary_returns = None
    for code in ["000300", "000905", "000016"]:
        if code in benchmark_returns and len(benchmark_returns[code]) >= 60:
            primary_code = code
            primary_returns = benchmark_returns[code]
            break

    if primary_returns is None:
        # 取任何有足够数据的指数
        for code, rets in benchmark_returns.items():
            if len(rets) >= 60:
                primary_code = code
                primary_returns = rets
                break

    if primary_returns is None:
        return result

    result.data_available = True
    arr = np.array(primary_returns, dtype=np.float64)

    # 1. 市场动量因子
    result.market_momentum_score = _compute_market_momentum(arr)

    # 2. 波动率因子
    result.volatility_score = _compute_volatility_factor(arr)

    # 3. 估值因子
    if valuation_data:
        result.valuation_score = _compute_valuation_factor(valuation_data)

    # 4. 趋势一致性因子
    result.trend_consensus_score = _compute_trend_consensus(benchmark_returns)

    # 综合评分（加权）
    weights = {
        "momentum": 0.35,
        "valuation": 0.25,
        "volatility": 0.20,
        "consensus": 0.20,
    }

    composite = (
        weights["momentum"] * result.market_momentum_score
        + weights["valuation"] * result.valuation_score
        + weights["volatility"] * result.volatility_score
        + weights["consensus"] * result.trend_consensus_score
    )
    result.composite_score = float(np.clip(composite, -1.0, 1.0))

    # 判断市场状态
    if result.composite_score > 0.3:
        result.market_state = "bullish"
    elif result.composite_score < -0.3:
        result.market_state = "bearish"
    elif abs(result.volatility_score) > 0.5:
        result.market_state = "volatile"
    else:
        result.market_state = "neutral"

    # 判断估值状态
    if result.valuation_score > 0.3:
        result.valuation_state = "cheap"
    elif result.valuation_score < -0.3:
        result.valuation_state = "expensive"
    else:
        result.valuation_state = "fair"

    return result


def _compute_market_momentum(returns: np.ndarray) -> float:
    """计算市场动量因子。

    方法：
    - 20 日累计收益率（短期动量）
    - 60 日累计收益率（中期趋势确认）
    - 两者加权融合，tanh 压缩到 [-1, 1]
    """
    n = len(returns)
    if n < 60:
        return 0.0

    # 20 日累计收益
    ret_20d = float(np.prod(1 + returns[-20:]) - 1)
    # 60 日累计收益
    ret_60d = float(np.prod(1 + returns[-60:]) - 1)

    # 短期动量信号（tanh 压缩）
    short_signal = float(np.tanh(ret_20d * 8))
    # 中期趋势信号
    mid_signal = float(np.tanh(ret_60d * 4))

    # 加权：短期 40% + 中期 60%（中期趋势更可靠）
    score = 0.4 * short_signal + 0.6 * mid_signal
    return float(np.clip(score, -1.0, 1.0))


def _compute_volatility_factor(returns: np.ndarray) -> float:
    """计算波动率因子。

    方法：
    - 当前 20 日年化波动率 vs 历史波动率分布
    - 高波动 → 负分（风险偏好降低）
    - 低波动 → 正分（适合加仓）
    """
    n = len(returns)
    if n < 60:
        return 0.0

    # 当前 20 日年化波动率
    current_vol = float(np.std(returns[-20:], ddof=1) * np.sqrt(252))

    # 历史波动率分布
    rolling_vols = []
    for i in range(20, n):
        vol = float(np.std(returns[i - 20:i], ddof=1) * np.sqrt(252))
        rolling_vols.append(vol)

    if not rolling_vols:
        return 0.0

    # 百分位
    pct = sum(1 for v in rolling_vols if v < current_vol) / len(rolling_vols)

    # 映射：低波动(pct<0.3) → +0.5, 高波动(pct>0.8) → -0.7
    if pct > 0.9:
        return -0.8  # 极端高波动
    elif pct > 0.8:
        return -0.5
    elif pct > 0.6:
        return -0.2
    elif pct < 0.2:
        return 0.5  # 低波动环境
    elif pct < 0.3:
        return 0.3
    else:
        return 0.0  # 正常波动


def _compute_valuation_factor(
    valuation_data: dict[str, dict[str, float | None]],
) -> float:
    """计算估值因子。

    方法：
    - 取主要指数的 PE/PB 百分位
    - 低百分位（<30%）→ 正分（低估，利于买入）
    - 高百分位（>70%）→ 负分（高估，利于卖出）
    """
    pe_scores = []
    pb_scores = []

    for code, data in valuation_data.items():
        pe_pct = data.get("pe_percentile")
        pb_pct = data.get("pb_percentile")

        if pe_pct is not None:
            # PE 百分位 → 信号：低估=正，高估=负
            pe_signal = _percentile_to_signal(pe_pct)
            pe_scores.append(pe_signal)

        if pb_pct is not None:
            pb_signal = _percentile_to_signal(pb_pct)
            pb_scores.append(pb_signal)

    if not pe_scores and not pb_scores:
        return 0.0

    # PE 权重 60%，PB 权重 40%
    pe_avg = np.mean(pe_scores) if pe_scores else 0.0
    pb_avg = np.mean(pb_scores) if pb_scores else 0.0

    if pe_scores and pb_scores:
        return float(0.6 * pe_avg + 0.4 * pb_avg)
    elif pe_scores:
        return float(pe_avg)
    else:
        return float(pb_avg)


def _percentile_to_signal(percentile: float) -> float:
    """将百分位转换为信号 (-1 到 1)。

    低百分位 = 低估 = 正信号（买入）
    高百分位 = 高估 = 负信号（卖出）
    """
    if percentile <= 0.10:
        return 0.9  # 极度低估
    elif percentile <= 0.20:
        return 0.6
    elif percentile <= 0.30:
        return 0.3
    elif percentile >= 0.90:
        return -0.9  # 极度高估
    elif percentile >= 0.80:
        return -0.6
    elif percentile >= 0.70:
        return -0.3
    else:
        return 0.0  # 合理区间


def _compute_trend_consensus(
    benchmark_returns: dict[str, list[float]],
) -> float:
    """计算多指数趋势一致性因子。

    方法：
    - 对所有可用指数计算 20 日动量方向
    - 多数指数同向 → 强信号
    - 方向分歧 → 弱信号
    """
    directions = []
    for code, rets in benchmark_returns.items():
        if len(rets) < 20:
            continue
        ret_20d = float(np.prod(1 + np.array(rets[-20:])) - 1)
        if ret_20d > 0.01:
            directions.append(1)
        elif ret_20d < -0.01:
            directions.append(-1)
        else:
            directions.append(0)

    if len(directions) < 2:
        return 0.0

    # 一致性 = 多数方向的比例
    positive = sum(1 for d in directions if d > 0)
    negative = sum(1 for d in directions if d < 0)
    total = len(directions)

    if positive > negative and positive >= total * 0.6:
        return float(positive / total * 0.8)
    elif negative > positive and negative >= total * 0.6:
        return float(-negative / total * 0.8)
    else:
        return 0.0  # 分歧


# ---------------------------------------------------------------------------
# 数据库加载辅助
# ---------------------------------------------------------------------------


async def load_macro_data(
    session: Any,
    as_of_date: date | None = None,
) -> tuple[
    dict[str, list[float]], dict[str, dict[str, float | None]]
]:
    """从数据库加载宏观因子所需数据。

    Returns:
        (benchmark_returns, valuation_data) 元组
        - benchmark_returns: {index_code: [daily_return, ...]} 最近 120 天
        - valuation_data: {index_code: {pe_percentile, pb_percentile}} 最新
    """
    from sqlalchemy import text

    benchmark_returns: dict[str, list[float]] = {}
    valuation_data: dict[str, dict[str, float | None]] = {}

    today = as_of_date or date.today()
    start_date = today - timedelta(days=180)

    # 1. 加载基准指数日收益率
    try:
        query = text(
            "SELECT index_code, trade_date, daily_return "
            "FROM benchmark_nav "
            "WHERE trade_date >= :start_date "
            "AND trade_date <= :end_date "
            "AND daily_return IS NOT NULL "
            "ORDER BY index_code, trade_date"
        )
        result = await session.execute(
            query,
            {"start_date": start_date, "end_date": today},
        )
        for row in result:
            code = row[0]
            ret = float(row[2])
            if code not in benchmark_returns:
                benchmark_returns[code] = []
            benchmark_returns[code].append(ret)
    except Exception as e:
        logger.warning("macro_factor.load_benchmark_error: %s", str(e))

    # 2. 加载最新估值数据（跨数据库兼容，避免依赖 DISTINCT ON）
    try:
        query = text(
            "SELECT index_code, pe_percentile, pb_percentile "
            "FROM ("
            "  SELECT index_code, pe_percentile, pb_percentile, trade_date, "
            "         ROW_NUMBER() OVER (PARTITION BY index_code ORDER BY trade_date DESC) AS rn "
            "  FROM index_valuation "
            "  WHERE trade_date >= :min_date "
            "  AND trade_date <= :max_date "
            "  AND (pe_percentile IS NOT NULL OR pb_percentile IS NOT NULL)"
            ") ranked "
            "WHERE rn = 1"
        )
        result = await session.execute(
            query,
            {
                "min_date": today - timedelta(days=7),
                "max_date": today,
            },
        )
        for row in result:
            code = row[0]
            valuation_data[code] = {
                "pe_percentile": float(row[1]) if row[1] is not None else None,
                "pb_percentile": float(row[2]) if row[2] is not None else None,
            }
    except Exception as e:
        logger.warning("macro_factor.load_valuation_error: %s", str(e))

    return benchmark_returns, valuation_data


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------

__all__ = [
    "MacroFactorScore",
    "compute_macro_score",
    "load_macro_data",
]
