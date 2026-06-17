"""Data ingestion Celery tasks.

Implements the five core ingestion tasks that orchestrate the data
collection pipeline:

- ``update_fund_meta``       — fetch and upsert fund metadata
- ``update_daily_nav``       — fetch and upsert daily NAV records
- ``update_holdings``        — fetch and upsert quarterly holdings
- ``update_dividends``       — fetch and upsert dividend/split events
- ``update_announcements``   — fetch and upsert fund announcements

Each task follows the same pattern:
1. Read ``last_updated`` from the repository layer
2. Call ``CompositeProvider`` to fetch new data
3. Validate fetched data using the validator layer
4. Upsert valid records into the database
5. Record Prometheus metrics (latency, success/failure counts)

Tasks support two trigger modes:
- **Single fund**: pass ``fund_code`` to update one fund
- **Batch**: pass ``fund_codes`` (list) or omit both to update all funds

Requirements: 1.1, 1.2, 1.11, 8.1
"""

from __future__ import annotations

import asyncio
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from app.core.logging import get_logger
from app.observability.metrics import INGEST_LATENCY_SECONDS, INGEST_REQUESTS_TOTAL
from app.tasks.celery_app import celery_app

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _today() -> date:
    """Return today's date (mockable in tests)."""
    return date.today()


def _run_async(coro: Any) -> Any:
    """Run an async coroutine from a synchronous Celery task.

    Delegates to the shared async_utils.run_async which reuses
    per-thread event loops for connection pool efficiency.
    """
    from app.tasks.async_utils import run_async
    return run_async(coro)


def _resolve_fund_codes(
    fund_code: str | None = None,
    fund_codes: list[str] | None = None,
) -> list[str]:
    """Normalize the two trigger modes into a list of fund codes.

    - If ``fund_code`` is provided, return a single-element list.
    - If ``fund_codes`` is provided, return it directly.
    - If neither is provided, return an empty list (caller should
      fetch all fund codes from the database).
    """
    if fund_code:
        return [fund_code]
    if fund_codes:
        return fund_codes
    return []


async def _get_all_fund_codes() -> list[str]:
    """Fetch all active fund codes from the database."""
    from sqlalchemy import select

    from app.data.models.funds import Fund
    from app.data.session import get_sessionmaker

    factory = get_sessionmaker()
    async with factory() as session:
        result = await session.execute(
            select(Fund.code).where(Fund.status == "active")
        )
        return list(result.scalars().all())


async def _get_composite_provider():
    """Build a CompositeProvider instance with the shared default chain."""
    from app.data.providers.factory import build_default_composite_provider

    return build_default_composite_provider(logger=log)


# ---------------------------------------------------------------------------
# Task: update_fund_meta
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.tasks.ingest.update_fund_meta",
    queue="ingest",
    bind=True,
    max_retries=2,
    soft_time_limit=20 * 60,
    time_limit=25 * 60,
)
def update_fund_meta(
    self,
    fund_code: str | None = None,
    fund_codes: list[str] | None = None,
) -> dict[str, Any]:
    """Fetch and upsert fund metadata.

    Args:
        fund_code: Single fund code to update.
        fund_codes: List of fund codes to update.
            If neither is provided, updates all active funds.

    Returns:
        Summary dict with counts of success/failure.
    """
    return _run_async(_update_fund_meta_async(fund_code, fund_codes))


async def _update_fund_meta_async(
    fund_code: str | None,
    fund_codes: list[str] | None,
) -> dict[str, Any]:
    """Async implementation of update_fund_meta."""
    from app.data.cache import invalidate_fund_meta
    from app.data.repositories.fund_repo import FundRepo
    from app.data.session import get_sessionmaker

    codes = _resolve_fund_codes(fund_code, fund_codes)
    if not codes:
        codes = await _get_all_fund_codes()
        if not codes:
            log.info("ingest.update_fund_meta.no_funds")
            return {"success": 0, "failed": 0, "skipped": 0}

    provider = await _get_composite_provider()
    repo = FundRepo()
    factory = get_sessionmaker()

    success = 0
    failed = 0

    for code in codes:
        start_time = time.monotonic()
        try:
            meta, source = await provider.fetch_fund_meta(code)
            elapsed = time.monotonic() - start_time

            # Convert Pydantic model to dict for upsert
            record = meta.model_dump(exclude_none=False)
            record["source"] = source

            async with factory() as session:
                await repo.upsert_many(session, [record])
                await session.commit()

            # Invalidate cached metadata after successful upsert
            await invalidate_fund_meta(code)

            INGEST_REQUESTS_TOTAL.labels(
                provider=source, endpoint="fund_meta", status="success"
            ).inc()
            INGEST_LATENCY_SECONDS.labels(
                provider=source, endpoint="fund_meta"
            ).observe(elapsed)

            success += 1
            log.info(
                "ingest.update_fund_meta.ok",
                fund_code=code,
                source=source,
                elapsed=f"{elapsed:.2f}s",
            )

        except Exception as exc:
            elapsed = time.monotonic() - start_time
            failed += 1
            INGEST_REQUESTS_TOTAL.labels(
                provider="composite", endpoint="fund_meta", status="error"
            ).inc()
            log.error(
                "ingest.update_fund_meta.failed",
                fund_code=code,
                error=str(exc),
                elapsed=f"{elapsed:.2f}s",
            )

    return {"success": success, "failed": failed, "total": len(codes)}


# ---------------------------------------------------------------------------
# Helper: backfill missing NAV data
# ---------------------------------------------------------------------------


