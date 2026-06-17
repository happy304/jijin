"""Point-in-time fund metadata lookup service.

Wraps queries against ``fund_meta_history`` so callers (backtest engine,
factor research, attribution reports) can ask "what did fund X look like
on date D?" without re-implementing the SELECT logic.

Backward compatibility: when no PIT history exists for a fund, falls back
to the live ``funds`` row, treating its current values as if they applied
throughout the history. The fallback emits a warning so users can spot
funds that need PIT backfill.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models.fund_meta_history import FundMetaHistory
from app.data.models.funds import Fund

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PITFundMeta:
    """Point-in-time fund metadata snapshot.

    Mirrors the subset of fields that affect backtest behaviour and
    research queries. Use ``source`` to distinguish PIT history rows
    from "live fallback" rows.
    """

    fund_code: str
    as_of: date
    manager_id: str | None = None
    company_id: str | None = None
    fund_size: Decimal | None = None
    status: str | None = None
    is_purchasable: bool | None = None
    purchase_limit: Decimal | None = None
    benchmark: str | None = None
    management_fee: Decimal | None = None
    source: str = "history"  # "history" / "live_fallback" / "missing"


async def get_fund_meta_at(
    session: AsyncSession,
    fund_code: str,
    as_of: date,
    allow_live_fallback: bool = True,
) -> PITFundMeta:
    """Return the metadata snapshot active for ``fund_code`` on ``as_of``.

    Algorithm:
        1. Look up the most recent ``fund_meta_history`` row with
           ``effective_date <= as_of``.
        2. If found, return it (source='history').
        3. Otherwise, if allow_live_fallback=True, fall back to the live ``funds`` row
           (source='live_fallback').
        4. If strict mode is enabled or the live row is missing, return a placeholder
           (source='missing').

    Args:
        session: Async SQLAlchemy session.
        fund_code: Fund code (e.g. '000001').
        as_of: The date to look back to.

    Returns:
        PITFundMeta. Always returns a value — never raises for "not found".
    """
    # 1. PIT history lookup (most recent <= as_of)
    stmt = (
        select(FundMetaHistory)
        .where(
            FundMetaHistory.fund_code == fund_code,
            FundMetaHistory.effective_date <= as_of,
        )
        .order_by(FundMetaHistory.effective_date.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    snap = result.scalar_one_or_none()

    if snap is not None:
        return PITFundMeta(
            fund_code=snap.fund_code,
            as_of=as_of,
            manager_id=snap.manager_id,
            company_id=snap.company_id,
            fund_size=snap.fund_size,
            status=snap.status,
            is_purchasable=snap.is_purchasable,
            purchase_limit=snap.purchase_limit,
            benchmark=snap.benchmark,
            management_fee=snap.management_fee,
            source="history",
        )

    if not allow_live_fallback:
        logger.warning(
            "fund_pit.strict_missing_history",
            extra={"fund_code": fund_code, "as_of": as_of.isoformat()},
        )
        return PITFundMeta(fund_code=fund_code, as_of=as_of, source="missing")

    # 2. Live row fallback
    live_stmt = select(Fund).where(Fund.code == fund_code)
    live_result = await session.execute(live_stmt)
    live = live_result.scalar_one_or_none()

    if live is not None:
        logger.warning(
            "fund_pit.fallback_to_live",
            extra={"fund_code": fund_code, "as_of": as_of.isoformat()},
        )
        return PITFundMeta(
            fund_code=fund_code,
            as_of=as_of,
            manager_id=None,  # Fund table doesn't carry manager
            company_id=live.company_id,
            fund_size=None,
            status=live.status,
            is_purchasable=live.is_purchasable,
            purchase_limit=live.purchase_limit,
            benchmark=live.benchmark,
            management_fee=live.management_fee,
            source="live_fallback",
        )

    # 3. Truly missing
    return PITFundMeta(fund_code=fund_code, as_of=as_of, source="missing")


async def get_fund_meta_at_batch(
    session: AsyncSession,
    fund_codes: Iterable[str],
    as_of: date,
    allow_live_fallback: bool = True,
) -> dict[str, PITFundMeta]:
    """Batch version of ``get_fund_meta_at``.

    Issues a single SQL query against ``fund_meta_history`` for all funds,
    then a single fallback against ``funds``. Returns a dict keyed by
    fund_code.

    Args:
        session: Async SQLAlchemy session.
        fund_codes: Iterable of fund codes.
        as_of: The date to look back to.

    Returns:
        Dict mapping fund_code → PITFundMeta. Codes not found anywhere
        are still present with ``source='missing'``.
    """
    codes = list(fund_codes)
    if not codes:
        return {}

    # 1. Bulk PIT history query: for each code, find the latest snapshot <= as_of.
    # Strategy: select all history rows for these codes with effective_date <= as_of,
    # then keep only the most recent per code in Python.
    history_stmt = (
        select(FundMetaHistory)
        .where(
            FundMetaHistory.fund_code.in_(codes),
            FundMetaHistory.effective_date <= as_of,
        )
        .order_by(FundMetaHistory.effective_date.desc())
    )
    history_result = await session.execute(history_stmt)
    history_rows = history_result.scalars().all()

    by_code: dict[str, PITFundMeta] = {}
    for row in history_rows:
        # First (most recent) row per code wins because we ordered DESC.
        if row.fund_code not in by_code:
            by_code[row.fund_code] = PITFundMeta(
                fund_code=row.fund_code,
                as_of=as_of,
                manager_id=row.manager_id,
                company_id=row.company_id,
                fund_size=row.fund_size,
                status=row.status,
                is_purchasable=row.is_purchasable,
                purchase_limit=row.purchase_limit,
                benchmark=row.benchmark,
                management_fee=row.management_fee,
                source="history",
            )

    # 2. Live fallback for codes still missing
    missing_codes = [c for c in codes if c not in by_code]
    if missing_codes and allow_live_fallback:
        live_stmt = select(Fund).where(Fund.code.in_(missing_codes))
        live_result = await session.execute(live_stmt)
        for live in live_result.scalars().all():
            by_code[live.code] = PITFundMeta(
                fund_code=live.code,
                as_of=as_of,
                company_id=live.company_id,
                status=live.status,
                is_purchasable=live.is_purchasable,
                purchase_limit=live.purchase_limit,
                benchmark=live.benchmark,
                management_fee=live.management_fee,
                source="live_fallback",
            )

    # 3. Anything still missing → placeholder
    for code in codes:
        if code not in by_code:
            by_code[code] = PITFundMeta(
                fund_code=code,
                as_of=as_of,
                source="missing",
            )

    return by_code


__all__ = [
    "PITFundMeta",
    "get_fund_meta_at",
    "get_fund_meta_at_batch",
]
