"""User-level learning for the Advisor execution loop.

This module intentionally learns only low-risk UX/execution preferences from
user execution records: amount pacing, batching cadence and explanation style.
It does **not** change model signal weights or bypass data-quality/OOS gates.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models.advisor_execution_records import AdvisorExecutionRecord
from app.data.models.advisor_user_learning_profiles import AdvisorUserLearningProfile


DEFAULT_PROFILE_KEY = "default"
_ACTIONABLE_INTENTS = {"subscribe", "redeem"}
_EXECUTED_STATUSES = {"executed", "partial"}


@dataclass
class UserLearningSafeguards:
    """Anti-overfitting limits for user-level preference learning."""

    min_samples: int = 5
    confidence_full_samples: int = 30
    min_amount_scale: float = 0.75
    max_amount_scale: float = 1.10
    max_single_update_delta: float = 0.20
    min_batch_count: int = 2
    max_batch_count: int = 4
    min_batch_interval_days: int = 5
    max_batch_interval_days: int = 14


@dataclass
class UserLearningProfileSnapshot:
    """Serializable user-level Advisor preference snapshot."""

    profile_key: str = DEFAULT_PROFILE_KEY
    sample_count: int = 0
    confidence: float = 0.0
    adoption_rate: float = 0.0
    partial_rate: float = 0.0
    avg_execution_ratio: float | None = None
    avg_execution_lag_days: float | None = None
    amount_scale: float = 1.0
    preferred_execution_style: str = "neutral"
    preferred_batch_count: int | None = None
    preferred_batch_interval_days: int | None = None
    explanation_style: str = "balanced"
    safeguards: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    learning_log: list[str] = field(default_factory=list)
    last_learned_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AdvisorUserLearningService:
    """Learns per-user execution preferences from recorded executions."""

    @staticmethod
    def normalize_profile_key(value: Any = None) -> str:
        key = str(value or "").strip()
        return key[:128] if key else DEFAULT_PROFILE_KEY

    @classmethod
    async def load_profile(
        cls,
        session: AsyncSession,
        *,
        profile_key: str | None = None,
    ) -> UserLearningProfileSnapshot | None:
        key = cls.normalize_profile_key(profile_key)
        result = await session.execute(
            select(AdvisorUserLearningProfile).where(
                AdvisorUserLearningProfile.profile_key == key
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return cls._snapshot_from_row(row)

    @classmethod
    async def learn_and_persist(
        cls,
        session: AsyncSession,
        *,
        profile_key: str | None = None,
        safeguards: UserLearningSafeguards | None = None,
        limit: int = 200,
    ) -> UserLearningProfileSnapshot:
        """Recompute and persist the latest user-level learning snapshot."""

        key = cls.normalize_profile_key(profile_key)
        guard = safeguards or UserLearningSafeguards()
        result = await session.execute(
            select(AdvisorExecutionRecord)
            .order_by(
                AdvisorExecutionRecord.executed_date.desc().nullslast(),
                AdvisorExecutionRecord.created_at.desc().nullslast(),
                AdvisorExecutionRecord.id.desc(),
            )
            .limit(limit)
        )
        records = list(result.scalars().all())
        snapshot = cls._learn_from_records(records, key, guard)

        existing_result = await session.execute(
            select(AdvisorUserLearningProfile).where(
                AdvisorUserLearningProfile.profile_key == key
            )
        )
        row = existing_result.scalar_one_or_none()
        if row is None:
            row = AdvisorUserLearningProfile(profile_key=key)
            session.add(row)

        row.sample_count = snapshot.sample_count
        row.confidence = snapshot.confidence
        row.adoption_rate = snapshot.adoption_rate
        row.partial_rate = snapshot.partial_rate
        row.avg_execution_ratio = snapshot.avg_execution_ratio
        row.avg_execution_lag_days = snapshot.avg_execution_lag_days
        row.amount_scale = snapshot.amount_scale
        row.preferred_execution_style = snapshot.preferred_execution_style
        row.preferred_batch_count = snapshot.preferred_batch_count
        row.preferred_batch_interval_days = snapshot.preferred_batch_interval_days
        row.explanation_style = snapshot.explanation_style
        row.safeguards = snapshot.safeguards
        row.metrics = snapshot.metrics
        row.learning_log = snapshot.learning_log
        row.last_learned_at = datetime.now(timezone.utc)
        snapshot.last_learned_at = row.last_learned_at.isoformat()
        await session.flush()
        return snapshot

    @classmethod
    async def load_or_learn(
        cls,
        session: AsyncSession,
        *,
        profile_key: str | None = None,
        min_samples: int | None = None,
    ) -> UserLearningProfileSnapshot | None:
        """Load a persisted profile, recomputing when missing or below sample floor."""

        current = await cls.load_profile(session, profile_key=profile_key)
        if current is not None and (min_samples is None or current.sample_count >= min_samples):
            return current
        learned = await cls.learn_and_persist(session, profile_key=profile_key)
        return learned if learned.sample_count > 0 else current

    @classmethod
    def apply_to_user_profile(
        cls,
        user_profile: dict[str, Any],
        learned: UserLearningProfileSnapshot | None,
    ) -> dict[str, Any]:
        """Attach learned preferences to the request profile without overriding raw user inputs."""

        payload = dict(user_profile or {})
        if learned is None:
            payload.setdefault("advisor_personalization", {"status": "not_available"})
            return payload
        payload["advisor_personalization"] = learned.to_dict()
        return payload

    @classmethod
    def _learn_from_records(
        cls,
        records: list[AdvisorExecutionRecord],
        profile_key: str,
        guard: UserLearningSafeguards,
    ) -> UserLearningProfileSnapshot:
        actionable = [
            record for record in records
            if str(record.trade_intent or "hold") in _ACTIONABLE_INTENTS
            or str(record.advice_action or "hold") in {"buy", "sell"}
        ]
        sample_count = len(actionable)
        confidence = 0.0 if sample_count < guard.min_samples else min(1.0, sample_count / guard.confidence_full_samples)
        log: list[str] = []
        metrics: dict[str, Any] = {
            "total_records": len(records),
            "actionable_records": sample_count,
            "min_samples": guard.min_samples,
            "confidence_full_samples": guard.confidence_full_samples,
        }

        if sample_count == 0:
            log.append("暂无可学习的交易执行记录，保留默认执行偏好")
            return UserLearningProfileSnapshot(
                profile_key=profile_key,
                safeguards=asdict(guard),
                metrics=metrics,
                learning_log=log,
                last_learned_at=datetime.now(timezone.utc).isoformat(),
            )

        adopted = [r for r in actionable if str(r.execution_status) in _EXECUTED_STATUSES]
        partial = [r for r in actionable if str(r.execution_status) == "partial"]
        adoption_rate = len(adopted) / sample_count
        partial_rate = len(partial) / sample_count

        execution_ratios: list[float] = []
        lag_days: list[int] = []
        for record in adopted:
            suggested = float(record.suggested_amount or 0.0)
            executed = float(record.executed_amount or 0.0)
            if suggested > 0 and executed > 0:
                execution_ratios.append(float(np.clip(executed / suggested, 0.0, 1.5)))
            if record.executed_date is not None and record.advice_date is not None:
                lag_days.append(max(0, (record.executed_date - record.advice_date).days))

        avg_ratio = float(np.mean(execution_ratios)) if execution_ratios else None
        avg_lag = float(np.mean(lag_days)) if lag_days else None
        metrics.update({
            "executed_records": len(adopted),
            "partial_records": len(partial),
            "execution_ratio_samples": len(execution_ratios),
            "execution_lag_samples": len(lag_days),
        })

        amount_scale = 1.0
        if confidence > 0 and avg_ratio is not None:
            raw_scale = avg_ratio
            if adoption_rate < 0.45:
                raw_scale = min(raw_scale, 0.85)
            shrunk = 1.0 + (raw_scale - 1.0) * min(0.35, confidence * 0.35)
            amount_scale = float(np.clip(shrunk, guard.min_amount_scale, guard.max_amount_scale))
            log.append(f"按历史执行金额比例 {avg_ratio:.1%} 学习金额节奏，收缩后乘数为 {amount_scale:.2f}")
        elif sample_count < guard.min_samples:
            log.append(f"样本量不足({sample_count}/{guard.min_samples})，仅记录偏好，不调整金额")

        preferred_style = "neutral"
        batch_count: int | None = None
        batch_interval: int | None = None
        if confidence > 0:
            if partial_rate >= 0.25 or (avg_ratio is not None and avg_ratio < 0.75):
                preferred_style = "batch"
                batch_count = int(np.clip(round(2 + confidence), guard.min_batch_count, guard.max_batch_count))
                batch_interval = int(np.clip(10, guard.min_batch_interval_days, guard.max_batch_interval_days))
                log.append("用户历史上更常部分执行，后续交易计划优先采用分批与较小单笔")
            if adoption_rate < 0.45 or (avg_lag is not None and avg_lag >= 3):
                preferred_style = "slower_cadence" if preferred_style == "neutral" else preferred_style
                batch_interval = int(np.clip(12, guard.min_batch_interval_days, guard.max_batch_interval_days))
                log.append("历史采纳率偏低或执行滞后，后续提醒/计划节奏将更保守")
            if avg_ratio is not None and avg_ratio < 0.5:
                preferred_style = "small_steps"
                batch_count = max(batch_count or 3, 3)
                batch_interval = batch_interval or 10
                log.append("用户通常只执行小比例金额，后续建议会强调小步执行")

        explanation_style = "balanced"
        if adoption_rate < 0.5 or partial_rate >= 0.25:
            explanation_style = "risk_first"
        elif adoption_rate >= 0.8 and (avg_lag is None or avg_lag <= 1):
            explanation_style = "action_first"

        return UserLearningProfileSnapshot(
            profile_key=profile_key,
            sample_count=sample_count,
            confidence=round(float(confidence), 4),
            adoption_rate=round(float(adoption_rate), 4),
            partial_rate=round(float(partial_rate), 4),
            avg_execution_ratio=round(avg_ratio, 4) if avg_ratio is not None else None,
            avg_execution_lag_days=round(avg_lag, 2) if avg_lag is not None else None,
            amount_scale=round(amount_scale, 4),
            preferred_execution_style=preferred_style,
            preferred_batch_count=batch_count,
            preferred_batch_interval_days=batch_interval,
            explanation_style=explanation_style,
            safeguards=asdict(guard),
            metrics=metrics,
            learning_log=log,
            last_learned_at=datetime.now(timezone.utc).isoformat(),
        )

    @staticmethod
    def _snapshot_from_row(row: AdvisorUserLearningProfile) -> UserLearningProfileSnapshot:
        return UserLearningProfileSnapshot(
            profile_key=row.profile_key,
            sample_count=int(row.sample_count or 0),
            confidence=float(row.confidence or 0.0),
            adoption_rate=float(row.adoption_rate or 0.0),
            partial_rate=float(row.partial_rate or 0.0),
            avg_execution_ratio=row.avg_execution_ratio,
            avg_execution_lag_days=row.avg_execution_lag_days,
            amount_scale=float(row.amount_scale or 1.0),
            preferred_execution_style=row.preferred_execution_style or "neutral",
            preferred_batch_count=row.preferred_batch_count,
            preferred_batch_interval_days=row.preferred_batch_interval_days,
            explanation_style=row.explanation_style or "balanced",
            safeguards=dict(row.safeguards or {}),
            metrics=dict(row.metrics or {}),
            learning_log=list(row.learning_log or []),
            last_learned_at=row.last_learned_at.isoformat() if row.last_learned_at else None,
        )


__all__ = [
    "AdvisorUserLearningService",
    "DEFAULT_PROFILE_KEY",
    "UserLearningProfileSnapshot",
    "UserLearningSafeguards",
]
