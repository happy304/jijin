"""add advisor_oos_snapshots table

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-05-26 12:00:00.000000+00:00
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, Sequence[str], None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_json_type = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "advisor_oos_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="Snapshot unique identifier"),
        sa.Column("fund_code", sa.String(length=20), nullable=False, comment="Fund code"),
        sa.Column("risk_level", sa.String(length=20), nullable=False, comment="Risk level: conservative/moderate/aggressive"),
        sa.Column("updated_at", sa.Date(), nullable=False, comment="Snapshot logical update date"),
        sa.Column("requested_days", sa.Integer(), nullable=True, comment="Requested lookback days when validation ran"),
        sa.Column("actual_trading_days", sa.Integer(), nullable=False, server_default="0", comment="Actual trading days used in validation"),
        sa.Column("avg_oos_ic", _json_type, nullable=True, comment="Average OOS IC stored as scalar JSON"),
        sa.Column("avg_is_ic", _json_type, nullable=True, comment="Average IS IC stored as scalar JSON"),
        sa.Column("ic_degradation", _json_type, nullable=True, comment="OOS IC / IS IC stored as scalar JSON"),
        sa.Column("avg_oos_buy_hit_rate", _json_type, nullable=True, comment="Average OOS buy hit rate stored as scalar JSON"),
        sa.Column("avg_oos_sell_hit_rate", _json_type, nullable=True, comment="Average OOS sell hit rate stored as scalar JSON"),
        sa.Column("total_oos_signals", sa.Integer(), nullable=False, server_default="0", comment="Total OOS signals"),
        sa.Column("total_oos_buy", sa.Integer(), nullable=False, server_default="0", comment="Total OOS buy signals"),
        sa.Column("total_oos_sell", sa.Integer(), nullable=False, server_default="0", comment="Total OOS sell signals"),
        sa.Column("warnings_json", _json_type, nullable=True, comment="Validation warnings"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True, comment="Creation timestamp (UTC)"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_advisor_oos_snapshots")),
        sa.UniqueConstraint("fund_code", "risk_level", name="uq_advisor_oos_snapshots_fund_code_risk_level"),
    )
    op.create_index(op.f("ix_advisor_oos_snapshots_fund_code"), "advisor_oos_snapshots", ["fund_code"], unique=False)
    op.create_index(op.f("ix_advisor_oos_snapshots_risk_level"), "advisor_oos_snapshots", ["risk_level"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_advisor_oos_snapshots_risk_level"), table_name="advisor_oos_snapshots")
    op.drop_index(op.f("ix_advisor_oos_snapshots_fund_code"), table_name="advisor_oos_snapshots")
    op.drop_table("advisor_oos_snapshots")
