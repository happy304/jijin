"""Alembic migration environment.

This module is executed by Alembic for every invocation of
`alembic upgrade`, `alembic revision`, etc. It is intentionally thin:

* Source the database URL from `app.core.config.Settings.database_sync_url`
  (psycopg driver — Alembic runs synchronously).
* Import `app.data.models.Base` so `target_metadata` reflects every ORM
  model currently declared in the project. This is what powers
  `alembic revision --autogenerate`.
* Support both **online** (connected to a live database) and **offline**
  (emit SQL to stdout) migration modes.

Keeping the URL in `Settings` rather than `alembic.ini` means a single
environment variable (`DATABASE_SYNC_URL`) governs all runtime paths —
dev shell, Docker compose, CI, production.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# ---------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------
# Alembic invokes this script with `migrations/` (or the project root)
# on `sys.path`. To let the file work regardless of the invocation CWD,
# ensure the repo root (`backend/`) is on `sys.path` so `import app…`
# resolves.
_THIS_DIR = Path(__file__).resolve().parent
_BACKEND_ROOT = _THIS_DIR.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

# Imported after sys.path tweak.
from app.core.config import get_settings  # noqa: E402
from app.data.models import Base  # noqa: E402

# ---------------------------------------------------------------------
# Alembic config plumbing
# ---------------------------------------------------------------------
config = context.config

# Configure Python logging from `alembic.ini` if the caller passed one.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The metadata that `--autogenerate` compares against the live database.
# Importing `app.data.models` above registers every ORM model with
# `Base.metadata`, so this reference stays correct even as new models
# are added.
target_metadata = Base.metadata


def _resolve_database_url() -> str:
    """Return the sync database URL for Alembic.

    Precedence:
    1. `-x url=…` passed on the command line (handy in CI one-offs).
    2. `sqlalchemy.url` set in `alembic.ini`.
    3. `Settings.database_sync_url` — the default for every normal run.
    """
    x_args = context.get_x_argument(as_dictionary=True)
    if "url" in x_args and x_args["url"]:
        return x_args["url"]

    ini_url = config.get_main_option("sqlalchemy.url")
    if ini_url:
        return ini_url

    return get_settings().database_sync_url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    No database connection is opened; Alembic renders SQL to stdout.
    Useful for producing a migration script a DBA can review and apply
    manually. ``--sql`` flag on the CLI puts us in this mode.
    """
    url = _resolve_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode against a live database.

    We use `engine_from_config` so pool settings stay configurable via
    `alembic.ini`; the URL is overridden at runtime from Settings.
    """
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _resolve_database_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
