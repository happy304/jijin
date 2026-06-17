"""ORM model for the ``strategies`` table.

Mirrors the DDL in design.md §2.1 — stores user-defined strategy
configurations with JSONB params and universe fields.

Requirements: 7.5
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, JSON, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.data.models import Base

# Use JSONB on PostgreSQL, fall back to JSON on other dialects (e.g. SQLite in tests)
_JsonType = JSON().with_variant(JSONB, "postgresql")

# BigInteger on PostgreSQL (BIGSERIAL), Integer on SQLite (for autoincrement support)
_IdType = Integer().with_variant(BigInteger, "postgresql")


class Strategy(Base):
    """Persistent representation of a user-defined strategy configuration."""

    __tablename__ = "strategies"

    # ------------------------------------------------------------------
    # Primary key
    # ------------------------------------------------------------------
    id: Mapped[int] = mapped_column(
        _IdType,
        primary_key=True,
        autoincrement=True,
        comment="Strategy unique identifier",
    )

    # ------------------------------------------------------------------
    # Core fields
    # ------------------------------------------------------------------
    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Strategy display name",
    )
    strategy_type: Mapped[str | None] = mapped_column(
        String(40),
        nullable=True,
        comment="Strategy type: dca/momentum/risk_parity/mean_variance/timing/fof",
    )
    params: Mapped[dict] = mapped_column(
        _JsonType,
        nullable=False,
        comment="Strategy parameters (validated via Pydantic JSON Schema)",
    )
    universe: Mapped[dict] = mapped_column(
        _JsonType,
        nullable=False,
        comment="Fund pool configuration",
    )
    benchmark: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        comment="Benchmark index code",
    )
    created_by: Mapped[str | None] = mapped_column(
        String(40),
        nullable=True,
        comment="Creator identifier",
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        server_default=func.now(),
        comment="Creation timestamp (UTC)",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Strategy id={self.id!r} name={self.name!r} type={self.strategy_type!r}>"
