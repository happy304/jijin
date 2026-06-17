"""add unique constraint to signals

Revision ID: a7b8c9d0e1f2
Revises: 7f8e9d0c1b2a
Create Date: 2026-05-25 20:30:00.000000+00:00

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, Sequence[str], None] = '7f8e9d0c1b2a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    dialect = conn.dialect.name
    inspector = sa.inspect(conn)
    if "signals" not in inspector.get_table_names():
        return

    if dialect == "sqlite":
        conn.execute(sa.text("""
            DELETE FROM signals
            WHERE id IN (
                SELECT s.id
                FROM signals s
                JOIN signals dup
                  ON s.id < dup.id
                 AND s.strategy_id = dup.strategy_id
                 AND s.fund_code = dup.fund_code
                 AND s.signal_date = dup.signal_date
                 AND s.direction = dup.direction
            )
        """))
    else:
        conn.execute(sa.text("""
            DELETE FROM signals s
            USING signals dup
            WHERE s.id < dup.id
              AND s.strategy_id = dup.strategy_id
              AND s.fund_code = dup.fund_code
              AND s.signal_date = dup.signal_date
              AND s.direction = dup.direction
        """))
    op.create_unique_constraint(
        'uq_signals_strategy_fund_date_direction',
        'signals',
        ['strategy_id', 'fund_code', 'signal_date', 'direction'],
    )


def downgrade() -> None:
    op.drop_constraint(
        'uq_signals_strategy_fund_date_direction',
        'signals',
        type_='unique',
    )
