"""ORM model for persisted advisor reminders.

Stores reminder items derived from saved advisor results, execution records,
and execution-plan status so the frontend and future notification channels can
reuse a single source of truth.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.data.models import Base

_JsonType = JSON().with_variant(JSONB, "postgresql")
_IdType = Integer().with_variant(BigInteger, "postgresql")


class AdvisorReminder(Base):
    """Persistent reminder item for advisor follow-up and notification workflows."""

    __tablename__ = "advisor_reminders"

    id: Mapped[int] = mapped_column(
        _IdType,
        primary_key=True,
        autoincrement=True,
        comment="Advisor reminder unique identifier",
    )
    advisor_result_id: Mapped[int] = mapped_column(
        _IdType,
        ForeignKey("advisor_results.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Saved advisor result this reminder belongs to",
    )
    fund_code: Mapped[str | None] = mapped_column(
        String(10),
        nullable=True,
        index=True,
        comment="Fund code for fund-level reminders; null for result-level reminders",
    )
    category: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
        comment="Reminder category: validity/risk/execution/plan/system",
    )
    reminder_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
        comment="Reminder type key such as validity_expired/plan_overdue",
    )
    severity: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
        comment="Severity level: info/warning/error/success",
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="active",
        index=True,
        comment="Reminder lifecycle status: active/resolved/dismissed",
    )
    title: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        comment="Reminder title shown to users",
    )
    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Reminder description shown to users",
    )
    payload_json: Mapped[dict | None] = mapped_column(
        _JsonType,
        nullable=True,
        comment="Structured context for this reminder",
    )
    trigger_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        index=True,
        comment="Logical trigger date for the reminder",
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp when the reminder was auto-resolved",
    )
    dismissed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp when the reminder was manually dismissed",
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
            f"<AdvisorReminder id={self.id!r} result={self.advisor_result_id!r} "
            f"type={self.reminder_type!r} status={self.status!r}>"
        )
