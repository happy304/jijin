"""Runtime health helpers for queue-backed advisor/backtest features."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class QueueHealth:
    """Lightweight broker/queue health snapshot."""

    status: str = "unknown"
    redis_available: bool = False
    broker_url_configured: bool = False
    queues: dict[str, int | None] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "redis_available": self.redis_available,
            "broker_url_configured": self.broker_url_configured,
            "queues": self.queues,
            "warnings": self.warnings,
            "error": self.error,
        }


def check_queue_health(queue_names: tuple[str, ...] = ("ingest", "backtest", "ai", "notify")) -> QueueHealth:
    """Return a best-effort Redis/Celery queue snapshot without raising.

    The API layer uses this to explain degraded Advisor/backtest reliability
    when Redis or Celery workers are not reachable, instead of surfacing a
    generic 500 or leaving users with an apparently stuck task.
    """
    from app.core.config import get_settings

    settings = get_settings()
    health = QueueHealth(
        broker_url_configured=bool(getattr(settings, "celery_broker_url", None)),
        queues={name: None for name in queue_names},
    )

    try:
        import redis

        client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        try:
            client.ping()
            health.redis_available = True
            for queue in queue_names:
                try:
                    health.queues[queue] = int(client.llen(queue))
                except Exception:
                    health.queues[queue] = None
        finally:
            client.close()
    except Exception as exc:
        health.status = "unavailable"
        health.error = str(exc)
        health.warnings.append("Redis/Celery broker 不可用，后台任务、进度推送和样本外刷新可能无法执行")
        return health

    backlog = sum(length or 0 for length in health.queues.values())
    if backlog >= 100:
        health.status = "degraded"
        health.warnings.append(f"Celery 队列积压 {backlog} 个任务，请检查 worker 并发和任务耗时")
    else:
        health.status = "healthy"
    return health


__all__ = ["QueueHealth", "check_queue_health"]
