"""add advisor positions table

Revision ID: f0a1b2c3d4e5
Revises: e8f9a0b1c2d3
Create Date: 2026-06-02 12:00:00.000000+00:00
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f0a1b2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "e8f9a0b1c2d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JsonType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
_IdType = sa.Integer().with_variant(sa.BigInteger(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "advisor_positions",
        sa.Column("id", _IdType, autoincrement=True, nullable=False, comment="Advisor position unique identifier"),
        sa.Column("fund_code", sa.String(length=10), nullable=False, comment="Fund code"),
        sa.Column("market_value", sa.Numeric(precision=20, scale=2), nullable=False, server_default="0", comment="Current market value in CNY"),
        sa.Column("shares", sa.Numeric(precision=20, scale=4), nullable=False, server_default="0", comment="Current holding shares"),
        sa.Column("cost_basis", sa.Numeric(precision=20, scale=2), nullable=False, server_default="0", comment="Current cost basis in CNY"),
        sa.Column("buy_date", sa.Date(), nullable=True, comment="Original buy date if known"),
        sa.Column("source", sa.String(length=40), nullable=False, server_default="manual", comment="Latest update source: manual/import/api/history"),
        sa.Column("metadata_json", _JsonType, nullable=True, comment="Additional metadata for the saved position"),
        sa.Column("note", sa.Text(), nullable=True, comment="Optional note for this saved position"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True, comment="Creation timestamp (UTC)"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True, comment="Last update timestamp (UTC)"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_advisor_positions")),
        sa.UniqueConstraint("fund_code", name="uq_advisor_positions_fund_code"),
    )
    op.create_index(op.f("ix_advisor_positions_fund_code"), "advisor_positions", ["fund_code"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_advisor_positions_fund_code"), table_name="advisor_positions")
    op.drop_table("advisor_positions")
