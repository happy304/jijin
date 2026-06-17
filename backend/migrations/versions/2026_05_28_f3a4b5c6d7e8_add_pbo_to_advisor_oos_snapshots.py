"""add pbo diagnostics to advisor oos snapshots

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-05-28 12:00:00.000000+00:00
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f3a4b5c6d7e8"
down_revision: Union[str, Sequence[str], None] = "e2f3a4b5c6d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JsonType = sa.JSON().with_variant(postgresql.JSONB, "postgresql")


def upgrade() -> None:
    op.add_column(
        "advisor_oos_snapshots",
        sa.Column(
            "pbo",
            _JsonType,
            nullable=True,
            comment="Probability of Backtest Overfitting from CPCV/PBO diagnostics",
        ),
    )
    op.add_column(
        "advisor_oos_snapshots",
        sa.Column(
            "cpcv_n_paths",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Number of CPCV paths evaluated for PBO",
        ),
    )
    op.add_column(
        "advisor_oos_snapshots",
        sa.Column(
            "cpcv_avg_oos_sharpe",
            _JsonType,
            nullable=True,
            comment="Average OOS Sharpe across CPCV paths",
        ),
    )
    op.add_column(
        "advisor_oos_snapshots",
        sa.Column(
            "cpcv_std_oos_sharpe",
            _JsonType,
            nullable=True,
            comment="Std of OOS Sharpe across CPCV paths",
        ),
    )
    op.add_column(
        "advisor_oos_snapshots",
        sa.Column(
            "cpcv_avg_is_sharpe",
            _JsonType,
            nullable=True,
            comment="Average IS Sharpe across CPCV paths",
        ),
    )


def downgrade() -> None:
    op.drop_column("advisor_oos_snapshots", "cpcv_avg_is_sharpe")
    op.drop_column("advisor_oos_snapshots", "cpcv_std_oos_sharpe")
    op.drop_column("advisor_oos_snapshots", "cpcv_avg_oos_sharpe")
    op.drop_column("advisor_oos_snapshots", "cpcv_n_paths")
    op.drop_column("advisor_oos_snapshots", "pbo")
