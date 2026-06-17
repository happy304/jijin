"""add fund_meta_history table

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-05-17

Point-in-time fund metadata snapshots. See ``fund_meta_history.py`` for
field-level documentation. This table is purely additive — existing
queries against ``funds`` continue to see the live mutable row.

Backfill recipe (per fund, run once):
    INSERT INTO fund_meta_history
        (fund_code, effective_date, manager_id, company_id, fund_size,
         status, is_purchasable, purchase_limit, benchmark, management_fee,
         source)
    SELECT code, COALESCE(inception_date, CURRENT_DATE),
           NULL, company_id, NULL, status, is_purchasable, purchase_limit,
           benchmark, management_fee, 'backfill_initial'
      FROM funds;
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fund_meta_history",
        sa.Column("fund_code", sa.String(10), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("manager_id", sa.String(255), nullable=True),
        sa.Column("company_id", sa.String(20), nullable=True),
        sa.Column("fund_size", sa.Numeric(18, 2), nullable=True),
        sa.Column("status", sa.String(20), nullable=True),
        sa.Column("is_purchasable", sa.Boolean, nullable=True),
        sa.Column("purchase_limit", sa.Numeric(18, 2), nullable=True),
        sa.Column("benchmark", sa.Text, nullable=True),
        sa.Column("management_fee", sa.Numeric(6, 4), nullable=True),
        sa.Column("source", sa.String(20), nullable=True),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=True,
            # CURRENT_TIMESTAMP is portable across PostgreSQL and SQLite
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("fund_code", "effective_date", name="pk_fund_meta_history"),
    )
    op.create_index("idx_fmh_effective", "fund_meta_history", ["effective_date"])
    op.create_index(
        "idx_fmh_fund_effective",
        "fund_meta_history",
        ["fund_code", "effective_date"],
    )


def downgrade() -> None:
    op.drop_index("idx_fmh_fund_effective", table_name="fund_meta_history")
    op.drop_index("idx_fmh_effective", table_name="fund_meta_history")
    op.drop_table("fund_meta_history")
