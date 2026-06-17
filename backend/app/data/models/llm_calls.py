"""ORM model for the ``llm_calls`` table.

Stores audit records for every LLM API call made by the platform.
Each row captures the provider, model, use case, prompt/response,
token usage, cost, latency, and success status.

Requirements: 11.5
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.data.models import Base

# BigInteger on PostgreSQL (BIGSERIAL), Integer on SQLite (for autoincrement support)
_IdType = Integer().with_variant(BigInteger, "postgresql")


class LLMCall(Base):
    """Persistent audit record for a single LLM API call.

    Every call to a cloud LLM provider — whether successful or not —
    is logged here for cost tracking, debugging, and compliance.
    """

    __tablename__ = "llm_calls"

    # ------------------------------------------------------------------
    # Primary key
    # ------------------------------------------------------------------
    id: Mapped[int] = mapped_column(
        _IdType,
        primary_key=True,
        autoincrement=True,
        comment="Unique call identifier",
    )

    # ------------------------------------------------------------------
    # Provider & model
    # ------------------------------------------------------------------
    provider: Mapped[str | None] = mapped_column(
        String(40),
        nullable=True,
        index=True,
        comment="LLM provider name (e.g. openai, anthropic, deepseek)",
    )
    model: Mapped[str | None] = mapped_column(
        String(80),
        nullable=True,
        comment="Model identifier (e.g. gpt-4o, claude-3-5-sonnet)",
    )

    # ------------------------------------------------------------------
    # Use case & prompt
    # ------------------------------------------------------------------
    use_case: Mapped[str | None] = mapped_column(
        String(60),
        nullable=True,
        index=True,
        comment="Use case identifier (e.g. announcement_parse, nl_query)",
    )
    prompt_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
        comment="SHA-256 hash of the prompt for cache key correlation",
    )
    prompt_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Full prompt text sent to the LLM",
    )
    response_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Full response text received from the LLM",
    )

    # ------------------------------------------------------------------
    # Token usage & cost
    # ------------------------------------------------------------------
    prompt_tokens: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Number of prompt/input tokens",
    )
    completion_tokens: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Number of completion/output tokens",
    )
    cost_usd: Mapped[float | None] = mapped_column(
        Numeric(10, 6),
        nullable=True,
        comment="Estimated cost in USD",
    )

    # ------------------------------------------------------------------
    # Performance & status
    # ------------------------------------------------------------------
    latency_ms: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Request latency in milliseconds",
    )
    success: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
        comment="Whether the call succeeded",
    )
    error_msg: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Error message if the call failed",
    )

    # ------------------------------------------------------------------
    # Timestamps
    # ------------------------------------------------------------------
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        server_default=func.now(),
        comment="Timestamp when the call was recorded (UTC)",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<LLMCall id={self.id!r} provider={self.provider!r} "
            f"model={self.model!r} use_case={self.use_case!r} "
            f"success={self.success!r}>"
        )
