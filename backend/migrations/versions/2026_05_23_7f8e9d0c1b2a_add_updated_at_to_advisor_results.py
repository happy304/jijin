"""add updated_at to advisor_results

Revision ID: 7f8e9d0c1b2a
Revises: 1a2b3c4d5e6f
Create Date: 2026-05-23

Add an updated_at timestamp to advisor_results so refreshed advisor
history records can expose their latest regeneration time.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "7f8e9d0c1b2a"
down_revision: Union[str, Sequence[str], None] = "1a2b3c4d5e6f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "advisor_results",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.text("now()"),
            comment="Last update timestamp (UTC)",
        ),
    )
    bind = op.get_bind()
    current_ts = "CURRENT_TIMESTAMP" if bind.dialect.name == "sqlite" else "now()"
    op.execute(
        sa.text(
            "UPDATE advisor_results "
            f"SET updated_at = COALESCE(created_at, {current_ts}) "
            "WHERE updated_at IS NULL"
        )
    )


def downgrade() -> None:
    op.drop_column("advisor_results", "updated_at")
