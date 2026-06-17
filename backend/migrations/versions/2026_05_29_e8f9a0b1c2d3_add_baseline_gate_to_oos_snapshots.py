"""add baseline comparison gate to advisor oos snapshots

Revision ID: e8f9a0b1c2d3
Revises: d7e8f9a0b1c2
Create Date: 2026-05-29 10:30:00.000000+00:00
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "e8f9a0b1c2d3"
down_revision: Union[str, Sequence[str], None] = "d7e8f9a0b1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JsonType = sa.JSON().with_variant(postgresql.JSONB, "postgresql")


def upgrade() -> None:
    op.add_column(
        "advisor_oos_snapshots",
        sa.Column(
            "baseline_adjusted_score",
            _JsonType,
            nullable=True,
            comment="Complexity-penalized score after comparing against simple baselines",
        ),
    )
    op.add_column(
        "advisor_oos_snapshots",
        sa.Column(
            "baseline_comparison",
            _JsonType,
            nullable=True,
            comment="Diagnostics comparing candidate snapshot against DCA/risk-parity/momentum baselines",
        ),
    )
    op.add_column(
        "advisor_oos_snapshots",
        sa.Column(
            "baseline_passed",
            _JsonType,
            nullable=True,
            comment="Whether the snapshot passed the simple-baseline release gate",
        ),
    )
    op.add_column(
        "advisor_oos_snapshots",
        sa.Column(
            "baseline_reasons",
            _JsonType,
            nullable=True,
            comment="Reasons emitted by the baseline comparison gate",
        ),
    )


def downgrade() -> None:
    op.drop_column("advisor_oos_snapshots", "baseline_reasons")
    op.drop_column("advisor_oos_snapshots", "baseline_passed")
    op.drop_column("advisor_oos_snapshots", "baseline_comparison")
    op.drop_column("advisor_oos_snapshots", "baseline_adjusted_score")
