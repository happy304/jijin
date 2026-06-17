"""Celery application factory for the Fund Quant Platform.

This module owns **the** Celery application instance used by both the
worker processes (``celery -A app.tasks.celery_app worker``) and the
Beat scheduler (``celery -A app.tasks.celery_app beat``) defined in
``deploy/docker-compose.yml``.

Design notes
------------
* **Single source of truth for config.** Broker, backend and timezone
  values are pulled from :class:`app.core.config.Settings` so
  development, CI and production all share the exact same configuration
  surface (``.env`` / environment variables). No hard-coded URLs.
* **Four queues** are declared explicitly, matching design §10.2::

      ingest    — data ingestion (default, low priority, high concurrency)
      backtest  — backtest runs (CPU heavy, dedicated pool)
      ai        — LLM calls (IO bound)
      notify    — alerting / push notifications (fast lane)

"""

from __future__ import annotations

import asyncio
import sys

# Windows 上 psycopg async 需要 SelectorEventLoop
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from celery import Celery
from kombu import Exchange, Queue

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.tasks.schedule import get_beat_schedule

log = get_logger(__name__)


# ---------------------------------------------------------------------
# Queue catalogue
# ---------------------------------------------------------------------
# Keep this in one place so tests, worker startup scripts and anyone
# reviewing the queue layout have a single reference. The order is the
# order in which workers will pull tasks when ``--queues`` is omitted.

#: Name of the default queue used when a task does not declare its own.
#: We pick ``ingest`` because it is the most common destination and
#: because a smoke-test ``ping`` task has no natural home otherwise.
DEFAULT_QUEUE: str = "ingest"

#: Canonical list of the four logical queues used by the platform.
QUEUE_NAMES: tuple[str, ...] = ("ingest", "backtest", "ai", "notify")


#: Task modules that Celery should import at startup. Each module must
#: register its ``@celery_app.task`` definitions via the module-level
#: ``celery_app`` singleton. Add new phase-1+ modules here
#: (``"app.tasks.backtest"``, ``"app.tasks.signals"``, ...).
TASK_MODULES: tuple[str, ...] = (
    "app.tasks.ping",
    "app.tasks.ingest",
    "app.tasks.discovery",
    "app.tasks.backup",
    "app.tasks.optimization",
    "app.tasks.backtest",
    "app.tasks.signals",
    "app.tasks.simulation",
    "app.tasks.advisor",
    "app.tasks.advisor_tracking",
    "app.tasks.chain_guard",
    "app.tasks.valuation_ingest",
)


def _build_queues() -> tuple[Queue, ...]:
    """Build the ``kombu.Queue`` objects for every logical queue.

    Each queue uses a dedicated direct exchange named after the queue
    itself. This keeps routing predictable (``routing_key == queue``)
    and makes it trivial to run a worker dedicated to a single queue
    (``celery -A app.tasks.celery_app worker -Q backtest``).
    """
    return tuple(
        Queue(name, Exchange(name, type="direct"), routing_key=name) for name in QUEUE_NAMES
    )


# ---------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------


def create_celery_app(settings: Settings | None = None) -> Celery:
    """Build and configure a :class:`celery.Celery` instance.

    Exposed as a factory so that tests can build a fresh, isolated app
    with overridden settings (eager mode, a custom timezone, ...) via
    :func:`create_celery_app(Settings(...))` without having to patch
    the module-level singleton.
    """
    settings = settings or get_settings()

    app = Celery(
        "fund_quant_platform",
        broker=settings.celery_broker_url,
        backend=settings.celery_result_backend,
        # Explicit list of modules Celery will import on startup so that
        # ``@celery_app.task`` decorators actually run. See the
        # ``TASK_MODULES`` docstring above for how to register new
        # modules in later phases.
        include=list(TASK_MODULES),
    )

    queues = _build_queues()

    app.conf.update(
        # ----- Broker / result ----------------------------------------
        broker_connection_retry_on_startup=True,
        result_expires=3600,  # 1h — avoid stale result buildup in Redis
        # ----- Serialization ------------------------------------------
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        # ----- Time zone (see design §10 + requirement 8.1) -----------
        timezone=settings.celery_timezone,
        enable_utc=True,
        # ----- Routing -------------------------------------------------
        task_queues=queues,
        task_default_queue=DEFAULT_QUEUE,
        task_default_exchange=DEFAULT_QUEUE,
        task_default_exchange_type="direct",
        task_default_routing_key=DEFAULT_QUEUE,
        # ----- Worker behaviour ---------------------------------------
        # Long-running ingest/backtest tasks must not be lost on worker
        # crash. ``acks_late`` + ``reject_on_worker_lost`` makes Celery
        # requeue them, and ``prefetch_multiplier=1`` prevents a single
        # worker from hoarding tasks it cannot finish in time.
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        # Emit a ``STARTED`` state so UIs / WebSocket progress can tell
        # "queued" from "running" (see design §8.2 backtest flow).
        task_track_started=True,
        # ----- Time limits --------------------------------------------
        # 30 min hard / 25 min soft: safe upper bound for the heaviest
        # planned task (full-universe daily ingest). Individual tasks
        # may override via ``@task(time_limit=..., soft_time_limit=...)``.
        task_time_limit=30 * 60,
        task_soft_time_limit=25 * 60,
        # ----- Beat ----------------------------------------------------
        beat_schedule=get_beat_schedule(settings.schedule_mode),
    )

    log.info(
        "celery.configured",
        broker=settings.celery_broker_url,
        backend=settings.celery_result_backend,
        queues=list(QUEUE_NAMES),
        default_queue=DEFAULT_QUEUE,
        timezone=settings.celery_timezone,
        schedule_mode=settings.schedule_mode,
    )
    return app


# ---------------------------------------------------------------------
# Module-level singleton used by Celery CLI and by ``app.tasks.*`` task
# modules that register tasks via ``@celery_app.task``.
# ---------------------------------------------------------------------
celery_app: Celery = create_celery_app()

# Alias so the Celery CLI can find the application when invoked with
# ``-A app.tasks.celery_app`` (without the explicit ``:celery_app``
# attribute). Celery's auto-discovery looks for attributes named
# ``app`` or ``celery`` in the referenced module.
app: Celery = celery_app


__all__ = [
    "DEFAULT_QUEUE",
    "QUEUE_NAMES",
    "TASK_MODULES",
    "app",
    "celery_app",
    "create_celery_app",
]
