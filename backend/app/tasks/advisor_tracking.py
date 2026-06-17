"""建议执行跟踪 + 样本外验证定时任务。

定时任务：
1. 每日 23:00 — 跟踪历史建议的实际收益（track_advice_performance）
2. 每周日 03:00 — 运行样本外验证，检测 IC 衰减（validate_engine_health）

Requirements: 交易建议引擎 v3 增强
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.core.logging import get_logger
from app.tasks.celery_app import celery_app

log = get_logger(__name__)


@celery_app.task(
    name="app.tasks.advisor_tracking.track_advice_performance",
    queue="backtest",
    bind=True,
    max_retries=2,
    soft_time_limit=10 * 60,
    time_limit=15 * 60,
)
def track_advice_performance(self) -> dict[str, Any]:
    """每日跟踪历史建议的实际收益。

    检查最近90天内的建议记录，计算建议后的实际净值变化，
    回填到 advisor_results.tracked_returns 字段。

    Returns:
        执行摘要
    """
    log.info("advisor_tracking.track.start")

    from app.services.advisor_tracking import track_advice_performance_sync

    result = track_advice_performance_sync(lookback_days=90)

    # 标记任务完成（供 chain_guard 检测）
    try:
        from app.tasks.chain_guard import mark_task_done
        mark_task_done("daily-advice-tracking")
    except Exception:
        pass

    log.info("advisor_tracking.track.complete", **result)
    return result


@celery_app.task(
    name="app.tasks.advisor_tracking.validate_engine_health",
    queue="backtest",
    bind=True,
    max_retries=1,
    soft_time_limit=10 * 60,
    time_limit=15 * 60,
)
def validate_engine_health(self) -> dict[str, Any]:
    """每周验证引擎健康度，IC 衰减时告警。

    计算滚动 IC 和命中率，如果 IC 低于阈值（0.02）或
    命中率低于 50%，通过通知模块发送告警。

    Returns:
        健康度指标摘要
    """
    log.info("advisor_tracking.validate.start")

    from app.services.advisor_tracking import compute_engine_health_sync

    metrics = compute_engine_health_sync()
    result = metrics.to_dict()

    log.info(
        "advisor_tracking.validate.complete",
        status=metrics.status,
        ic=metrics.rolling_ic_20d,
        samples=metrics.rolling_ic_samples,
    )

    # IC 衰减告警
    if metrics.status in ("unhealthy", "degraded"):
        _send_health_alert(metrics)

    return result


def _send_health_alert(metrics: Any) -> None:
    """发送引擎健康度告警通知。"""
    try:
        from app.notify.service import SignalNotification, send_signal_notifications

        alert_msg = (
            f"⚠️ 交易建议引擎健康度告警\n"
            f"状态: {metrics.status}\n"
            f"原因: {metrics.status_reason}\n"
            f"IC: {metrics.rolling_ic_20d}\n"
            f"IC趋势: {metrics.ic_trend}\n"
            f"样本量: {metrics.rolling_ic_samples}\n"
            f"建议: 检查引擎参数或暂停使用"
        )

        notification = SignalNotification(
            strategy_id=0,
            strategy_name="引擎健康监控",
            fund_code="SYSTEM",
            direction="alert",
            signal_date=date.today().isoformat(),
            strength=1.0,
            reason=alert_msg,
        )

        send_signal_notifications([notification])
        log.warning("advisor_tracking.health_alert_sent", status=metrics.status)

    except Exception as e:
        log.error("advisor_tracking.alert_error", error=str(e))


__all__ = ["track_advice_performance", "validate_engine_health", "refresh_advisor_reminders", "send_advisor_reminder_digest", "run_feedback_learning"]


async def _refresh_advisor_reminders_async(lookback_days: int = 120, limit: int = 200) -> dict[str, Any]:
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.core.config import get_settings
    from app.data.models.advisor_results import AdvisorResult
    from app.data.session import create_async_engine_from_settings
    from app.services.advisor_execution_records import (
        build_execution_plan_statuses,
        load_execution_records_for_result,
        summarize_execution_records,
    )
    from app.services.advisor_reminders import sync_advisor_reminders_for_result

    log.info("advisor_reminders.refresh.start", lookback_days=lookback_days, limit=limit)
    settings = get_settings()
    engine = create_async_engine_from_settings(settings)
    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    min_date = date.today() - timedelta(days=lookback_days)
    processed = 0
    created = 0
    reactivated = 0
    updated = 0
    resolved = 0

    try:
        async with session_factory() as session:
            result = await session.execute(
                select(AdvisorResult)
                .where(AdvisorResult.advice_date >= min_date)
                .order_by(AdvisorResult.updated_at.desc().nullslast(), AdvisorResult.id.desc())
                .limit(limit)
            )
            rows = list(result.scalars().all())
            for row in rows:
                records = await load_execution_records_for_result(session, int(row.id))
                execution_summary = summarize_execution_records(row.advices, records)
                execution_plan_status = build_execution_plan_statuses(row.advices, records)
                stats = await sync_advisor_reminders_for_result(
                    session,
                    row,
                    execution_summary=execution_summary,
                    execution_plan_status=execution_plan_status,
                )
                processed += 1
                created += stats["created"]
                reactivated += stats["reactivated"]
                updated += stats["updated"]
                resolved += stats["resolved"]
    finally:
        await engine.dispose()

    result = {
        "status": "success",
        "processed": processed,
        "created": created,
        "reactivated": reactivated,
        "updated": updated,
        "resolved": resolved,
    }
    log.info("advisor_reminders.refresh.complete", **result)
    return result


@celery_app.task(
    name="app.tasks.advisor_tracking.refresh_advisor_reminders",
    queue="notify",
    bind=True,
    max_retries=1,
    soft_time_limit=10 * 60,
    time_limit=15 * 60,
)
def refresh_advisor_reminders(self, lookback_days: int = 120, limit: int = 200) -> dict[str, Any]:
    """每日刷新 Advisor 历史建议提醒。"""
    from app.tasks.async_utils import run_async

    return run_async(_refresh_advisor_reminders_async(lookback_days=lookback_days, limit=limit))


async def _send_advisor_reminder_digest_async(
    days: int | None = None,
    min_severity: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.core.config import get_settings
    from app.data.session import create_async_engine_from_settings
    from app.services.advisor_reminders import send_advisor_reminder_digest as send_digest

    log.info("advisor_reminders.digest.start", days=days, min_severity=min_severity, limit=limit)
    settings = get_settings()
    engine = create_async_engine_from_settings(settings)
    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    try:
        async with session_factory() as session:
            result = await send_digest(
                session,
                days=days,
                min_severity=min_severity,
                dry_run=False,
                limit=limit,
            )
    finally:
        await engine.dispose()
    log.info(
        "advisor_reminders.digest.complete",
        status=result.get("status"),
        total=((result.get("digest") or {}).get("summary") or {}).get("total"),
    )
    return result


@celery_app.task(
    name="app.tasks.advisor_tracking.send_advisor_reminder_digest",
    queue="notify",
    bind=True,
    max_retries=1,
    soft_time_limit=5 * 60,
    time_limit=10 * 60,
)
def send_advisor_reminder_digest(self, days: int | None = None, min_severity: str | None = None, limit: int = 50) -> dict[str, Any]:
    """主动发送 Advisor 提醒摘要，作为跨端通知闭环的服务端入口。"""
    from app.tasks.async_utils import run_async

    return run_async(_send_advisor_reminder_digest_async(days=days, min_severity=min_severity, limit=limit))


@celery_app.task(
    name="app.tasks.advisor_tracking.run_feedback_learning",
    queue="backtest",
    bind=True,
    max_retries=1,
    soft_time_limit=10 * 60,
    time_limit=15 * 60,
)
def run_feedback_learning(self) -> dict[str, Any]:
    """每周运行反馈学习，基于历史效果自动调整引擎参数。

    从 tracked_returns 中提取各因子评分与实际收益的关系，
    学习最优权重乘数和阈值调整，保存供引擎下次使用。

    Returns:
        学习结果摘要
    """
    log.info("advisor_feedback.learn.start")

    from app.services.advisor_feedback import AdvisorFeedbackLearner, FeedbackConfig

    learner = AdvisorFeedbackLearner(FeedbackConfig(lookback_days=180))
    learned = learner.learn_from_history_sync()

    result = {
        "learn_date": learned.learn_date,
        "sample_count": learned.sample_count,
        "confidence": learned.confidence,
        "weight_multipliers": {
            "technical": learned.multiplier_technical,
            "momentum": learned.multiplier_momentum,
            "strategy": learned.multiplier_strategy,
            "prediction": learned.multiplier_prediction,
            "cross_sectional": learned.multiplier_cross_sectional,
        },
        "threshold_adjustment": learned.threshold_adjustment,
        "momentum_discount_calibrated": learned.momentum_discount_calibrated,
        "adjustments_log": learned.adjustments_log,
    }

    log.info(
        "advisor_feedback.learn.complete",
        samples=learned.sample_count,
        confidence=learned.confidence,
    )

    return result
