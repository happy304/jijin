"""add_advisor_results_table

Revision ID: f6a7b8c9d0e1
Revises: b39fac44aa72
Create Date: 2026-05-19 15:00:00.000000+00:00

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, Sequence[str], None] = 'b39fac44aa72'
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
    op.create_table(
        'advisor_results',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False, comment='Result unique identifier'),
        sa.Column('advice_date', sa.Date(), nullable=False, comment='Date the advice was generated'),
        sa.Column('fund_codes', json_type, nullable=False, comment='List of fund codes analyzed'),
        sa.Column('total_capital', sa.Numeric(precision=20, scale=2), nullable=False, comment='Total capital used for analysis'),
        sa.Column('risk_level', sa.String(length=20), nullable=False, comment='Risk level: conservative/moderate/aggressive'),
        sa.Column('strategy_id', sa.BigInteger(), nullable=True, comment='Strategy ID if portfolio mode'),
        sa.Column('strategy_name', sa.String(length=100), nullable=True, comment='Strategy name if portfolio mode'),
        sa.Column('current_positions', json_type, nullable=True, comment='Current positions at time of analysis'),
        sa.Column('positions_detail', json_type, nullable=True, comment='Position details (amount, buy_date, cost)'),
        sa.Column('advices', json_type, nullable=False, comment='Full advice list'),
        sa.Column('summary', json_type, nullable=False, comment='Summary statistics'),
        sa.Column('note', sa.Text(), nullable=True, comment='User note/memo for this result'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True, comment='Creation timestamp (UTC)'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_advisor_results_advice_date', 'advisor_results', ['advice_date'])


def downgrade() -> None:
    op.drop_index('ix_advisor_results_advice_date', table_name='advisor_results')
    op.drop_table('advisor_results')
