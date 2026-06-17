"""ORM model for user-level advisor learning profiles.

The current Advisor product has no full auth/user table yet, so this model uses a
stable ``profile_key`` to scope learned execution preferences. The default key is
``default`` and future auth integration can map real user IDs to this field.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.data.models import Base

_JsonType = JSON().with_variant(JSONB, "postgresql")
_IdType = Integer().with_variant(BigInteger, "postgresql")


class AdvisorUserLearningProfile(Base):
    """Persisted user-level execution preference snapshot."""

    __tablename__ = "advisor_user_learning_profiles"
    __table_args__ = (
        UniqueConstraint("profile_key", name="uq_advisor_user_learning_profiles_profile_key"),
    )

    id: Mapped[int] = mapped_column(
        _IdType,
        primary_key=True,
        autoincrement=True,
        comment="User learning profile unique identifier",
    )
    profile_key: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        index=True,
        comment="Stable user/profile key; defaults to 'default' before auth integration",
    )
    sample_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Execution records used for this profile",
    )
    confidence: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
        comment="Learning confidence after shrinkage, 0~1",
    )
    adoption_rate: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
        comment="Share of actionable records executed or partially executed",
    )
    partial_rate: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
        comment="Share of records partially executed",
    )
    avg_execution_ratio: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="Average executed/suggested amount ratio for adopted trades",
    )
    avg_execution_lag_days: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="Average days between advice and actual execution",
    )
    amount_scale: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=1.0,
        comment="Conservative learned multiplier for future suggested amounts",
    )
    preferred_execution_style: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="neutral",
        comment="neutral/batch/slower_cadence/small_steps",
    )
    preferred_batch_count: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Learned preferred batch count when batch execution is suitable",
    )
    preferred_batch_interval_days: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Learned preferred days between batches",
    )
    explanation_style: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="balanced",
        comment="balanced/risk_first/action_first",
    )
    safeguards: Mapped[dict | None] = mapped_column(
        _JsonType,
        nullable=True,
        comment="Anti-overfitting bounds applied to this profile",
    )
    metrics: Mapped[dict | None] = mapped_column(
        _JsonType,
        nullable=True,
        comment="Raw learning metrics for audit",
    )
    learning_log: Mapped[list | None] = mapped_column(
        _JsonType,
        nullable=True,
        comment="Human-readable adjustment log",
    )
    last_learned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Last learning timestamp",
    )
    note: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Optional operator note",
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
        return f"<AdvisorUserLearningProfile id={self.id!r} key={self.profile_key!r}>"
