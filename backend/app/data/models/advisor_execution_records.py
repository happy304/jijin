"""ORM model for user-reported advisor execution records.

Each row captures how a user actually acted on one saved advisor suggestion.
This lets replay/performance views distinguish model signal quality from user
adoption or execution drift.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Integer, JSON, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.data.models import Base

_JsonType = JSON().with_variant(JSONB, "postgresql")
_IdType = Integer().with_variant(BigInteger, "postgresql")


class AdvisorExecutionRecord(Base):
    """Persistent representation of a user's actual execution of an advice."""

    __tablename__ = "advisor_execution_records"

    id: Mapped[int] = mapped_column(
        _IdType,
        primary_key=True,
        autoincrement=True,
        comment="Execution record unique identifier",
    )
    advisor_result_id: Mapped[int] = mapped_column(
        _IdType,
        ForeignKey("advisor_results.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Saved advisor result this execution belongs to",
    )
    advice_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        index=True,
        comment="Original advice date",
    )
    fund_code: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        index=True,
        comment="Fund code this execution record applies to",
    )
    advice_action: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="Original advice action: buy/sell/hold",
    )
    trade_intent: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="hold",
        comment="Normalized trade intent: subscribe/redeem/hold",
    )
    suggested_amount: Mapped[float | None] = mapped_column(
        Numeric(20, 2),
        nullable=True,
        comment="Original suggested amount snapshot",
    )
    suggested_shares: Mapped[float | None] = mapped_column(
        Numeric(20, 4),
        nullable=True,
        comment="Original suggested shares snapshot",
    )
    suggested_pct: Mapped[float | None] = mapped_column(
        Numeric(12, 6),
        nullable=True,
        comment="Original suggested portfolio percentage snapshot",
    )
    confidence: Mapped[float | None] = mapped_column(
        Numeric(8, 4),
        nullable=True,
        comment="Original advice confidence snapshot",
    )
    execution_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="planned",
        index=True,
        comment="User execution status: planned/executed/partial/not_executed",
    )
    executed_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        index=True,
        comment="Actual execution date when executed or partially executed",
    )
    executed_amount: Mapped[float | None] = mapped_column(
        Numeric(20, 2),
        nullable=True,
        comment="Actual executed amount",
    )
    executed_shares: Mapped[float | None] = mapped_column(
        Numeric(20, 4),
        nullable=True,
        comment="Actual executed shares",
    )
    executed_nav: Mapped[float | None] = mapped_column(
        Numeric(20, 6),
        nullable=True,
        comment="Actual execution NAV/price",
    )
    executed_fee: Mapped[float | None] = mapped_column(
        Numeric(20, 2),
        nullable=True,
        comment="Actual execution fee",
    )
    execution_channel: Mapped[str | None] = mapped_column(
        String(80),
        nullable=True,
        comment="Execution channel or platform entered by user",
    )
    not_executed_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Reason when the advice was not executed",
    )
    deviation_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Reason for partial execution or deviation from suggested amount",
    )
    user_note: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="User note for this execution record",
    )
    source: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="manual",
        comment="Record source: manual/import/api",
    )
    metadata_json: Mapped[dict | None] = mapped_column(
        _JsonType,
        nullable=True,
        comment="Additional execution metadata",
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
        return (
            f"<AdvisorExecutionRecord id={self.id!r} result={self.advisor_result_id!r} "
            f"fund={self.fund_code!r} status={self.execution_status!r}>"
        )