async def _build_nav_cross_source_diagnostics(
    code: str,
    provider,
    start: date,
    end: date,
    primary_source: str,
    primary_records: list[Any],
) -> dict[str, Any]:
    """Fetch multi-source NAV data and build a consistency hard-gate report."""
    if not hasattr(provider, "fetch_nav_history_all_sources"):
        return {
            "status": "not_available",
            "hard_gate": False,
            "provider_count": 1 if primary_source else 0,
            "providers": [primary_source] if primary_source else [],
            "reason": "当前 provider 不支持多源 NAV 原始对照",
        }

    try:
        from app.data.validators.cross_source_validator import build_cross_source_nav_diagnostics

        series_by_source, errors = await provider.fetch_nav_history_all_sources(code, start, end)
        series_by_source = dict(series_by_source or {})
        if primary_source and primary_records and primary_source not in series_by_source:
            series_by_source[primary_source] = list(primary_records)
        diagnostics = build_cross_source_nav_diagnostics(
            series_by_source,
            errors=errors,
        )
        return diagnostics.to_dict()
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "ingest.nav_cross_source_diagnostics_failed",
            fund_code=code,
            start=str(start),
            end=str(end),
            error=str(exc),
        )
        return {
            "status": "error",
            "hard_gate": False,
            "provider_count": 1 if primary_source else 0,
            "providers": [primary_source] if primary_source else [],
            "reason": f"多源 NAV 原始对照执行失败：{exc}",
        }


async def _backfill_missing_nav(
    code: str,
    repo,
    provider,
    factory,
    today: date,
    lookback_days: int = 90,
) -> int:
    """检测并回填最近 lookback_days 天内缺失的交易日净值数据。

    逻辑：
    1. 用交易日历获取最近 lookback_days 天内的所有交易日
    2. 与数据库中已有的记录对比，找出缺失的日期
    3. 按连续缺失区间分批从数据源获取并写入

    Args:
        code: 基金代码
        repo: NavRepo 实例
        provider: CompositeProvider 实例
        factory: session factory
        today: 当前日期
        lookback_days: 回看天数，默认 90 天

    Returns:
        回填写入的记录数
    """
    from app.data.validators.nav_validator import validate_nav_series

    try:
        from app.domain.backtest.calendar import trading_days
    except ImportError:
        log.warning("ingest.backfill.calendar_unavailable", fund_code=code)
        return 0

    lookback_start = today - timedelta(days=lookback_days)

    # 获取回看区间内的所有交易日
    expected_dates = trading_days(lookback_start, today)
    if not expected_dates:
        return 0

    # 查询数据库中缺失的日期
    async with factory() as session:
        missing = await repo.missing_dates(session, code, expected_dates)

    if not missing:
        return 0

    missing.sort()
    log.info(
        "ingest.backfill.detected_gaps",
        fund_code=code,
        missing_count=len(missing),
        first_missing=str(missing[0]),
        last_missing=str(missing[-1]),
    )

    # 将缺失日期按连续区间分组，减少请求次数
    ranges = _group_consecutive_dates(missing)
    total_inserted = 0

    for range_start, range_end in ranges:
        try:
            nav_records, source = await provider.fetch_nav_history(
                code, range_start, range_end
            )

            if not nav_records:
                log.debug(
                    "ingest.backfill.no_data_for_range",
                    fund_code=code,
                    start=str(range_start),
                    end=str(range_end),
                )
                continue

            cross_source_diagnostics = await _build_nav_cross_source_diagnostics(
                code,
                provider,
                range_start,
                range_end,
                source,
                nav_records,
            )
            if cross_source_diagnostics.get("hard_gate"):
                log.error(
                    "ingest.backfill.cross_source_hard_gate",
                    fund_code=code,
                    source=source,
                    start=str(range_start),
                    end=str(range_end),
                    diagnostics=cross_source_diagnostics,
                )
                continue

            # Validate and prepare records
            validation_results = validate_nav_series(nav_records)
            valid_records: list[dict[str, Any]] = []
            for record, vr in zip(nav_records, validation_results):
                row = {
                    "fund_code": record.fund_code,
                    "trade_date": record.trade_date,
                    "unit_nav": record.unit_nav,
                    "accum_nav": record.accum_nav,
                    "adj_nav": record.adj_nav,
                    "daily_return": record.daily_return,
                    "source": source,
                }
                if not vr.is_valid:
                    row["status"] = "suspect"
                else:
                    row["status"] = "normal"
                valid_records.append(row)

            if valid_records:
                async with factory() as session:
                    count = await repo.upsert_many(session, valid_records)
                    from app.data.services.adj_nav import recalculate_adj_nav

                    await recalculate_adj_nav(session, code)
                    await session.commit()
                total_inserted += count

            log.info(
                "ingest.backfill.range_ok",
                fund_code=code,
                source=source,
                start=str(range_start),
                end=str(range_end),
                records=len(valid_records),
            )

        except Exception as exc:
            log.warning(
                "ingest.backfill.range_failed",
                fund_code=code,
                start=str(range_start),
                end=str(range_end),
                error=str(exc),
            )
            # 回填失败不影响整体任务，继续下一个区间
            continue

    if total_inserted > 0:
        log.info(
            "ingest.backfill.completed",
            fund_code=code,
            total_inserted=total_inserted,
        )

    return total_inserted


