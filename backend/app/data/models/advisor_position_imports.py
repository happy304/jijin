"""ORM model for advisor position import history.

Stores a compact audit trail for each file-based position import so the
frontend can show when a snapshot was imported, from which file, and with what
success/failure summary.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, JSON, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.data.models import Base

_JsonType = JSON().with_variant(JSONB, "postgresql")
_IdType = Integer().with_variant(BigInteger(), "postgresql")


class AdvisorPositionImport(Base):
    """Persistent record of one advisor position import."""

    __tablename__ = "advisor_position_imports"

    id: Mapped[int] = mapped_column(
        _IdType,
        primary_key=True,
        autoincrement=True,
        comment="Advisor position import unique identifier",
    )
    filename: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Original uploaded filename",
    )
    file_format: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="Import file format: csv/xls/xlsx",
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="Import status: completed/partial/failed",
    )
    total_rows: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Total parsed data rows",
    )
    imported_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Successfully imported position row count",
    )
    failed_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Failed row count",
    )
    replaced_position_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Final persisted position count after replacement",
    )
    rows_json: Mapped[list | None] = mapped_column(
        _JsonType,
        nullable=True,
        comment="Per-row import results returned to frontend",
    )
    positions_json: Mapped[list | None] = mapped_column(
        _JsonType,
        nullable=True,
        comment="Normalized positions successfully imported in this batch",
    )
    metadata_json: Mapped[dict | None] = mapped_column(
        _JsonType,
        nullable=True,
        comment="Additional import metadata",
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        server_default=func.now(),
        comment="Creation timestamp (UTC)",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AdvisorPositionImport id={self.id!r} filename={self.filename!r} status={self.status!r}>"
