"""ORM models for backtest-related tables.

Mirrors the DDL in design.md §2.1:
- ``backtest_runs``   — tracks each backtest execution
- ``backtest_equity`` — daily equity curve per run
- ``backtest_trades`` — individual trade records per run

Requirements: 7.3, 7.4
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.data.models import Base

# Portable type aliases (PostgreSQL vs SQLite in tests)
from sqlalchemy import JSON

_JsonType = JSON().with_variant(JSONB, "postgresql")
_IdType = Integer().with_variant(BigInteger, "postgresql")


class BacktestRun(Base):
    """Persistent representation of a backtest execution."""

    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(
        _IdType,
        primary_key=True,
        autoincrement=True,
        comment="Backtest run unique identifier",
    )
    strategy_id: Mapped[int | None] = mapped_column(
        _IdType,
        nullable=True,
        comment="Reference to strategies.id",
    )
    start_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="Backtest start date",
    )
    end_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="Backtest end date",
    )
    initial_capital: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 2),
        nullable=True,
        comment="Initial capital amount",
    )
    status: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        default="pending",
        comment="Status: pending/running/done/failed",
    )
    progress: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2),
        nullable=True,
        default=Decimal("0"),
        comment="Progress percentage 0-100",
    )
    metrics: Mapped[dict | None] = mapped_column(
        _JsonType,
        nullable=True,
        comment="Key metrics summary (JSON)",
    )
    error_msg: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Error message if failed",
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Execution start timestamp",
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Execution finish timestamp",
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        server_default=func.now(),
        comment="Record creation timestamp",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<BacktestRun id={self.id!r} status={self.status!r}>"


class BacktestEquity(Base):
    """Daily equity curve record for a backtest run."""

    __tablename__ = "backtest_equity"

    run_id: Mapped[int] = mapped_column(
        _IdType,
        primary_key=True,
        comment="Reference to backtest_runs.id",
    )
    trade_date: Mapped[date] = mapped_column(
        Date,
        primary_key=True,
        comment="Trading date",
    )
    equity: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 2),
        nullable=True,
        comment="Total equity value",
    )
    cash: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 2),
        nullable=True,
        comment="Cash balance",
    )
    position_value: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 2),
        nullable=True,
        comment="Total position market value",
    )
    benchmark_value: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 2),
        nullable=True,
        comment="Benchmark value for comparison",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<BacktestEquity run_id={self.run_id!r} date={self.trade_date!r}>"


class BacktestTrade(Base):
    """Individual trade record for a backtest run."""

    __tablename__ = "backtest_trades"

    run_id: Mapped[int] = mapped_column(
        _IdType,
        primary_key=True,
        comment="Reference to backtest_runs.id",
    )
    trade_id: Mapped[int] = mapped_column(
        _IdType,
        primary_key=True,
        comment="Trade sequence number within run",
    )
    order_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="Order placement date",
    )
    confirm_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="Order confirmation date",
    )
    fund_code: Mapped[str | None] = mapped_column(
        String(10),
        nullable=True,
        comment="Fund code",
    )
    direction: Mapped[str | None] = mapped_column(
        String(10),
        nullable=True,
        comment="Trade direction: subscribe/redeem",
    )
    amount: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 2),
        nullable=True,
        comment="Trade amount",
    )
    shares: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 4),
        nullable=True,
        comment="Trade shares",
    )
    nav: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 6),
        nullable=True,
        comment="NAV at trade time",
    )
    fee: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 4),
        nullable=True,
        comment="Transaction fee",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<BacktestTrade run_id={self.run_id!r} trade_id={self.trade_id!r}>"