def _group_consecutive_dates(dates: list[date]) -> list[tuple[date, date]]:
    """将排序后的日期列表按连续区间分组。

    连续的定义：两个日期之间间隔不超过 5 天（考虑周末和短假期）。

    Returns:
        [(range_start, range_end), ...] 列表
    """
    if not dates:
        return []

    ranges: list[tuple[date, date]] = []
    range_start = dates[0]
    range_end = dates[0]

    for d in dates[1:]:
        # 如果间隔不超过 5 天，认为是同一个连续区间
        if (d - range_end).days <= 5:
            range_end = d
        else:
            ranges.append((range_start, range_end))
            range_start = d
            range_end = d

    ranges.append((range_start, range_end))
    return ranges


# ---------------------------------------------------------------------------
# Task: update_daily_nav
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.tasks.ingest.update_daily_nav",
    queue="ingest",
    bind=True,
    max_retries=2,
    soft_time_limit=25 * 60,
    time_limit=30 * 60,
)
def update_daily_nav(
    self,
    fund_code: str | None = None,
    fund_codes: list[str] | None = None,
    full: bool = False,
) -> dict[str, Any]:
    """Fetch and upsert daily NAV records (incremental).

    Reads the last stored trade_date for each fund, then fetches only
    new data from that date onwards.

    Args:
        fund_code: Single fund code to update.
        fund_codes: List of fund codes to update.
            If neither is provided, updates all active funds.
        full: If True, ignore last_date and fetch from inception date
            (full history). Used by the one-click ingest endpoint.

    Returns:
        Summary dict with counts of success/failure and total records.
    """
    return _run_async(_update_daily_nav_async(fund_code, fund_codes, full=full))


async def _update_daily_nav_async(
    fund_code: str | None,
    fund_codes: list[str] | None,
    full: bool = False,
) -> dict[str, Any]:
    """Async implementation of update_daily_nav.

    增量更新逻辑：
    1. 查找数据库中最新的 trade_date
    2. 从 last_date + 1 天开始采集到今天（追加新数据）
    3. 检测最近 60 个交易日内的数据空洞并回填
    """
    from app.data.cache import invalidate_nav
    from app.data.repositories.nav_repo import NavRepo
    from app.data.session import get_sessionmaker
    from app.data.validators.nav_validator import validate_nav_series

    codes = _resolve_fund_codes(fund_code, fund_codes)
    if not codes:
        codes = await _get_all_fund_codes()
        if not codes:
            log.info("ingest.update_daily_nav.no_funds")
            return {"success": 0, "failed": 0, "records_inserted": 0}

    provider = await _get_composite_provider()
    repo = NavRepo()
    factory = get_sessionmaker()
    today = _today()

    success = 0
    failed = 0
    total_records = 0

    for code in codes:
        start_time = time.monotonic()
        try:
            # Determine start date from last_updated
            async with factory() as session:
                last_date = await repo.latest_date(session, code)

            # If full=True, always fetch from inception date (full history)
            if not full and last_date:
                start = last_date + timedelta(days=1)
            else:
                # 全量采集：从基金成立日期开始
                from app.data.models.funds import Fund as FundModel
                async with factory() as session:
                    from sqlalchemy import select as sa_select
                    fund_result = await session.execute(
                        sa_select(FundModel.inception_date).where(FundModel.code == code)
                    )
                    inception = fund_result.scalar_one_or_none()
                if inception:
                    start = inception
                    log.info(
                        "ingest.update_daily_nav.full_from_inception",
                        fund_code=code,
                        inception_date=str(inception),
                    )
                else:
                    start = today - timedelta(days=365 * 10)
                    log.warning(
                        "ingest.update_daily_nav.no_inception_date",
                        fund_code=code,
                        fallback_start=str(start),
                        msg="未找到成立日期，回退到10年前开始采集",
                    )

            if start > today:
                # 数据已是最新，但仍需检查空洞
                backfill_count = await _backfill_missing_nav(
                    code, repo, provider, factory, today
                )
                total_records += backfill_count
                if backfill_count > 0:
                    await invalidate_nav(code)
                log.debug("ingest.update_daily_nav.up_to_date", fund_code=code)
                success += 1
                continue

            # Fetch from provider
            nav_records, source = await provider.fetch_nav_history(code, start, today)
            elapsed = time.monotonic() - start_time

            if not nav_records:
                # 全量采集模式下，空数据可能是数据源首次请求未命中缓存
                # 等待后重试一次
                if full:
                    log.warning(
                        "ingest.update_daily_nav.empty_retry",
                        fund_code=code,
                        source=source,
                        start=str(start),
                        msg="全量采集首次返回空数据，等待 5 秒后重试",
                    )
                    await asyncio.sleep(5)
                    nav_records, source = await provider.fetch_nav_history(code, start, today)
                    elapsed = time.monotonic() - start_time

                if not nav_records:
                    if full:
                        failed += 1
                        log.warning(
                            "ingest.update_daily_nav.empty_full",
                            fund_code=code,
                            source=source,
                            start=str(start),
                            end=str(today),
                            elapsed=f"{elapsed:.2f}s",
                            msg="全量采集重试后仍返回空数据，可能是数据源限制或基金代码无效",
                        )
                        continue

                    INGEST_REQUESTS_TOTAL.labels(
                        provider=source, endpoint="daily_nav", status="success"
                    ).inc()
                    INGEST_LATENCY_SECONDS.labels(
                        provider=source, endpoint="daily_nav"
                    ).observe(elapsed)
                    success += 1
                    continue

            cross_source_diagnostics = await _build_nav_cross_source_diagnostics(
                code,
                provider,
                start,
                today,
                source,
                nav_records,
            )
            if cross_source_diagnostics.get("hard_gate"):
                failed += 1
                log.error(
                    "ingest.update_daily_nav.cross_source_hard_gate",
                    fund_code=code,
                    source=source,
                    start=str(start),
                    end=str(today),
                    diagnostics=cross_source_diagnostics,
                    msg="多源 NAV 原始对照失败，阻断本次写入",
                )
                continue

            # Validate NAV series
            validation_results = validate_nav_series(nav_records)

            # Filter out records with validation errors (mark as suspect)
            valid_records: list[dict[str, Any]] = []
            for record, vr in zip(nav_records, validation_results):
                row = {
                    "fund_code": record.fund_code,
                    "trade_date": record.trade_date,
                    "unit_nav": record.unit_nav,
                    "accum_nav": record.accum_nav,
                    "adj_nav": record.adj_nav,
                    "daily_return": record.daily_return,
                    "source": source,
                }
                if not vr.is_valid:
                    # Mark suspect records with status
                    row["status"] = "suspect"
                    log.warning(
                        "ingest.update_daily_nav.suspect",
                        fund_code=code,
                        trade_date=str(record.trade_date),
                        issues=[str(i) for i in vr.issues],
                    )
                else:
                    row["status"] = "normal"
                valid_records.append(row)

            # Upsert into database
            if valid_records:
                async with factory() as session:
                    count = await repo.upsert_many(session, valid_records)
                    from app.data.services.adj_nav import recalculate_adj_nav

                    await recalculate_adj_nav(session, code)
                    await session.commit()
                total_records += count

                # Invalidate NAV cache after successful upsert
                await invalidate_nav(code)

            INGEST_REQUESTS_TOTAL.labels(
                provider=source, endpoint="daily_nav", status="success"
            ).inc()
            INGEST_LATENCY_SECONDS.labels(
                provider=source, endpoint="daily_nav"
            ).observe(elapsed)

            # 增量更新完成后，检测并回填最近的数据空洞
            if not full:
                backfill_count = await _backfill_missing_nav(
                    code, repo, provider, factory, today
                )
                total_records += backfill_count
                if backfill_count > 0:
                    await invalidate_nav(code)

            success += 1
            log.info(
                "ingest.update_daily_nav.ok",
                fund_code=code,
                source=source,
                records=len(valid_records),
                elapsed=f"{elapsed:.2f}s",
            )

        except Exception as exc:
            elapsed = time.monotonic() - start_time
            failed += 1
            INGEST_REQUESTS_TOTAL.labels(
                provider="composite", endpoint="daily_nav", status="error"
            ).inc()
            log.error(
                "ingest.update_daily_nav.failed",
                fund_code=code,
                error=str(exc),
                elapsed=f"{elapsed:.2f}s",
            )

    return {
        "success": success,
        "failed": failed,
        "total": len(codes),
        "records_inserted": total_records,
    }


