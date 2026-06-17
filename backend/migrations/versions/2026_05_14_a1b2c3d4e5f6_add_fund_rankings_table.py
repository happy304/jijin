"""add_fund_rankings_table

Creates the ``fund_rankings`` table for storing daily ranking snapshots
from Eastmoney's fund ranking API. Used by the auto-discovery task to
dynamically maintain the fund watchlist.

Indexes:
  - idx_ranking_date_metric — (snapshot_date, sort_metric) for daily queries
  - idx_ranking_code        — fund_code for per-fund lookups

Revision ID: a1b2c3d4e5f6
Revises: 9435bbd59a7f
Create Date: 2026-05-14 14:00:00.000000+00:00
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "9435bbd59a7f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fund_rankings",
        sa.Column(
            "fund_code",
            sa.String(length=10),
            nullable=False,
            comment="基金代码",
        ),
        sa.Column(
            "snapshot_date",
            sa.Date(),
            nullable=False,
            comment="排名快照日期",
        ),
        sa.Column(
            "sort_metric",
            sa.String(length=20),
            nullable=False,
            comment="排序维度: 6yzf/1nzf/3nzf/jnzf 等",
        ),
        sa.Column(
            "rank_position",
            sa.Integer(),
            nullable=False,
            comment="排名位次（1=第一名）",
        ),
        sa.Column(
            "fund_name",
            sa.String(length=100),
            nullable=True,
            comment="基金名称",
        ),
        sa.Column(
            "fund_type",
            sa.String(length=20),
            nullable=True,
            comment="基金类型筛选条件: all/stock/mixed/bond/index/qdii/fof",
        ),
        sa.Column(
            "daily_return",
            sa.Numeric(precision=10, scale=6),
            nullable=True,
            comment="日涨幅",
        ),
        sa.Column(
            "weekly_return",
            sa.Numeric(precision=10, scale=6),
            nullable=True,
            comment="近1周涨幅",
        ),
        sa.Column(
            "monthly_return",
            sa.Numeric(precision=10, scale=6),
            nullable=True,
            comment="近1月涨幅",
        ),
        sa.Column(
            "quarterly_return",
            sa.Numeric(precision=10, scale=6),
            nullable=True,
            comment="近3月涨幅",
        ),
        sa.Column(
            "half_year_return",
            sa.Numeric(precision=10, scale=6),
            nullable=True,
            comment="近6月涨幅",
        ),
        sa.Column(
            "yearly_return",
            sa.Numeric(precision=10, scale=6),
            nullable=True,
            comment="近1年涨幅",
        ),
        sa.Column(
            "unit_nav",
            sa.Numeric(precision=12, scale=6),
            nullable=True,
            comment="单位净值",
        ),
        sa.Column(
            "accum_nav",
            sa.Numeric(precision=12, scale=6),
            nullable=True,
            comment="累计净值",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default="NOW()",
            nullable=True,
            comment="记录创建时间",
        ),
        sa.PrimaryKeyConstraint(
            "fund_code",
            "snapshot_date",
            "sort_metric",
            name=op.f("pk_fund_rankings"),
        ),
    )

    # Indexes for common query patterns
    op.create_index(
        "idx_ranking_date_metric",
        "fund_rankings",
        ["snapshot_date", "sort_metric"],
        unique=False,
    )
    op.create_index(
        "idx_ranking_code",
        "fund_rankings",
        ["fund_code"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_ranking_code", table_name="fund_rankings")
    op.drop_index("idx_ranking_date_metric", table_name="fund_rankings")
    op.drop_table("fund_rankings")
