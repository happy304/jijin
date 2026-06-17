"""Tests for `app.data.session`.

We use the `aiosqlite` async driver so these tests run without a live
PostgreSQL server. The goal is to validate the session factory plumbing,
not SQL semantics — that is covered by repository-level tests in task 1.3.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.data import session as session_module
from app.data.session import (
    create_async_engine_from_settings,
    dispose_engine,
    get_engine,
    get_session,
    get_sessionmaker,
)


def _sqlite_settings() -> Settings:
    """Settings bound to an in-memory SQLite database for fast tests."""
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        DATABASE_URL="sqlite+aiosqlite:///:memory:",
        DATABASE_SYNC_URL="sqlite:///:memory:",
        DB_AUTO_MIGRATE="false",
    )


@pytest.fixture
async def _clean_engine() -> AsyncIterator[None]:
    """Ensure every test starts and ends with a clean engine cache."""
    await dispose_engine()
    try:
        yield
    finally:
        await dispose_engine()


async def test_create_async_engine_from_settings_builds_engine(
    _clean_engine: None,
) -> None:
    settings = _sqlite_settings()
    engine = create_async_engine_from_settings(settings)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            assert result.scalar_one() == 1
    finally:
        await engine.dispose()


async def test_get_engine_caches_single_instance(_clean_engine: None) -> None:
    settings = _sqlite_settings()
    engine_a = get_engine(settings)
    engine_b = get_engine(settings)
    assert engine_a is engine_b, "get_engine must return a cached singleton"


async def test_get_sessionmaker_returns_working_factory(
    _clean_engine: None,
) -> None:
    settings = _sqlite_settings()
    factory = get_sessionmaker(settings)
    async with factory() as session:
        assert isinstance(session, AsyncSession)
        result = await session.execute(text("SELECT 42"))
        assert result.scalar_one() == 42


async def test_get_session_yields_async_session_and_closes(
    _clean_engine: None,
) -> None:
    # Prime the cache with sqlite settings so `get_session` uses them.
    settings = _sqlite_settings()
    get_engine(settings)

    agen = get_session()
    session = await agen.__anext__()
    try:
        assert isinstance(session, AsyncSession)
        result = await session.execute(text("SELECT 7"))
        assert result.scalar_one() == 7
    finally:
        # Exhaust the generator so the `async with` inside `get_session`
        # runs its teardown and closes the session.
        with pytest.raises(StopAsyncIteration):
            await agen.__anext__()
    # After teardown `AsyncSession.close()` has been awaited, which
    # releases the underlying connection back to the pool. SQLAlchemy
    # leaves the session object usable but without an active
    # transaction; asserting no transaction is a stable proxy for
    # "teardown ran".
    assert session.in_transaction() is False


async def test_async_session_local_lazy_attribute(_clean_engine: None) -> None:
    """`AsyncSessionLocal` is exposed as a lazy module-level alias."""
    settings = _sqlite_settings()
    get_engine(settings)

    factory = session_module.AsyncSessionLocal  # resolved via __getattr__
    async with factory() as session:
        assert isinstance(session, AsyncSession)


async def test_dispose_engine_resets_cache(_clean_engine: None) -> None:
    settings = _sqlite_settings()
    engine_a = get_engine(settings)
    await dispose_engine()
    engine_b = get_engine(settings)
    assert engine_a is not engine_b, "dispose_engine must clear the cache"
