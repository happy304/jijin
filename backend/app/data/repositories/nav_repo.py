"""Repository for the ``fund_nav`` table (daily NAV records).

``NavRepo`` manages the ``FundNav`` ORM model. The date column is
``trade_date``, which is also the TimescaleDB partition dimension.

Requirements: 2.6, 1.11
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models.fund_nav import FundNav
from app.data.repositories.base import BaseRepo


def _is_sqlite(session: AsyncSession) -> bool:
    bind = session.get_bind()
    return bind.dialect.name == "sqlite"


class NavRepo(BaseRepo[FundNav]):
    """CRUD + upsert operations for the ``fund_nav`` table.

    The composite primary key is ``(fund_code, trade_date)``.
    """

    # ------------------------------------------------------------------
    # upsert_many
    # ------------------------------------------------------------------

    async def upsert_many(
        self,
        session: AsyncSession,
        records: list[dict[str, Any]],
    ) -> int:
        """Upsert a batch of NAV records.

        On conflict on ``(fund_code, trade_date)`` all non-PK columns
        are updated.
        """
        if not records:
            return 0

        pk_cols = {"fund_code", "trade_date"}

        if _is_sqlite(session):
            stmt = sqlite_insert(FundNav).values(records)
            update_cols = {c: stmt.excluded[c] for c in records[0] if c not in pk_cols}
            stmt = stmt.on_conflict_do_update(
                index_elements=["fund_code", "trade_date"],
                set_=update_cols,
            )
        else:
            stmt = pg_insert(FundNav).values(records)
            update_cols = {c: stmt.excluded[c] for c in records[0] if c not in pk_cols}
            stmt = stmt.on_conflict_do_update(
                index_elements=["fund_code", "trade_date"],
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
    ) -> list[FundNav]:
        """Return NAV rows for *fund_code* with ``trade_date`` in [start, end]."""
        q = (
            select(FundNav)
            .where(
                FundNav.fund_code == fund_code,
                FundNav.trade_date >= start,
                FundNav.trade_date <= end,
            )
            .order_by(FundNav.trade_date)
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
        """Return the most recent ``trade_date`` stored for *fund_code*."""
        from sqlalchemy import func

        q = select(func.max(FundNav.trade_date)).where(FundNav.fund_code == fund_code)
        result = await session.execute(q)
        value = result.scalar_one_or_none()
        if value is None:
            return None
        if isinstance(value, date):
            return value
        # SQLite may return a string
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
        """Return dates from *expected_dates* absent in ``fund_nav``."""
        if not expected_dates:
            return []

        q = select(FundNav.trade_date).where(
            FundNav.fund_code == fund_code,
            FundNav.trade_date.in_(expected_dates),
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
