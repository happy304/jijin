"""ORM model for the ``fund_holdings`` table.

Stores quarterly holding snapshots (top-N positions per fund per
report period). Mirrors the DDL in design.md §2.1 and the Pydantic
DTO ``HoldingSnapshot`` / ``HoldingPosition``.

The composite primary key ``(fund_code, report_date, stock_code)``
ensures one row per holding line per quarter.

Requirements: 2.3 (holding weight validation at app layer), 2.8.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Date, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.data.models import Base


class FundHolding(Base):
    """A single holding position within a quarterly snapshot.

    One ``FundHolding`` row represents one security held by one fund
    as of one report date. The full snapshot for a fund/quarter is the
    set of all rows sharing the same ``(fund_code, report_date)``.
    """

    __tablename__ = "fund_holdings"

    # ------------------------------------------------------------------
    # Primary key (composite)
    # ------------------------------------------------------------------
    fund_code: Mapped[str] = mapped_column(
        String(10),
        primary_key=True,
        comment="Fund code — FK to funds.code (enforced at app layer)",
    )
    report_date: Mapped[date] = mapped_column(
        Date,
        primary_key=True,
        comment="Quarter-end report date",
    )
    stock_code: Mapped[str] = mapped_column(
        String(20),
        primary_key=True,
        comment="Underlying security code",
    )

    # ------------------------------------------------------------------
    # Position details
    # ------------------------------------------------------------------
    stock_name: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="Underlying security name",
    )
    weight: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 4),
        nullable=True,
        comment="Position weight as fraction of NAV (e.g. 0.05 = 5%)",
    )
    shares: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 2),
        nullable=True,
        comment="Number of shares held",
    )
    market_value: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 2),
        nullable=True,
        comment="Market value in CNY",
    )
    industry: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="Industry classification",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<FundHolding fund_code={self.fund_code!r} "
            f"report_date={self.report_date!r} "
            f"stock_code={self.stock_code!r} "
            f"weight={self.weight!r}>"
        )
