"""Repository for the ``fund_holdings`` table (quarterly snapshots).

``HoldingRepo`` manages the ``FundHolding`` ORM model. The date column
is ``report_date`` (quarter-end).

Requirements: 2.6, 1.11
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models.fund_holdings import FundHolding
from app.data.repositories.base import BaseRepo


def _is_sqlite(session: AsyncSession) -> bool:
    bind = session.get_bind()
    return bind.dialect.name == "sqlite"


class HoldingRepo(BaseRepo[FundHolding]):
    """CRUD + upsert operations for the ``fund_holdings`` table.

    The composite primary key is ``(fund_code, report_date, stock_code)``.
    """

    # ------------------------------------------------------------------
    # upsert_many
    # ------------------------------------------------------------------

    async def upsert_many(
        self,
        session: AsyncSession,
        records: list[dict[str, Any]],
    ) -> int:
        """Upsert a batch of holding position rows.

        On conflict on ``(fund_code, report_date, stock_code)`` all
        non-PK columns are updated.
        """
        if not records:
            return 0

        pk_cols = {"fund_code", "report_date", "stock_code"}

        # 去重：同一批中可能有重复主键（数据源返回重复股票），保留最后一条
        seen: dict[tuple, int] = {}
        for idx, rec in enumerate(records):
            key = (rec["fund_code"], rec["report_date"], rec["stock_code"])
            seen[key] = idx
        if len(seen) < len(records):
            records = [records[i] for i in sorted(seen.values())]

        if _is_sqlite(session):
            stmt = sqlite_insert(FundHolding).values(records)
            update_cols = {c: stmt.excluded[c] for c in records[0] if c not in pk_cols}
            stmt = stmt.on_conflict_do_update(
                index_elements=["fund_code", "report_date", "stock_code"],
                set_=update_cols,
            )
        else:
            stmt = pg_insert(FundHolding).values(records)
            update_cols = {c: stmt.excluded[c] for c in records[0] if c not in pk_cols}
            stmt = stmt.on_conflict_do_update(
                index_elements=["fund_code", "report_date", "stock_code"],
                set_=update_cols,
            )

        result = await session.execute(stmt)
        return result.rowcount

    # ------------------------------------------------------------------
    # get_by_date_range
    # ------------------------------------------------------------------

    async def get_by_date_range(
        self,
        session: AsyncSession,
        fund_code: str,
        start: date,
        end: date,
    ) -> list[FundHolding]:
        """Return holding rows for *fund_code* with ``report_date`` in [start, end]."""
        q = (
            select(FundHolding)
            .where(
                FundHolding.fund_code == fund_code,
                FundHolding.report_date >= start,
                FundHolding.report_date <= end,
            )
            .order_by(FundHolding.report_date, FundHolding.stock_code)
        )
        result = await session.execute(q)
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # latest_date
    # ------------------------------------------------------------------

    async def latest_date(
        self,
        session: AsyncSession,
        fund_code: str,
    ) -> date | None:
        """Return the most recent ``report_date`` stored for *fund_code*."""
        from sqlalchemy import func

        q = select(func.max(FundHolding.report_date)).where(
            FundHolding.fund_code == fund_code
        )
        result = await session.execute(q)
        value = result.scalar_one_or_none()
        if value is None:
            return None
        if isinstance(value, date):
            return value
        from datetime import datetime
        return datetime.strptime(str(value), "%Y-%m-%d").date()

    # ------------------------------------------------------------------
    # missing_dates
    # ------------------------------------------------------------------

    async def missing_dates(
        self,
        session: AsyncSession,
        fund_code: str,
        expected_dates: list[date],
    ) -> list[date]:
        """Return report dates from *expected_dates* absent in ``fund_holdings``."""
        if not expected_dates:
            return []

        q = (
            select(FundHolding.report_date)
            .where(
                FundHolding.fund_code == fund_code,
                FundHolding.report_date.in_(expected_dates),
            )
            .distinct()
        )
        result = await session.execute(q)
        stored_raw = result.scalars().all()

        stored: set[date] = set()
        for v in stored_raw:
            if isinstance(v, date):
                stored.add(v)
            else:
                from datetime import datetime
                stored.add(datetime.strptime(str(v), "%Y-%m-%d").date())

        return [d for d in expected_dates if d not in stored]

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    async def get_snapshot(
        self,
        session: AsyncSession,
        fund_code: str,
        report_date: date,
    ) -> list[FundHolding]:
        """Return all holding positions for a specific fund/quarter."""
        q = (
            select(FundHolding)
            .where(
                FundHolding.fund_code == fund_code,
                FundHolding.report_date == report_date,
            )
            .order_by(FundHolding.stock_code)
        )
        result = await session.execute(q)
        return list(result.scalars().all())
