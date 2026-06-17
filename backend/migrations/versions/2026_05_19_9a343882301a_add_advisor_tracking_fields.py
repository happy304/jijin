"""add advisor tracking fields

Revision ID: 9a343882301a
Revises: f6a7b8c9d0e1
Create Date: 2026-05-19 17:42:25.735746+00:00

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '9a343882301a'
down_revision: Union[str, Sequence[str], None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json_type() -> sa.JSON:
    return (
        postgresql.JSONB(astext_type=sa.Text())
        if op.get_bind().dialect.name == 'postgresql'
        else sa.JSON()
    )


def upgrade() -> None:
    json_type = _json_type()
    # 只添加两个新字段到 advisor_results 表
    op.add_column('advisor_results', sa.Column(
        'tracked_returns',
        json_type,
        nullable=True,
        comment='Tracked actual returns after advice: {fund_code: {return_5d, return_10d, ...}}',
    ))
    op.add_column('advisor_results', sa.Column(
        'tracked_at',
        sa.DateTime(timezone=True),
        nullable=True,
        comment='Last tracking update timestamp',
    ))


def downgrade() -> None:
    op.drop_column('advisor_results', 'tracked_at')
    op.drop_column('advisor_results', 'tracked_returns')