# ---------------------------------------------------------------------------
# Task: update_holdings
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.tasks.ingest.update_holdings",
    queue="ingest",
    bind=True,
    max_retries=2,
    soft_time_limit=20 * 60,
    time_limit=25 * 60,
)
def update_holdings(
    self,
    fund_code: str | None = None,
    fund_codes: list[str] | None = None,
    quarter: str | None = None,
) -> dict[str, Any]:
    """Fetch and upsert quarterly holding snapshots.

    If ``quarter`` is not specified, determines the latest quarter to
    fetch based on the current date and last stored report_date.

    Args:
        fund_code: Single fund code to update.
        fund_codes: List of fund codes to update.
        quarter: Specific quarter to fetch (format "YYYY-QN", e.g. "2024-Q1").

    Returns:
        Summary dict with counts of success/failure.
    """
    return _run_async(_update_holdings_async(fund_code, fund_codes, quarter))


def _determine_quarter(ref_date: date) -> str:
    """Determine the most recent completed quarter for a given date.

    Returns format "YYYY-QN" (e.g. "2024-Q1").
    """
    year = ref_date.year
    month = ref_date.month

    if month <= 3:
        # Q4 of previous year
        return f"{year - 1}-Q4"
    elif month <= 6:
        return f"{year}-Q1"
    elif month <= 9:
        return f"{year}-Q2"
    else:
        return f"{year}-Q3"


