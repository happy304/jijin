"""add_index_valuation_table

Revision ID: b39fac44aa72
Revises: e5f6a7b8c9d0
Create Date: 2026-05-19 11:11:09.148068+00:00

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b39fac44aa72'
down_revision: Union[str, Sequence[str], None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'index_valuation',
        sa.Column('index_code', sa.String(length=10), nullable=False, comment='指数代码（如 000300 = 沪深300）'),
        sa.Column('trade_date', sa.Date(), nullable=False, comment='交易日期'),
        sa.Column('pe_ttm', sa.Numeric(precision=10, scale=4), nullable=True, comment='市盈率 TTM（滚动12个月）'),
        sa.Column('pe_percentile', sa.Numeric(precision=6, scale=4), nullable=True, comment='PE 历史百分位（0~1，基于近10年数据）'),
        sa.Column('pb', sa.Numeric(precision=10, scale=4), nullable=True, comment='市净率'),
        sa.Column('pb_percentile', sa.Numeric(precision=6, scale=4), nullable=True, comment='PB 历史百分位（0~1）'),
        sa.Column('dividend_yield', sa.Numeric(precision=6, scale=4), nullable=True, comment='股息率'),
        sa.Column('roe', sa.Numeric(precision=8, scale=4), nullable=True, comment='净资产收益率 ROE'),
        sa.Column('index_name', sa.String(length=50), nullable=True, comment='指数名称'),
        sa.Column('source', sa.String(length=20), nullable=True, comment='数据来源'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True, comment='入库时间'),
        sa.PrimaryKeyConstraint('index_code', 'trade_date', name=op.f('pk_index_valuation')),
    )
    op.create_index('idx_valuation_code_date', 'index_valuation', ['index_code', 'trade_date'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_valuation_code_date', table_name='index_valuation')
    op.drop_table('index_valuation')
