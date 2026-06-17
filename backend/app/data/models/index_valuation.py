"""ORM model for the ``index_valuation`` table.

存储指数的历史估值数据（PE/PB/股息率），用于替代
"净值百分位=估值"的不准确方法。

数据来源：中证指数公司（免费公开数据）
适用场景：指数基金的真实估值分析

Requirements: 估值分析改进
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, Index, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.data.models import Base

# BigInteger on PostgreSQL, Integer on SQLite
_IdType = Integer().with_variant(BigInteger, "postgresql")


class IndexValuation(Base):
    """指数每日估值数据。"""

    __tablename__ = "index_valuation"

    # 复合主键
    index_code: Mapped[str] = mapped_column(
        String(10),
        primary_key=True,
        comment="指数代码（如 000300 = 沪深300）",
    )
    trade_date: Mapped[date] = mapped_column(
        Date,
        primary_key=True,
        comment="交易日期",
    )

    # 估值指标
    pe_ttm: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 4),
        nullable=True,
        comment="市盈率 TTM（滚动12个月）",
    )
    pe_percentile: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 4),
        nullable=True,
        comment="PE 历史百分位（0~1，基于近10年数据）",
    )
    pb: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 4),
        nullable=True,
        comment="市净率",
    )
    pb_percentile: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 4),
        nullable=True,
        comment="PB 历史百分位（0~1）",
    )
    dividend_yield: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 4),
        nullable=True,
        comment="股息率",
    )
    roe: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 4),
        nullable=True,
        comment="净资产收益率 ROE",
    )

    # 元数据
    index_name: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="指数名称",
    )
    source: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        comment="数据来源",
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        server_default=func.now(),
        comment="入库时间",
    )

    __table_args__ = (
        Index("idx_valuation_code_date", "index_code", "trade_date"),
    )

    def __repr__(self) -> str:
        return (
            f"<IndexValuation index={self.index_code!r} "
            f"date={self.trade_date!r} pe={self.pe_ttm!r}>"
        )
