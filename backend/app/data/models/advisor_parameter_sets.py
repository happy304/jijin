"""ORM model for governed advisor parameter sets.

The registry stores versioned default advisor configurations and optional
feedback-learning derived parameter payloads.  Each advisor execution can then
record the exact parameter set that was visible at the execution time, while
operators can review, activate, shadow, archive, and roll back parameter
versions without mutating historical rows.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.data.models import Base

_JsonType = JSON().with_variant(JSONB, "postgresql")
_IdType = Integer().with_variant(BigInteger, "postgresql")


class AdvisorParameterSet(Base):
    """Persistent representation of one governed advisor parameter set."""

    __tablename__ = "advisor_parameter_sets"
    __table_args__ = (
        UniqueConstraint("param_set_id", name="uq_advisor_parameter_sets_param_set_id"),
    )

    id: Mapped[int] = mapped_column(
        _IdType,
        primary_key=True,
        autoincrement=True,
        comment="Advisor parameter set unique identifier",
    )
    param_set_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        comment="Stable external parameter set identifier",
    )
    kind: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="default_config",
        index=True,
        comment="Parameter set kind: default_config/feedback_learning",
    )
    risk_level: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="moderate",
        index=True,
        comment="Risk level this parameter set targets",
    )
    engine_version: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="5.0",
        comment="Advisor engine version tag",
    )
    name: Mapped[str | None] = mapped_column(
        String(120),
        nullable=True,
        comment="Human readable parameter set name",
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Parameter set description",
    )
    payload: Mapped[dict] = mapped_column(
        _JsonType,
        nullable=False,
        comment="Full advisor parameter payload for replay",
    )
    config_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        comment="Stable hash of the parameter payload",
    )
    source_learned_params_version_id: Mapped[int | None] = mapped_column(
        _IdType,
        nullable=True,
        index=True,
        comment="Optional learned-parameter version that produced this set",
    )
    train_window: Mapped[dict | None] = mapped_column(
        _JsonType,
        nullable=True,
        comment="Training window metadata",
    )
    validation_window: Mapped[dict | None] = mapped_column(
        _JsonType,
        nullable=True,
        comment="Validation window metadata",
    )
    oos_window: Mapped[dict | None] = mapped_column(
        _JsonType,
        nullable=True,
        comment="Out-of-sample window metadata",
    )
    created_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Why this parameter set was created",
    )
    gate_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="not_evaluated",
        index=True,
        comment="Release gate status: approved/shadow_only/blocked/not_evaluated",
    )
    gate_action: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="shadow_only",
        comment="Gate action: allow_default/shadow_only/block_default",
    )
    gate_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Human readable release gate reason",
    )
    gate_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp when release gate was evaluated",
    )
    gate_metrics: Mapped[dict | None] = mapped_column(
        _JsonType,
        nullable=True,
        comment="OOS/PBO metrics used by the release gate",
    )
    review_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="pending",
        index=True,
        comment="Manual review status: pending/approved/rejected",
    )
    reviewed_by: Mapped[str | None] = mapped_column(
        String(120),
        nullable=True,
        comment="Reviewer identifier",
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Manual review timestamp",
    )
    review_notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Manual review notes",
    )
    release_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="shadow",
        index=True,
        comment="Release status: shadow/active/archived/rolled_back",
    )
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
        comment="Timestamp when this parameter set became active",
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp when this parameter set was archived",
    )
    rolled_back_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp when this parameter set was rolled back from active",
    )
    rollback_from_param_set_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
        comment="Previous active parameter set id when this set was activated by rollback",
    )
    rollback_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Rollback reason",
    )
    effective_from: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        index=True,
        comment="Logical effective start date",
    )
    effective_to: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="Logical effective end date",
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
            f"<AdvisorParameterSet id={self.id!r} param_set_id={self.param_set_id!r} "
            f"kind={self.kind!r} risk={self.risk_level!r} status={self.release_status!r}>"
        )
