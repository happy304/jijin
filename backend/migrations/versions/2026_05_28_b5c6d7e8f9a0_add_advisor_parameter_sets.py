"""add advisor parameter sets registry

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9
Create Date: 2026-05-28 16:00:00.000000+00:00
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "b5c6d7e8f9a0"
down_revision: Union[str, Sequence[str], None] = "a4b5c6d7e8f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JsonType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
_IdType = sa.Integer().with_variant(sa.BigInteger(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "advisor_parameter_sets",
        sa.Column("id", _IdType, autoincrement=True, nullable=False, comment="Advisor parameter set unique identifier"),
        sa.Column("param_set_id", sa.String(length=64), nullable=False, comment="Stable external parameter set identifier"),
        sa.Column("kind", sa.String(length=40), nullable=False, server_default="default_config", comment="Parameter set kind: default_config/feedback_learning"),
        sa.Column("risk_level", sa.String(length=20), nullable=False, server_default="moderate", comment="Risk level this parameter set targets"),
        sa.Column("engine_version", sa.String(length=20), nullable=False, server_default="5.0", comment="Advisor engine version tag"),
        sa.Column("name", sa.String(length=120), nullable=True, comment="Human readable parameter set name"),
        sa.Column("description", sa.Text(), nullable=True, comment="Parameter set description"),
        sa.Column("payload", _JsonType, nullable=False, comment="Full advisor parameter payload for replay"),
        sa.Column("config_hash", sa.String(length=64), nullable=False, comment="Stable hash of the parameter payload"),
        sa.Column("source_learned_params_version_id", _IdType, nullable=True, comment="Optional learned-parameter version that produced this set"),
        sa.Column("train_window", _JsonType, nullable=True, comment="Training window metadata"),
        sa.Column("validation_window", _JsonType, nullable=True, comment="Validation window metadata"),
        sa.Column("oos_window", _JsonType, nullable=True, comment="Out-of-sample window metadata"),
        sa.Column("created_reason", sa.Text(), nullable=True, comment="Why this parameter set was created"),
        sa.Column("gate_status", sa.String(length=30), nullable=False, server_default="not_evaluated", comment="Release gate status: approved/shadow_only/blocked/not_evaluated"),
        sa.Column("gate_action", sa.String(length=30), nullable=False, server_default="shadow_only", comment="Gate action: allow_default/shadow_only/block_default"),
        sa.Column("gate_reason", sa.Text(), nullable=True, comment="Human readable release gate reason"),
        sa.Column("gate_checked_at", sa.DateTime(timezone=True), nullable=True, comment="Timestamp when release gate was evaluated"),
        sa.Column("gate_metrics", _JsonType, nullable=True, comment="OOS/PBO metrics used by the release gate"),
        sa.Column("review_status", sa.String(length=30), nullable=False, server_default="pending", comment="Manual review status: pending/approved/rejected"),
        sa.Column("reviewed_by", sa.String(length=120), nullable=True, comment="Reviewer identifier"),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True, comment="Manual review timestamp"),
        sa.Column("review_notes", sa.Text(), nullable=True, comment="Manual review notes"),
        sa.Column("release_status", sa.String(length=30), nullable=False, server_default="shadow", comment="Release status: shadow/active/archived/rolled_back"),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True, comment="Timestamp when this parameter set became active"),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True, comment="Timestamp when this parameter set was archived"),
        sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True, comment="Timestamp when this parameter set was rolled back from active"),
        sa.Column("rollback_from_param_set_id", sa.String(length=64), nullable=True, comment="Previous active parameter set id when this set was activated by rollback"),
        sa.Column("rollback_reason", sa.Text(), nullable=True, comment="Rollback reason"),
        sa.Column("effective_from", sa.Date(), nullable=True, comment="Logical effective start date"),
        sa.Column("effective_to", sa.Date(), nullable=True, comment="Logical effective end date"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True, comment="Creation timestamp (UTC)"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True, comment="Last update timestamp (UTC)"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_advisor_parameter_sets")),
        sa.UniqueConstraint("param_set_id", name="uq_advisor_parameter_sets_param_set_id"),
    )
    op.create_index(op.f("ix_advisor_parameter_sets_param_set_id"), "advisor_parameter_sets", ["param_set_id"], unique=False)
    op.create_index(op.f("ix_advisor_parameter_sets_kind"), "advisor_parameter_sets", ["kind"], unique=False)
    op.create_index(op.f("ix_advisor_parameter_sets_risk_level"), "advisor_parameter_sets", ["risk_level"], unique=False)
    op.create_index(op.f("ix_advisor_parameter_sets_config_hash"), "advisor_parameter_sets", ["config_hash"], unique=False)
    op.create_index(op.f("ix_advisor_parameter_sets_source_learned_params_version_id"), "advisor_parameter_sets", ["source_learned_params_version_id"], unique=False)
    op.create_index(op.f("ix_advisor_parameter_sets_gate_status"), "advisor_parameter_sets", ["gate_status"], unique=False)
    op.create_index(op.f("ix_advisor_parameter_sets_review_status"), "advisor_parameter_sets", ["review_status"], unique=False)
    op.create_index(op.f("ix_advisor_parameter_sets_release_status"), "advisor_parameter_sets", ["release_status"], unique=False)
    op.create_index(op.f("ix_advisor_parameter_sets_activated_at"), "advisor_parameter_sets", ["activated_at"], unique=False)
    op.create_index(op.f("ix_advisor_parameter_sets_rollback_from_param_set_id"), "advisor_parameter_sets", ["rollback_from_param_set_id"], unique=False)
    op.create_index(op.f("ix_advisor_parameter_sets_effective_from"), "advisor_parameter_sets", ["effective_from"], unique=False)
    op.create_index(
        "ix_advisor_parameter_sets_kind_risk_release",
        "advisor_parameter_sets",
        ["kind", "risk_level", "release_status"],
        unique=False,
    )

    op.add_column(
        "advisor_results",
        sa.Column(
            "parameter_set_id",
            sa.String(length=64),
            nullable=True,
            comment="Resolved governed default parameter set id used during analysis",
        ),
    )
    op.create_index(op.f("ix_advisor_results_parameter_set_id"), "advisor_results", ["parameter_set_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_advisor_results_parameter_set_id"), table_name="advisor_results")
    op.drop_column("advisor_results", "parameter_set_id")

    op.drop_index("ix_advisor_parameter_sets_kind_risk_release", table_name="advisor_parameter_sets")
    op.drop_index(op.f("ix_advisor_parameter_sets_effective_from"), table_name="advisor_parameter_sets")
    op.drop_index(op.f("ix_advisor_parameter_sets_rollback_from_param_set_id"), table_name="advisor_parameter_sets")
    op.drop_index(op.f("ix_advisor_parameter_sets_activated_at"), table_name="advisor_parameter_sets")
    op.drop_index(op.f("ix_advisor_parameter_sets_release_status"), table_name="advisor_parameter_sets")
    op.drop_index(op.f("ix_advisor_parameter_sets_review_status"), table_name="advisor_parameter_sets")
    op.drop_index(op.f("ix_advisor_parameter_sets_gate_status"), table_name="advisor_parameter_sets")
    op.drop_index(op.f("ix_advisor_parameter_sets_source_learned_params_version_id"), table_name="advisor_parameter_sets")
    op.drop_index(op.f("ix_advisor_parameter_sets_config_hash"), table_name="advisor_parameter_sets")
    op.drop_index(op.f("ix_advisor_parameter_sets_risk_level"), table_name="advisor_parameter_sets")
    op.drop_index(op.f("ix_advisor_parameter_sets_kind"), table_name="advisor_parameter_sets")
    op.drop_index(op.f("ix_advisor_parameter_sets_param_set_id"), table_name="advisor_parameter_sets")
    op.drop_table("advisor_parameter_sets")
