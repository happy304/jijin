"""Repository for the ``funds`` table (fund metadata).

``FundRepo`` manages the ``Fund`` ORM model. Because ``funds`` is a
standard relational table (not a time-series), the date-range and
missing-dates methods operate on ``updated_at`` (cast to date) rather
than a dedicated date column.

Requirements: 2.6, 1.11
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models.funds import Fund
from app.data.repositories.base import BaseRepo


def _is_sqlite(session: AsyncSession) -> bool:
    """Return True when the session is backed by SQLite (test mode)."""
    bind = session.get_bind()
    return bind.dialect.name == "sqlite"


class FundRepo(BaseRepo[Fund]):
    """CRUD + upsert operations for the ``funds`` table.

    ``upsert_many`` uses PostgreSQL's ``INSERT … ON CONFLICT DO UPDATE``
    (``pg_insert``) in production and SQLite's equivalent in tests.
    """

    # ------------------------------------------------------------------
    # upsert_many
    # ------------------------------------------------------------------

    async def upsert_many(
        self,
        session: AsyncSession,
        records: list[dict[str, Any]],
    ) -> int:
        """Upsert a batch of fund metadata rows.

        The primary key is ``code``. On conflict the row is updated with
        all supplied fields except ``code`` itself.
        """
        if not records:
            return 0

        if _is_sqlite(session):
            stmt = sqlite_insert(Fund).values(records)
            update_cols = {
                c: stmt.excluded[c]
                for c in records[0]
                if c != "code"
            }
            stmt = stmt.on_conflict_do_update(index_elements=["code"], set_=update_cols)
        else:
            stmt = pg_insert(Fund).values(records)
            update_cols = {
                c: stmt.excluded[c]
                for c in records[0]
                if c != "code"
            }
            stmt = stmt.on_conflict_do_update(index_elements=["code"], set_=update_cols)

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
    ) -> list[Fund]:
        """Return funds whose ``updated_at`` date falls in [start, end].

        For ``funds`` the "date" dimension is the last-updated timestamp.
        ``fund_code`` is treated as a prefix filter when non-empty; pass
        an empty string to query all funds updated in the window.
        """
        q = select(Fund).where(
            func.date(Fund.updated_at) >= start,
            func.date(Fund.updated_at) <= end,
        )
        if fund_code:
            q = q.where(Fund.code == fund_code)
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
        """Return the date of the most recent ``updated_at`` for *fund_code*."""
        q = select(func.max(func.date(Fund.updated_at))).where(Fund.code == fund_code)
        result = await session.execute(q)
        value = result.scalar_one_or_none()
        if value is None:
            return None
        if isinstance(value, date):
            return value
        # SQLite returns a string
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
        """Return dates from *expected_dates* not present in ``updated_at``.

        For the ``funds`` table this is mainly useful to detect whether a
        fund's metadata has ever been fetched on a given date.
        """
        if not expected_dates:
            return []

        q = select(func.date(Fund.updated_at)).where(Fund.code == fund_code)
        result = await session.execute(q)
        stored_raw = result.scalars().all()

        stored: set[date] = set()
        for v in stored_raw:
            if v is None:
                continue
            if isinstance(v, date):
                stored.add(v)
            else:
                from datetime import datetime
                stored.add(datetime.strptime(str(v), "%Y-%m-%d").date())

        return [d for d in expected_dates if d not in stored]

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    async def get_by_code(self, session: AsyncSession, code: str) -> Fund | None:
        """Fetch a single fund by its primary key."""
        result = await session.execute(select(Fund).where(Fund.code == code))
        return result.scalar_one_or_none()

    async def get_all(self, session: AsyncSession) -> list[Fund]:
        """Return all funds (use with care on large datasets)."""
        result = await session.execute(select(Fund))
        return list(result.scalars().all())
