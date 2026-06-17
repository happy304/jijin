"""ORM model for the ``fund_rankings`` table.

Stores daily ranking snapshots fetched from Eastmoney's fund ranking API.
Used by the auto-discovery task to dynamically maintain the fund watchlist.

Each row represents one fund's ranking data on a specific snapshot date,
sorted by a specific metric (e.g. 6-month return).

Requirements: auto-discovery feature
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.data.models import Base


class FundRanking(Base):
    """A single fund's ranking snapshot on a given date.

    The composite primary key ``(fund_code, snapshot_date, sort_metric)``
    ensures one row per fund per day per ranking dimension.
    """

    __tablename__ = "fund_rankings"

    # ------------------------------------------------------------------
    # Primary key (composite)
    # ------------------------------------------------------------------
    fund_code: Mapped[str] = mapped_column(
        String(10),
        primary_key=True,
        comment="基金代码",
    )
    snapshot_date: Mapped[date] = mapped_column(
        Date,
        primary_key=True,
        comment="排名快照日期",
    )
    sort_metric: Mapped[str] = mapped_column(
        String(20),
        primary_key=True,
        comment="排序维度: 6yzf/1nzf/3nzf/jnzf 等",
    )

    # ------------------------------------------------------------------
    # Ranking data
    # ------------------------------------------------------------------
    rank_position: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="排名位次（1=第一名）",
    )
    fund_name: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="基金名称",
    )
    fund_type: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        comment="基金类型筛选条件: all/stock/mixed/bond/index/qdii/fof",
    )

    # ------------------------------------------------------------------
    # Performance metrics (as decimal fractions)
    # ------------------------------------------------------------------
    daily_return: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 6),
        nullable=True,
        comment="日涨幅",
    )
    weekly_return: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 6),
        nullable=True,
        comment="近1周涨幅",
    )
    monthly_return: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 6),
        nullable=True,
        comment="近1月涨幅",
    )
    quarterly_return: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 6),
        nullable=True,
        comment="近3月涨幅",
    )
    half_year_return: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 6),
        nullable=True,
        comment="近6月涨幅",
    )
    yearly_return: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 6),
        nullable=True,
        comment="近1年涨幅",
    )

    # ------------------------------------------------------------------
    # NAV at snapshot time
    # ------------------------------------------------------------------
    unit_nav: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 6),
        nullable=True,
        comment="单位净值",
    )
    accum_nav: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 6),
        nullable=True,
        comment="累计净值",
    )

    # ------------------------------------------------------------------
    # Provenance
    # ------------------------------------------------------------------
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        server_default="NOW()",
        comment="记录创建时间",
    )

    # ------------------------------------------------------------------
    # Indexes
    # ------------------------------------------------------------------
    __table_args__ = (
        Index("idx_ranking_date_metric", "snapshot_date", "sort_metric"),
        Index("idx_ranking_code", "fund_code"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<FundRanking fund_code={self.fund_code!r} "
            f"snapshot_date={self.snapshot_date!r} "
            f"sort_metric={self.sort_metric!r} "
            f"rank={self.rank_position}>"
        )
