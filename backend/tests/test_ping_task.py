"""End-to-end smoke tests for :mod:`app.tasks.ping`.

The tests force Celery into eager mode so ``.delay()`` runs the task
synchronously inside the test process — no broker required. This keeps
the suite fast and offline-safe while still exercising the full
``sender → broker → executor → result backend`` code path in a single
process.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.tasks.celery_app import celery_app
from app.tasks.ping import ping, ping_with_arg


@pytest.fixture
def eager_celery() -> Iterator[None]:
    """Temporarily switch the shared Celery app into eager mode."""
    previous_always_eager = celery_app.conf.task_always_eager
    previous_eager_propagates = celery_app.conf.task_eager_propagates
    previous_store_eager = celery_app.conf.task_store_eager_result

    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    # Store results so AsyncResult.get() works even in eager mode.
    celery_app.conf.task_store_eager_result = True
    try:
        yield
    finally:
        celery_app.conf.task_always_eager = previous_always_eager
        celery_app.conf.task_eager_propagates = previous_eager_propagates
        celery_app.conf.task_store_eager_result = previous_store_eager


# ---------------------------------------------------------------------
# Task registration
# ---------------------------------------------------------------------


def test_ping_tasks_are_registered_on_the_celery_app() -> None:
    """Both ``ping`` tasks must be picked up by the Celery registry."""
    assert "app.tasks.ping.ping" in celery_app.tasks
    assert "app.tasks.ping.ping_with_arg" in celery_app.tasks


def test_ping_tasks_route_to_the_ingest_queue() -> None:
    assert ping.queue == "ingest"
    assert ping_with_arg.queue == "ingest"


# ---------------------------------------------------------------------
# Behaviour
# ---------------------------------------------------------------------


def test_ping_returns_pong_directly() -> None:
    """The callable form returns ``"pong"`` without any Celery hop."""
    assert ping() == "pong"


def test_ping_delay_returns_pong(eager_celery: None) -> None:
    """`.delay()` in eager mode still round-trips through Celery."""
    result = ping.delay()
    assert result.get(timeout=1) == "pong"
    assert result.successful() is True


def test_ping_with_arg_roundtrips_payload(eager_celery: None) -> None:
    result = ping_with_arg.delay("hello")
    assert result.get(timeout=1) == "hello"


def test_ping_with_arg_accepts_unicode_payload(eager_celery: None) -> None:
    """JSON serialization must preserve non-ASCII characters."""
    payload = "你好，世界 🎉"
    result = ping_with_arg.delay(payload)
    assert result.get(timeout=1) == payload
