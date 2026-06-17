"""add multi objective diagnostics to advisor oos snapshots

Revision ID: d7e8f9a0b1c2
Revises: c6d7e8f9a0b1
Create Date: 2026-05-29 09:00:00.000000+00:00
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "d7e8f9a0b1c2"
down_revision: Union[str, Sequence[str], None] = "c6d7e8f9a0b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JsonType = sa.JSON().with_variant(postgresql.JSONB, "postgresql")


def upgrade() -> None:
    op.add_column(
        "advisor_oos_snapshots",
        sa.Column(
            "multi_objective_score",
            _JsonType,
            nullable=True,
            comment="Composite multi-objective OOS robustness score",
        ),
    )
    op.add_column(
        "advisor_oos_snapshots",
        sa.Column(
            "multi_objective_components",
            _JsonType,
            nullable=True,
            comment="Component scores used to build multi_objective_score",
        ),
    )
    op.add_column(
        "advisor_oos_snapshots",
        sa.Column(
            "multi_objective_eliminated",
            _JsonType,
            nullable=True,
            comment="Whether this snapshot would be eliminated by multi-objective guardrails",
        ),
    )
    op.add_column(
        "advisor_oos_snapshots",
        sa.Column(
            "multi_objective_reasons",
            _JsonType,
            nullable=True,
            comment="Reasons for multi-objective elimination or penalties",
        ),
    )


def downgrade() -> None:
    op.drop_column("advisor_oos_snapshots", "multi_objective_reasons")
    op.drop_column("advisor_oos_snapshots", "multi_objective_eliminated")
    op.drop_column("advisor_oos_snapshots", "multi_objective_components")
    op.drop_column("advisor_oos_snapshots", "multi_objective_score")
