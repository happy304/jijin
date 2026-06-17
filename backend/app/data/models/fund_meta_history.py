"""ORM model for the ``fund_meta_history`` table.

Stores point-in-time (PIT) snapshots of fund metadata. The current ``funds``
table is the live, mutable view; this table records every observed change
to the fields that materially affect backtesting and research:

- ``manager_id`` — current portfolio manager(s)
- ``company_id`` — fund company (in case of mergers / re-licensing)
- ``fund_size``  — AUM (regulatory filings)
- ``status``     — active / suspended / delisted
- ``is_purchasable`` and ``purchase_limit``
- ``benchmark``  — declared benchmark

PIT semantics: each row's ``effective_date`` is the date from which the
metadata snapshot becomes the "as-known truth". To answer "what was the
manager of fund X on date D", select the row with
``fund_code = X AND effective_date <= D`` ordered by ``effective_date DESC``
(top 1).

The ingestion task is responsible for inserting a new row whenever any
tracked field changes versus the latest snapshot. Initial backfill can
seed a single row per fund with ``effective_date = inception_date``.

Indexes
-------
- Primary key composite (fund_code, effective_date) gives O(log n) PIT
  lookups via descending traversal.
- ``idx_fmh_effective`` accelerates "all snapshots up to date D" queries.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Index, Numeric, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.data.models import Base


class FundMetaHistory(Base):
    """Point-in-time snapshot of a fund's metadata.

    Each row represents the metadata as known *from* ``effective_date`` until
    the next-newer row for the same ``fund_code`` (or until present if last).
    """

    __tablename__ = "fund_meta_history"

    fund_code: Mapped[str] = mapped_column(
        String(10),
        primary_key=True,
        comment="Fund code (matches funds.code)",
    )
    effective_date: Mapped[date] = mapped_column(
        Date,
        primary_key=True,
        comment="The date from which this snapshot becomes truth",
    )

    # ------------------------------------------------------------------
    # Tracked metadata fields (snapshot)
    # ------------------------------------------------------------------
    manager_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Manager(s) at this point in time. Multiple managers comma-separated.",
    )
    company_id: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        comment="Fund management company at this point in time",
    )
    fund_size: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2),
        nullable=True,
        comment="Fund AUM in CNY at this point in time",
    )
    status: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        comment="Lifecycle status as of effective_date",
    )
    is_purchasable: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
        comment="Whether subscriptions were open as of effective_date",
    )
    purchase_limit: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2),
        nullable=True,
        comment="Single-transaction cap as of effective_date",
    )
    benchmark: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Declared benchmark at this point in time",
    )
    management_fee: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 4),
        nullable=True,
        comment="Management fee rate at this point in time",
    )

    # ------------------------------------------------------------------
    # Provenance
    # ------------------------------------------------------------------
    source: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        comment="Data source for this snapshot (e.g. 'eastmoney', 'manual')",
    )
    recorded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        # CURRENT_TIMESTAMP works on both PostgreSQL and SQLite (used in tests).
        # NOW() would fail under SQLite — keep this dialect-portable.
        server_default=text("CURRENT_TIMESTAMP"),
        comment="UTC timestamp when this snapshot was inserted",
    )

    __table_args__ = (
        Index("idx_fmh_effective", "effective_date"),
        Index("idx_fmh_fund_effective", "fund_code", "effective_date"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<FundMetaHistory code={self.fund_code!r} "
            f"effective_date={self.effective_date} status={self.status!r}>"
        )
