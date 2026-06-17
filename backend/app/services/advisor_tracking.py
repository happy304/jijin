"""建议执行跟踪 + 样本外验证框架。

两个核心功能：

1. 建议执行跟踪（Performance Tracking）
   - 每日检查已保存的建议记录
   - 计算建议后 5/10/20/60 日的实际收益
   - 回填到 advisor_results.tracked_returns 字段
   - 统计命中率和平均收益

2. 样本外验证框架（Out-of-Sample Validation）
   - 每周自动运行 rolling window IC 验证
   - 检测 IC 是否衰减到阈值以下
   - 通过通知模块告警
   - 提供引擎健康度 API

Requirements: 交易建议引擎 v3 增强
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class TrackingResult:
    """单条建议的跟踪结果。"""

    fund_code: str
    advice_date: str
    action: str  # buy/sell/hold
    composite_score: float
    # 实际收益
    return_5d: float | None = None
    return_10d: float | None = None
    return_20d: float | None = None
    return_60d: float | None = None
    # 命中判断
    hit_5d: bool | None = None
    hit_10d: bool | None = None
    hit_20d: bool | None = None


@dataclass
class EngineHealthMetrics:
    """引擎健康度指标。"""

    # 滚动 IC（最近 N 次建议的评分与实际收益相关性）
    rolling_ic_20d: float | None = None
    rolling_ic_samples: int = 0

    # 命中率趋势
    recent_buy_hit_rate: float | None = None  # 最近30条买入建议的命中率
    recent_sell_hit_rate: float | None = None  # 最近30条卖出建议的命中率
    recent_buy_count: int = 0
    recent_sell_count: int = 0

    # 健康状态
    status: str = "unknown"  # healthy/degraded/unhealthy/insufficient_data
    status_reason: str = ""

    # IC 衰减检测
    ic_trend: str = "stable"  # improving/stable/declining/critical
    ic_3month_avg: float | None = None
    ic_1month_avg: float | None = None

    # 最后验证时间
    last_validated: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "status_reason": self.status_reason,
            "rolling_ic": {
                "ic_20d": self.rolling_ic_20d,
                "samples": self.rolling_ic_samples,
                "trend": self.ic_trend,
                "ic_3month_avg": self.ic_3month_avg,
                "ic_1month_avg": self.ic_1month_avg,
            },
            "hit_rates": {
                "buy": self.recent_buy_hit_rate,
                "sell": self.recent_sell_hit_rate,
                "buy_count": self.recent_buy_count,
                "sell_count": self.recent_sell_count,
            },
            "last_validated": self.last_validated,
            "thresholds": {
                "ic_healthy": 0.05,
                "ic_degraded": 0.02,
                "hit_rate_healthy": 0.55,
                "min_samples": 30,
            },
        }


# ---------------------------------------------------------------------------
# 1. 建议执行跟踪
# ---------------------------------------------------------------------------


def track_advice_performance_sync(
    lookback_days: int = 90,
) -> dict[str, Any]:
    """同步执行建议跟踪：计算历史建议的实际收益并回填。

    流程：
    1. 查询最近 N 天内的建议记录（尚未完成跟踪的）
    2. 对每条建议，查询建议日期后 5/10/20/60 天的实际净值
    3. 计算实际收益和命中情况
    4. 回填到 advisor_results.tracked_returns

    Args:
        lookback_days: 回看多少天的建议记录

    Returns:
        执行摘要
    """
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    from app.core.config import get_settings
    from app.data.models.advisor_results import AdvisorResult

    settings = get_settings()
    engine = create_engine(settings.database_sync_url)

    today = date.today()
    min_date = today - timedelta(days=lookback_days)
    tracked_count = 0
    skipped_count = 0

    try:
        with Session(engine) as session:
            # 查询需要跟踪的建议记录
            # 条件：建议日期在 lookback 范围内，且 tracked_returns 为空或不完整
            results = (
                session.query(AdvisorResult)
                .filter(AdvisorResult.advice_date >= min_date)
                .filter(AdvisorResult.advice_date <= today - timedelta(days=5))  # 至少5天后才有数据
                .order_by(AdvisorResult.advice_date.desc())
                .limit(50)  # 每次最多处理50条
                .all()
            )

            for result in results:
                if not result.advices:
                    continue

                advice_d = result.advice_date
                days_since = (today - advice_d).days

                # 检查是否已完成跟踪（60天后的数据都有了）
                if result.tracked_returns and days_since <= 60:
                    # 检查是否需要更新（可能之前只有5天数据，现在有20天了）
                    existing = result.tracked_returns
                    all_complete = all(
                        existing.get(code, {}).get("return_60d") is not None
                        for code in result.fund_codes
                        if any(
                            a.get("fund_code") == code and a.get("action") != "hold"
                            for a in result.advices
                        )
                    )
                    if all_complete:
                        skipped_count += 1
                        continue

                # 计算跟踪收益
                tracked = _compute_tracked_returns(
                    session, result.advices, advice_d, days_since
                )

                if tracked:
                    result.tracked_returns = tracked
                    from datetime import datetime, timezone
                    result.tracked_at = datetime.now(timezone.utc)
                    tracked_count += 1

            session.commit()

    except Exception as e:
        logger.error("advisor_tracking.error: %s", str(e))
    finally:
        engine.dispose()

    return {
        "tracked": tracked_count,
        "skipped": skipped_count,
        "date": today.isoformat(),
    }


def _compute_tracked_returns(
    session: Any,
    advices: list[dict[str, Any]],
    advice_date: date,
    days_since: int,
) -> dict[str, dict[str, Any]]:
    """计算建议后的实际收益。

    Args:
        session: SQLAlchemy Session
        advices: 建议列表
        advice_date: 建议日期
        days_since: 距今天数

    Returns:
        {fund_code: {return_5d, return_10d, return_20d, return_60d, hit_5d, ...}}
    """
    from sqlalchemy import text

    # 收集需要跟踪的基金（非 hold 的）
    funds_to_track = []
    for adv in advices:
        if adv.get("action") != "hold":
            funds_to_track.append({
                "code": adv["fund_code"],
                "action": adv["action"],
                "score": adv.get("scores", {}).get("composite", 0),
            })

    if not funds_to_track:
        return {}

    fund_codes = [f["code"] for f in funds_to_track]
    placeholders = ", ".join([f":code_{i}" for i in range(len(fund_codes))])

    # 查询建议日期后的净值
    # 需要的日期点：advice_date, +5d, +10d, +20d, +60d
    end_date = min(advice_date + timedelta(days=90), date.today())

    query = text(
        f"SELECT fund_code, trade_date, COALESCE(adj_nav, unit_nav) as nav "
        f"FROM fund_nav "
        f"WHERE fund_code IN ({placeholders}) "
        f"AND trade_date BETWEEN :start_date AND :end_date "
        f"AND (adj_nav IS NOT NULL OR unit_nav IS NOT NULL) "
        f"ORDER BY fund_code, trade_date"
    )
    params = {f"code_{i}": code for i, code in enumerate(fund_codes)}
    params["start_date"] = advice_date
    params["end_date"] = end_date

    # 构建 {fund_code: [(date, nav), ...]}
    nav_data: dict[str, list[tuple[date, float]]] = {}
    try:
        result = session.execute(query, params)
        for row in result:
            code = row[0]
            if code not in nav_data:
                nav_data[code] = []
            nav_data[code].append((row[1], float(row[2])))
    except Exception:
        return {}

    # 计算各基金的跟踪收益
    tracked: dict[str, dict[str, Any]] = {}

    for fund_info in funds_to_track:
        code = fund_info["code"]
        action = fund_info["action"]
        score = fund_info["score"]

        if code not in nav_data or len(nav_data[code]) < 2:
            continue

        navs = nav_data[code]
        base_nav = navs[0][1]  # 建议日净值

        fund_result: dict[str, Any] = {
            "action": action,
            "composite_score": score,
            "base_nav": base_nav,
            "base_date": str(navs[0][0]),
        }

        # 计算各期收益
        for target_days, key in [(5, "5d"), (10, "10d"), (20, "20d"), (60, "60d")]:
            if len(navs) > target_days:
                target_nav = navs[min(target_days, len(navs) - 1)][1]
                ret = (target_nav / base_nav) - 1
                fund_result[f"return_{key}"] = round(ret, 6)

                # 命中判断
                if action == "buy":
                    fund_result[f"hit_{key}"] = ret > 0
                elif action == "sell":
                    fund_result[f"hit_{key}"] = ret < 0
            else:
                fund_result[f"return_{key}"] = None
                fund_result[f"hit_{key}"] = None

        tracked[code] = fund_result

    return tracked


# ---------------------------------------------------------------------------
# 2. 样本外验证框架
# ---------------------------------------------------------------------------


def compute_engine_health_sync() -> EngineHealthMetrics:
    """计算引擎健康度指标（同步版本）。

    方法：
    1. 从 advisor_results 中提取已跟踪的建议
    2. 计算滚动 IC（评分与实际20日收益的相关性）
    3. 计算近期命中率
    4. 判断健康状态和 IC 趋势

    Returns:
        EngineHealthMetrics
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.core.config import get_settings
    from app.data.models.advisor_results import AdvisorResult

    settings = get_settings()
    engine = create_engine(settings.database_sync_url)
    metrics = EngineHealthMetrics()

    try:
        with Session(engine) as session:
            # 查询有跟踪数据的建议记录（最近6个月）
            min_date = date.today() - timedelta(days=180)
            results = (
                session.query(AdvisorResult)
                .filter(AdvisorResult.advice_date >= min_date)
                .filter(AdvisorResult.tracked_returns.isnot(None))
                .order_by(AdvisorResult.advice_date.desc())
                .limit(200)
                .all()
            )

            if not results:
                metrics.status = "insufficient_data"
                metrics.status_reason = "无已跟踪的建议记录，请等待跟踪任务运行"
                return metrics

            # 提取所有已跟踪的建议数据
            all_scores: list[float] = []
            all_returns_20d: list[float] = []
            buy_hits: list[bool] = []
            sell_hits: list[bool] = []

            # 按时间分组（用于趋势检测）
            recent_1m_scores: list[float] = []
            recent_1m_returns: list[float] = []
            recent_3m_scores: list[float] = []
            recent_3m_returns: list[float] = []

            today = date.today()

            for result in results:
                if not result.tracked_returns:
                    continue

                advice_age = (today - result.advice_date).days

                for code, tracking in result.tracked_returns.items():
                    score = tracking.get("composite_score", 0)
                    ret_20d = tracking.get("return_20d")
                    action = tracking.get("action")
                    hit_20d = tracking.get("hit_20d")

                    if ret_20d is not None and score != 0:
                        all_scores.append(score)
                        all_returns_20d.append(ret_20d)

                        if advice_age <= 30:
                            recent_1m_scores.append(score)
                            recent_1m_returns.append(ret_20d)
                        if advice_age <= 90:
                            recent_3m_scores.append(score)
                            recent_3m_returns.append(ret_20d)

                    if hit_20d is not None:
                        if action == "buy":
                            buy_hits.append(hit_20d)
                        elif action == "sell":
                            sell_hits.append(hit_20d)

            # 计算滚动 IC（Spearman Rank IC — 量化行业标准）
            metrics.rolling_ic_samples = len(all_scores)

            if len(all_scores) >= 20:
                scores_arr = np.array(all_scores)
                returns_arr = np.array(all_returns_20d)
                if np.std(scores_arr) > 0 and np.std(returns_arr) > 0:
                    from scipy.stats import spearmanr
                    ic_val, _ = spearmanr(scores_arr, returns_arr)
                    metrics.rolling_ic_20d = round(float(ic_val), 4) if not np.isnan(ic_val) else None

            # 计算分段 IC（趋势检测）
            if len(recent_1m_scores) >= 10:
                s = np.array(recent_1m_scores)
                r = np.array(recent_1m_returns)
                if np.std(s) > 0 and np.std(r) > 0:
                    from scipy.stats import spearmanr
                    ic_val, _ = spearmanr(s, r)
                    metrics.ic_1month_avg = round(float(ic_val), 4) if not np.isnan(ic_val) else None

            if len(recent_3m_scores) >= 20:
                s = np.array(recent_3m_scores)
                r = np.array(recent_3m_returns)
                if np.std(s) > 0 and np.std(r) > 0:
                    from scipy.stats import spearmanr
                    ic_val, _ = spearmanr(s, r)
                    metrics.ic_3month_avg = round(float(ic_val), 4) if not np.isnan(ic_val) else None

            # 命中率
            if len(buy_hits) >= 5:
                metrics.recent_buy_hit_rate = round(
                    sum(1 for h in buy_hits[-30:] if h) / len(buy_hits[-30:]), 4
                )
                metrics.recent_buy_count = len(buy_hits[-30:])

            if len(sell_hits) >= 5:
                metrics.recent_sell_hit_rate = round(
                    sum(1 for h in sell_hits[-30:] if h) / len(sell_hits[-30:]), 4
                )
                metrics.recent_sell_count = len(sell_hits[-30:])

            # 判断健康状态
            metrics.last_validated = today.isoformat()

            if metrics.rolling_ic_samples < 30:
                metrics.status = "insufficient_data"
                metrics.status_reason = f"样本量不足（{metrics.rolling_ic_samples}/30），需要更多跟踪数据"
            elif metrics.rolling_ic_20d is not None:
                if metrics.rolling_ic_20d >= 0.05:
                    metrics.status = "healthy"
                    metrics.status_reason = f"IC={metrics.rolling_ic_20d:.4f}，信号有效"
                elif metrics.rolling_ic_20d >= 0.02:
                    metrics.status = "degraded"
                    metrics.status_reason = f"IC={metrics.rolling_ic_20d:.4f}，信号微弱，建议谨慎"
                else:
                    metrics.status = "unhealthy"
                    metrics.status_reason = (
                        f"IC={metrics.rolling_ic_20d:.4f}，信号可能已失效，"
                        f"建议暂停使用或调整参数"
                    )
            else:
                metrics.status = "unknown"
                metrics.status_reason = "无法计算 IC（数据方差为零）"

            # IC 趋势检测
            if metrics.ic_1month_avg is not None and metrics.ic_3month_avg is not None:
                diff = metrics.ic_1month_avg - metrics.ic_3month_avg
                if diff > 0.02:
                    metrics.ic_trend = "improving"
                elif diff < -0.03:
                    metrics.ic_trend = "critical"
                elif diff < -0.01:
                    metrics.ic_trend = "declining"
                else:
                    metrics.ic_trend = "stable"

    except Exception as e:
        logger.error("advisor_health.compute_error: %s", str(e))
        metrics.status = "unknown"
        metrics.status_reason = f"计算异常: {str(e)}"
    finally:
        engine.dispose()

    return metrics