async def _update_holdings_async(
    fund_code: str | None,
    fund_codes: list[str] | None,
    quarter: str | None,
) -> dict[str, Any]:
    """Async implementation of update_holdings."""
    from app.data.repositories.holding_repo import HoldingRepo
    from app.data.session import get_sessionmaker
    from app.data.validators.holding_validator import validate_holding_snapshot

    codes = _resolve_fund_codes(fund_code, fund_codes)
    if not codes:
        codes = await _get_all_fund_codes()
        if not codes:
            log.info("ingest.update_holdings.no_funds")
            return {"success": 0, "failed": 0}

    # Determine which quarter to fetch
    target_quarter = quarter or _determine_quarter(_today())

    provider = await _get_composite_provider()
    repo = HoldingRepo()
    factory = get_sessionmaker()

    success = 0
    failed = 0

    for code in codes:
        start_time = time.monotonic()
        try:
            snapshot, source = await provider.fetch_holdings(code, target_quarter)
            elapsed = time.monotonic() - start_time

            # Validate holding snapshot
            vr = validate_holding_snapshot(snapshot)
            if not vr.is_valid:
                log.warning(
                    "ingest.update_holdings.validation_issues",
                    fund_code=code,
                    quarter=target_quarter,
                    issues=[str(i) for i in vr.issues],
                )

            # Convert positions to records for upsert
            records: list[dict[str, Any]] = []
            for pos in snapshot.positions:
                records.append({
                    "fund_code": snapshot.fund_code,
                    "report_date": snapshot.report_date,
                    "stock_code": pos.stock_code or "UNKNOWN",
                    "stock_name": pos.stock_name,
                    "weight": pos.weight,
                    "shares": pos.shares,
                    "market_value": pos.market_value,
                    "industry": pos.industry,
                })

            if records:
                async with factory() as session:
                    await repo.upsert_many(session, records)
                    await session.commit()

            INGEST_REQUESTS_TOTAL.labels(
                provider=source, endpoint="holdings", status="success"
            ).inc()
            INGEST_LATENCY_SECONDS.labels(
                provider=source, endpoint="holdings"
            ).observe(elapsed)

            success += 1
            log.info(
                "ingest.update_holdings.ok",
                fund_code=code,
                quarter=target_quarter,
                source=source,
                positions=len(records),
                elapsed=f"{elapsed:.2f}s",
            )

        except Exception as exc:
            elapsed = time.monotonic() - start_time
            failed += 1
            INGEST_REQUESTS_TOTAL.labels(
                provider="composite", endpoint="holdings", status="error"
            ).inc()
            log.error(
                "ingest.update_holdings.failed",
                fund_code=code,
                quarter=target_quarter,
                error=str(exc),
                elapsed=f"{elapsed:.2f}s",
            )

    return {
        "success": success,
        "failed": failed,
        "total": len(codes),
        "quarter": target_quarter,
    }


# ---------------------------------------------------------------------------
# Task: update_dividends
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.tasks.ingest.update_dividends",
    queue="ingest",
    bind=True,
    max_retries=2,
    soft_time_limit=20 * 60,
    time_limit=25 * 60,
)
def update_dividends(
    self,
    fund_code: str | None = None,
    fund_codes: list[str] | None = None,
) -> dict[str, Any]:
    """Fetch and upsert dividend/split events.

    Args:
        fund_code: Single fund code to update.
        fund_codes: List of fund codes to update.
            If neither is provided, updates all active funds.

    Returns:
        Summary dict with counts of success/failure.
    """
    return _run_async(_update_dividends_async(fund_code, fund_codes))


async def _update_dividends_async(
    fund_code: str | None,
    fund_codes: list[str] | None,
) -> dict[str, Any]:
    """Async implementation of update_dividends."""
    from app.data.repositories.dividend_repo import DividendRepo
    from app.data.session import get_sessionmaker

    codes = _resolve_fund_codes(fund_code, fund_codes)
    if not codes:
        codes = await _get_all_fund_codes()
        if not codes:
            log.info("ingest.update_dividends.no_funds")
            return {"success": 0, "failed": 0}

    provider = await _get_composite_provider()
    repo = DividendRepo()
    factory = get_sessionmaker()

    success = 0
    failed = 0
    total_records = 0

    for code in codes:
        start_time = time.monotonic()
        try:
            dividends, source = await provider.fetch_dividends(code)
            elapsed = time.monotonic() - start_time

            if not dividends:
                INGEST_REQUESTS_TOTAL.labels(
                    provider=source, endpoint="dividends", status="success"
                ).inc()
                INGEST_LATENCY_SECONDS.labels(
                    provider=source, endpoint="dividends"
                ).observe(elapsed)
                success += 1
                continue

            # Convert to records for upsert
            records: list[dict[str, Any]] = []
            for div in dividends:
                records.append({
                    "fund_code": div.fund_code,
                    "ex_date": div.ex_date,
                    "record_date": div.record_date,
                    "pay_date": div.pay_date,
                    "dividend_per_share": div.dividend_per_share,
                    "split_ratio": div.split_ratio,
                })

            async with factory() as session:
                count = await repo.upsert_many(session, records)
                await session.commit()
            total_records += count

            # After dividend upsert succeeds, recalculate adj_nav
            async with factory() as session:
                from app.data.services.adj_nav import recalculate_adj_nav

                updated = await recalculate_adj_nav(session, code)
                await session.commit()
                if updated > 0:
                    log.info(
                        "ingest.update_dividends.adj_nav_recalculated",
                        fund_code=code,
                        records_updated=updated,
                    )

            INGEST_REQUESTS_TOTAL.labels(
                provider=source, endpoint="dividends", status="success"
            ).inc()
            INGEST_LATENCY_SECONDS.labels(
                provider=source, endpoint="dividends"
            ).observe(elapsed)

            success += 1
            log.info(
                "ingest.update_dividends.ok",
                fund_code=code,
                source=source,
                records=len(records),
                elapsed=f"{elapsed:.2f}s",
            )

        except Exception as exc:
            elapsed = time.monotonic() - start_time
            failed += 1
            INGEST_REQUESTS_TOTAL.labels(
                provider="composite", endpoint="dividends", status="error"
            ).inc()
            log.error(
                "ingest.update_dividends.failed",
                fund_code=code,
                error=str(exc),
                elapsed=f"{elapsed:.2f}s",
            )

    return {
        "success": success,
        "failed": failed,
        "total": len(codes),
        "records_upserted": total_records,
    }


