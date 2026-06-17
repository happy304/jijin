"""Unit tests for the LLM audit logging module (app/ai/audit.py).

Tests use an in-memory SQLite database to verify audit recording and
statistics queries without requiring a live PostgreSQL instance.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.ai.audit import LLMAuditLog
from app.data.models import Base
from app.data.models.llm_calls import LLMCall


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def async_session_factory():
    """Create an in-memory SQLite async engine and session factory for tests."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Create all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    yield factory

    await engine.dispose()


@pytest.fixture
def audit_log(async_session_factory) -> LLMAuditLog:
    """Create an LLMAuditLog instance with the test session factory."""
    return LLMAuditLog(session_factory=async_session_factory)


# ---------------------------------------------------------------------------
# LLMCall model tests
# ---------------------------------------------------------------------------


class TestLLMCallModel:
    """Tests for the LLMCall ORM model."""

    def test_tablename(self) -> None:
        """Model should map to the 'llm_calls' table."""
        assert LLMCall.__tablename__ == "llm_calls"

    def test_model_columns(self) -> None:
        """Model should have all expected columns."""
        column_names = {c.name for c in LLMCall.__table__.columns}
        expected = {
            "id",
            "provider",
            "model",
            "use_case",
            "prompt_hash",
            "prompt_text",
            "response_text",
            "prompt_tokens",
            "completion_tokens",
            "cost_usd",
            "latency_ms",
            "success",
            "error_msg",
            "created_at",
        }
        assert expected.issubset(column_names)

    @pytest.mark.asyncio
    async def test_create_record(self, async_session_factory) -> None:
        """Should be able to create and persist an LLMCall record."""
        async with async_session_factory() as session:
            record = LLMCall(
                provider="openai",
                model="gpt-4o",
                use_case="announcement_parse",
                prompt_hash="a" * 64,
                prompt_text="Parse this announcement",
                response_text='{"category": "DIVIDEND"}',
                prompt_tokens=100,
                completion_tokens=50,
                cost_usd=0.001,
                latency_ms=500,
                success=True,
                error_msg=None,
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)

            assert record.id is not None
            assert record.provider == "openai"
            assert record.model == "gpt-4o"
            assert record.use_case == "announcement_parse"
            assert record.success is True


# ---------------------------------------------------------------------------
# LLMAuditLog.record() tests
# ---------------------------------------------------------------------------


class TestLLMAuditLogRecord:
    """Tests for the LLMAuditLog.record() method."""

    @pytest.mark.asyncio
    async def test_record_successful_call(
        self, audit_log: LLMAuditLog, async_session_factory
    ) -> None:
        """record() should persist a successful LLM call."""
        await audit_log.record(
            provider="openai",
            model="gpt-4o",
            use_case="nl_query",
            prompt_hash="b" * 64,
            prompt_text="Find top funds by Sharpe",
            response_text='{"intent": "search_funds"}',
            prompt_tokens=200,
            completion_tokens=80,
            cost_usd=0.002,
            latency_ms=350,
            success=True,
            error_msg=None,
        )

        # Verify the record was persisted
        async with async_session_factory() as session:
            from sqlalchemy import select

            result = await session.execute(select(LLMCall))
            records = result.scalars().all()
            assert len(records) == 1
            assert records[0].provider == "openai"
            assert records[0].model == "gpt-4o"
            assert records[0].use_case == "nl_query"
            assert records[0].prompt_tokens == 200
            assert records[0].completion_tokens == 80
            assert records[0].success is True

    @pytest.mark.asyncio
    async def test_record_failed_call(
        self, audit_log: LLMAuditLog, async_session_factory
    ) -> None:
        """record() should persist a failed LLM call with error message."""
        await audit_log.record(
            provider="anthropic",
            model="claude-3-5-sonnet",
            use_case="strategy_gen",
            prompt_hash="c" * 64,
            prompt_text="Generate a momentum strategy",
            response_text=None,
            prompt_tokens=150,
            completion_tokens=None,
            cost_usd=None,
            latency_ms=5000,
            success=False,
            error_msg="Timeout after 5000ms",
        )

        async with async_session_factory() as session:
            from sqlalchemy import select

            result = await session.execute(select(LLMCall))
            records = result.scalars().all()
            assert len(records) == 1
            assert records[0].success is False
            assert records[0].error_msg == "Timeout after 5000ms"
            assert records[0].response_text is None

    @pytest.mark.asyncio
    async def test_record_partial_data(
        self, audit_log: LLMAuditLog, async_session_factory
    ) -> None:
        """record() should handle partial data (all fields optional)."""
        await audit_log.record(
            provider="deepseek",
            model="deepseek-chat",
            use_case="factor_brainstorm",
            success=True,
        )

        async with async_session_factory() as session:
            from sqlalchemy import select

            result = await session.execute(select(LLMCall))
            records = result.scalars().all()
            assert len(records) == 1
            assert records[0].provider == "deepseek"
            assert records[0].prompt_text is None
            assert records[0].prompt_tokens is None

    @pytest.mark.asyncio
    async def test_record_does_not_raise_on_db_error(self) -> None:
        """record() should swallow database errors gracefully."""
        # Use a broken session factory that always raises
        from unittest.mock import AsyncMock, MagicMock

        broken_factory = MagicMock()
        broken_session = AsyncMock()
        broken_session.add = MagicMock(side_effect=RuntimeError("DB down"))
        broken_factory.return_value.__aenter__ = AsyncMock(return_value=broken_session)
        broken_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        audit = LLMAuditLog(session_factory=broken_factory)

        # Should not raise
        await audit.record(
            provider="openai",
            model="gpt-4o",
            use_case="test",
            success=True,
        )

    @pytest.mark.asyncio
    async def test_record_multiple_calls(
        self, audit_log: LLMAuditLog, async_session_factory
    ) -> None:
        """record() should handle multiple sequential calls."""
        for i in range(5):
            await audit_log.record(
                provider="openai",
                model="gpt-4o",
                use_case=f"use_case_{i}",
                prompt_tokens=100 + i * 10,
                completion_tokens=50 + i * 5,
                cost_usd=0.001 * (i + 1),
                latency_ms=200 + i * 50,
                success=True,
            )

        async with async_session_factory() as session:
            from sqlalchemy import select

            result = await session.execute(select(LLMCall))
            records = result.scalars().all()
            assert len(records) == 5