# ---------------------------------------------------------------------------
# 异步版本（供 API 端点使用）
# ---------------------------------------------------------------------------


async def compute_engine_health_async(
    session: Any,
    as_of_date: date | None = None,
) -> EngineHealthMetrics:
    """异步计算引擎健康度（供 API 使用）。"""
    from app.data.models.advisor_results import AdvisorResult
    from sqlalchemy import select

    metrics = EngineHealthMetrics()
    today = as_of_date or date.today()
    min_date = today - timedelta(days=180)

    try:
        result = await session.execute(
            select(AdvisorResult)
            .where(AdvisorResult.advice_date >= min_date)
            .where(AdvisorResult.advice_date <= today)
            .where(AdvisorResult.tracked_returns.isnot(None))
            .order_by(AdvisorResult.advice_date.desc())
            .limit(200)
        )
        results = result.scalars().all()

        if not results:
            metrics.status = "insufficient_data"
            metrics.status_reason = "无已跟踪的建议记录"
            return metrics

        # 提取数据（与同步版本相同逻辑）
        all_scores: list[float] = []
        all_returns_20d: list[float] = []
        buy_hits: list[bool] = []
        sell_hits: list[bool] = []

        for r in results:
            if not r.tracked_returns:
                continue
            for code, tracking in r.tracked_returns.items():
                score = tracking.get("composite_score", 0)
                ret_20d = tracking.get("return_20d")
                action = tracking.get("action")
                hit_20d = tracking.get("hit_20d")

                if ret_20d is not None and score != 0:
                    all_scores.append(score)
                    all_returns_20d.append(ret_20d)
                if hit_20d is not None:
                    if action == "buy":
                        buy_hits.append(hit_20d)
                    elif action == "sell":
                        sell_hits.append(hit_20d)

        metrics.rolling_ic_samples = len(all_scores)
        metrics.last_validated = today.isoformat()

        if len(all_scores) >= 20:
            scores_arr = np.array(all_scores)
            returns_arr = np.array(all_returns_20d)
            if np.std(scores_arr) > 0 and np.std(returns_arr) > 0:
                from scipy.stats import spearmanr
                ic_val, _ = spearmanr(scores_arr, returns_arr)
                metrics.rolling_ic_20d = round(float(ic_val), 4) if not np.isnan(ic_val) else None

        if len(buy_hits) >= 5:
            metrics.recent_buy_hit_rate = round(
                sum(1 for h in buy_hits[-30:] if h) / len(buy_hits[-30:]), 4
            )
            metrics.recent_buy_count = len(buy_hits[-30:])

        if len(sell_hits) >= 5:
            metrics.recent_sell_hit_rate = round(
                sum(1 for h in sell_hits[-30:] if h) / len(sell_hits[-30:]), 4
            )
            metrics.recent_sell_count = len(sell_hits[-30:])

        # 健康状态判断
        if metrics.rolling_ic_samples < 30:
            metrics.status = "insufficient_data"
            metrics.status_reason = f"样本量不足（{metrics.rolling_ic_samples}/30）"
        elif metrics.rolling_ic_20d is not None:
            if metrics.rolling_ic_20d >= 0.05:
                metrics.status = "healthy"
                metrics.status_reason = f"IC={metrics.rolling_ic_20d:.4f}，信号有效"
            elif metrics.rolling_ic_20d >= 0.02:
                metrics.status = "degraded"
                metrics.status_reason = f"IC={metrics.rolling_ic_20d:.4f}，信号微弱"
            else:
                metrics.status = "unhealthy"
                metrics.status_reason = f"IC={metrics.rolling_ic_20d:.4f}，信号可能失效"

    except Exception as e:
        metrics.status = "unknown"
        metrics.status_reason = f"计算异常: {str(e)}"

    return metrics


__all__ = [
    "track_advice_performance_sync",
    "compute_engine_health_sync",
    "compute_engine_health_async",
    "EngineHealthMetrics",
    "TrackingResult",
]
