"""Shared pytest fixtures for the backend test suite."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    """Deterministic test settings — no `.env` interference.

    We pin CORS origins so CORS-related tests have a known allowlist,
    and set `app_env=test` so the app sees the test environment.
    """
    # Clear the LRU cache so `Depends(get_settings)` picks up the test
    # instance whenever the app uses the default factory.
    get_settings.cache_clear()
    return Settings(
        APP_ENV="test",
        DEBUG="true",
        LOG_LEVEL="WARNING",
        API_PREFIX="/api/v1",
        API_CORS_ORIGINS="http://allowed.example,http://also-allowed.example",
        # Disable the lifespan migration hook so the TestClient can
        # boot without a live Postgres. Migration-specific tests build
        # their own Settings instance with db_auto_migrate=True.
        DB_AUTO_MIGRATE="false",
        # Disable Prometheus instrumentation by default so the default
        # collector registry isn't polluted across the many small apps
        # the test suite spins up. Observability-specific tests build
        # their own Settings/app and manage registry cleanup locally.
        PROMETHEUS_ENABLED="false",
    )


@pytest.fixture
def app(settings: Settings) -> Iterator[FastAPI]:
    """Build a fresh FastAPI app bound to the test settings."""
    application = create_app(settings)
    # Override the get_settings dependency so any endpoint using
    # `Depends(get_settings)` observes the test instance.
    application.dependency_overrides[get_settings] = lambda: settings
    try:
        yield application
    finally:
        application.dependency_overrides.clear()
        get_settings.cache_clear()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """Synchronous TestClient — triggers the app's lifespan handler."""
    with TestClient(app) as tc:
        yield tc
