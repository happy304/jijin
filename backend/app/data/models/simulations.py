"""ORM models for Monte Carlo simulation tables.

Provides:
- ``simulation_runs``   — tracks each simulation execution
- ``simulation_paths``  — stores percentile path data for visualization

Requirements: Monte Carlo simulation prediction feature
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


class SimulationRun(Base):
    """Persistent representation of a Monte Carlo simulation execution."""

    __tablename__ = "simulation_runs"

    id: Mapped[int] = mapped_column(
        _IdType,
        primary_key=True,
        autoincrement=True,
        comment="Simulation run unique identifier",
    )
    strategy_id: Mapped[int | None] = mapped_column(
        _IdType,
        nullable=True,
        comment="Reference to strategies.id",
    )
    # Simulation parameters
    horizon_days: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=252,
        comment="Forecast horizon in trading days",
    )
    num_simulations: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=10000,
        comment="Number of Monte Carlo paths",
    )
    method: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="gbm",
        comment="Simulation method: gbm/bootstrap/hybrid",
    )
    initial_capital: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 2),
        nullable=True,
        comment="Initial capital amount",
    )
    target_return: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 4),
        nullable=True,
        comment="Target return rate (e.g. 0.15 = 15%)",
    )
    confidence_levels: Mapped[dict | None] = mapped_column(
        _JsonType,
        nullable=True,
        comment="Confidence levels for VaR/CVaR (e.g. [0.95, 0.99])",
    )
    # Lookback window for parameter estimation
    lookback_days: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        default=504,
        comment="Historical lookback days for parameter estimation (default 2 years)",
    )
    strategy_snapshot: Mapped[dict | None] = mapped_column(
        _JsonType,
        nullable=True,
        comment="Strategy snapshot captured at simulation submission time",
    )
    # Status tracking
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
    # Results
    metrics: Mapped[dict | None] = mapped_column(
        _JsonType,
        nullable=True,
        comment="Simulation result metrics (JSON)",
    )
    percentile_paths: Mapped[dict | None] = mapped_column(
        _JsonType,
        nullable=True,
        comment="Percentile paths for fan chart visualization (JSON)",
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
        return f"<SimulationRun id={self.id!r} status={self.status!r}>"
