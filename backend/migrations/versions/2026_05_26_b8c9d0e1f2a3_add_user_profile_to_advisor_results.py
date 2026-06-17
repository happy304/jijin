"""add user_profile to advisor_results

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-05-26 00:00:00.000000+00:00

Store the investment profile used when generating advisor results so
history refresh and form reload can preserve profile-based constraints.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, Sequence[str], None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_json_type = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column(
        "advisor_results",
        sa.Column(
            "user_profile",
            _json_type,
            nullable=True,
            comment="User investment profile at time of analysis",
        ),
    )


def downgrade() -> None:
    op.drop_column("advisor_results", "user_profile")
