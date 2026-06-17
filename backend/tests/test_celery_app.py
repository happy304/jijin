"""Tests for :mod:`app.tasks.celery_app`.

These tests validate the Celery wiring without touching a live broker:

* broker / backend URLs are sourced from :class:`app.core.config.Settings`
* all four logical queues (``ingest``/``backtest``/``ai``/``notify``)
  are declared
* timezone, serialization and long-running-task safety options have
  the values required by design §10 and requirement 8.1.

A fresh :class:`celery.Celery` instance is built via
:func:`create_celery_app` for each test so the shared module-level
singleton cannot leak state between tests.
"""

from __future__ import annotations

from celery import Celery

from app.core.config import Settings
from app.tasks.celery_app import (
    DEFAULT_QUEUE,
    QUEUE_NAMES,
    celery_app as module_celery_app,
    create_celery_app,
)


def _test_settings() -> Settings:
    """Build deterministic Settings for Celery configuration tests."""
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        APP_ENV="test",
        CELERY_BROKER_URL="redis://test-broker:6379/1",
        CELERY_RESULT_BACKEND="redis://test-backend:6379/2",
        CELERY_TIMEZONE="Asia/Shanghai",
    )


# ---------------------------------------------------------------------
# Instance / identity
# ---------------------------------------------------------------------


def test_module_level_celery_app_is_a_celery_instance() -> None:
    assert isinstance(module_celery_app, Celery)
    assert module_celery_app.main == "fund_quant_platform"


def test_create_celery_app_uses_settings_for_broker_and_backend() -> None:
    settings = _test_settings()
    app = create_celery_app(settings)

    assert app.conf.broker_url == settings.celery_broker_url
    assert app.conf.result_backend == settings.celery_result_backend


# ---------------------------------------------------------------------
# Queues
# ---------------------------------------------------------------------


def test_all_four_queues_are_declared() -> None:
    """design §10.2: four queues must exist with matching names."""
    app = create_celery_app(_test_settings())
    queues = {q.name for q in app.conf.task_queues}

    assert queues == {"ingest", "backtest", "ai", "notify"}
    # Exposed tuple stays in sync with the actual Celery config.
    assert set(QUEUE_NAMES) == queues


def test_default_queue_is_ingest() -> None:
    app = create_celery_app(_test_settings())
    assert DEFAULT_QUEUE == "ingest"
    assert app.conf.task_default_queue == "ingest"
    assert app.conf.task_default_routing_key == "ingest"


def test_each_queue_has_a_direct_exchange_matching_its_name() -> None:
    app = create_celery_app(_test_settings())
    for q in app.conf.task_queues:
        assert q.exchange.name == q.name
        assert q.exchange.type == "direct"
        assert q.routing_key == q.name


# ---------------------------------------------------------------------
# Worker / reliability config
# ---------------------------------------------------------------------


def test_reliability_options_are_set_for_long_running_tasks() -> None:
    app = create_celery_app(_test_settings())

    assert app.conf.task_acks_late is True
    assert app.conf.task_reject_on_worker_lost is True
    assert app.conf.worker_prefetch_multiplier == 1
    assert app.conf.task_track_started is True


def test_time_limits_are_configured() -> None:
    app = create_celery_app(_test_settings())

    # Soft limit must be strictly smaller than the hard limit so the
    # SoftTimeLimitExceeded signal can fire before the worker is killed.
    assert app.conf.task_soft_time_limit < app.conf.task_time_limit
    assert app.conf.task_time_limit > 0
    assert app.conf.task_soft_time_limit > 0


def test_result_expires_prevents_backend_bloat() -> None:
    app = create_celery_app(_test_settings())
    assert app.conf.result_expires == 3600


# ---------------------------------------------------------------------
# Serialization and timezone
# ---------------------------------------------------------------------


def test_timezone_is_pulled_from_settings() -> None:
    settings = _test_settings()
    app = create_celery_app(settings)
    assert app.conf.timezone == settings.celery_timezone
    assert app.conf.enable_utc is True


def test_json_only_serialization() -> None:
    app = create_celery_app(_test_settings())
    assert app.conf.task_serializer == "json"
    assert app.conf.result_serializer == "json"
    assert "json" in app.conf.accept_content
