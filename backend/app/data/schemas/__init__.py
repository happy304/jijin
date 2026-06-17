"""Data transfer objects (Pydantic v2) for the Fund Quant Platform.

These DTOs are the canonical in-process representation of fund data.
They are used by:
- Data providers (EastMoney, AkShare) as return types
- Repository layer as input/output types
- API response models (via inheritance or direct use)
- Celery task payloads (JSON-serialisable)

All monetary values use ``Decimal`` to preserve precision.
All date-only fields use ``datetime.date``.
All timestamp fields use ``datetime.datetime`` (timezone-aware).
"""

from __future__ import annotations

from app.data.schemas.funds import (
    Announcement,
    AnnouncementCategory,
    DividendRecord,
    FeeTier,
    FeeType,
    FundMeta,
    FundStatus,
    FundType,
    HoldingPosition,
    HoldingSnapshot,
    NavRecord,
    NavStatus,
)

__all__ = [
    "Announcement",
    "AnnouncementCategory",
    "DividendRecord",
    "FeeTier",
    "FeeType",
    "FundMeta",
    "FundStatus",
    "FundType",
    "HoldingPosition",
    "HoldingSnapshot",
    "NavRecord",
    "NavStatus",
]
