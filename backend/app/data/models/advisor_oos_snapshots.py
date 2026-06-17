"""ORM model for advisor OOS validation snapshots.

Stores the latest walk-forward / OOS validation summary per fund and
risk level, so the advisor can reuse recent out-of-sample evidence as a
second anti-overfitting layer.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, Integer, JSON, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.data.models import Base

_JsonType = JSON().with_variant(JSONB, "postgresql")
_IdType = Integer().with_variant(BigInteger, "postgresql")


class AdvisorOOSSnapshot(Base):
    """Persistent representation of latest OOS validation snapshot."""

    __tablename__ = "advisor_oos_snapshots"

    id: Mapped[int] = mapped_column(
        _IdType,
        primary_key=True,
        autoincrement=True,
        comment="Snapshot unique identifier",
    )
    fund_code: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
        comment="Fund code",
    )
    risk_level: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
        comment="Risk level: conservative/moderate/aggressive",
    )
    updated_at: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        index=True,
        comment="Snapshot logical update date",
    )
    snapshot_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        index=True,
        comment="Logical validation snapshot date; defaults to updated_at for legacy rows",
    )
    config_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
        comment="Hash of advisor / validation config used by this snapshot",
    )
    data_version: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="Input data version or data window fingerprint",
    )
    validation_window: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="Validation window descriptor, e.g. 2024-01-01~2026-05-27;n=750;folds=5",
    )
    requested_days: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Requested lookback days when validation ran",
    )
    actual_trading_days: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Actual trading days used in validation",
    )
    pbo: Mapped[float | None] = mapped_column(
        _JsonType,
        nullable=True,
        comment="Probability of Backtest Overfitting from CPCV/PBO diagnostics",
    )
    cpcv_n_paths: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Number of CPCV paths evaluated for PBO",
    )
    cpcv_avg_oos_sharpe: Mapped[float | None] = mapped_column(
        _JsonType,
        nullable=True,
        comment="Average OOS Sharpe across CPCV paths",
    )
    cpcv_std_oos_sharpe: Mapped[float | None] = mapped_column(
        _JsonType,
        nullable=True,
        comment="Std of OOS Sharpe across CPCV paths",
    )
    cpcv_avg_is_sharpe: Mapped[float | None] = mapped_column(
        _JsonType,
        nullable=True,
        comment="Average IS Sharpe across CPCV paths",
    )
    multi_objective_score: Mapped[float | None] = mapped_column(
        _JsonType,
        nullable=True,
        comment="Composite multi-objective OOS robustness score",
    )
    multi_objective_components: Mapped[dict | None] = mapped_column(
        _JsonType,
        nullable=True,
        comment="Component scores used to build multi_objective_score",
    )
    multi_objective_eliminated: Mapped[bool | None] = mapped_column(
        _JsonType,
        nullable=True,
        comment="Whether this snapshot would be eliminated by multi-objective guardrails",
    )
    multi_objective_reasons: Mapped[list | None] = mapped_column(
        _JsonType,
        nullable=True,
        comment="Reasons for multi-objective elimination or penalties",
    )
    baseline_adjusted_score: Mapped[float | None] = mapped_column(
        _JsonType,
        nullable=True,
        comment="Complexity-penalized score after comparing against simple baselines",
    )
    baseline_comparison: Mapped[dict | None] = mapped_column(
        _JsonType,
        nullable=True,
        comment="Diagnostics comparing candidate snapshot against DCA/risk-parity/momentum baselines",
    )
    baseline_passed: Mapped[bool | None] = mapped_column(
        _JsonType,
        nullable=True,
        comment="Whether the snapshot passed the simple-baseline release gate",
    )
    baseline_reasons: Mapped[list | None] = mapped_column(
        _JsonType,
        nullable=True,
        comment="Reasons emitted by the baseline comparison gate",
    )
    avg_oos_ic: Mapped[float | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=True,
        comment="Average OOS IC stored as scalar JSON for cross-dialect simplicity",
    )
    avg_is_ic: Mapped[float | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=True,
        comment="Average IS IC stored as scalar JSON for cross-dialect simplicity",
    )
    ic_degradation: Mapped[float | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=True,
        comment="OOS IC / IS IC stored as scalar JSON",
    )
    avg_oos_buy_hit_rate: Mapped[float | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=True,
        comment="Average OOS buy hit rate stored as scalar JSON",
    )
    avg_oos_sell_hit_rate: Mapped[float | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=True,
        comment="Average OOS sell hit rate stored as scalar JSON",
    )
    total_oos_signals: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Total OOS signals",
    )
    total_oos_buy: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Total OOS buy signals",
    )
    total_oos_sell: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Total OOS sell signals",
    )
    warnings_json: Mapped[list | None] = mapped_column(
        _JsonType,
        nullable=True,
        comment="Validation warnings",
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        server_default=func.now(),
        comment="Creation timestamp (UTC)",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<AdvisorOOSSnapshot fund_code={self.fund_code!r} "
            f"risk={self.risk_level!r} updated_at={self.updated_at!r}>"
        )
