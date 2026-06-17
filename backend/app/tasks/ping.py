"""End-to-end smoke-test tasks for the Celery wiring.

These tasks exist solely to prove that a task can be:

1. Submitted from a client (`ping.delay()`),
2. Routed to the ``ingest`` queue,
3. Picked up by a worker,
4. Executed,
5. Its result retrieved from the Redis result backend.

Running ``python -c "from app.tasks.ping import ping; print(ping.delay().get(timeout=5))"``
against a live stack is the canonical phase-0 verification for the
Celery + Redis integration (requirement 8.8).

In the pytest suite these tasks are executed eagerly
(``task_always_eager=True``) so the suite has no hard dependency on a
running broker/backend.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.tasks.celery_app import celery_app

log = get_logger(__name__)


@celery_app.task(
    name="app.tasks.ping.ping",
    queue="ingest",
    bind=False,
    ignore_result=False,
)
def ping() -> str:
    """Return a fixed ``"pong"`` string.

    The simplest possible task: no arguments, no side effects, a stable
    return value that is trivial to assert against in tests and CLI
    smoke checks.
    """
    log.debug("ping.called")
    return "pong"


@celery_app.task(
    name="app.tasks.ping.ping_with_arg",
    queue="ingest",
    bind=False,
    ignore_result=False,
)
def ping_with_arg(message: str) -> str:
    """Echo ``message`` back unchanged.

    Complements :func:`ping` by proving that positional argument
    serialization (JSON by default) round-trips cleanly through the
    broker and result backend.
    """
    log.debug("ping_with_arg.called", message=message)
    return message


__all__ = ["ping", "ping_with_arg"]
