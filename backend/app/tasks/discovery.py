"""基金自动发现 Celery 任务。

从天天基金排行榜接口自动抓取排名靠前的基金，动态更新采集列表。

设计要点：
- 每天 20:30 执行（在 21:00 数据采集之前完成）
- 多维度排名：6月涨幅、1年涨幅、3年涨幅
- 多类型覆盖：股票型、混合型、指数型分开排名
- 最低规模门槛过滤（通过后续元数据校验）
- 观察期机制：新发现的基金标记为 pending，连续 N 天上榜后激活
- 掉出榜单的基金设置冷却期，不立即停止采集

流程：
1. 从排行榜接口获取各维度 Top N 基金
2. 存储排名快照到 fund_rankings 表
3. 对新发现的基金代码，自动创建 Fund 记录（status=active）
4. 触发新基金的历史数据回填

Requirements: auto-discovery feature
"""

from __future__ import annotations

import asyncio
import time
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from app.core.logging import get_logger
from app.tasks.celery_app import celery_app

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# 配置常量
# ---------------------------------------------------------------------------

# 排名维度配置：(sort_by 参数, 中文描述)
RANKING_DIMENSIONS: list[tuple[str, str]] = [
    ("3yzf", "近3月涨幅"),
    ("6yzf", "近6月涨幅"),
    ("1nzf", "近1年涨幅"),
    ("2nzf", "近2年涨幅"),
    ("3nzf", "近3年涨幅"),
]

# 基金类型配置：(type 参数, 中文描述)
FUND_TYPE_FILTERS: list[tuple[str, str]] = [
    ("stock", "股票型"),
    ("mixed", "混合型"),
    ("index", "指数型"),
]

# 每个维度+类型组合取 Top N
TOP_N: int = 30

# 观察期天数：连续上榜 N 天后才正式纳入采集
OBSERVATION_DAYS: int = 3

# 冷却期天数：掉出榜单后继续采集 N 天
COOLDOWN_DAYS: int = 7

# 最大采集基金数量上限（防止列表无限膨胀）
MAX_WATCHLIST_SIZE: int = 200


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _today() -> date:
    """Return today's date (mockable in tests)."""
    return date.today()


def _run_async(coro: Any) -> Any:
    """Run an async coroutine from a synchronous Celery task."""
    from app.tasks.async_utils import run_async
    return run_async(coro)


# ---------------------------------------------------------------------------
# Task: discover_funds
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.tasks.discovery.discover_funds",
    queue="ingest",
    bind=True,
    max_retries=2,
    soft_time_limit=15 * 60,
    time_limit=20 * 60,
)
def discover_funds(self) -> dict[str, Any]:
    """从排行榜自动发现并注册新基金。

    执行流程：
    1. 遍历所有 (维度 × 类型) 组合，获取排名数据
    2. 存储排名快照
    3. 识别新基金（不在 funds 表中的代码）
    4. 为新基金创建元数据记录
    5. 触发新基金的历史数据回填

    Returns:
        执行摘要，包含发现数量、新增数量等。
    """
    return _run_async(_discover_funds_async())


