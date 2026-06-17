"""Data access layer.

This package owns everything that touches persistent stores:

* `session`       — async SQLAlchemy engine + session factory
* `models`        — ORM declarative base and entity definitions
* `migrations`    — runtime hook that applies Alembic migrations
* `repositories`  — (added in later tasks) Repository pattern wrappers
* `providers`     — (added in later tasks) external data-source adapters
* `validators`    — (added in later tasks) data quality rules
* `cache`         — (added in later tasks) Redis cache helpers

Keeping the async engine and the ORM base in this package means the rest
of the codebase never imports SQLAlchemy directly — it only touches the
typed helpers exposed here.
"""

from __future__ import annotations

from app.data.session import (
    AsyncSessionLocal,
    create_async_engine_from_settings,
    dispose_engine,
    get_engine,
    get_session,
    get_sessionmaker,
)

__all__ = [
    "AsyncSessionLocal",
    "create_async_engine_from_settings",
    "dispose_engine",
    "get_engine",
    "get_session",
    "get_sessionmaker",
]