# ---------------------------------------------------------------------------
# LLMAuditLog.get_stats() tests
# ---------------------------------------------------------------------------


class TestLLMAuditLogGetStats:
    """Tests for the LLMAuditLog.get_stats() method."""

    @pytest.mark.asyncio
    async def test_get_stats_empty_table(self, audit_log: LLMAuditLog) -> None:
        """get_stats() should return zeroed stats when no records exist."""
        stats = await audit_log.get_stats(days=30)

        assert stats["total_calls"] == 0
        assert stats["successful_calls"] == 0
        assert stats["failed_calls"] == 0
        assert stats["total_prompt_tokens"] == 0
        assert stats["total_completion_tokens"] == 0
        assert stats["total_tokens"] == 0
        assert stats["total_cost_usd"] == 0.0
        assert stats["avg_latency_ms"] is None
        assert stats["by_provider"] == {}
        assert stats["by_use_case"] == {}
        assert "period_start" in stats
        assert "period_end" in stats

    @pytest.mark.asyncio
    async def test_get_stats_with_data(
        self, audit_log: LLMAuditLog, async_session_factory
    ) -> None:
        """get_stats() should aggregate data correctly."""
        # Insert test records directly
        async with async_session_factory() as session:
            now = datetime.now(timezone.utc)
            records = [
                LLMCall(
                    provider="openai",
                    model="gpt-4o",
                    use_case="nl_query",
                    prompt_tokens=100,
                    completion_tokens=50,
                    cost_usd=0.002,
                    latency_ms=300,
                    success=True,
                    created_at=now - timedelta(days=1),
                ),
                LLMCall(
                    provider="openai",
                    model="gpt-4o",
                    use_case="announcement_parse",
                    prompt_tokens=200,
                    completion_tokens=100,
                    cost_usd=0.004,
                    latency_ms=500,
                    success=True,
                    created_at=now - timedelta(days=2),
                ),
                LLMCall(
                    provider="anthropic",
                    model="claude-3-5-sonnet",
                    use_case="nl_query",
                    prompt_tokens=150,
                    completion_tokens=75,
                    cost_usd=0.003,
                    latency_ms=400,
                    success=False,
                    error_msg="Rate limited",
                    created_at=now - timedelta(days=3),
                ),
            ]
            session.add_all(records)
            await session.commit()

        stats = await audit_log.get_stats(days=30)

        assert stats["total_calls"] == 3
        assert stats["successful_calls"] == 2
        assert stats["failed_calls"] == 1
        assert stats["total_prompt_tokens"] == 450  # 100 + 200 + 150
        assert stats["total_completion_tokens"] == 225  # 50 + 100 + 75
        assert stats["total_tokens"] == 675  # 450 + 225
        assert abs(stats["total_cost_usd"] - 0.009) < 1e-6
        assert stats["avg_latency_ms"] is not None
        assert abs(stats["avg_latency_ms"] - 400.0) < 1.0  # (300+500+400)/3

    @pytest.mark.asyncio
    async def test_get_stats_by_provider(
        self, audit_log: LLMAuditLog, async_session_factory
    ) -> None:
        """get_stats() should break down stats by provider."""
        async with async_session_factory() as session:
            now = datetime.now(timezone.utc)
            records = [
                LLMCall(
                    provider="openai",
                    model="gpt-4o",
                    use_case="test",
                    prompt_tokens=100,
                    completion_tokens=50,
                    cost_usd=0.002,
                    success=True,
                    created_at=now - timedelta(days=1),
                ),
                LLMCall(
                    provider="openai",
                    model="gpt-4o",
                    use_case="test",
                    prompt_tokens=200,
                    completion_tokens=100,
                    cost_usd=0.004,
                    success=True,
                    created_at=now - timedelta(days=2),
                ),
                LLMCall(
                    provider="anthropic",
                    model="claude-3-5-sonnet",
                    use_case="test",
                    prompt_tokens=150,
                    completion_tokens=75,
                    cost_usd=0.003,
                    success=True,
                    created_at=now - timedelta(days=3),
                ),
            ]
            session.add_all(records)
            await session.commit()

        stats = await audit_log.get_stats(days=30)

        assert "openai" in stats["by_provider"]
        assert "anthropic" in stats["by_provider"]
        assert stats["by_provider"]["openai"]["calls"] == 2
        assert stats["by_provider"]["openai"]["prompt_tokens"] == 300
        assert stats["by_provider"]["openai"]["completion_tokens"] == 150
        assert abs(stats["by_provider"]["openai"]["cost_usd"] - 0.006) < 1e-6
        assert stats["by_provider"]["anthropic"]["calls"] == 1

    @pytest.mark.asyncio
    async def test_get_stats_by_use_case(
        self, audit_log: LLMAuditLog, async_session_factory
    ) -> None:
        """get_stats() should break down stats by use case."""
        async with async_session_factory() as session:
            now = datetime.now(timezone.utc)
            records = [
                LLMCall(
                    provider="openai",
                    model="gpt-4o",
                    use_case="nl_query",
                    prompt_tokens=100,
                    completion_tokens=50,
                    cost_usd=0.002,
                    success=True,
                    created_at=now - timedelta(days=1),
                ),
                LLMCall(
                    provider="openai",
                    model="gpt-4o",
                    use_case="announcement_parse",
                    prompt_tokens=200,
                    completion_tokens=100,
                    cost_usd=0.004,
                    success=True,
                    created_at=now - timedelta(days=2),
                ),
                LLMCall(
                    provider="openai",
                    model="gpt-4o",
                    use_case="nl_query",
                    prompt_tokens=120,
                    completion_tokens=60,
                    cost_usd=0.0025,
                    success=True,
                    created_at=now - timedelta(days=3),
                ),
            ]
            session.add_all(records)
            await session.commit()

        stats = await audit_log.get_stats(days=30)

        assert "nl_query" in stats["by_use_case"]
        assert "announcement_parse" in stats["by_use_case"]
        assert stats["by_use_case"]["nl_query"]["calls"] == 2
        assert stats["by_use_case"]["announcement_parse"]["calls"] == 1

    @pytest.mark.asyncio
    async def test_get_stats_respects_days_filter(
        self, audit_log: LLMAuditLog, async_session_factory
    ) -> None:
        """get_stats() should only include records within the specified window."""
        async with async_session_factory() as session:
            now = datetime.now(timezone.utc)
            records = [
                # Within 7 days
                LLMCall(
                    provider="openai",
                    model="gpt-4o",
                    use_case="recent",
                    prompt_tokens=100,
                    completion_tokens=50,
                    cost_usd=0.002,
                    success=True,
                    created_at=now - timedelta(days=3),
                ),
                # Outside 7 days (but within 30)
                LLMCall(
                    provider="openai",
                    model="gpt-4o",
                    use_case="old",
                    prompt_tokens=200,
                    completion_tokens=100,
                    cost_usd=0.004,
                    success=True,
                    created_at=now - timedelta(days=15),
                ),
            ]
            session.add_all(records)
            await session.commit()

        # Query with 7-day window
        stats_7d = await audit_log.get_stats(days=7)
        assert stats_7d["total_calls"] == 1
        assert stats_7d["total_prompt_tokens"] == 100

        # Query with 30-day window
        stats_30d = await audit_log.get_stats(days=30)
        assert stats_30d["total_calls"] == 2
        assert stats_30d["total_prompt_tokens"] == 300

    @pytest.mark.asyncio
    async def test_get_stats_handles_null_tokens(
        self, audit_log: LLMAuditLog, async_session_factory
    ) -> None:
        """get_stats() should handle records with null token counts."""
        async with async_session_factory() as session:
            now = datetime.now(timezone.utc)
            record = LLMCall(
                provider="openai",
                model="gpt-4o",
                use_case="test",
                prompt_tokens=None,
                completion_tokens=None,
                cost_usd=None,
                latency_ms=None,
                success=False,
                error_msg="Connection refused",
                created_at=now - timedelta(days=1),
            )
            session.add(record)
            await session.commit()

        stats = await audit_log.get_stats(days=30)

        assert stats["total_calls"] == 1
        assert stats["total_prompt_tokens"] == 0
        assert stats["total_completion_tokens"] == 0
        assert stats["total_cost_usd"] == 0.0

    @pytest.mark.asyncio
    async def test_get_stats_does_not_raise_on_db_error(self) -> None:
        """get_stats() should return empty stats on database errors."""
        from unittest.mock import AsyncMock, MagicMock

        broken_factory = MagicMock()
        broken_session = AsyncMock()
        broken_session.execute = AsyncMock(side_effect=RuntimeError("DB down"))
        broken_factory.return_value.__aenter__ = AsyncMock(return_value=broken_session)
        broken_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        audit = LLMAuditLog(session_factory=broken_factory)
        stats = await audit.get_stats(days=30)

        assert stats["total_calls"] == 0
        assert stats["by_provider"] == {}
        assert stats["by_use_case"] == {}