# ---------------------------------------------------------------------------
# Task: recalculate_adj_nav_history
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.tasks.ingest.recalculate_adj_nav_history",
    queue="ingest",
    bind=True,
    max_retries=1,
    soft_time_limit=60 * 60,
    time_limit=75 * 60,
)
def recalculate_adj_nav_history(
    self,
    fund_code: str | None = None,
    fund_codes: list[str] | None = None,
    invalidate_cache: bool = True,
    mark_stale_results: bool = True,
) -> dict[str, Any]:
    """Recalculate historical adj_nav and daily_return for existing NAV rows.

    This maintenance task is intended after formula or dividend-data changes.
    It recomputes each target fund's ``adj_nav`` and derives ``daily_return``
    from ``adj_nav.pct_change()`` so historical rows match the current return
    methodology.
    """
    return _run_async(
        _recalculate_adj_nav_history_async(
            fund_code,
            fund_codes,
            invalidate_cache,
            mark_stale_results,
        )
    )


async def _recalculate_adj_nav_history_async(
    fund_code: str | None,
    fund_codes: list[str] | None,
    invalidate_cache: bool,
    mark_stale_results: bool,
) -> dict[str, Any]:
    """Async implementation of recalculate_adj_nav_history."""
    from app.data.cache import invalidate_nav
    from app.data.services.adj_nav import recalculate_adj_nav
    from app.data.session import get_sessionmaker

    codes = _resolve_fund_codes(fund_code, fund_codes)
    if not codes:
        codes = await _get_all_fund_codes()
        if not codes:
            log.info("ingest.recalculate_adj_nav_history.no_funds")
            return {
                "success": 0,
                "failed": 0,
                "total": 0,
                "records_updated": 0,
            }

    factory = get_sessionmaker()
    success = 0
    failed = 0
    total_updated = 0
    stale_marked = {
        "advisor_results": 0,
        "advisor_oos_snapshots": 0,
        "backtest_runs": 0,
        "simulation_runs": 0,
    }

    for code in codes:
        start_time = time.monotonic()
        try:
            stale_counts = {
                "advisor_results": 0,
                "advisor_oos_snapshots": 0,
                "backtest_runs": 0,
                "simulation_runs": 0,
            }
            async with factory() as session:
                updated = await recalculate_adj_nav(session, code)
                if mark_stale_results and updated > 0:
                    stale_counts = await _mark_nav_dependent_results_stale(
                        session,
                        [code],
                        reason="adj_nav_history_recalculated",
                    )
                await session.commit()

            for key, value in stale_counts.items():
                stale_marked[key] += value

            if invalidate_cache:
                await invalidate_nav(code)

            elapsed = time.monotonic() - start_time
            total_updated += updated
            success += 1

            INGEST_REQUESTS_TOTAL.labels(
                provider="internal", endpoint="adj_nav_recalculate", status="success"
            ).inc()
            INGEST_LATENCY_SECONDS.labels(
                provider="internal", endpoint="adj_nav_recalculate"
            ).observe(elapsed)
            log.info(
                "ingest.recalculate_adj_nav_history.ok",
                fund_code=code,
                records_updated=updated,
                elapsed=f"{elapsed:.2f}s",
            )

        except Exception as exc:
            elapsed = time.monotonic() - start_time
            failed += 1
            INGEST_REQUESTS_TOTAL.labels(
                provider="internal", endpoint="adj_nav_recalculate", status="error"
            ).inc()
            log.error(
                "ingest.recalculate_adj_nav_history.failed",
                fund_code=code,
                error=str(exc),
                elapsed=f"{elapsed:.2f}s",
            )

    return {
        "success": success,
        "failed": failed,
        "total": len(codes),
        "records_updated": total_updated,
        "stale_marked": stale_marked,
    }


async def _mark_nav_dependent_results_stale(
    session,
    fund_codes: list[str],
    *,
    reason: str,
) -> dict[str, int]:
    """Soft-mark persisted results that depend on recalculated NAV data.

    Uses existing JSON fields instead of schema changes. Advisor rows are
    matched directly by ``fund_codes``; OOS snapshots by ``fund_code``; backtest
    and simulation rows are matched through strategy universe fund pools when
    available. Rows are not deleted or failed — they remain reviewable but carry
    a stale marker that callers can surface or use to trigger recomputation.
    """
    from sqlalchemy import select

    from app.data.models.advisor_oos_snapshots import AdvisorOOSSnapshot
    from app.data.models.advisor_results import AdvisorResult
    from app.data.models.backtests import BacktestRun
    from app.data.models.simulations import SimulationRun
    from app.data.models.strategies import Strategy

    target_codes = {str(code) for code in fund_codes if code}
    now_iso = datetime.now(timezone.utc).isoformat()
    marker = {
        "stale": True,
        "reason": reason,
        "fund_codes": sorted(target_codes),
        "marked_at": now_iso,
        "message": "Historical adj_nav/daily_return changed; rerun this result before relying on metrics.",
    }
    counts = {
        "advisor_results": 0,
        "advisor_oos_snapshots": 0,
        "backtest_runs": 0,
        "simulation_runs": 0,
    }

    advisor_result = await session.execute(select(AdvisorResult))
    for row in advisor_result.scalars().all():
        row_codes = {str(code) for code in (row.fund_codes or [])}
        if not row_codes.intersection(target_codes):
            continue
        context = dict(row.execution_context or {})
        context["nav_data_stale"] = marker
        row.execution_context = context
        counts["advisor_results"] += 1

    oos_result = await session.execute(
        select(AdvisorOOSSnapshot).where(AdvisorOOSSnapshot.fund_code.in_(target_codes))
    )
    for snapshot in oos_result.scalars().all():
        warnings = list(snapshot.warnings_json or [])
        warnings.append({"type": "nav_data_stale", **marker})
        snapshot.warnings_json = warnings
        snapshot.data_version = f"stale:{reason}:{now_iso}"
        counts["advisor_oos_snapshots"] += 1

    strategy_result = await session.execute(select(Strategy))
    affected_strategy_ids: set[int] = set()
    for strategy in strategy_result.scalars().all():
        strategy_codes = _extract_strategy_fund_codes(strategy.universe)
        if strategy_codes.intersection(target_codes):
            affected_strategy_ids.add(strategy.id)

    if affected_strategy_ids:
        backtest_result = await session.execute(
            select(BacktestRun).where(
                BacktestRun.strategy_id.in_(affected_strategy_ids),
                BacktestRun.status == "done",
            )
        )
        for run in backtest_result.scalars().all():
            metrics = dict(run.metrics or {})
            metrics["nav_data_stale"] = marker
            run.metrics = metrics
            counts["backtest_runs"] += 1

        simulation_result = await session.execute(
            select(SimulationRun).where(
                SimulationRun.strategy_id.in_(affected_strategy_ids),
                SimulationRun.status == "done",
            )
        )
        for run in simulation_result.scalars().all():
            metrics = dict(run.metrics or {})
            metrics["nav_data_stale"] = marker
            run.metrics = metrics
            counts["simulation_runs"] += 1

    return counts


