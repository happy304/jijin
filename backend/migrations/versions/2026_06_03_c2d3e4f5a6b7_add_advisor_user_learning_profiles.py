"""add advisor user learning profiles table

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-06-03 10:00:00.000000+00:00
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, Sequence[str], None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JsonType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
_IdType = sa.Integer().with_variant(sa.BigInteger(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "advisor_user_learning_profiles",
        sa.Column("id", _IdType, autoincrement=True, nullable=False, comment="User learning profile unique identifier"),
        sa.Column("profile_key", sa.String(length=128), nullable=False, comment="Stable user/profile key; defaults to 'default' before auth integration"),
        sa.Column("sample_count", sa.Integer(), nullable=False, server_default="0", comment="Execution records used for this profile"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0", comment="Learning confidence after shrinkage, 0~1"),
        sa.Column("adoption_rate", sa.Float(), nullable=False, server_default="0", comment="Share of actionable records executed or partially executed"),
        sa.Column("partial_rate", sa.Float(), nullable=False, server_default="0", comment="Share of records partially executed"),
        sa.Column("avg_execution_ratio", sa.Float(), nullable=True, comment="Average executed/suggested amount ratio for adopted trades"),
        sa.Column("avg_execution_lag_days", sa.Float(), nullable=True, comment="Average days between advice and actual execution"),
        sa.Column("amount_scale", sa.Float(), nullable=False, server_default="1", comment="Conservative learned multiplier for future suggested amounts"),
        sa.Column("preferred_execution_style", sa.String(length=40), nullable=False, server_default="neutral", comment="neutral/batch/slower_cadence/small_steps"),
        sa.Column("preferred_batch_count", sa.Integer(), nullable=True, comment="Learned preferred batch count when batch execution is suitable"),
        sa.Column("preferred_batch_interval_days", sa.Integer(), nullable=True, comment="Learned preferred days between batches"),
        sa.Column("explanation_style", sa.String(length=40), nullable=False, server_default="balanced", comment="balanced/risk_first/action_first"),
        sa.Column("safeguards", _JsonType, nullable=True, comment="Anti-overfitting bounds applied to this profile"),
        sa.Column("metrics", _JsonType, nullable=True, comment="Raw learning metrics for audit"),
        sa.Column("learning_log", _JsonType, nullable=True, comment="Human-readable adjustment log"),
        sa.Column("last_learned_at", sa.DateTime(timezone=True), nullable=True, comment="Last learning timestamp"),
        sa.Column("note", sa.Text(), nullable=True, comment="Optional operator note"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True, comment="Creation timestamp (UTC)"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True, comment="Last update timestamp (UTC)"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_advisor_user_learning_profiles")),
        sa.UniqueConstraint("profile_key", name="uq_advisor_user_learning_profiles_profile_key"),
    )
    op.create_index(op.f("ix_advisor_user_learning_profiles_profile_key"), "advisor_user_learning_profiles", ["profile_key"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_advisor_user_learning_profiles_profile_key"), table_name="advisor_user_learning_profiles")
    op.drop_table("advisor_user_learning_profiles")
