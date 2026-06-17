"""ORM model for persisted advisor positions.

This table stores the current portfolio snapshot entered on the Advisor page,
so the frontend no longer depends only on browser localStorage for position
state. At the moment the scope is a single shared snapshot (no user dimension
attached yet), which is enough for the current product stage.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, Integer, JSON, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.data.models import Base

_JsonType = JSON().with_variant(JSONB, "postgresql")
_IdType = Integer().with_variant(BigInteger(), "postgresql")


class AdvisorPosition(Base):
    """Persistent representation of one current fund position."""

    __tablename__ = "advisor_positions"
    __table_args__ = (
        UniqueConstraint("fund_code", name="uq_advisor_positions_fund_code"),
    )

    id: Mapped[int] = mapped_column(
        _IdType,
        primary_key=True,
        autoincrement=True,
        comment="Advisor position unique identifier",
    )
    fund_code: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        index=True,
        comment="Fund code",
    )
    market_value: Mapped[float] = mapped_column(
        Numeric(20, 2),
        nullable=False,
        default=0,
        comment="Current market value in CNY",
    )
    shares: Mapped[float] = mapped_column(
        Numeric(20, 4),
        nullable=False,
        default=0,
        comment="Current holding shares",
    )
    cost_basis: Mapped[float] = mapped_column(
        Numeric(20, 2),
        nullable=False,
        default=0,
        comment="Current cost basis in CNY",
    )
    buy_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="Original buy date if known",
    )
    source: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="manual",
        comment="Latest update source: manual/import/api/history",
    )
    metadata_json: Mapped[dict | None] = mapped_column(
        _JsonType,
        nullable=True,
        comment="Additional metadata for the saved position",
    )
    note: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Optional note for this saved position",
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        server_default=func.now(),
        comment="Creation timestamp (UTC)",
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        server_default=func.now(),
        onupdate=func.now(),
        comment="Last update timestamp (UTC)",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AdvisorPosition id={self.id!r} fund={self.fund_code!r}>"
