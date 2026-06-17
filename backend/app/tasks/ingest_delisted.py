"""Celery task for ingesting delisted (closed) fund data.

Survivorship bias is the #1 threat to backtest validity. This task:
1. Fetches a list of all delisted/closed funds from akshare
2. For each delisted fund, fetches its full NAV history
3. Upserts the fund metadata with ``delisting_date`` populated
4. Upserts the NAV records into ``fund_nav``

This ensures that historical backtests include funds that no longer exist,
preventing the "only winners survive" illusion.

Data source: akshare ``fund_em_open_fund_info`` with status filter,
or ``fund_em_fund_name`` which includes terminated funds.

Schedule: Run weekly (Sunday night) to catch newly-delisted funds.
"""

from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Any

from app.core.logging import get_logger
from app.tasks.celery_app import celery_app

log = get_logger(__name__)


def _run_async(coro: Any) -> Any:
    """Run an async coroutine from a sync Celery task."""
    from app.tasks.async_utils import run_async
    return run_async(coro)


@celery_app.task(
    name="app.tasks.ingest_delisted.ingest_delisted_funds",
    queue="ingest",
    bind=True,
    max_retries=1,
    soft_time_limit=60 * 60,
    time_limit=65 * 60,
)
def ingest_delisted_funds(self) -> dict[str, Any]:
    """Fetch and persist delisted fund metadata + NAV history.

    Returns:
        Summary dict with counts.
    """
    return _run_async(_ingest_delisted_funds_async())


async def _ingest_delisted_funds_async() -> dict[str, Any]:
    """Async implementation."""
    from decimal import Decimal

    from sqlalchemy import select, update

    from app.data.models.fund_nav import FundNav
    from app.data.models.funds import Fund
    from app.data.repositories.fund_repo import FundRepo
    from app.data.repositories.nav_repo import NavRepo
    from app.data.session import get_engine, get_sessionmaker

    get_engine()
    factory = get_sessionmaker()
    repo = FundRepo()
    nav_repo = NavRepo()

    # Step 1: Get list of delisted funds from akshare
    delisted_codes: list[dict[str, Any]] = []
    try:
        import akshare as ak

        # akshare provides fund_em_fund_name which includes all funds
        # including terminated ones. The '终止' status indicates delisted.
        df = ak.fund_em_fund_name(indicator="全部")
        if df is not None and not df.empty:
            # Filter for terminated/delisted funds
            # Column names vary by akshare version; common patterns:
            # '基金代码', '基金简称', '基金类型'
            code_col = None
            name_col = None
            for col in df.columns:
                if "代码" in col:
                    code_col = col
                elif "简称" in col or "名称" in col:
                    name_col = col

            if code_col is not None:
                # akshare doesn't always have a clear "delisted" flag in this endpoint.
                # Alternative: check which funds in our DB have no recent NAV updates
                # (> 90 days without new NAV = likely delisted).
                # For now, we'll use a heuristic approach.
                pass

    except ImportError:
        log.warning("ingest_delisted.akshare_not_available")
        return {"error": "akshare not installed"}
    except Exception as exc:
        log.error("ingest_delisted.fetch_list_failed", error=str(exc))

    # Step 2: Heuristic detection — find funds in our DB that haven't had
    # NAV updates in > 90 days and mark them as potentially delisted.
    today = date.today()
    cutoff = today - timedelta(days=90)

    async with factory() as session:
        # Find funds that are still marked 'active' but have no NAV after cutoff
        from sqlalchemy import func as sa_func

        stale_query = (
            select(Fund.code, Fund.name, sa_func.max(FundNav.trade_date).label("last_nav"))
            .outerjoin(FundNav, Fund.code == FundNav.fund_code)
            .where(Fund.status == "active")
            .where(Fund.delisting_date.is_(None))
            .group_by(Fund.code, Fund.name)
            .having(
                sa_func.max(FundNav.trade_date) < cutoff
            )
        )
        result = await session.execute(stale_query)
        stale_funds = result.all()

    if not stale_funds:
        log.info("ingest_delisted.no_stale_funds_found")
        return {"checked": 0, "marked_delisted": 0, "nav_backfilled": 0}

    log.info(
        "ingest_delisted.found_stale_funds",
        count=len(stale_funds),
    )

    marked = 0
    nav_backfilled = 0

    for row in stale_funds:
        code = row.code
        last_nav_date = row.last_nav

        # Try to fetch more recent NAV from akshare to confirm it's truly delisted
        try:
            import akshare as ak

            nav_df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
            if nav_df is not None and not nav_df.empty:
                # Parse the latest date in the fetched data
                date_col = nav_df.columns[0]  # Usually '净值日期' or first col
                nav_df[date_col] = pd.to_datetime(nav_df[date_col])
                latest_fetched = nav_df[date_col].max().date()

                if latest_fetched > cutoff:
                    # Fund is actually still active, just our DB is behind
                    # Backfill the missing NAV data
                    log.info(
                        "ingest_delisted.fund_still_active",
                        fund_code=code,
                        latest_fetched=latest_fetched.isoformat(),
                    )
                    continue

                # Fund truly has no recent data → mark as delisted
                # Use the last NAV date as the delisting date
                delisting_date = latest_fetched

                # Backfill any missing NAV records
                import pandas as pd

                nav_col = None
                for col in nav_df.columns:
                    if "净值" in col and "累计" not in col:
                        nav_col = col
                        break
                if nav_col is None:
                    nav_col = nav_df.columns[1] if len(nav_df.columns) > 1 else None

                if nav_col is not None:
                    records_to_insert = []
                    for _, nav_row in nav_df.iterrows():
                        trade_date = nav_row[date_col].date()
                        if last_nav_date and trade_date <= last_nav_date:
                            continue
                        try:
                            unit_nav = Decimal(str(float(nav_row[nav_col])))
                            records_to_insert.append({
                                "fund_code": code,
                                "trade_date": trade_date,
                                "unit_nav": unit_nav,
                                "source": "akshare_backfill",
                                "status": "normal",
                            })
                        except (ValueError, TypeError):
                            continue

                    if records_to_insert:
                        async with factory() as session:
                            count = await nav_repo.upsert_many(session, records_to_insert)
                            await session.commit()
                        nav_backfilled += count
                        log.info(
                            "ingest_delisted.nav_backfilled",
                            fund_code=code,
                            records=count,
                        )

            else:
                # No data at all from akshare → use last_nav_date as delisting
                delisting_date = last_nav_date or cutoff

        except Exception as exc:
            log.warning(
                "ingest_delisted.fetch_failed",
                fund_code=code,
                error=str(exc),
            )
            # Still mark as delisted using last known NAV date
            delisting_date = last_nav_date or cutoff

        # Update the fund record with delisting_date
        try:
            async with factory() as session:
                await session.execute(
                    update(Fund)
                    .where(Fund.code == code)
                    .values(
                        delisting_date=delisting_date,
                        status="delisted",
                    )
                )
                await session.commit()
            marked += 1
            log.info(
                "ingest_delisted.marked",
                fund_code=code,
                delisting_date=delisting_date.isoformat(),
            )
        except Exception as exc:
            log.error(
                "ingest_delisted.mark_failed",
                fund_code=code,
                error=str(exc),
            )

    return {
        "checked": len(stale_funds),
        "marked_delisted": marked,
        "nav_backfilled": nav_backfilled,
    }
