"""Runtime Alembic integration.

The FastAPI lifespan hook (see `app.main.lifespan`) calls
`run_migrations_if_enabled()` on startup so a fresh checkout / Docker
container is schema-ready without an operator having to run
`alembic upgrade head` manually.

Design choices
--------------
* Alembic is **synchronous** — running it directly in an asyncio event
  loop blocks the loop for however long the migration takes. We offload
  the call to a worker thread via `asyncio.to_thread`.
* Gating is controlled by `Settings.db_auto_migrate`. Production
  deployments should set this to `False` and run migrations from a
  dedicated release job so schema changes happen exactly once per
  deploy rather than once per container start.
* The Alembic `Config` object is built in code (not loaded from disk
  with `read_configfile`) so tests can inject a custom URL without
  mutating `alembic.ini`. The ini file is still used for logging and
  hook configuration.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.core.config import Settings
from app.core.logging import get_logger

log = get_logger("app.data.migrations")

# Repository layout:
#   backend/
#     alembic.ini
#     migrations/
#       env.py
# `backend/` is two levels above this file (app/data/migrations.py).
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_INI_PATH = _BACKEND_ROOT / "alembic.ini"
_MIGRATIONS_DIR = _BACKEND_ROOT / "migrations"


def build_alembic_config(settings: Settings) -> Config:
    """Construct a programmatically-configured Alembic `Config`.

    We still point Alembic at the physical `alembic.ini` so it picks up
    logging + post-write hook settings, but we **override** the
    database URL and script location from Settings. This keeps the ini
    file free of credentials and lets tests swap URLs freely.
    """
    cfg = Config(str(_ALEMBIC_INI_PATH)) if _ALEMBIC_INI_PATH.exists() else Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", settings.database_sync_url)
    return cfg


def run_upgrade(settings: Settings, revision: str = "head") -> None:
    """Apply migrations synchronously up to `revision` (default `head`).

    Kept public so CLI commands and tests can call it directly. In
    async contexts prefer `run_migrations_if_enabled` which wraps this
    call in `asyncio.to_thread`.
    """
    cfg = build_alembic_config(settings)
    log.info(
        "db.migrate.start",
        target=revision,
        url=_redact_url(settings.database_sync_url),
    )
    command.upgrade(cfg, revision)
    log.info("db.migrate.done", target=revision)


async def run_migrations_if_enabled(settings: Settings) -> None:
    """Apply migrations from within an async startup hook.

    Behaviour:
    * No-op when `settings.db_auto_migrate` is False.
    * Otherwise runs `alembic upgrade head` in a worker thread so the
      event loop stays responsive.
    * Any exception is logged and re-raised — a failed migration should
      fail fast and prevent the app from serving requests against a
      stale schema.
    """
    if not settings.db_auto_migrate:
        log.info("db.migrate.skipped", reason="DB_AUTO_MIGRATE=false")
        return

    try:
        await asyncio.to_thread(run_upgrade, settings, "head")
    except Exception:
        # Use `exception` so the traceback is captured in structured logs.
        log.exception("db.migrate.failed")
        raise


def _redact_url(url: str) -> str:
    """Strip credentials from a SQLAlchemy URL for log output.

    Avoids leaking the DB password into centralised logging systems.
    """
    if "@" not in url:
        return url
    scheme_sep = url.find("://")
    if scheme_sep == -1:
        return url
    prefix = url[: scheme_sep + 3]
    rest = url[scheme_sep + 3 :]
    at_idx = rest.rfind("@")
    return f"{prefix}***:***@{rest[at_idx + 1 :]}"


__all__ = [
    "build_alembic_config",
    "run_migrations_if_enabled",
    "run_upgrade",
]
