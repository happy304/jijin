"""add advisor execution records

Revision ID: c6d7e8f9a0b1
Revises: b5c6d7e8f9a0
Create Date: 2026-05-28 18:00:00.000000+00:00
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c6d7e8f9a0b1"
down_revision: Union[str, Sequence[str], None] = "b5c6d7e8f9a0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JsonType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
_IdType = sa.Integer().with_variant(sa.BigInteger(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "advisor_execution_records",
        sa.Column("id", _IdType, autoincrement=True, nullable=False, comment="Execution record unique identifier"),
        sa.Column("advisor_result_id", _IdType, nullable=False, comment="Saved advisor result this execution belongs to"),
        sa.Column("advice_date", sa.Date(), nullable=False, comment="Original advice date"),
        sa.Column("fund_code", sa.String(length=10), nullable=False, comment="Fund code this execution record applies to"),
        sa.Column("advice_action", sa.String(length=20), nullable=False, comment="Original advice action: buy/sell/hold"),
        sa.Column("trade_intent", sa.String(length=20), nullable=False, server_default="hold", comment="Normalized trade intent: subscribe/redeem/hold"),
        sa.Column("suggested_amount", sa.Numeric(precision=20, scale=2), nullable=True, comment="Original suggested amount snapshot"),
        sa.Column("suggested_shares", sa.Numeric(precision=20, scale=4), nullable=True, comment="Original suggested shares snapshot"),
        sa.Column("suggested_pct", sa.Numeric(precision=12, scale=6), nullable=True, comment="Original suggested portfolio percentage snapshot"),
        sa.Column("confidence", sa.Numeric(precision=8, scale=4), nullable=True, comment="Original advice confidence snapshot"),
        sa.Column("execution_status", sa.String(length=30), nullable=False, server_default="planned", comment="User execution status: planned/executed/partial/not_executed"),
        sa.Column("executed_date", sa.Date(), nullable=True, comment="Actual execution date when executed or partially executed"),
        sa.Column("executed_amount", sa.Numeric(precision=20, scale=2), nullable=True, comment="Actual executed amount"),
        sa.Column("executed_shares", sa.Numeric(precision=20, scale=4), nullable=True, comment="Actual executed shares"),
        sa.Column("executed_nav", sa.Numeric(precision=20, scale=6), nullable=True, comment="Actual execution NAV/price"),
        sa.Column("executed_fee", sa.Numeric(precision=20, scale=2), nullable=True, comment="Actual execution fee"),
        sa.Column("execution_channel", sa.String(length=80), nullable=True, comment="Execution channel or platform entered by user"),
        sa.Column("not_executed_reason", sa.Text(), nullable=True, comment="Reason when the advice was not executed"),
        sa.Column("deviation_reason", sa.Text(), nullable=True, comment="Reason for partial execution or deviation from suggested amount"),
        sa.Column("user_note", sa.Text(), nullable=True, comment="User note for this execution record"),
        sa.Column("source", sa.String(length=40), nullable=False, server_default="manual", comment="Record source: manual/import/api"),
        sa.Column("metadata_json", _JsonType, nullable=True, comment="Additional execution metadata"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True, comment="Creation timestamp (UTC)"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True, comment="Last update timestamp (UTC)"),
        sa.ForeignKeyConstraint(["advisor_result_id"], ["advisor_results.id"], name=op.f("fk_advisor_execution_records_advisor_result_id_advisor_results"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_advisor_execution_records")),
    )
    op.create_index(op.f("ix_advisor_execution_records_advisor_result_id"), "advisor_execution_records", ["advisor_result_id"], unique=False)
    op.create_index(op.f("ix_advisor_execution_records_advice_date"), "advisor_execution_records", ["advice_date"], unique=False)
    op.create_index(op.f("ix_advisor_execution_records_fund_code"), "advisor_execution_records", ["fund_code"], unique=False)
    op.create_index(op.f("ix_advisor_execution_records_execution_status"), "advisor_execution_records", ["execution_status"], unique=False)
    op.create_index(op.f("ix_advisor_execution_records_executed_date"), "advisor_execution_records", ["executed_date"], unique=False)
    op.create_index(
        "ix_advisor_execution_records_result_fund_status",
        "advisor_execution_records",
        ["advisor_result_id", "fund_code", "execution_status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_advisor_execution_records_result_fund_status", table_name="advisor_execution_records")
    op.drop_index(op.f("ix_advisor_execution_records_executed_date"), table_name="advisor_execution_records")
    op.drop_index(op.f("ix_advisor_execution_records_execution_status"), table_name="advisor_execution_records")
    op.drop_index(op.f("ix_advisor_execution_records_fund_code"), table_name="advisor_execution_records")
    op.drop_index(op.f("ix_advisor_execution_records_advice_date"), table_name="advisor_execution_records")
    op.drop_index(op.f("ix_advisor_execution_records_advisor_result_id"), table_name="advisor_execution_records")
    op.drop_table("advisor_execution_records")
