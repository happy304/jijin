"""add advisor reminder preferences table

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-06-03 11:00:00.000000+00:00
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "d3e4f5a6b7c8"
down_revision: Union[str, Sequence[str], None] = "c2d3e4f5a6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JsonType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
_IdType = sa.Integer().with_variant(sa.BigInteger(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "advisor_reminder_preferences",
        sa.Column("id", _IdType, autoincrement=True, nullable=False, comment="Advisor reminder preference unique identifier"),
        sa.Column("profile_key", sa.String(length=120), nullable=False, comment="User/profile scope key; defaults to global fallback profile"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true"), comment="Whether active reminder digest notifications are enabled"),
        sa.Column("min_severity", sa.String(length=20), nullable=False, server_default="warning", comment="Minimum severity for active digest notification: info/warning/error"),
        sa.Column("lookahead_days", sa.Integer(), nullable=False, server_default="3", comment="How many future days to include in reminder digest"),
        sa.Column("channels_json", _JsonType, nullable=True, comment="Notification channels: email/wecom/telegram; null means use environment defaults"),
        sa.Column("muted_categories_json", _JsonType, nullable=True, comment="Reminder categories muted for notification digest"),
        sa.Column("quiet_hours_json", _JsonType, nullable=True, comment="Optional quiet hours config"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True, comment="Creation timestamp (UTC)"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True, comment="Last update timestamp (UTC)"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_advisor_reminder_preferences")),
    )
    op.create_index(op.f("ix_advisor_reminder_preferences_profile_key"), "advisor_reminder_preferences", ["profile_key"], unique=True)
    op.create_index(
        "ix_advisor_reminder_preferences_enabled_severity",
        "advisor_reminder_preferences",
        ["enabled", "min_severity"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_advisor_reminder_preferences_enabled_severity", table_name="advisor_reminder_preferences")
    op.drop_index(op.f("ix_advisor_reminder_preferences_profile_key"), table_name="advisor_reminder_preferences")
    op.drop_table("advisor_reminder_preferences")
