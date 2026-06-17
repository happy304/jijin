"""ORM model for the ``funds`` table (standard PostgreSQL relation).

Mirrors the DDL in design.md §2.1 and the Pydantic DTO ``FundMeta``
defined in ``app.data.schemas.funds``.

Indexes
-------
* ``idx_funds_type``    — fund_type column (requirement 2.8 / design §2.1)
* ``idx_funds_company`` — company_id column (requirement 2.8 / design §2.1)

Both indexes are declared inline so Alembic autogenerate picks them up
and emits the correct ``CREATE INDEX`` statements in the migration.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.data.models import Base


class Fund(Base):
    """Persistent representation of a public fund's metadata.

    Column names and types match the DDL in design.md §2.1 exactly so
    that Alembic autogenerate produces a clean, no-diff migration after
    the initial creation.
    """

    __tablename__ = "funds"

    # ------------------------------------------------------------------
    # Primary key
    # ------------------------------------------------------------------
    code: Mapped[str] = mapped_column(
        String(10),
        primary_key=True,
        comment="Fund code (e.g. '000001')",
    )

    # ------------------------------------------------------------------
    # Core metadata
    # ------------------------------------------------------------------
    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Fund full name",
    )
    fund_type: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        comment="Broad category: stock/bond/mixed/money/qdii/fof/index",
    )
    sub_type: Mapped[str | None] = mapped_column(
        String(40),
        nullable=True,
        comment="Detailed sub-category",
    )
    company_id: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        comment="Fund management company identifier",
    )
    inception_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="Fund inception date",
    )
    delisting_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment=(
            "Fund delisting/closure date (NULL = still active). "
            "Used by backtest engine to force liquidation on this date "
            "and by discovery to reduce survivorship bias."
        ),
    )
    benchmark: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Benchmark description",
    )

    # ------------------------------------------------------------------
    # Fee rates (stored as decimal fractions, e.g. 0.015 = 1.5%)
    # ------------------------------------------------------------------
    management_fee: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 4),
        nullable=True,
        comment="Annual management fee rate",
    )
    custodian_fee: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 4),
        nullable=True,
        comment="Annual custodian fee rate",
    )

    # ------------------------------------------------------------------
    # Operational fields
    # ------------------------------------------------------------------
    currency: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        server_default="CNY",
        comment="Settlement currency",
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="active",
        comment="Lifecycle status: active/suspended/delisted",
    )
    is_purchasable: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="true",
        comment="Whether new subscriptions are currently open",
    )
    purchase_limit: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2),
        nullable=True,
        comment="Single-transaction purchase cap (CNY); NULL = no limit",
    )

    # ------------------------------------------------------------------
    # Provenance
    # ------------------------------------------------------------------
    source: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        comment="Data source identifier (e.g. 'eastmoney', 'akshare')",
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        server_default="NOW()",
        comment="Last update timestamp (UTC)",
    )

    # ------------------------------------------------------------------
    # Table-level indexes (design §2.1)
    # ------------------------------------------------------------------
    __table_args__ = (
        Index("idx_funds_type", "fund_type"),
        Index("idx_funds_company", "company_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Fund code={self.code!r} name={self.name!r} type={self.fund_type!r}>"
