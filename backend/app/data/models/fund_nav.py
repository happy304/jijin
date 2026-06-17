"""ORM model for the ``fund_nav`` TimescaleDB hypertable.

Mirrors the DDL in design.md §2.1. The hypertable creation
(``SELECT create_hypertable('fund_nav', 'trade_date')``) is emitted
as a raw SQL statement in the Alembic migration rather than through
SQLAlchemy DDL, because TimescaleDB's ``create_hypertable`` is a
PostgreSQL function call, not a standard DDL construct.

Indexes
-------
* ``idx_nav_code_date`` — (fund_code, trade_date DESC) composite index
  for the most common query pattern: "latest N rows for a given fund".

Requirements: 2.6 (unit_nav, accum_nav, adj_nav fields), 2.7 (hypertable).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.data.models import Base


class FundNav(Base):
    """Single-day NAV record stored in a TimescaleDB hypertable.

    The composite primary key ``(fund_code, trade_date)`` is also the
    natural partition key for TimescaleDB's time-based chunking on
    ``trade_date``.
    """

    __tablename__ = "fund_nav"

    # ------------------------------------------------------------------
    # Primary key (composite)
    # ------------------------------------------------------------------
    fund_code: Mapped[str] = mapped_column(
        String(10),
        primary_key=True,
        comment="Fund code — FK to funds.code (enforced at app layer)",
    )
    trade_date: Mapped[date] = mapped_column(
        Date,
        primary_key=True,
        comment="Trading date (T); TimescaleDB partition dimension",
    )

    # ------------------------------------------------------------------
    # NAV fields (requirement 2.6)
    # ------------------------------------------------------------------
    unit_nav: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 6),
        nullable=True,
        comment="Unit net asset value (CNY per share)",
    )
    accum_nav: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 6),
        nullable=True,
        comment="Cumulative net asset value since inception",
    )
    adj_nav: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 6),
        nullable=True,
        comment="Dividend-adjusted (forward-adjusted) NAV; computed by adj_nav service",
    )
    daily_return: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 6),
        nullable=True,
        comment="Daily return as a decimal fraction (e.g. 0.0123 = +1.23%)",
    )

    # ------------------------------------------------------------------
    # Status & provenance
    # ------------------------------------------------------------------
    status: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        server_default="normal",
        comment="Per-day status: normal/suspended/limited",
    )
    source: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        comment="Data source identifier",
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        server_default="NOW()",
        comment="Row creation timestamp (UTC)",
    )

    # ------------------------------------------------------------------
    # Table-level indexes (design §2.1)
    # ------------------------------------------------------------------
    __table_args__ = (
        # Composite descending index for "latest NAV for a fund" queries.
        Index("idx_nav_code_date", "fund_code", "trade_date"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<FundNav fund_code={self.fund_code!r} "
            f"trade_date={self.trade_date!r} "
            f"unit_nav={self.unit_nav!r}>"
        )
