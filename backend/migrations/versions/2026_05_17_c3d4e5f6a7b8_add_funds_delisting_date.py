"""add funds.delisting_date column

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-05-17

This column tracks the date on which a fund was delisted or closed.
NULL means the fund is still active. Used by the backtest engine to
force liquidation on this date (avoids ghost positions in delisted
funds) and by fund discovery / 4433 ranking to mitigate survivorship
bias by including the histories of closed funds.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "funds",
        sa.Column(
            "delisting_date",
            sa.Date(),
            nullable=True,
            comment=(
                "Fund delisting/closure date (NULL = still active). "
                "Used by backtest engine to force liquidation on this date "
                "and by discovery to reduce survivorship bias."
            ),
        ),
    )
    # Index helps the discovery / backtest engine quickly partition active
    # vs delisted funds.
    op.create_index(
        "idx_funds_delisting",
        "funds",
        ["delisting_date"],
        postgresql_where=sa.text("delisting_date IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_funds_delisting", table_name="funds")
    op.drop_column("funds", "delisting_date")