async def _discover_funds_async() -> dict[str, Any]:
    """discover_funds 的异步实现。"""
    from app.data.models.fund_ranking import FundRanking
    from app.data.models.funds import Fund
    from app.data.providers.eastmoney import EastmoneyProvider
    from app.data.session import get_sessionmaker

    today = _today()
    provider = EastmoneyProvider()
    factory = get_sessionmaker()

    # 收集所有排名数据
    all_rankings: list[dict[str, Any]] = []
    discovered_codes: set[str] = set()
    fetch_errors: int = 0

    log.info("discovery.start", date=str(today))

    for sort_by, sort_desc in RANKING_DIMENSIONS:
        for fund_type, type_desc in FUND_TYPE_FILTERS:
            try:
                rankings = await provider.fetch_fund_ranking(
                    fund_type=fund_type,
                    sort_by=sort_by,
                    page=1,
                    page_size=TOP_N,
                )

                log.info(
                    "discovery.fetch_ranking",
                    sort_by=sort_by,
                    fund_type=fund_type,
                    count=len(rankings),
                )

                for rank_idx, item in enumerate(rankings, start=1):
                    code = item.get("code", "")
                    if not code:
                        continue

                    discovered_codes.add(code)
                    all_rankings.append({
                        "fund_code": code,
                        "snapshot_date": today,
                        "sort_metric": sort_by,
                        "rank_position": rank_idx,
                        "fund_name": item.get("name"),
                        "fund_type": fund_type,
                        "daily_return": item.get("daily_return"),
                        "weekly_return": item.get("weekly_return"),
                        "monthly_return": item.get("monthly_return"),
                        "quarterly_return": item.get("quarterly_return"),
                        "half_year_return": item.get("half_year_return"),
                        "yearly_return": item.get("yearly_return"),
                        "unit_nav": item.get("unit_nav"),
                        "accum_nav": item.get("accum_nav"),
                    })

            except Exception as exc:
                fetch_errors += 1
                log.warning(
                    "discovery.fetch_error",
                    sort_by=sort_by,
                    fund_type=fund_type,
                    error=str(exc),
                )

    log.info(
        "discovery.rankings_collected",
        total_rankings=len(all_rankings),
        unique_codes=len(discovered_codes),
        fetch_errors=fetch_errors,
    )

    # ------------------------------------------------------------------
    # 2. 存储排名快照
    # ------------------------------------------------------------------
    rankings_stored = 0
    if all_rankings:
        rankings_stored = await _store_rankings(factory, all_rankings)

    # ------------------------------------------------------------------
    # 3. 识别新基金
    # ------------------------------------------------------------------
    existing_codes = await _get_existing_fund_codes(factory)
    new_codes = discovered_codes - existing_codes

    log.info(
        "discovery.new_funds_identified",
        existing=len(existing_codes),
        discovered=len(discovered_codes),
        new=len(new_codes),
    )

    # ------------------------------------------------------------------
    # 4. 检查观察期（连续上榜天数）
    # ------------------------------------------------------------------
    qualified_codes = await _filter_by_observation(
        factory, new_codes, today, OBSERVATION_DAYS
    )

    # ------------------------------------------------------------------
    # 5. 检查总量上限
    # ------------------------------------------------------------------
    current_active = len(existing_codes)
    available_slots = max(0, MAX_WATCHLIST_SIZE - current_active)
    codes_to_add = list(qualified_codes)[:available_slots]

    if len(qualified_codes) > available_slots:
        log.warning(
            "discovery.watchlist_limit_reached",
            qualified=len(qualified_codes),
            available_slots=available_slots,
            max_size=MAX_WATCHLIST_SIZE,
        )

    # ------------------------------------------------------------------
    # 6. 为新基金创建记录
    # ------------------------------------------------------------------
    funds_created = 0
    if codes_to_add:
        funds_created = await _create_new_funds(factory, codes_to_add, all_rankings)

    # ------------------------------------------------------------------
    # 7. 触发新基金的历史数据回填
    # ------------------------------------------------------------------
    backfill_triggered = 0
    if codes_to_add:
        backfill_triggered = _trigger_backfill(codes_to_add)

    # ------------------------------------------------------------------
    # 8. 返回摘要
    # ------------------------------------------------------------------
    result = {
        "status": "success",
        "snapshot_date": str(today),
        "rankings_stored": rankings_stored,
        "unique_codes_discovered": len(discovered_codes),
        "new_codes_found": len(new_codes),
        "qualified_after_observation": len(qualified_codes),
        "funds_created": funds_created,
        "backfill_triggered": backfill_triggered,
        "fetch_errors": fetch_errors,
        "watchlist_size": current_active + funds_created,
    }

    log.info("discovery.complete", **result)
    return result


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------


