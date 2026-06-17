"""Shared fixtures for data services tests.

Reuses the same SQLite in-memory pattern as repository tests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.data.migrations import run_upgrade


@pytest.fixture(scope="session")
def sqlite_db_path(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Create a single SQLite file for the whole test session."""
    db_file = tmp_path_factory.mktemp("db") / "test_services.db"
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        DATABASE_SYNC_URL=f"sqlite:///{db_file}",
        DB_AUTO_MIGRATE="true",
    )
    run_upgrade(settings, "head")
    return str(db_file)


@pytest.fixture
async def session(sqlite_db_path: str) -> AsyncIterator[AsyncSession]:
    """Yield an AsyncSession backed by the session-scoped SQLite DB.

    Each test runs inside a SAVEPOINT so changes are rolled back
    automatically, keeping tests isolated without recreating the schema.
    """
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{sqlite_db_path}",
        echo=False,
        future=True,
    )
    factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    async with factory() as sess:
        async with sess.begin():
            yield sess
            await sess.rollback()

    await engine.dispose()
