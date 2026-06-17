"""add benchmark_nav table

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-05-16

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'benchmark_nav',
        sa.Column('index_code', sa.String(10), nullable=False, comment='指数代码'),
        sa.Column('trade_date', sa.Date(), nullable=False, comment='交易日期'),
        sa.Column('close', sa.Numeric(14, 4), nullable=True, comment='收盘点位/净值'),
        sa.Column('daily_return', sa.Numeric(10, 6), nullable=True, comment='日收益率'),
        sa.Column('index_name', sa.String(50), nullable=True, comment='指数名称'),
        sa.Column('source', sa.String(20), nullable=True, comment='数据源标识'),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.text('NOW()'),
            comment='入库时间',
        ),
        sa.PrimaryKeyConstraint('index_code', 'trade_date', name='pk_benchmark_nav'),
    )
    op.create_index(
        'idx_benchmark_code_date',
        'benchmark_nav',
        ['index_code', 'trade_date'],
    )


def downgrade() -> None:
    op.drop_index('idx_benchmark_code_date', table_name='benchmark_nav')
    op.drop_table('benchmark_nav')
