"""Redis cache layer for fund data.

Provides caching for fund metadata and recent NAV data to reduce
database load on frequently accessed queries (requirement 2.9).

Key naming conventions
----------------------
* Fund metadata:  ``fund:meta:{code}``
* NAV range:      ``fund:nav:{code}:{start}:{end}``

Cache invalidation
------------------
After data ingestion (upsert), the corresponding cache keys are
invalidated so subsequent reads fetch fresh data from the database.
NAV cache uses a pattern-based invalidation (all NAV keys for a given
fund code) since date ranges overlap.

Design notes
------------
* Uses ``redis.asyncio`` for non-blocking I/O in async contexts.
* A synchronous helper is provided for Celery tasks that run in
  their own event loop.
* TTL defaults: metadata = 1 hour, NAV = 30 minutes.
* Serialization uses JSON via Pydantic's model serialization for
  type safety and human-readable cache inspection.
* Graceful degradation: all cache operations catch exceptions and
  log warnings — a Redis outage must never break the data pipeline.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date
from typing import Any

import redis.asyncio as aioredis

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Default TTL for fund metadata cache (seconds).
META_TTL_SECONDS: int = 3600  # 1 hour

#: Default TTL for NAV data cache (seconds).
NAV_TTL_SECONDS: int = 1800  # 30 minutes

#: Key prefix for fund metadata.
META_KEY_PREFIX: str = "fund:meta"

#: Key prefix for NAV data.
NAV_KEY_PREFIX: str = "fund:nav"


# ---------------------------------------------------------------------------
# Key builders
# ---------------------------------------------------------------------------


def meta_key(code: str) -> str:
    """Build the cache key for fund metadata.

    Format: ``fund:meta:{code}``
    """
    return f"{META_KEY_PREFIX}:{code}"


def nav_key(code: str, start: date, end: date) -> str:
    """Build the cache key for a NAV date range.

    Format: ``fund:nav:{code}:{start}:{end}``

    Dates are formatted as ISO 8601 (YYYY-MM-DD).
    """
    return f"{NAV_KEY_PREFIX}:{code}:{start.isoformat()}:{end.isoformat()}"


def nav_pattern(code: str) -> str:
    """Build a glob pattern matching all NAV keys for a fund.

    Used for pattern-based invalidation after NAV data ingestion.
    """
    return f"{NAV_KEY_PREFIX}:{code}:*"


# ---------------------------------------------------------------------------
# Redis connection
# ---------------------------------------------------------------------------

_redis_client: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    """Return the shared async Redis client, creating it on first call.

    Uses the ``REDIS_URL`` from application settings (default db 0).
    """
    global _redis_client
    if _redis_client is None:
        settings = get_settings()
        _redis_client = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            encoding="utf-8",
        )
    return _redis_client


async def close_redis() -> None:
    """Close the Redis connection pool. Call on application shutdown."""
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None


# ---------------------------------------------------------------------------
# Cache read operations
# ---------------------------------------------------------------------------


async def get_fund_meta(code: str) -> dict[str, Any] | None:
    """Read fund metadata from cache.

    Returns:
        Deserialized dict if cache hit, None on miss or error.
    """
    try:
        client = await get_redis()
        raw = await client.get(meta_key(code))
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as exc:
        log.warning("cache.get_fund_meta.error", code=code, error=str(exc))
        return None


async def get_nav_records(
    code: str, start: date, end: date
) -> list[dict[str, Any]] | None:
    """Read NAV records from cache for a specific date range.

    Returns:
        List of NAV record dicts if cache hit, None on miss or error.
    """
    try:
        client = await get_redis()
        raw = await client.get(nav_key(code, start, end))
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as exc:
        log.warning(
            "cache.get_nav_records.error",
            code=code,
            start=str(start),
            end=str(end),
            error=str(exc),
        )
        return None


# ---------------------------------------------------------------------------
# Cache write operations
# ---------------------------------------------------------------------------


async def set_fund_meta(code: str, data: dict[str, Any]) -> None:
    """Write fund metadata to cache with default TTL.

    Args:
        code: Fund code.
        data: Serializable dict (typically from FundMeta.model_dump()).
    """
    try:
        client = await get_redis()
        serialized = json.dumps(data, default=str)
        await client.set(meta_key(code), serialized, ex=META_TTL_SECONDS)
    except Exception as exc:
        log.warning("cache.set_fund_meta.error", code=code, error=str(exc))


async def set_nav_records(
    code: str, start: date, end: date, data: list[dict[str, Any]]
) -> None:
    """Write NAV records to cache with default TTL.

    Args:
        code: Fund code.
        start: Start date of the range.
        end: End date of the range.
        data: List of NAV record dicts.
    """
    try:
        client = await get_redis()
        serialized = json.dumps(data, default=str)
        await client.set(nav_key(code, start, end), serialized, ex=NAV_TTL_SECONDS)
    except Exception as exc:
        log.warning(
            "cache.set_nav_records.error",
            code=code,
            start=str(start),
            end=str(end),
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# Cache invalidation
# ---------------------------------------------------------------------------


async def invalidate_fund_meta(code: str) -> None:
    """Delete the cached metadata for a specific fund.

    Called after fund metadata is upserted into the database.
    """
    try:
        client = await get_redis()
        await client.delete(meta_key(code))
        log.debug("cache.invalidate_fund_meta", code=code)
    except Exception as exc:
        log.warning("cache.invalidate_fund_meta.error", code=code, error=str(exc))


async def invalidate_nav(code: str) -> None:
    """Delete all cached NAV entries for a specific fund.

    Uses SCAN + DELETE to avoid blocking Redis with a KEYS command
    on large keyspaces. Called after NAV data is upserted.
    """
    try:
        client = await get_redis()
        pattern = nav_pattern(code)
        deleted = 0
        async for key in client.scan_iter(match=pattern, count=100):
            await client.delete(key)
            deleted += 1
        if deleted > 0:
            log.debug("cache.invalidate_nav", code=code, keys_deleted=deleted)
    except Exception as exc:
        log.warning("cache.invalidate_nav.error", code=code, error=str(exc))


async def invalidate_fund(code: str) -> None:
    """Invalidate all cache entries (meta + NAV) for a fund.

    Convenience function that combines both invalidation operations.
    """
    await invalidate_fund_meta(code)
    await invalidate_nav(code)


# ---------------------------------------------------------------------------
# Synchronous wrappers (for Celery tasks)
# ---------------------------------------------------------------------------


def sync_invalidate_fund_meta(code: str) -> None:
    """Synchronous wrapper for cache invalidation in Celery tasks."""
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(invalidate_fund_meta(code))
    except Exception as exc:
        log.warning("cache.sync_invalidate_fund_meta.error", code=code, error=str(exc))
    finally:
        loop.close()


def sync_invalidate_nav(code: str) -> None:
    """Synchronous wrapper for NAV cache invalidation in Celery tasks."""
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(invalidate_nav(code))
    except Exception as exc:
        log.warning("cache.sync_invalidate_nav.error", code=code, error=str(exc))
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

__all__ = [
    "META_KEY_PREFIX",
    "META_TTL_SECONDS",
    "NAV_KEY_PREFIX",
    "NAV_TTL_SECONDS",
    "close_redis",
    "get_fund_meta",
    "get_nav_records",
    "get_redis",
    "invalidate_fund",
    "invalidate_fund_meta",
    "invalidate_nav",
    "meta_key",
    "nav_key",
    "nav_pattern",
    "set_fund_meta",
    "set_nav_records",
    "sync_invalidate_fund_meta",
    "sync_invalidate_nav",
]
