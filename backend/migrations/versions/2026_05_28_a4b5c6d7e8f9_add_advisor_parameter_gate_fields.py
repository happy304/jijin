"""add advisor parameter gate fields

Revision ID: a4b5c6d7e8f9
Revises: f3a4b5c6d7e8
Create Date: 2026-05-28 13:30:00.000000+00:00
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a4b5c6d7e8f9"
down_revision: Union[str, Sequence[str], None] = "f3a4b5c6d7e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JsonType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column(
        "advisor_learned_params_versions",
        sa.Column(
            "config_hash",
            sa.String(length=64),
            nullable=True,
            comment="Stable hash of the full learned-parameter payload",
        ),
    )
    op.add_column(
        "advisor_learned_params_versions",
        sa.Column(
            "gate_status",
            sa.String(length=30),
            nullable=False,
            server_default="not_evaluated",
            comment="Parameter release gate status: approved/shadow_only/blocked/not_evaluated",
        ),
    )
    op.add_column(
        "advisor_learned_params_versions",
        sa.Column(
            "gate_action",
            sa.String(length=30),
            nullable=False,
            server_default="shadow_only",
            comment="Gate action: allow_default/shadow_only/block_default",
        ),
    )
    op.add_column(
        "advisor_learned_params_versions",
        sa.Column(
            "gate_reason",
            sa.Text(),
            nullable=True,
            comment="Human readable parameter release gate reason",
        ),
    )
    op.add_column(
        "advisor_learned_params_versions",
        sa.Column(
            "gate_checked_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Timestamp when the parameter gate was evaluated",
        ),
    )
    op.add_column(
        "advisor_learned_params_versions",
        sa.Column(
            "gate_metrics",
            _JsonType,
            nullable=True,
            comment="OOS/PBO metrics used by the parameter release gate",
        ),
    )
    op.create_index(
        op.f("ix_advisor_learned_params_versions_config_hash"),
        "advisor_learned_params_versions",
        ["config_hash"],
        unique=False,
    )
    op.create_index(
        op.f("ix_advisor_learned_params_versions_gate_status"),
        "advisor_learned_params_versions",
        ["gate_status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_advisor_learned_params_versions_gate_status"),
        table_name="advisor_learned_params_versions",
    )
    op.drop_index(
        op.f("ix_advisor_learned_params_versions_config_hash"),
        table_name="advisor_learned_params_versions",
    )
    op.drop_column("advisor_learned_params_versions", "gate_metrics")
    op.drop_column("advisor_learned_params_versions", "gate_checked_at")
    op.drop_column("advisor_learned_params_versions", "gate_reason")
    op.drop_column("advisor_learned_params_versions", "gate_action")
    op.drop_column("advisor_learned_params_versions", "gate_status")
    op.drop_column("advisor_learned_params_versions", "config_hash")
