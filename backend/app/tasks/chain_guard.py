"""任务链保障机制 — 确保定时任务不丢失、按依赖顺序执行。

解决的问题：
1. 错过的任务自动补跑（worker 宕机、重启后检测并补执行）
2. 任务依赖保障（NAV采集完成后才触发信号生成，信号完成后才触发建议）
3. 执行状态记录（Redis 记录每日各任务的执行状态）

机制：
- 每个关键任务执行成功后，在 Redis 中标记 "task:{date}:{task_name} = done"
- 下游任务启动前检查上游是否完成，未完成则等待或跳过
- 每日 06:00 运行 catch-up 检查，补跑前一天未完成的任务

任务依赖链：
  NAV采集(21:00) → 信号生成(22:00) → 交易建议(22:30) → 执行跟踪(23:00)

Requirements: 系统可靠性
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any

from app.core.logging import get_logger
from app.tasks.celery_app import celery_app

log = get_logger(__name__)

# Redis key 格式
TASK_STATUS_PREFIX = "task_chain:"
# 任务依赖定义
TASK_CHAIN = {
    "daily-nav-ingest": {
        "depends_on": None,
        "task": "app.tasks.ingest.update_daily_nav",
    },
    "daily-strategy-signals": {
        "depends_on": "daily-nav-ingest",
        "task": "app.tasks.signals.generate_strategy_signals",
    },
    "daily-trading-advice": {
        "depends_on": "daily-strategy-signals",
        "task": "app.tasks.advisor.generate_daily_advice",
    },
    "daily-advice-tracking": {
        "depends_on": "daily-trading-advice",
        "task": "app.tasks.advisor_tracking.track_advice_performance",
    },
}


def _get_redis():
    """获取 Redis 客户端。"""
    import redis

    from app.core.config import get_settings

    settings = get_settings()
    return redis.from_url(settings.redis_url, decode_responses=True)


def mark_task_done(task_name: str, task_date: date | None = None) -> None:
    """标记任务为已完成。

    Args:
        task_name: 任务名称（如 "daily-nav-ingest"）
        task_date: 任务日期，默认今天
    """
    if task_date is None:
        task_date = date.today()

    key = f"{TASK_STATUS_PREFIX}{task_date.isoformat()}:{task_name}"
    try:
        r = _get_redis()
        r.set(key, json.dumps({
            "status": "done",
            "completed_at": datetime.now().isoformat(),
        }), ex=7 * 86400)  # 7天过期
    except Exception as e:
        log.warning("chain_guard.mark_done_error", task=task_name, error=str(e))


def is_task_done(task_name: str, task_date: date | None = None) -> bool:
    """检查任务是否已完成。

    Args:
        task_name: 任务名称
        task_date: 任务日期，默认今天

    Returns:
        True 如果已完成
    """
    if task_date is None:
        task_date = date.today()

    key = f"{TASK_STATUS_PREFIX}{task_date.isoformat()}:{task_name}"
    try:
        r = _get_redis()
        val = r.get(key)
        if val:
            data = json.loads(val)
            return data.get("status") == "done"
    except Exception:
        pass
    return False


def check_upstream_ready(task_name: str, task_date: date | None = None) -> bool:
    """检查上游依赖是否已完成。

    Args:
        task_name: 当前任务名称
        task_date: 任务日期

    Returns:
        True 如果上游已完成（或无依赖）
    """
    chain_info = TASK_CHAIN.get(task_name)
    if not chain_info:
        return True

    depends_on = chain_info.get("depends_on")
    if not depends_on:
        return True

    return is_task_done(depends_on, task_date)


@celery_app.task(
    name="app.tasks.chain_guard.catchup_missed_tasks",
    queue="backtest",
    bind=True,
    max_retries=1,
    soft_time_limit=30 * 60,
    time_limit=35 * 60,
)
def catchup_missed_tasks(self) -> dict[str, Any]:
    """补跑前一天未完成的关键任务。

    每日 06:00 运行，检查前一天的任务链是否全部完成。
    对于未完成的任务，按依赖顺序补跑。

    Returns:
        补跑摘要
    """
    log.info("chain_guard.catchup.start")

    yesterday = date.today() - timedelta(days=1)
    caught_up: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []

    # 按依赖顺序检查
    ordered_tasks = [
        "daily-nav-ingest",
        "daily-strategy-signals",
        "daily-trading-advice",
        "daily-advice-tracking",
    ]

    for task_name in ordered_tasks:
        if is_task_done(task_name, yesterday):
            skipped.append(task_name)
            continue

        # 检查上游是否完成
        if not check_upstream_ready(task_name, yesterday):
            # 上游也没完成，先补跑上游（已在前面的循环中处理）
            log.warning(
                "chain_guard.upstream_missing",
                task=task_name,
                upstream=TASK_CHAIN[task_name]["depends_on"],
            )
            failed.append(f"{task_name}(上游未完成)")
            continue

        # 补跑
        chain_info = TASK_CHAIN[task_name]
        celery_task_name = chain_info["task"]

        try:
            log.info("chain_guard.catchup.running", task=task_name, date=str(yesterday))
            # 同步调用任务（在当前 worker 中执行）
            celery_app.send_task(celery_task_name)
            caught_up.append(task_name)
            # 标记完成（实际完成由任务本身标记，这里只是触发）
        except Exception as e:
            log.error("chain_guard.catchup.error", task=task_name, error=str(e))
            failed.append(f"{task_name}({str(e)[:50]})")

    result = {
        "date": yesterday.isoformat(),
        "caught_up": caught_up,
        "skipped": skipped,
        "failed": failed,
        "total_checked": len(ordered_tasks),
    }

    # 如果有补跑的任务，发送通知
    if caught_up:
        _notify_catchup(result)

    log.info("chain_guard.catchup.complete", **result)
    return result


def _notify_catchup(result: dict[str, Any]) -> None:
    """发送补跑通知。"""
    try:
        from app.notify.service import SignalNotification, send_signal_notifications

        msg = (
            f"⚠️ 任务补跑通知\n"
            f"日期: {result['date']}\n"
            f"补跑: {', '.join(result['caught_up'])}\n"
            f"失败: {', '.join(result['failed']) if result['failed'] else '无'}"
        )

        notification = SignalNotification(
            strategy_id=0,
            strategy_name="任务链监控",
            fund_code="SYSTEM",
            direction="alert",
            signal_date=date.today().isoformat(),
            strength=0.8,
            reason=msg,
        )
        send_signal_notifications([notification])
    except Exception as e:
        log.error("chain_guard.notify_error", error=str(e))


__all__ = [
    "mark_task_done",
    "is_task_done",
    "check_upstream_ready",
    "catchup_missed_tasks",
]