async def _store_rankings(
    factory: Any,
    rankings: list[dict[str, Any]],
) -> int:
    """将排名快照批量写入 fund_rankings 表。"""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.data.models.fund_ranking import FundRanking

    async with factory() as session:
        try:
            # 使用 upsert 避免重复插入
            stmt = pg_insert(FundRanking).values(rankings)
            stmt = stmt.on_conflict_do_update(
                index_elements=["fund_code", "snapshot_date", "sort_metric"],
                set_={
                    "rank_position": stmt.excluded.rank_position,
                    "fund_name": stmt.excluded.fund_name,
                    "fund_type": stmt.excluded.fund_type,
                    "daily_return": stmt.excluded.daily_return,
                    "weekly_return": stmt.excluded.weekly_return,
                    "monthly_return": stmt.excluded.monthly_return,
                    "quarterly_return": stmt.excluded.quarterly_return,
                    "half_year_return": stmt.excluded.half_year_return,
                    "yearly_return": stmt.excluded.yearly_return,
                    "unit_nav": stmt.excluded.unit_nav,
                    "accum_nav": stmt.excluded.accum_nav,
                },
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount
        except Exception as exc:
            log.error("discovery.store_rankings_error", error=str(exc))
            await session.rollback()
            return 0


async def _get_existing_fund_codes(factory: Any) -> set[str]:
    """获取 funds 表中所有已存在的基金代码。"""
    from sqlalchemy import select

    from app.data.models.funds import Fund

    async with factory() as session:
        result = await session.execute(select(Fund.code))
        return set(result.scalars().all())


async def _filter_by_observation(
    factory: Any,
    new_codes: set[str],
    today: date,
    min_days: int,
) -> set[str]:
    """过滤出连续上榜达到观察期要求的基金代码。

    检查 fund_rankings 表中，该基金在最近 min_days 天内是否每天都有记录。
    对于首次发现的基金（今天第一次出现），直接通过（观察期=1天时）。
    """
    if min_days <= 1:
        # 观察期为1天，所有今天上榜的都通过
        return new_codes

    if not new_codes:
        return set()

    from sqlalchemy import func, select

    from app.data.models.fund_ranking import FundRanking

    qualified: set[str] = set()
    lookback_start = today - timedelta(days=min_days - 1)

    async with factory() as session:
        for code in new_codes:
            # 统计该基金在观察窗口内出现的不同天数
            stmt = (
                select(func.count(func.distinct(FundRanking.snapshot_date)))
                .where(
                    FundRanking.fund_code == code,
                    FundRanking.snapshot_date >= lookback_start,
                    FundRanking.snapshot_date <= today,
                )
            )
            result = await session.execute(stmt)
            days_on_list = result.scalar_one()

            if days_on_list >= min_days:
                qualified.add(code)
                log.debug(
                    "discovery.observation_passed",
                    fund_code=code,
                    days_on_list=days_on_list,
                )

    log.info(
        "discovery.observation_filter",
        input_count=len(new_codes),
        qualified_count=len(qualified),
        min_days=min_days,
    )
    return qualified


async def _create_new_funds(
    factory: Any,
    codes: list[str],
    rankings: list[dict[str, Any]],
) -> int:
    """为新发现的基金创建 Fund 记录。

    从排名数据中提取基金名称，创建最小化的 Fund 记录。
    后续的 update_fund_meta 任务会补全完整元数据。
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.data.models.funds import Fund

    # 从排名数据中提取基金名称
    code_to_name: dict[str, str] = {}
    for item in rankings:
        code = item["fund_code"]
        if code in codes and code not in code_to_name:
            name = item.get("fund_name") or f"基金{code}"
            code_to_name[code] = name

    records = []
    for code in codes:
        records.append({
            "code": code,
            "name": code_to_name.get(code, f"基金{code}"),
            "status": "active",
            "source": "discovery",
        })

    if not records:
        return 0

    async with factory() as session:
        try:
            stmt = pg_insert(Fund).values(records)
            # 如果基金已存在（并发情况），不覆盖
            stmt = stmt.on_conflict_do_nothing(index_elements=["code"])
            result = await session.execute(stmt)
            await session.commit()
            created = result.rowcount
            log.info("discovery.funds_created", count=created, codes=codes[:10])
            return created
        except Exception as exc:
            log.error("discovery.create_funds_error", error=str(exc))
            await session.rollback()
            return 0


def _trigger_backfill(codes: list[str]) -> int:
    """为新发现的基金触发历史数据回填任务。

    分发以下子任务：
    - update_fund_meta: 获取完整元数据
    - update_daily_nav: 回填历史净值（默认1年）
    - update_dividends: 获取分红记录
    """
    from app.tasks.ingest import update_daily_nav, update_dividends, update_fund_meta

    triggered = 0
    for code in codes:
        try:
            # 先获取元数据
            update_fund_meta.apply_async(
                kwargs={"fund_code": code},
                queue="ingest",
                countdown=5,  # 5秒后执行，避免瞬间并发
            )
            # 然后获取历史净值（延迟执行，等元数据完成）
            update_daily_nav.apply_async(
                kwargs={"fund_code": code},
                queue="ingest",
                countdown=30,  # 30秒后执行
            )
            # 获取分红记录
            update_dividends.apply_async(
                kwargs={"fund_code": code},
                queue="ingest",
                countdown=60,  # 60秒后执行
            )
            triggered += 1
        except Exception as exc:
            log.warning(
                "discovery.backfill_trigger_error",
                fund_code=code,
                error=str(exc),
            )

    log.info("discovery.backfill_triggered", count=triggered)
    return triggered


# ---------------------------------------------------------------------------
# Task: cleanup_stale_rankings
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.tasks.discovery.cleanup_stale_rankings",
    queue="ingest",
    bind=True,
    max_retries=1,
    soft_time_limit=5 * 60,
    time_limit=10 * 60,
)
def cleanup_stale_rankings(self, retention_days: int = 30) -> dict[str, Any]:
    """清理过期的排名快照数据。

    保留最近 retention_days 天的数据，删除更早的记录。

    Args:
        retention_days: 数据保留天数，默认30天。

    Returns:
        清理摘要。
    """
    return _run_async(_cleanup_stale_rankings_async(retention_days))


async def _cleanup_stale_rankings_async(retention_days: int) -> dict[str, Any]:
    """cleanup_stale_rankings 的异步实现。"""
    from sqlalchemy import delete

    from app.data.models.fund_ranking import FundRanking
    from app.data.session import get_sessionmaker

    cutoff = _today() - timedelta(days=retention_days)
    factory = get_sessionmaker()

    async with factory() as session:
        try:
            stmt = delete(FundRanking).where(FundRanking.snapshot_date < cutoff)
            result = await session.execute(stmt)
            await session.commit()
            deleted = result.rowcount

            log.info(
                "discovery.cleanup_complete",
                cutoff=str(cutoff),
                deleted=deleted,
            )
            return {
                "status": "success",
                "cutoff_date": str(cutoff),
                "records_deleted": deleted,
            }
        except Exception as exc:
            log.error("discovery.cleanup_error", error=str(exc))
            await session.rollback()
            return {"status": "error", "error": str(exc)}


__all__ = ["cleanup_stale_rankings", "discover_funds"]
