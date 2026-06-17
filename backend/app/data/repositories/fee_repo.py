"""Repository for the ``fund_fees`` table (tiered fee schedules).

``FeeRepo`` manages the ``FundFee`` ORM model. Because fee tiers are
not time-series data, the date-range and missing-dates methods are
adapted: they operate on a conceptual "effective date" that is not
stored in the table. For ``FeeRepo`` these methods are provided for
interface completeness but have limited practical use — the primary
operations are ``upsert_many`` and the fee-lookup helpers.

Requirements: 2.6, 1.11
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models.fund_fees import FundFee
from app.data.repositories.base import BaseRepo


def _is_sqlite(session: AsyncSession) -> bool:
    bind = session.get_bind()
    return bind.dialect.name == "sqlite"


class FeeRepo(BaseRepo[FundFee]):
    """CRUD + upsert operations for the ``fund_fees`` table.

    The composite primary key is
    ``(fund_code, fee_type, min_amount, min_holding_days)``.

    Note on date methods
    --------------------
    ``fund_fees`` has no date column. ``get_by_date_range``,
    ``latest_date``, and ``missing_dates`` are implemented as no-ops /
    empty returns to satisfy the ``BaseRepo`` interface. Callers that
    need fee data should use ``get_tiers`` or ``get_applicable_tier``
    instead.
    """

    # ------------------------------------------------------------------
    # upsert_many
    # ------------------------------------------------------------------

    async def upsert_many(
        self,
        session: AsyncSession,
        records: list[dict[str, Any]],
    ) -> int:
        """Upsert a batch of fee tier rows.

        On conflict on the composite PK all non-PK columns are updated.
        """
        if not records:
            return 0

        pk_cols = {"fund_code", "fee_type", "min_amount", "min_holding_days"}

        if _is_sqlite(session):
            stmt = sqlite_insert(FundFee).values(records)
            update_cols = {c: stmt.excluded[c] for c in records[0] if c not in pk_cols}
            stmt = stmt.on_conflict_do_update(
                index_elements=["fund_code", "fee_type", "min_amount", "min_holding_days"],
                set_=update_cols,
            )
        else:
            stmt = pg_insert(FundFee).values(records)
            update_cols = {c: stmt.excluded[c] for c in records[0] if c not in pk_cols}
            stmt = stmt.on_conflict_do_update(
                index_elements=["fund_code", "fee_type", "min_amount", "min_holding_days"],
                set_=update_cols,
            )

        result = await session.execute(stmt)
        return result.rowcount

    # ------------------------------------------------------------------
    # get_by_date_range  (no-op — fees have no date column)
    # ------------------------------------------------------------------

    async def get_by_date_range(
        self,
        session: AsyncSession,
        fund_code: str,
        start: date,
        end: date,
    ) -> list[FundFee]:
        """Return all fee tiers for *fund_code* (date range is ignored).

        ``fund_fees`` has no date column; this method returns all tiers
        for the fund regardless of the supplied date window.
        """
        return await self.get_tiers(session, fund_code)

    # ------------------------------------------------------------------
    # latest_date  (no-op — fees have no date column)
    # ------------------------------------------------------------------

    async def latest_date(
        self,
        session: AsyncSession,
        fund_code: str,
    ) -> date | None:
        """Always returns ``None`` — ``fund_fees`` has no date column."""
        return None

    # ------------------------------------------------------------------
    # missing_dates  (no-op — fees have no date column)
    # ------------------------------------------------------------------

    async def missing_dates(
        self,
        session: AsyncSession,
        fund_code: str,
        expected_dates: list[date],
    ) -> list[date]:
        """Always returns empty list — ``fund_fees`` has no date column."""
        return []

    # ------------------------------------------------------------------
    # Domain-specific helpers
    # ------------------------------------------------------------------

    async def get_tiers(
        self,
        session: AsyncSession,
        fund_code: str,
        fee_type: str | None = None,
    ) -> list[FundFee]:
        """Return all fee tiers for *fund_code*, optionally filtered by type."""
        q = select(FundFee).where(FundFee.fund_code == fund_code)
        if fee_type is not None:
            q = q.where(FundFee.fee_type == fee_type)
        q = q.order_by(FundFee.fee_type, FundFee.min_amount, FundFee.min_holding_days)
        result = await session.execute(q)
        return list(result.scalars().all())

    async def get_applicable_tier(
        self,
        session: AsyncSession,
        fund_code: str,
        fee_type: str,
        amount: Decimal | None = None,
        holding_days: int | None = None,
    ) -> FundFee | None:
        """Return the single fee tier that applies to the given parameters.

        For ``subscribe`` tiers, pass *amount* (CNY).
        For ``redeem`` tiers, pass *holding_days*.

        The matching logic:
        * ``min_amount <= amount < max_amount``  (or ``max_amount IS NULL``)
        * ``min_holding_days <= holding_days < max_holding_days``
          (or ``max_holding_days IS NULL``)
        """
        from sqlalchemy import or_

        q = select(FundFee).where(
            FundFee.fund_code == fund_code,
            FundFee.fee_type == fee_type,
        )

        if amount is not None:
            q = q.where(
                FundFee.min_amount <= amount,
                or_(FundFee.max_amount.is_(None), FundFee.max_amount > amount),
            )

        if holding_days is not None:
            q = q.where(
                FundFee.min_holding_days <= holding_days,
                or_(
                    FundFee.max_holding_days.is_(None),
                    FundFee.max_holding_days > holding_days,
                ),
            )

        result = await session.execute(q.limit(1))
        return result.scalar_one_or_none()