def _extract_strategy_fund_codes(universe: Any) -> set[str]:
    """Extract fund codes from common strategy universe JSON shapes."""
    if isinstance(universe, dict):
        raw_codes = universe.get("fund_codes") or universe.get("codes") or []
        if isinstance(raw_codes, list):
            return {str(code) for code in raw_codes if code}
        if isinstance(raw_codes, str):
            return {raw_codes}
    if isinstance(universe, list):
        return {str(code) for code in universe if code}
    return set()


# ---------------------------------------------------------------------------
# Task: update_announcements
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.tasks.ingest.update_announcements",
    queue="ingest",
    bind=True,
    max_retries=2,
    soft_time_limit=20 * 60,
    time_limit=25 * 60,
)
def update_announcements(
    self,
    fund_code: str | None = None,
    fund_codes: list[str] | None = None,
    since_days: int = 30,
) -> dict[str, Any]:
    """Fetch and upsert fund announcements.

    Args:
        fund_code: Single fund code to update.
        fund_codes: List of fund codes to update.
            If neither is provided, updates all active funds.
        since_days: Number of days to look back for announcements
            (default 30).

    Returns:
        Summary dict with counts of success/failure.
    """
    return _run_async(
        _update_announcements_async(fund_code, fund_codes, since_days)
    )


