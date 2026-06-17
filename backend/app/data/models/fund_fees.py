"""ORM model for the ``fund_fees`` table.

Stores tiered fee schedules for both subscription (subscribe) and
redemption (redeem) fee types.

* Subscribe tiers use ``min_amount`` / ``max_amount`` (CNY brackets).
* Redeem tiers use ``min_holding_days`` / ``max_holding_days`` (day brackets).

Mirrors the DDL in design.md §2.1 and the Pydantic DTO ``FeeTier``.

Requirements: 2.8, 4.4, 4.5.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.data.models import Base


class FundFee(Base):
    """A single fee tier row for a fund's subscribe or redeem schedule.

    The composite primary key ``(fund_code, fee_type, min_amount,
    min_holding_days)`` uniquely identifies each tier bracket.
    """

    __tablename__ = "fund_fees"

    # ------------------------------------------------------------------
    # Primary key (composite — mirrors design §2.1 DDL)
    # ------------------------------------------------------------------
    fund_code: Mapped[str] = mapped_column(
        String(10),
        primary_key=True,
        comment="Fund code — FK to funds.code (enforced at app layer)",
    )
    fee_type: Mapped[str] = mapped_column(
        String(20),
        primary_key=True,
        comment="Fee category: subscribe or redeem",
    )
    min_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 2),
        primary_key=True,
        server_default="0",
        comment="Lower bound of subscription amount bracket (CNY, inclusive)",
    )
    min_holding_days: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        server_default="0",
        comment="Lower bound of holding-period bracket (days, inclusive)",
    )

    # ------------------------------------------------------------------
    # Bracket upper bounds (NULL = no cap)
    # ------------------------------------------------------------------
    max_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 2),
        nullable=True,
        comment="Upper bound of subscription amount bracket (CNY, exclusive); NULL = no cap",
    )
    max_holding_days: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Upper bound of holding-period bracket (days, exclusive); NULL = no cap",
    )

    # ------------------------------------------------------------------
    # Fee rate
    # ------------------------------------------------------------------
    rate: Mapped[Decimal] = mapped_column(
        Numeric(8, 6),
        nullable=False,
        comment="Fee rate as a decimal fraction (e.g. 0.015 = 1.5%)",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<FundFee fund_code={self.fund_code!r} "
            f"fee_type={self.fee_type!r} "
            f"min_amount={self.min_amount!r} "
            f"rate={self.rate!r}>"
        )
