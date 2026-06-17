"""Advisor reminder computation and persistence helpers.

This module turns saved advisor history + execution follow-up context into a
reusable reminder stream. It supports two use cases:

1. compute reminders on the fly for history detail pages
2. persist / refresh reminder rows for cross-end reminder inbox workflows
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.data.models.advisor_reminders import AdvisorReminder

REMINDER_STATUS_ACTIVE = "active"
REMINDER_STATUS_RESOLVED = "resolved"
REMINDER_STATUS_DISMISSED = "dismissed"
REMINDER_STATUSES = {
    REMINDER_STATUS_ACTIVE,
    REMINDER_STATUS_RESOLVED,
    REMINDER_STATUS_DISMISSED,
}

REMINDER_CATEGORIES = {"validity", "risk", "execution", "plan", "system"}
REMINDER_SEVERITIES = {"info", "warning", "error", "success"}
REMINDER_SEVERITY_ORDER = {"success": 0, "info": 1, "warning": 2, "error": 3}
REMINDER_NOTIFICATION_CHANNELS = {"email", "wecom", "telegram"}
DEFAULT_REMINDER_PROFILE_KEY = "default"


@dataclass
class ReminderCandidate:
    advisor_result_id: int
    fund_code: str | None
    category: str
    reminder_type: str
    severity: str
    title: str
    description: str
    trigger_date: date
    payload: dict[str, Any] | None = None

    @property
    def dedupe_key(self) -> tuple[int, str | None, str, str]:
        return (
            int(self.advisor_result_id),
            self.fund_code or None,
            str(self.category),
            str(self.reminder_type),
        )


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _serialize_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _serialize_date(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _days_until(target_date: date | None, *, today: date) -> int | None:
    if target_date is None:
        return None
    return (target_date - today).days


def serialize_advisor_reminder(reminder: AdvisorReminder) -> dict[str, Any]:
    return {
        "id": int(reminder.id),
        "advisor_result_id": int(reminder.advisor_result_id),
        "fund_code": reminder.fund_code,
        "category": reminder.category,
        "reminder_type": reminder.reminder_type,
        "severity": reminder.severity,
        "status": reminder.status,
        "title": reminder.title,
        "description": reminder.description,
        "payload": reminder.payload_json,
        "trigger_date": _serialize_date(reminder.trigger_date),
        "resolved_at": _serialize_datetime(reminder.resolved_at),
        "dismissed_at": _serialize_datetime(reminder.dismissed_at),
        "created_at": _serialize_datetime(reminder.created_at),
        "updated_at": _serialize_datetime(reminder.updated_at),
    }


def normalize_reminder_status(value: str | None) -> str:
    normalized = str(value or REMINDER_STATUS_ACTIVE).strip().lower()
    if normalized not in REMINDER_STATUSES:
        allowed = ", ".join(sorted(REMINDER_STATUSES))
        raise ValueError(f"reminder status 必须是以下之一: {allowed}")
    return normalized


def build_advisor_reminder_candidates(
    row: Any,
    *,
    execution_summary: dict[str, Any] | None = None,
    execution_plan_status: dict[str, Any] | None = None,
    today: date | None = None,
) -> list[ReminderCandidate]:
    """Compute current reminder candidates for one saved advisor result."""
    today = today or date.today()
    advices = list(getattr(row, "advices", None) or [])
    result_id = int(getattr(row, "id"))
    candidates: list[ReminderCandidate] = []

    expiring_funds: list[str] = []
    expired_funds: list[str] = []
    poor_quality_funds: list[str] = []
    high_overfit_funds: list[str] = []
    batch_plan_funds: list[str] = []
    watch_only_funds: list[str] = []

    for advice in advices:
        if not isinstance(advice, dict):
            continue
        fund_code = str(advice.get("fund_code") or "").strip() or None
        validity = advice.get("validity") if isinstance(advice.get("validity"), dict) else {}
        valid_until = _parse_iso_date(validity.get("valid_until"))
        days = _days_until(valid_until, today=today)
        if days is not None and days < 0 and fund_code:
            expired_funds.append(fund_code)
        elif days is not None and days <= 2 and fund_code:
            expiring_funds.append(fund_code)

        data_quality = advice.get("data_quality") if isinstance(advice.get("data_quality"), dict) else {}
        if data_quality.get("status") == "poor" and fund_code:
            poor_quality_funds.append(fund_code)

        overfit = advice.get("overfit_risk") if isinstance(advice.get("overfit_risk"), dict) else {}
        if overfit.get("level") == "high" and fund_code:
            high_overfit_funds.append(fund_code)

        trade_plan = advice.get("trade_plan") if isinstance(advice.get("trade_plan"), dict) else {}
        if trade_plan.get("execution_type") == "batch" and fund_code:
            batch_plan_funds.append(fund_code)

        if str(advice.get("action") or "") == "watch" and fund_code:
            watch_only_funds.append(fund_code)

    if expired_funds:
        candidates.append(
            ReminderCandidate(
                advisor_result_id=result_id,
                fund_code=None,
                category="validity",
                reminder_type="validity_expired",
                severity="error",
                title="部分建议已过有效期",
                description=f"{len(expired_funds)} 条基金建议已超过有效期，建议先刷新再执行。",
                trigger_date=today,
                payload={"fund_codes": sorted(expired_funds)},
            )
        )
    elif expiring_funds:
        candidates.append(
            ReminderCandidate(
                advisor_result_id=result_id,
                fund_code=None,
                category="validity",
                reminder_type="validity_expiring",
                severity="warning",
                title="建议即将到期",
                description=f"{len(expiring_funds)} 条基金建议将在 2 天内到期，执行前请优先核对时效。",
                trigger_date=today,
                payload={"fund_codes": sorted(expiring_funds)},
            )
        )

    if poor_quality_funds:
        candidates.append(
            ReminderCandidate(
                advisor_result_id=result_id,
                fund_code=None,
                category="risk",
                reminder_type="poor_quality",
                severity="error",
                title="存在数据质量较差的建议",
                description=f"{len(poor_quality_funds)} 条建议的数据质量为“较差”，更适合先观察或等待数据更新。",
                trigger_date=today,
                payload={"fund_codes": sorted(poor_quality_funds)},
            )
        )

    if high_overfit_funds:
        candidates.append(
            ReminderCandidate(
                advisor_result_id=result_id,
                fund_code=None,
                category="risk",
                reminder_type="overfit_high",
                severity="warning",
                title="存在高过拟合风险信号",
                description=f"{len(high_overfit_funds)} 条建议带有高过拟合风险，建议结合 OOS/PBO 审计谨慎执行。",
                trigger_date=today,
                payload={"fund_codes": sorted(high_overfit_funds)},
            )
        )

    if batch_plan_funds:
        candidates.append(
            ReminderCandidate(
                advisor_result_id=result_id,
                fund_code=None,
                category="plan",
                reminder_type="batch_plan_present",
                severity="info",
                title="包含分批执行计划",
                description=f"当前有 {len(batch_plan_funds)} 条建议采用分批方式执行，请关注后续批次任务。",
                trigger_date=today,
                payload={"fund_codes": sorted(batch_plan_funds)},
            )
        )

    if watch_only_funds:
        candidates.append(
            ReminderCandidate(
                advisor_result_id=result_id,
                fund_code=None,
                category="plan",
                reminder_type="watch_actions",
                severity="info",
                title="包含观察建议",
                description=f"{len(watch_only_funds)} 条建议当前更适合持续观察，不建议立即大额操作。",
                trigger_date=today,
                payload={"fund_codes": sorted(watch_only_funds)},
            )
        )

    summary = execution_summary or {}
    if not summary or summary.get("status") == "no_execution_records":
        candidates.append(
            ReminderCandidate(
                advisor_result_id=result_id,
                fund_code=None,
                category="execution",
                reminder_type="execution_missing",
                severity="info",
                title="这条历史建议还没有执行记录",
                description="补充执行记录后，系统才能区分模型建议表现与用户实际采纳/偏离。",
                trigger_date=today,
                payload=None,
            )
        )
    elif int(summary.get("significant_deviation_count") or 0) > 0:
        candidates.append(
            ReminderCandidate(
                advisor_result_id=result_id,
                fund_code=None,
                category="execution",
                reminder_type="execution_drift",
                severity="warning",
                title="存在明显执行偏离",
                description=f"本次建议有 {int(summary.get('significant_deviation_count') or 0)} 条执行记录与建议金额存在明显偏离，复盘时请结合偏离原因查看。",
                trigger_date=today,
                payload={"significant_deviation_count": int(summary.get("significant_deviation_count") or 0)},
            )
        )

    plan_status = execution_plan_status or {}
    plan_summary = plan_status.get("summary") if isinstance(plan_status.get("summary"), dict) else {}
    by_fund = plan_status.get("by_fund") if isinstance(plan_status.get("by_fund"), dict) else {}
    all_tasks: list[dict[str, Any]] = []
    for fund_data in by_fund.values():
        if not isinstance(fund_data, dict):
            continue
        tasks = fund_data.get("tasks") if isinstance(fund_data.get("tasks"), list) else []
        all_tasks.extend([task for task in tasks if isinstance(task, dict)])

    pending_tasks = [task for task in all_tasks if str(task.get("status") or "") == "pending"]
    overdue_tasks = []
    upcoming_tasks = []
    for task in pending_tasks:
        scheduled_date = _parse_iso_date(task.get("scheduled_date"))
        days = _days_until(scheduled_date, today=today)
        if days is None:
            continue
        if days < 0:
            overdue_tasks.append(task)
        elif days <= 3:
            upcoming_tasks.append(task)

    if overdue_tasks:
        candidates.append(
            ReminderCandidate(
                advisor_result_id=result_id,
                fund_code=None,
                category="plan",
                reminder_type="plan_overdue",
                severity="warning",
                title="存在逾期未处理的计划任务",
                description=f"{len(overdue_tasks)} 个分批/执行任务已超过计划日期，建议补记执行结果或刷新建议。",
                trigger_date=today,
                payload={
                    "task_count": len(overdue_tasks),
                    "task_keys": sorted(str(task.get("task_key") or "") for task in overdue_tasks if task.get("task_key")),
                },
            )
        )
    elif upcoming_tasks:
        candidates.append(
            ReminderCandidate(
                advisor_result_id=result_id,
                fund_code=None,
                category="plan",
                reminder_type="plan_upcoming",
                severity="info",
                title="近期有待执行计划",
                description=f"{len(upcoming_tasks)} 个任务将在未来 3 天内到期，可提前安排申购/赎回。",
                trigger_date=today,
                payload={
                    "task_count": len(upcoming_tasks),
                    "task_keys": sorted(str(task.get("task_key") or "") for task in upcoming_tasks if task.get("task_key")),
                },
            )
        )

    if int(plan_summary.get("task_count") or 0) > 0 and int(plan_summary.get("pending_count") or 0) == 0:
        candidates.append(
            ReminderCandidate(
                advisor_result_id=result_id,
                fund_code=None,
                category="plan",
                reminder_type="plan_all_resolved",
                severity="success",
                title="计划任务均已处理完毕",
                description=f"本次建议的 {int(plan_summary.get('task_count') or 0)} 个计划任务当前都已完成或跳过。",
                trigger_date=today,
                payload={
                    "task_count": int(plan_summary.get("task_count") or 0),
                    "done_count": int(plan_summary.get("done_count") or 0),
                    "skipped_count": int(plan_summary.get("skipped_count") or 0),
                },
            )
        )

    return candidates[:12]


async def load_advisor_reminders(
    session: Any,
    *,
    status: str | None = None,
    category: str | None = None,
    severity: str | None = None,
    advisor_result_id: int | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[AdvisorReminder], int]:
    safe_page = max(1, int(page or 1))
    safe_page_size = max(1, int(page_size or 20))
    query = select(AdvisorReminder)
    if status:
        query = query.where(AdvisorReminder.status == status)
    if category:
        query = query.where(AdvisorReminder.category == category)
    if severity:
        query = query.where(AdvisorReminder.severity == severity)
    if advisor_result_id is not None:
        query = query.where(AdvisorReminder.advisor_result_id == advisor_result_id)

    all_rows = list((await session.execute(query.order_by(
        AdvisorReminder.trigger_date.desc(),
        AdvisorReminder.updated_at.desc().nullslast(),
        AdvisorReminder.id.desc(),
    ))).scalars().all())
    total = len(all_rows)
    offset = (safe_page - 1) * safe_page_size
    return all_rows[offset: offset + safe_page_size], total


async def load_advisor_reminders_for_result(session: Any, advisor_result_id: int) -> list[AdvisorReminder]:
    result = await session.execute(
        select(AdvisorReminder)
        .where(AdvisorReminder.advisor_result_id == advisor_result_id)
        .order_by(
            AdvisorReminder.status.asc(),
            AdvisorReminder.trigger_date.desc(),
            AdvisorReminder.id.desc(),
        )
    )
    return list(result.scalars().all())


def _severity_rank(value: str | None) -> int:
    return REMINDER_SEVERITY_ORDER.get(str(value or "").lower(), 0)


def _severity_at_least(value: str | None, minimum: str | None) -> bool:
    if minimum is None:
        return True
    return _severity_rank(value) >= _severity_rank(minimum)


def _normalize_profile_key(value: str | None) -> str:
    return str(value or DEFAULT_REMINDER_PROFILE_KEY).strip() or DEFAULT_REMINDER_PROFILE_KEY


def _normalize_channels(value: list[str] | None) -> list[str] | None:
    if value is None:
        return None
    channels = sorted({str(item).strip().lower() for item in value if str(item or "").strip()})
    invalid = [item for item in channels if item not in REMINDER_NOTIFICATION_CHANNELS]
    if invalid:
        allowed = ", ".join(sorted(REMINDER_NOTIFICATION_CHANNELS))
        raise ValueError(f"通知通道必须是以下之一: {allowed}")
    return channels


def _normalize_categories(value: list[str] | None) -> list[str]:
    categories = sorted({str(item).strip().lower() for item in (value or []) if str(item or "").strip()})
    invalid = [item for item in categories if item not in REMINDER_CATEGORIES]
    if invalid:
        allowed = ", ".join(sorted(REMINDER_CATEGORIES))
        raise ValueError(f"提醒分类必须是以下之一: {allowed}")
    return categories


def _normalize_min_severity(value: str | None, *, allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    normalized = str(value or "warning").strip().lower()
    if normalized not in REMINDER_SEVERITIES:
        allowed = ", ".join(sorted(REMINDER_SEVERITIES))
        raise ValueError(f"min_severity 必须是以下之一: {allowed}")
    return normalized


# 主动提醒订阅/推送暂不启用（个人自用场景）。
# quiet_hours 字段仍保留在偏好表/API 中，后续恢复主动推送时再接入免打扰判断。


def serialize_advisor_reminder_preference(preference: Any | None, *, profile_key: str | None = None) -> dict[str, Any]:
    if preference is None:
        return {
            "profile_key": _normalize_profile_key(profile_key),
            "enabled": True,
            "min_severity": "warning",
            "lookahead_days": 3,
            "channels": None,
            "muted_categories": [],
            "quiet_hours": None,
            "created_at": None,
            "updated_at": None,
        }
    return {
        "id": int(preference.id),
        "profile_key": preference.profile_key,
        "enabled": bool(preference.enabled),
        "min_severity": preference.min_severity,
        "lookahead_days": int(preference.lookahead_days or 3),
        "channels": preference.channels_json,
        "muted_categories": preference.muted_categories_json or [],
        "quiet_hours": preference.quiet_hours_json,
        "created_at": _serialize_datetime(preference.created_at),
        "updated_at": _serialize_datetime(preference.updated_at),
    }


async def load_advisor_reminder_preference(session: Any, profile_key: str | None = None) -> Any | None:
    from app.data.models.advisor_reminder_preferences import AdvisorReminderPreference

    normalized_key = _normalize_profile_key(profile_key)
    result = await session.execute(
        select(AdvisorReminderPreference).where(AdvisorReminderPreference.profile_key == normalized_key)
    )
    return result.scalar_one_or_none()


async def upsert_advisor_reminder_preference(
    session: Any,
    *,
    profile_key: str | None = None,
    enabled: bool | None = None,
    min_severity: str | None = None,
    lookahead_days: int | None = None,
    channels: list[str] | None = None,
    muted_categories: list[str] | None = None,
    quiet_hours: dict[str, Any] | None = None,
) -> Any:
    from app.data.models.advisor_reminder_preferences import AdvisorReminderPreference

    normalized_key = _normalize_profile_key(profile_key)
    normalized_min_severity = _normalize_min_severity(min_severity) if min_severity is not None else "warning"
    normalized_channels = _normalize_channels(channels)
    normalized_muted_categories = _normalize_categories(muted_categories)
    safe_lookahead_days = max(0, min(30, int(lookahead_days if lookahead_days is not None else 3)))

    preference = await load_advisor_reminder_preference(session, normalized_key)
    if preference is None:
        preference = AdvisorReminderPreference(profile_key=normalized_key)
        session.add(preference)
    preference.enabled = True if enabled is None else bool(enabled)
    preference.min_severity = normalized_min_severity
    preference.lookahead_days = safe_lookahead_days
    preference.channels_json = normalized_channels
    preference.muted_categories_json = normalized_muted_categories
    preference.quiet_hours_json = quiet_hours
    await session.commit()
    await session.refresh(preference)
    return preference


async def build_advisor_reminder_digest(
    session: Any,
    *,
    days: int = 3,
    min_severity: str | None = None,
    include_info: bool = True,
    muted_categories: list[str] | None = None,
    limit: int = 50,
    today: date | None = None,
) -> dict[str, Any]:
    """Build a cross-end reminder digest from persisted active reminders.

    The digest is intentionally derived from server-side reminder rows instead
    of frontend local state so web/mobile/notification channels share the same
    source of truth.
    """
    today = today or date.today()
    safe_days = max(0, int(days or 0))
    safe_limit = max(1, int(limit or 50))
    due_before = today + timedelta(days=safe_days)
    effective_min_severity = min_severity or (None if include_info else "warning")
    muted_category_set = set(_normalize_categories(muted_categories))

    query = (
        select(AdvisorReminder)
        .where(AdvisorReminder.status == REMINDER_STATUS_ACTIVE)
        .where(AdvisorReminder.trigger_date <= due_before)
        .order_by(
            AdvisorReminder.trigger_date.asc(),
            AdvisorReminder.severity.desc(),
            AdvisorReminder.updated_at.desc().nullslast(),
            AdvisorReminder.id.desc(),
        )
        .limit(safe_limit)
    )
    rows = list((await session.execute(query)).scalars().all())
    filtered = [
        row
        for row in rows
        if _severity_at_least(row.severity, effective_min_severity)
        and str(row.category) not in muted_category_set
    ]

    by_category: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    overdue_count = 0
    upcoming_count = 0
    max_rank = 0
    for row in filtered:
        by_category[row.category] = by_category.get(row.category, 0) + 1
        by_severity[row.severity] = by_severity.get(row.severity, 0) + 1
        max_rank = max(max_rank, _severity_rank(row.severity))
        days_until = _days_until(row.trigger_date, today=today)
        if days_until is not None and days_until < 0:
            overdue_count += 1
        else:
            upcoming_count += 1

    headline_map = {
        3: "存在需要优先处理的 Advisor 风险/过期提醒",
        2: "存在需要关注的 Advisor 提醒",
        1: "近期有 Advisor 执行/观察提醒",
        0: "当前没有需要主动推送的 Advisor 提醒",
    }
    return {
        "generated_at": _now_utc().isoformat(),
        "window": {
            "today": today.isoformat(),
            "days": safe_days,
            "due_before": due_before.isoformat(),
            "min_severity": effective_min_severity,
            "muted_categories": sorted(muted_category_set),
        },
        "summary": {
            "total": len(filtered),
            "overdue_count": overdue_count,
            "upcoming_count": upcoming_count,
            "by_category": by_category,
            "by_severity": by_severity,
            "headline": headline_map.get(max_rank, headline_map[0]),
            "highest_severity": max(by_severity, key=lambda key: _severity_rank(key)) if by_severity else None,
        },
        "items": [serialize_advisor_reminder(row) for row in filtered],
        "notification_ready": len(filtered) > 0 and max_rank >= _severity_rank(effective_min_severity or "info"),
    }


def format_advisor_reminder_digest_message(digest: dict[str, Any]) -> str:
    summary = digest.get("summary") if isinstance(digest.get("summary"), dict) else {}
    window = digest.get("window") if isinstance(digest.get("window"), dict) else {}
    items = digest.get("items") if isinstance(digest.get("items"), list) else []
    lines = [
        "Advisor 提醒摘要",
        f"范围: {window.get('today')} 至 {window.get('due_before')}",
        f"总数: {int(summary.get('total') or 0)}，逾期: {int(summary.get('overdue_count') or 0)}，近期: {int(summary.get('upcoming_count') or 0)}",
    ]
    for item in items[:5]:
        if not isinstance(item, dict):
            continue
        lines.append(
            f"- [{item.get('severity')}] {item.get('title')}（记录 {item.get('advisor_result_id')}，触发日 {item.get('trigger_date')}）"
        )
    if len(items) > 5:
        lines.append(f"- 其余 {len(items) - 5} 条请在 Advisor 提醒中心查看。")
    return "\n".join(lines)


async def send_advisor_reminder_digest(
    session: Any,
    *,
    days: int | None = 3,
    min_severity: str | None = "warning",
    channels: list[str] | None = None,
    muted_categories: list[str] | None = None,
    profile_key: str | None = None,
    use_preferences: bool = True,
    dry_run: bool = False,
    limit: int = 50,
) -> dict[str, Any]:
    preference = await load_advisor_reminder_preference(session, profile_key) if use_preferences else None
    preference_payload = serialize_advisor_reminder_preference(preference, profile_key=profile_key)
    if use_preferences and not preference_payload.get("enabled", True):
        return {
            "status": "disabled",
            "preference": preference_payload,
            "digest": None,
            "message": "Advisor 提醒摘要已在服务端订阅偏好中关闭。",
            "notification": {"total": 0, "sent": 0, "failed": 0, "channels_used": [], "errors": {}},
        }

    effective_days = int(days if days is not None else preference_payload.get("lookahead_days") or 3)
    effective_min_severity = min_severity if min_severity is not None else preference_payload.get("min_severity")
    effective_channels = _normalize_channels(channels) if channels is not None else preference_payload.get("channels")
    effective_muted_categories = muted_categories if muted_categories is not None else preference_payload.get("muted_categories")

    digest = await build_advisor_reminder_digest(
        session,
        days=effective_days,
        min_severity=effective_min_severity,
        include_info=effective_min_severity is None,
        muted_categories=effective_muted_categories,
        limit=limit,
    )
    digest["preference"] = preference_payload
    message = format_advisor_reminder_digest_message(digest)
    if dry_run or not digest.get("notification_ready"):
        return {
            "status": "dry_run" if dry_run else "skipped",
            "preference": preference_payload,
            "digest": digest,
            "message": message,
            "notification": {"total": 0, "sent": 0, "failed": 0, "channels_used": [], "errors": {}},
        }

    from app.notify.service import SignalNotification, send_signal_notifications

    notification = SignalNotification(
        strategy_id=0,
        strategy_name="Advisor 提醒中心",
        fund_code="ADVISOR",
        direction="alert",
        signal_date=date.today().isoformat(),
        strength=1.0,
        reason=message,
    )
    result = send_signal_notifications([notification], channels=effective_channels)
    return {
        "status": "sent" if result.sent > 0 else "failed",
        "preference": preference_payload,
        "digest": digest,
        "message": message,
        "notification": {
            "total": result.total,
            "sent": result.sent,
            "failed": result.failed,
            "channels_used": result.channels_used,
            "errors": result.errors,
        },
    }


async def load_advisor_reminder_by_id(session: Any, reminder_id: int) -> AdvisorReminder | None:
    result = await session.execute(select(AdvisorReminder).where(AdvisorReminder.id == reminder_id))
    return result.scalar_one_or_none()


async def sync_advisor_reminders_for_result(
    session: Any,
    row: Any,
    *,
    execution_summary: dict[str, Any] | None = None,
    execution_plan_status: dict[str, Any] | None = None,
    today: date | None = None,
) -> dict[str, int]:
    today = today or date.today()
    candidates = build_advisor_reminder_candidates(
        row,
        execution_summary=execution_summary,
        execution_plan_status=execution_plan_status,
        today=today,
    )
    existing = await load_advisor_reminders_for_result(session, int(getattr(row, "id")))
    existing_by_key = {
        (
            int(item.advisor_result_id),
            item.fund_code or None,
            str(item.category),
            str(item.reminder_type),
        ): item
        for item in existing
    }

    active_keys = {candidate.dedupe_key for candidate in candidates}
    created = 0
    reactivated = 0
    updated = 0
    resolved = 0
    now = _now_utc()

    for candidate in candidates:
        current = existing_by_key.get(candidate.dedupe_key)
        if current is None:
            session.add(
                AdvisorReminder(
                    advisor_result_id=candidate.advisor_result_id,
                    fund_code=candidate.fund_code,
                    category=candidate.category,
                    reminder_type=candidate.reminder_type,
                    severity=candidate.severity,
                    status=REMINDER_STATUS_ACTIVE,
                    title=candidate.title,
                    description=candidate.description,
                    payload_json=candidate.payload,
                    trigger_date=candidate.trigger_date,
                    resolved_at=None,
                    dismissed_at=None,
                )
            )
            created += 1
            continue

        changed = False
        if current.status != REMINDER_STATUS_ACTIVE:
            current.status = REMINDER_STATUS_ACTIVE
            current.resolved_at = None
            current.dismissed_at = None
            reactivated += 1
            changed = True
        if current.severity != candidate.severity:
            current.severity = candidate.severity
            changed = True
        if current.title != candidate.title:
            current.title = candidate.title
            changed = True
        if current.description != candidate.description:
            current.description = candidate.description
            changed = True
        if current.payload_json != candidate.payload:
            current.payload_json = candidate.payload
            changed = True
        if current.trigger_date != candidate.trigger_date:
            current.trigger_date = candidate.trigger_date
            changed = True
        if changed:
            updated += 1

    for item in existing:
        key = (
            int(item.advisor_result_id),
            item.fund_code or None,
            str(item.category),
            str(item.reminder_type),
        )
        if key in active_keys:
            continue
        if item.status == REMINDER_STATUS_ACTIVE:
            item.status = REMINDER_STATUS_RESOLVED
            item.resolved_at = now
            resolved += 1

    await session.commit()
    return {
        "created": created,
        "reactivated": reactivated,
        "updated": updated,
        "resolved": resolved,
        "active": len(candidates),
    }


__all__ = [
    "REMINDER_STATUS_ACTIVE",
    "REMINDER_STATUS_DISMISSED",
    "REMINDER_STATUS_RESOLVED",
    "DEFAULT_REMINDER_PROFILE_KEY",
    "build_advisor_reminder_candidates",
    "build_advisor_reminder_digest",
    "format_advisor_reminder_digest_message",
    "load_advisor_reminder_by_id",
    "load_advisor_reminder_preference",
    "load_advisor_reminders",
    "load_advisor_reminders_for_result",
    "normalize_reminder_status",
    "send_advisor_reminder_digest",
    "serialize_advisor_reminder",
    "serialize_advisor_reminder_preference",
    "sync_advisor_reminders_for_result",
    "upsert_advisor_reminder_preference",
]
