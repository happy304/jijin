"""Async SQLAlchemy engine and session factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings, get_settings

if TYPE_CHECKING:  # pragma: no cover
    pass


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _is_sqlite_url(url: str) -> bool:
    """Return True for any SQLite dialect URL."""
    return url.startswith("sqlite") or url.startswith("sqlite+")


def create_async_engine_from_settings(settings: Settings) -> AsyncEngine:
    """Build an AsyncEngine from a Settings instance."""
    url = settings.database_url
    kwargs: dict[str, Any] = {
        "echo": False,
        "pool_pre_ping": True,
        "future": True,
    }
    if not _is_sqlite_url(url):
        kwargs["pool_size"] = settings.db_pool_size
        kwargs["max_overflow"] = settings.db_max_overflow
    return create_async_engine(url, **kwargs)


def get_engine(settings: Settings | None = None) -> AsyncEngine:
    """Return the process-wide AsyncEngine."""
    global _engine, _sessionmaker
    if _engine is None:
        _engine = create_async_engine_from_settings(settings or get_settings())
        _sessionmaker = async_sessionmaker(
            bind=_engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _engine


def get_sessionmaker(
    settings: Settings | None = None,
) -> async_sessionmaker[AsyncSession]:
    """Return the cached async_sessionmaker."""
    get_engine(settings)
    assert _sessionmaker is not None
    return _sessionmaker


async def dispose_engine() -> None:
    """Close the engine and reset module-level caches."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency that yields a scoped AsyncSession."""
    factory = get_sessionmaker()
    async with factory() as session:
        try:
            yield session
        finally:
            pass


def __getattr__(name: str) -> Any:
    """Lazily expose AsyncSessionLocal as an alias."""
    if name == "AsyncSessionLocal":
        return get_sessionmaker()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AsyncEngine",
    "AsyncSession",
    "AsyncSessionLocal",
    "create_async_engine_from_settings",
    "dispose_engine",
    "get_engine",
    "get_session",
    "get_sessionmaker",
]
