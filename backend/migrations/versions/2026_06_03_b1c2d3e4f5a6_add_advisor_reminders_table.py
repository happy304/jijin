"""add advisor reminders table

Revision ID: b1c2d3e4f5a6
Revises: a1b2c3d4e5f7
Create Date: 2026-06-03 09:00:00.000000+00:00
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JsonType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
_IdType = sa.Integer().with_variant(sa.BigInteger(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "advisor_reminders",
        sa.Column("id", _IdType, autoincrement=True, nullable=False, comment="Advisor reminder unique identifier"),
        sa.Column("advisor_result_id", _IdType, nullable=False, comment="Saved advisor result this reminder belongs to"),
        sa.Column("fund_code", sa.String(length=10), nullable=True, comment="Fund code for fund-level reminders; null for result-level reminders"),
        sa.Column("category", sa.String(length=20), nullable=False, comment="Reminder category: validity/risk/execution/plan/system"),
        sa.Column("reminder_type", sa.String(length=50), nullable=False, comment="Reminder type key such as validity_expired/plan_overdue"),
        sa.Column("severity", sa.String(length=20), nullable=False, comment="Severity level: info/warning/error/success"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active", comment="Reminder lifecycle status: active/resolved/dismissed"),
        sa.Column("title", sa.String(length=200), nullable=False, comment="Reminder title shown to users"),
        sa.Column("description", sa.Text(), nullable=False, comment="Reminder description shown to users"),
        sa.Column("payload_json", _JsonType, nullable=True, comment="Structured context for this reminder"),
        sa.Column("trigger_date", sa.Date(), nullable=False, comment="Logical trigger date for the reminder"),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True, comment="Timestamp when the reminder was auto-resolved"),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True, comment="Timestamp when the reminder was manually dismissed"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True, comment="Creation timestamp (UTC)"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True, comment="Last update timestamp (UTC)"),
        sa.ForeignKeyConstraint(["advisor_result_id"], ["advisor_results.id"], ondelete="CASCADE", name=op.f("fk_advisor_reminders_advisor_result_id_advisor_results")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_advisor_reminders")),
    )
    op.create_index(op.f("ix_advisor_reminders_advisor_result_id"), "advisor_reminders", ["advisor_result_id"], unique=False)
    op.create_index(op.f("ix_advisor_reminders_fund_code"), "advisor_reminders", ["fund_code"], unique=False)
    op.create_index(op.f("ix_advisor_reminders_category"), "advisor_reminders", ["category"], unique=False)
    op.create_index(op.f("ix_advisor_reminders_reminder_type"), "advisor_reminders", ["reminder_type"], unique=False)
    op.create_index(op.f("ix_advisor_reminders_severity"), "advisor_reminders", ["severity"], unique=False)
    op.create_index(op.f("ix_advisor_reminders_status"), "advisor_reminders", ["status"], unique=False)
    op.create_index(op.f("ix_advisor_reminders_trigger_date"), "advisor_reminders", ["trigger_date"], unique=False)
    op.create_index(
        "ix_advisor_reminders_status_trigger_date",
        "advisor_reminders",
        ["status", "trigger_date"],
        unique=False,
    )
    op.create_index(
        "ix_advisor_reminders_result_status",
        "advisor_reminders",
        ["advisor_result_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_advisor_reminders_category_status",
        "advisor_reminders",
        ["category", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_advisor_reminders_category_status", table_name="advisor_reminders")
    op.drop_index("ix_advisor_reminders_result_status", table_name="advisor_reminders")
    op.drop_index("ix_advisor_reminders_status_trigger_date", table_name="advisor_reminders")
    op.drop_index(op.f("ix_advisor_reminders_trigger_date"), table_name="advisor_reminders")
    op.drop_index(op.f("ix_advisor_reminders_status"), table_name="advisor_reminders")
    op.drop_index(op.f("ix_advisor_reminders_severity"), table_name="advisor_reminders")
    op.drop_index(op.f("ix_advisor_reminders_reminder_type"), table_name="advisor_reminders")
    op.drop_index(op.f("ix_advisor_reminders_category"), table_name="advisor_reminders")
    op.drop_index(op.f("ix_advisor_reminders_fund_code"), table_name="advisor_reminders")
    op.drop_index(op.f("ix_advisor_reminders_advisor_result_id"), table_name="advisor_reminders")
    op.drop_table("advisor_reminders")
