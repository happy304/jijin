"""ORM model for the ``benchmark_nav`` table.

存储基准指数的日净值/点位数据，用于回测中的基准对比。

支持的基准指数：
- 000300: 沪深300
- 000905: 中证500
- 000016: 上证50
- H11001: 中证全债
- 000012: 国债指数

Indexes:
* ``idx_benchmark_code_date`` — (index_code, trade_date DESC)
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.data.models import Base


class BenchmarkNav(Base):
    """基准指数日净值/点位记录。

    复合主键 (index_code, trade_date)。
    """

    __tablename__ = "benchmark_nav"

    # ------------------------------------------------------------------
    # Primary key (composite)
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # 数据字段
    # ------------------------------------------------------------------
    close: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 4),
        nullable=True,
        comment="收盘点位/净值",
    )
    daily_return: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 6),
        nullable=True,
        comment="日收益率（小数形式）",
    )

    # ------------------------------------------------------------------
    # 元数据
    # ------------------------------------------------------------------
    index_name: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="指数名称",
    )
    source: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        comment="数据源标识",
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        server_default="NOW()",
        comment="入库时间",
    )

    # ------------------------------------------------------------------
    # Indexes
    # ------------------------------------------------------------------
    __table_args__ = (
        Index("idx_benchmark_code_date", "index_code", "trade_date"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<BenchmarkNav index_code={self.index_code!r} "
            f"trade_date={self.trade_date!r} close={self.close!r}>"
        )


# ---------------------------------------------------------------------------
# 预定义基准列表
# ---------------------------------------------------------------------------

BENCHMARK_INDICES = {
    "000300": "沪深300",
    "000905": "中证500",
    "000016": "上证50",
    "H11001": "中证全债",
    "000012": "国债指数",
}