async def _update_announcements_async(
    fund_code: str | None,
    fund_codes: list[str] | None,
    since_days: int,
) -> dict[str, Any]:
    """Async implementation of update_announcements."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    from app.data.models.fund_announcements import FundAnnouncement
    from app.data.session import get_sessionmaker

    codes = _resolve_fund_codes(fund_code, fund_codes)
    if not codes:
        codes = await _get_all_fund_codes()
        if not codes:
            log.info("ingest.update_announcements.no_funds")
            return {"success": 0, "failed": 0}

    provider = await _get_composite_provider()
    factory = get_sessionmaker()
    since = _today() - timedelta(days=since_days)

    success = 0
    failed = 0
    total_records = 0

    for code in codes:
        start_time = time.monotonic()
        try:
            announcements, source = await provider.fetch_announcements(code, since)
            elapsed = time.monotonic() - start_time

            if not announcements:
                INGEST_REQUESTS_TOTAL.labels(
                    provider=source, endpoint="announcements", status="success"
                ).inc()
                INGEST_LATENCY_SECONDS.labels(
                    provider=source, endpoint="announcements"
                ).observe(elapsed)
                success += 1
                continue

            # Insert announcements (no upsert since they have auto-increment PK)
            # We use a simple insert-ignore approach based on title + fund_code + date
            new_announcement_ids: list[int] = []
            async with factory() as session:
                for ann in announcements:
                    record = FundAnnouncement(
                        fund_code=ann.fund_code,
                        title=ann.title,
                        category=ann.category.value if ann.category else None,
                        publish_date=ann.publish_date,
                        content_url=ann.content_url,
                        parsed_data=ann.parsed_data,
                        requires_review=ann.requires_review,
                    )
                    session.add(record)
                    total_records += 1
                await session.commit()
                # Collect IDs of newly inserted announcements for async parsing
                for ann_record in session.new:
                    pass  # session.new is empty after commit
                # Refresh to get IDs (flush already happened at commit)

            # Trigger async LLM parsing for each new announcement
            # that doesn't already have a category assigned
            try:
                async with factory() as session:
                    from sqlalchemy import select

                    # Get recently inserted announcements without category
                    stmt = select(FundAnnouncement.id).where(
                        FundAnnouncement.fund_code == code,
                        FundAnnouncement.category.is_(None),
                        FundAnnouncement.publish_date >= since,
                    )
                    result = await session.execute(stmt)
                    new_announcement_ids = list(result.scalars().all())

                if new_announcement_ids:
                    from app.ai.use_cases.announcement_parse import (
                        trigger_announcement_parse,
                    )

                    for ann_id in new_announcement_ids:
                        trigger_announcement_parse(ann_id)
                    log.info(
                        "ingest.update_announcements.parse_triggered",
                        fund_code=code,
                        count=len(new_announcement_ids),
                    )
            except Exception as parse_exc:
                # Non-critical: parsing trigger failure should not
                # break the main ingestion flow
                log.warning(
                    "ingest.update_announcements.parse_trigger_failed",
                    fund_code=code,
                    error=str(parse_exc),
                )

            INGEST_REQUESTS_TOTAL.labels(
                provider=source, endpoint="announcements", status="success"
            ).inc()
            INGEST_LATENCY_SECONDS.labels(
                provider=source, endpoint="announcements"
            ).observe(elapsed)

            success += 1
            log.info(
                "ingest.update_announcements.ok",
                fund_code=code,
                source=source,
                records=len(announcements),
                elapsed=f"{elapsed:.2f}s",
            )

        except Exception as exc:
            elapsed = time.monotonic() - start_time
            failed += 1
            INGEST_REQUESTS_TOTAL.labels(
                provider="composite", endpoint="announcements", status="error"
            ).inc()
            log.error(
                "ingest.update_announcements.failed",
                fund_code=code,
                error=str(exc),
                elapsed=f"{elapsed:.2f}s",
            )

    return {
        "success": success,
        "failed": failed,
        "total": len(codes),
        "records_inserted": total_records,
    }


# ---------------------------------------------------------------------------
# Task: parse_announcement (async LLM parsing triggered after ingestion)
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.tasks.ingest.parse_announcement",
    queue="ai",
    bind=True,
    max_retries=2,
    soft_time_limit=60,
    time_limit=90,
)
def parse_announcement(self, announcement_id: int) -> dict[str, Any]:
    """Parse a single announcement using LLM classification.

    Triggered asynchronously after announcement ingestion. Updates the
    announcement record with category, parsed_data, and requires_review.

    Args:
        announcement_id: Database ID of the announcement to parse.

    Returns:
        Summary dict with parsing result.
    """
    return _run_async(_parse_announcement_async(announcement_id))


async def _parse_announcement_async(announcement_id: int) -> dict[str, Any]:
    """Async implementation of parse_announcement."""
    from sqlalchemy import select

    from app.ai.use_cases.announcement_parse import (
        AnnouncementParser,
    )
    from app.data.models.fund_announcements import FundAnnouncement
    from app.data.session import get_sessionmaker

    factory = get_sessionmaker()

    # Load the announcement
    async with factory() as session:
        stmt = select(FundAnnouncement).where(FundAnnouncement.id == announcement_id)
        result = await session.execute(stmt)
        announcement = result.scalar_one_or_none()

    if announcement is None:
        log.warning(
            "ingest.parse_announcement.not_found",
            announcement_id=announcement_id,
        )
        return {"status": "not_found", "announcement_id": announcement_id}

    # Skip if already parsed
    if announcement.category is not None:
        log.debug(
            "ingest.parse_announcement.already_parsed",
            announcement_id=announcement_id,
            category=announcement.category,
        )
        return {
            "status": "already_parsed",
            "announcement_id": announcement_id,
            "category": announcement.category,
        }

    # Build the LLM service (lightweight construction)
    try:
        from app.ai.service import LLMService, ProviderConfig
        from app.ai.providers import build_default_providers

        providers = build_default_providers()
        llm_service = LLMService(providers=providers)
    except Exception as exc:
        log.error(
            "ingest.parse_announcement.llm_init_failed",
            error=str(exc),
        )
        return {
            "status": "error",
            "announcement_id": announcement_id,
            "error": f"LLM init failed: {exc}",
        }

    # Parse the announcement
    parser = AnnouncementParser(llm_service)
    try:
        parse_result = await parser.parse(
            title=announcement.title or "",
            content="",  # Content URL only; parse from title
            fund_code=announcement.fund_code,
        )
    except Exception as exc:
        log.error(
            "ingest.parse_announcement.parse_failed",
            announcement_id=announcement_id,
            error=str(exc),
        )
        # Mark as requires_review on failure
        async with factory() as session:
            stmt = select(FundAnnouncement).where(
                FundAnnouncement.id == announcement_id
            )
            result = await session.execute(stmt)
            ann = result.scalar_one_or_none()
            if ann:
                ann.requires_review = True
                await session.commit()

        return {
            "status": "error",
            "announcement_id": announcement_id,
            "error": str(exc),
        }

    # Update the announcement record
    async with factory() as session:
        stmt = select(FundAnnouncement).where(
            FundAnnouncement.id == announcement_id
        )
        result = await session.execute(stmt)
        ann = result.scalar_one_or_none()
        if ann:
            ann.category = parse_result.category.value
            ann.parsed_data = {
                "effective_date": (
                    parse_result.effective_date.isoformat()
                    if parse_result.effective_date
                    else None
                ),
                "details": parse_result.details,
                "confidence": parse_result.confidence,
                "validation_issues": parse_result.validation_issues,
            }
            ann.requires_review = parse_result.requires_review
            await session.commit()

    log.info(
        "ingest.parse_announcement.ok",
        announcement_id=announcement_id,
        category=parse_result.category.value,
        requires_review=parse_result.requires_review,
        confidence=parse_result.confidence,
    )

    return {
        "status": "success",
        "announcement_id": announcement_id,
        "category": parse_result.category.value,
        "requires_review": parse_result.requires_review,
        "confidence": parse_result.confidence,
    }


__all__ = [
    "parse_announcement",
    "update_announcements",
    "update_daily_nav",
    "update_dividends",
    "update_fund_meta",
    "update_holdings",
]
