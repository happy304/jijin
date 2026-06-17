"""ORM model for the ``fund_announcements`` table.

Stores fund announcements. The ``category`` and ``parsed_data`` fields
are populated by the LLM pipeline (task 7.6) after initial ingestion.

Mirrors the DDL in design.md §2.1 and the Pydantic DTO ``Announcement``.

Requirements: 2.8, 11.10.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import BigInteger, Boolean, Date, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.data.models import Base


class FundAnnouncement(Base):
    """A fund announcement, optionally enriched by LLM classification.

    The ``id`` is a BIGSERIAL surrogate key. ``fund_code`` is not a
    strict FK at the database level (to allow ingesting announcements
    before the fund metadata row exists), but the application layer
    enforces referential integrity.
    """

    __tablename__ = "fund_announcements"

    # ------------------------------------------------------------------
    # Primary key
    # ------------------------------------------------------------------
    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
        comment="Surrogate primary key (BIGSERIAL)",
    )

    # ------------------------------------------------------------------
    # Core fields
    # ------------------------------------------------------------------
    fund_code: Mapped[str | None] = mapped_column(
        String(10),
        nullable=True,
        comment="Fund code — FK to funds.code (enforced at app layer)",
    )
    title: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Announcement title",
    )
    category: Mapped[str | None] = mapped_column(
        String(40),
        nullable=True,
        comment=(
            "LLM-classified category: LIMIT_PURCHASE/SUSPEND/DIVIDEND/"
            "MANAGER_CHANGE/CONTRACT_CHANGE/OTHER"
        ),
    )
    publish_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="Publication date",
    )
    content_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="URL to full announcement text",
    )

    # ------------------------------------------------------------------
    # LLM-enriched fields
    # ------------------------------------------------------------------
    parsed_data: Mapped[dict | None] = mapped_column(  # type: ignore[type-arg]
        JSON,
        nullable=True,
        comment="Structured fields extracted by LLM pipeline (JSONB on PostgreSQL)",
    )
    requires_review: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="false",
        comment="True when LLM confidence is low or rule-engine cross-check failed",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<FundAnnouncement id={self.id!r} "
            f"fund_code={self.fund_code!r} "
            f"category={self.category!r} "
            f"publish_date={self.publish_date!r}>"
        )
