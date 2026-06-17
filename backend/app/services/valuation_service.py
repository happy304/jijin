"""估值分析服务。

提供指数和基金的历史估值分位数分析：
1. PE/PB 历史分位数 — 当前估值在历史中的位置
2. 估值区间判断 — 低估/正常/高估
3. 基于估值的择时建议

v2 改进：
- 优先使用真实 PE/PB 数据（来自 index_valuation 表）
- 仅在无真实估值数据时，回退到净值百分位代理
- 明确标注数据来源和适用范围

数据优先级：
1. index_valuation 表中的真实 PE/PB 百分位（最可靠）
2. 净值历史百分位（仅对指数基金有参考价值）

适用范围：
- ✅ 宽基指数基金（如沪深300、中证500 ETF）— 使用真实 PE/PB
- ✅ 行业指数基金 — 使用真实 PE/PB
- ⚠️ 主动管理型基金 — 仅净值百分位，参考价值有限
- ❌ 货币基金、债券基金 — 不适用
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any


@dataclass
class ValuationResult:
    """估值分析结果。

    Attributes:
        fund_code: 基金/指数代码
        current_nav: 当前净值
        percentile: 当前净值在历史中的百分位（0~1）
        zone: 估值区间 (low/normal/high)
        suggestion: 操作建议
        history_days: 历史数据天数
        history_low: 历史最低净值
        history_high: 历史最高净值
        history_median: 历史中位数
    """

    fund_code: str
    current_nav: float
    percentile: float
    zone: str  # low / normal / high
    suggestion: str
    history_days: int
    history_low: float
    history_high: float
    history_median: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "fund_code": self.fund_code,
            "current_nav": round(self.current_nav, 4),
            "percentile": round(self.percentile, 4),
            "zone": self.zone,
            "suggestion": self.suggestion,
            "history_days": self.history_days,
            "history_low": round(self.history_low, 4),
            "history_high": round(self.history_high, 4),
            "history_median": round(self.history_median, 4),
        }


# 估值区间阈值
LOW_THRESHOLD = 0.3    # 百分位 <= 30% 为低估
HIGH_THRESHOLD = 0.7   # 百分位 >= 70% 为高估


def compute_valuation(
    nav_series: dict[date, Decimal],
    fund_code: str = "",
    low_threshold: float = LOW_THRESHOLD,
    high_threshold: float = HIGH_THRESHOLD,
) -> ValuationResult | None:
    """计算基金/指数的估值分位数。

    使用净值的历史百分位作为估值代理指标。

    重要局限性：
    - 仅对被动指数基金有较好参考价值
    - 对主动基金，净值上升是正常收益积累，高百分位不代表高估
    - 建议仅用于宽基/行业指数基金的辅助判断

    Args:
        nav_series: 历史净值数据 {date: nav}
        fund_code: 基金代码
        low_threshold: 低估阈值
        high_threshold: 高估阈值

    Returns:
        ValuationResult，数据不足返回 None
    """
    if len(nav_series) < 60:
        return None

    sorted_dates = sorted(nav_series.keys())
    values = [float(nav_series[d]) for d in sorted_dates]
    current_nav = values[-1]

    # 计算百分位：当前值在历史中的排名
    count_below = sum(1 for v in values if v < current_nav)
    percentile = count_below / (len(values) - 1) if len(values) > 1 else 0.5

    # 统计数据
    sorted_values = sorted(values)
    history_low = sorted_values[0]
    history_high = sorted_values[-1]
    mid_idx = len(sorted_values) // 2
    history_median = sorted_values[mid_idx]

    # 判断区间
    if percentile <= low_threshold:
        zone = "low"
        suggestion = (
            "当前净值处于历史低位区间，若为指数基金可考虑加仓或定投"
            "（注意：净值低位不等于真实低估，仅供参考）"
        )
    elif percentile >= high_threshold:
        zone = "high"
        suggestion = (
            "当前净值处于历史高位区间，若为指数基金可考虑减仓或暂停定投"
            "（注意：净值高位可能是正常收益积累，不一定代表高估）"
        )
    else:
        zone = "normal"
        suggestion = "当前净值处于历史正常区间，可维持现有仓位"

    return ValuationResult(
        fund_code=fund_code,
        current_nav=current_nav,
        percentile=percentile,
        zone=zone,
        suggestion=suggestion,
        history_days=len(values),
        history_low=history_low,
        history_high=history_high,
        history_median=history_median,
    )


def compute_batch_valuation(
    funds_nav: dict[str, dict[date, Decimal]],
    low_threshold: float = LOW_THRESHOLD,
    high_threshold: float = HIGH_THRESHOLD,
) -> list[ValuationResult]:
    """批量计算多只基金的估值。

    Args:
        funds_nav: {fund_code: {date: nav}}
        low_threshold: 低估阈值
        high_threshold: 高估阈值

    Returns:
        估值结果列表（按百分位升序，低估的排前面）
    """
    results: list[ValuationResult] = []

    for fund_code, nav_series in funds_nav.items():
        result = compute_valuation(nav_series, fund_code, low_threshold, high_threshold)
        if result is not None:
            results.append(result)

    results.sort(key=lambda r: r.percentile)
    return results


# ---------------------------------------------------------------------------
# v2: 真实 PE/PB 估值分析（优先使用 index_valuation 表数据）
# ---------------------------------------------------------------------------


@dataclass
class RealValuationResult:
    """基于真实 PE/PB 的估值分析结果。

    与 ValuationResult 的区别：
    - 使用指数的真实 PE/PB 百分位，而非净值百分位
    - 金融逻辑正确：PE 低 = 低估，PE 高 = 高估
    - 仅适用于有跟踪指数的被动基金
    """

    fund_code: str
    index_code: str
    index_name: str
    trade_date: date | None = None

    # PE 估值
    pe_ttm: float | None = None
    pe_percentile: float | None = None  # 0~1，在历史中的位置
    pe_zone: str = "normal"  # low/normal/high

    # PB 估值
    pb: float | None = None
    pb_percentile: float | None = None
    pb_zone: str = "normal"

    # 综合
    composite_percentile: float | None = None  # PE 和 PB 的加权平均
    zone: str = "normal"
    suggestion: str = ""
    data_source: str = "index_valuation"  # 标注数据来源
    history_days: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "fund_code": self.fund_code,
            "index_code": self.index_code,
            "index_name": self.index_name,
            "trade_date": str(self.trade_date) if self.trade_date else None,
            "pe": {
                "ttm": self.pe_ttm,
                "percentile": self.pe_percentile,
                "zone": self.pe_zone,
            },
            "pb": {
                "value": self.pb,
                "percentile": self.pb_percentile,
                "zone": self.pb_zone,
            },
            "composite_percentile": self.composite_percentile,
            "zone": self.zone,
            "suggestion": self.suggestion,
            "data_source": self.data_source,
            "history_days": self.history_days,
        }


async def compute_real_valuation(
    fund_code: str,
    session: Any,
    low_threshold: float = LOW_THRESHOLD,
    high_threshold: float = HIGH_THRESHOLD,
) -> RealValuationResult | None:
    """使用真实 PE/PB 数据计算基金估值。

    优先从 index_valuation 表获取跟踪指数的估值数据。
    如果找不到对应指数数据，返回 None（调用方可回退到净值百分位）。

    Args:
        fund_code: 基金代码
        session: AsyncSession
        low_threshold: 低估阈值
        high_threshold: 高估阈值

    Returns:
        RealValuationResult 或 None
    """
    from sqlalchemy import select, text

    from app.data.providers.index_valuation_provider import (
        FUND_TO_INDEX,
        INDEX_CODE_MAP,
    )

    # 1. 查找基金跟踪的指数
    index_code = FUND_TO_INDEX.get(fund_code)

    # 如果硬编码映射中没有，尝试从 funds.benchmark 字段解析
    if not index_code:
        query = text("SELECT benchmark FROM funds WHERE code = :code")
        result = await session.execute(query, {"code": fund_code})
        row = result.first()
        if row and row[0]:
            benchmark = row[0]
            # 尝试从 benchmark 字符串中提取指数代码
            for code in INDEX_CODE_MAP:
                if code in benchmark:
                    index_code = code
                    break

    if not index_code:
        return None  # 无法确定跟踪指数

    index_name = INDEX_CODE_MAP.get(index_code, index_code)

    # 2. 从 index_valuation 表加载历史数据
    query = text(
        "SELECT trade_date, pe_ttm, pe_percentile, pb, pb_percentile "
        "FROM index_valuation "
        "WHERE index_code = :index_code "
        "ORDER BY trade_date DESC "
        "LIMIT 2520"  # 约10年数据
    )

    try:
        result = await session.execute(query, {"index_code": index_code})
        rows = result.fetchall()
    except Exception:
        return None  # 表可能不存在

    if not rows:
        return None

    # 最新一条数据
    latest = rows[0]
    trade_date = latest[0]
    pe_ttm = float(latest[1]) if latest[1] else None
    pe_percentile_db = float(latest[2]) if latest[2] else None
    pb_val = float(latest[3]) if latest[3] else None
    pb_percentile_db = float(latest[4]) if latest[4] else None

    # 如果数据库中已有百分位，直接使用；否则自行计算
    if pe_percentile_db is None and pe_ttm is not None:
        pe_values = [float(r[1]) for r in rows if r[1] is not None]
        if pe_values:
            count_below = sum(1 for v in pe_values if v < pe_ttm)
            pe_percentile_db = count_below / max(len(pe_values) - 1, 1)

    if pb_percentile_db is None and pb_val is not None:
        pb_values = [float(r[3]) for r in rows if r[3] is not None]
        if pb_values:
            count_below = sum(1 for v in pb_values if v < pb_val)
            pb_percentile_db = count_below / max(len(pb_values) - 1, 1)

    # 3. 构建结果
    rv = RealValuationResult(
        fund_code=fund_code,
        index_code=index_code,
        index_name=index_name,
        trade_date=trade_date,
        pe_ttm=pe_ttm,
        pe_percentile=round(pe_percentile_db, 4) if pe_percentile_db is not None else None,
        pb=pb_val,
        pb_percentile=round(pb_percentile_db, 4) if pb_percentile_db is not None else None,
        history_days=len(rows),
    )

    # PE 区间判断
    if rv.pe_percentile is not None:
        if rv.pe_percentile <= low_threshold:
            rv.pe_zone = "low"
        elif rv.pe_percentile >= high_threshold:
            rv.pe_zone = "high"

    # PB 区间判断
    if rv.pb_percentile is not None:
        if rv.pb_percentile <= low_threshold:
            rv.pb_zone = "low"
        elif rv.pb_percentile >= high_threshold:
            rv.pb_zone = "high"

    # 综合百分位（PE 权重 0.6，PB 权重 0.4）
    if rv.pe_percentile is not None and rv.pb_percentile is not None:
        rv.composite_percentile = round(
            0.6 * rv.pe_percentile + 0.4 * rv.pb_percentile, 4
        )
    elif rv.pe_percentile is not None:
        rv.composite_percentile = rv.pe_percentile
    elif rv.pb_percentile is not None:
        rv.composite_percentile = rv.pb_percentile

    # 综合区间和建议
    if rv.composite_percentile is not None:
        if rv.composite_percentile <= low_threshold:
            rv.zone = "low"
            rv.suggestion = (
                f"{index_name}当前 PE {pe_ttm:.1f}（历史 {rv.pe_percentile*100:.0f}% 分位），"
                f"处于低估区间，可考虑加仓或定投"
                if pe_ttm else "当前处于历史低估区间，可考虑加仓"
            )
        elif rv.composite_percentile >= high_threshold:
            rv.zone = "high"
            rv.suggestion = (
                f"{index_name}当前 PE {pe_ttm:.1f}（历史 {rv.pe_percentile*100:.0f}% 分位），"
                f"处于高估区间，可考虑减仓或止盈"
                if pe_ttm else "当前处于历史高估区间，可考虑减仓"
            )
        else:
            rv.zone = "normal"
            rv.suggestion = f"{index_name}估值处于正常区间，可维持现有仓位"

    return rv
