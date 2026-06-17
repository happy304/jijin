"""ORM model for Advisor reminder subscription preferences."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, JSON, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.data.models import Base

_JsonType = JSON().with_variant(JSONB, "postgresql")
_IdType = Integer().with_variant(BigInteger, "postgresql")


class AdvisorReminderPreference(Base):
    """Server-side reminder subscription preference for a profile scope.

    The project does not yet have a full user system, so ``profile_key`` keeps
    the same ``default`` scope convention used by user-learning profiles while
    leaving room for future user/account identifiers.
    """

    __tablename__ = "advisor_reminder_preferences"

    id: Mapped[int] = mapped_column(
        _IdType,
        primary_key=True,
        autoincrement=True,
        comment="Advisor reminder preference unique identifier",
    )
    profile_key: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
        unique=True,
        default="default",
        index=True,
        comment="User/profile scope key; defaults to global fallback profile",
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="Whether active reminder digest notifications are enabled",
    )
    min_severity: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="warning",
        comment="Minimum severity for active digest notification: info/warning/error",
    )
    lookahead_days: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=3,
        comment="How many future days to include in reminder digest",
    )
    channels_json: Mapped[list[str] | None] = mapped_column(
        _JsonType,
        nullable=True,
        comment="Notification channels: email/wecom/telegram; null means use environment defaults",
    )
    muted_categories_json: Mapped[list[str] | None] = mapped_column(
        _JsonType,
        nullable=True,
        comment="Reminder categories muted for notification digest",
    )
    quiet_hours_json: Mapped[dict | None] = mapped_column(
        _JsonType,
        nullable=True,
        comment="Optional quiet hours config such as {start: '22:00', end: '08:00', timezone: 'Asia/Shanghai'}",
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
        return f"<AdvisorReminderPreference profile_key={self.profile_key!r} enabled={self.enabled!r}>"
