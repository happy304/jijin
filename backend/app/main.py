"""FastAPI application entrypoint.

Wiring only — no business logic lives here. Responsibilities:

1. Load configuration (`app.core.config.Settings`).
2. Configure structured logging (`app.core.logging`).
3. Build a FastAPI instance with the v1 router mounted under the
   configured prefix (default ``/api/v1``).
4. Register the request-ID middleware and CORS middleware.
5. Register exception handlers so every error response follows the
   standardised envelope.
6. Expose `/health` at the root for Dockerfile/compose healthchecks.

Uvicorn / gunicorn imports this module as ``app.main:app``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app import __version__
from app.api import router as api_v1_router
from app.api.errors import register_exception_handlers
from app.api.middleware import RequestIDMiddleware
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.data.migrations import run_migrations_if_enabled
from app.data.session import dispose_engine
from app.observability import setup_metrics


class HealthResponse(BaseModel):
    """Shape of the `/health` endpoint response."""

    status: str
    service: str
    version: str
    environment: str


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialise logging and log startup / shutdown banners.

    Also applies any pending Alembic migrations when
    ``settings.db_auto_migrate`` is true (see task 0.4). Redis and
    Celery wiring land here in later phase-0 tasks (0.5, 0.6).
    """
    settings: Settings = app.state.settings
    log = get_logger("app.main")
    log.info(
        "app.startup",
        service=settings.app_name,
        version=__version__,
        environment=settings.app_env,
    )
    # Apply pending migrations before serving traffic. In production
    # `DB_AUTO_MIGRATE` should be set to false and migrations run from
    # the release pipeline (see design §9.2 / requirement 9.4).
    await run_migrations_if_enabled(settings)
    try:
        yield
    finally:
        log.info("app.shutdown", service=settings.app_name)
        # Release database connection pools cleanly.
        await dispose_engine()


def _configure_cors(app: FastAPI, settings: Settings) -> None:
    """Attach the CORS middleware using configured origins.

    We deliberately avoid the ``allow_origins=["*"]`` shortcut; empty
    `api_cors_origins` means no cross-origin requests are allowed,
    which is the correct default when the frontend is served from the
    same origin as the API (production compose setup).
    """
    origins = settings.cors_origins
    if not origins:
        return
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a fully-configured FastAPI application.

    Exposed as a factory so tests can inject overridden settings:

        def test_something():
            app = create_app(Settings(app_env="test", ...))
            ...
    """
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(
        title="Fund Quant Platform API",
        description="Quant research, backtesting and monitoring platform for "
        "public mutual funds.",
        version=__version__,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        openapi_url="/openapi.json" if not settings.is_production else None,
        lifespan=lifespan,
    )
    # Stash settings on app.state so middleware, dependencies and the
    # lifespan hook can reach it without reimporting get_settings.
    app.state.settings = settings

    # Middleware order: add CORS first so it wraps the request-ID
    # middleware. Starlette executes middleware in reverse registration
    # order, meaning the *last* added runs first on the way in, so
    # registering request-ID last puts it outermost and guarantees a
    # request ID is available inside every other middleware and handler.
    _configure_cors(app, settings)
    app.add_middleware(RequestIDMiddleware)

    register_exception_handlers(app)

    # Instrument the app for Prometheus *before* endpoints are
    # registered so the instrumentator can see every route. The
    # `/metrics` endpoint is mounted on the root app (outside
    # `/api/v1`) to match the design and the scrape config in
    # `deploy/prometheus/prometheus.yml`.
    setup_metrics(app, settings)

    @app.get(
        "/health",
        response_model=HealthResponse,
        tags=["meta"],
        summary="Liveness / readiness probe",
    )
    async def health() -> HealthResponse:
        """Minimal liveness endpoint consumed by Docker/compose/k8s.

        Must stay dependency-free: a failing DB or Redis should **not**
        cause this probe to report unhealthy, otherwise the orchestrator
        would kill the only container that can serve an error page. A
        dedicated ``/ready`` endpoint is introduced in a later task for
        dependency-aware readiness.
        """
        return HealthResponse(
            status="ok",
            service=settings.app_name,
            version=__version__,
            environment=settings.app_env,
        )

    app.include_router(api_v1_router, prefix=settings.api_prefix)

    return app


# Module-level ASGI app used by uvicorn/gunicorn: `app.main:app`.
app: FastAPI = create_app()


__all__ = ["app", "create_app", "HealthResponse", "lifespan"]


# Re-export for type checkers that don't follow TYPE_CHECKING aliases.
_: Any = app
