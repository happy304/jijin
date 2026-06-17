"""ORM model for the ``fund_dividends`` table.

Stores dividend and split events used by the adjusted-NAV service
(task 1.10) to recompute ``adj_nav`` after each corporate action.

Mirrors the DDL in design.md §2.1 and the Pydantic DTO
``DividendRecord``.

Requirements: 2.6 (adj_nav computation), 2.8.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Date, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.data.models import Base


class FundDividend(Base):
    """A single dividend or split event for a fund.

    ``dividend_per_share`` is 0 for pure splits; ``split_ratio`` is 1
    for pure cash dividends. Both can be non-trivial for combined events.
    """

    __tablename__ = "fund_dividends"

    # ------------------------------------------------------------------
    # Primary key (composite)
    # ------------------------------------------------------------------
    fund_code: Mapped[str] = mapped_column(
        String(10),
        primary_key=True,
        comment="Fund code — FK to funds.code (enforced at app layer)",
    )
    ex_date: Mapped[date] = mapped_column(
        Date,
        primary_key=True,
        comment="Ex-dividend / ex-split date (除权日)",
    )

    # ------------------------------------------------------------------
    # Event details
    # ------------------------------------------------------------------
    record_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="Record date (权益登记日)",
    )
    pay_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="Payment date (派息日)",
    )
    dividend_per_share: Mapped[Decimal] = mapped_column(
        Numeric(10, 6),
        nullable=False,
        server_default="0",
        comment="Cash dividend per share (CNY); 0 for pure splits",
    )
    split_ratio: Mapped[Decimal] = mapped_column(
        Numeric(10, 6),
        nullable=False,
        server_default="1",
        comment="Split ratio (new shares / old shares); 1 = no split",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<FundDividend fund_code={self.fund_code!r} "
            f"ex_date={self.ex_date!r} "
            f"dividend_per_share={self.dividend_per_share!r} "
            f"split_ratio={self.split_ratio!r}>"
        )
