"""LLM response cache backed by Redis.

Caches LLM responses to avoid redundant API calls and reduce costs
(requirement 11.4). The cache key is derived from the use case, prompt,
and optional JSON schema using SHA-256 hashing.

Key format
----------
``llm:cache:{sha256_hex}``

where ``sha256_hex = sha256(use_case + prompt + json.dumps(schema, sort_keys=True))``

TTL
---
Default: 7 days (604800 seconds). Configurable per-call via the ``ttl``
parameter on :meth:`LLMCache.set`.

Design notes
------------
* Uses ``redis.asyncio`` for non-blocking I/O.
* Graceful degradation: all operations catch exceptions and log warnings.
  A Redis outage must never break the LLM pipeline — it simply means
  cache misses and re-calls to the provider.
* The cache stores raw response strings (text or JSON serialized).
"""

from __future__ import annotations

import hashlib
import json

import redis.asyncio as aioredis

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Default TTL for LLM cache entries (seconds). 7 days.
DEFAULT_TTL_SECONDS: int = 7 * 24 * 3600  # 604800

#: Key prefix for LLM cache entries.
LLM_CACHE_PREFIX: str = "llm:cache"


# ---------------------------------------------------------------------------
# Key generation
# ---------------------------------------------------------------------------


def build_cache_key(use_case: str, prompt: str, schema: dict | None = None) -> str:
    """Build the Redis key for an LLM cache entry.

    The key is ``llm:cache:{sha256_hex}`` where the hash input is the
    concatenation of *use_case*, *prompt*, and the deterministic JSON
    serialization of *schema* (or empty string if None).

    Args:
        use_case: The LLM use case identifier (e.g. "announcement_parse").
        prompt: The full prompt text sent to the LLM.
        schema: Optional JSON Schema dict for structured output.

    Returns:
        A Redis key string.
    """
    schema_str = json.dumps(schema, sort_keys=True) if schema is not None else ""
    raw = use_case + prompt + schema_str
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"{LLM_CACHE_PREFIX}:{digest}"


# ---------------------------------------------------------------------------
# LLMCache class
# ---------------------------------------------------------------------------


class LLMCache:
    """Redis-backed cache for LLM responses.

    Args:
        redis: An async Redis client instance. If not provided, one will
            be created from application settings on first use.
        default_ttl: Default time-to-live in seconds for cache entries.
            Defaults to :data:`DEFAULT_TTL_SECONDS` (7 days).
    """

    def __init__(
        self,
        redis: aioredis.Redis | None = None,
        default_ttl: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        self._redis = redis
        self._default_ttl = default_ttl

    async def _get_redis(self) -> aioredis.Redis:
        """Return the Redis client, creating one from settings if needed."""
        if self._redis is None:
            settings = get_settings()
            self._redis = aioredis.from_url(
                settings.redis_url,
                decode_responses=True,
                encoding="utf-8",
            )
        return self._redis

    async def get(
        self,
        use_case: str,
        prompt: str,
        schema: dict | None = None,
    ) -> str | None:
        """Retrieve a cached LLM response.

        Args:
            use_case: The LLM use case identifier.
            prompt: The full prompt text.
            schema: Optional JSON Schema dict.

        Returns:
            The cached response string if found, or None on cache miss
            or error.
        """
        try:
            client = await self._get_redis()
            key = build_cache_key(use_case, prompt, schema)
            value = await client.get(key)
            if value is not None:
                log.debug("llm_cache.hit", use_case=use_case, key=key)
            return value
        except Exception as exc:
            log.warning("llm_cache.get.error", use_case=use_case, error=str(exc))
            return None

    async def set(
        self,
        use_case: str,
        prompt: str,
        schema: dict | None = None,
        *,
        response: str,
        ttl: int | None = None,
    ) -> None:
        """Store an LLM response in the cache.

        Args:
            use_case: The LLM use case identifier.
            prompt: The full prompt text.
            schema: Optional JSON Schema dict.
            response: The LLM response string to cache.
            ttl: Time-to-live in seconds. Defaults to the instance's
                ``default_ttl`` (7 days).
        """
        effective_ttl = ttl if ttl is not None else self._default_ttl
        try:
            client = await self._get_redis()
            key = build_cache_key(use_case, prompt, schema)
            await client.set(key, response, ex=effective_ttl)
            log.debug("llm_cache.set", use_case=use_case, key=key, ttl=effective_ttl)
        except Exception as exc:
            log.warning("llm_cache.set.error", use_case=use_case, error=str(exc))

    async def invalidate(
        self,
        use_case: str,
        prompt: str,
        schema: dict | None = None,
    ) -> bool:
        """Remove a specific cache entry.

        Args:
            use_case: The LLM use case identifier.
            prompt: The full prompt text.
            schema: Optional JSON Schema dict.

        Returns:
            True if the key was deleted, False if it didn't exist or on error.
        """
        try:
            client = await self._get_redis()
            key = build_cache_key(use_case, prompt, schema)
            deleted = await client.delete(key)
            if deleted:
                log.debug("llm_cache.invalidate", use_case=use_case, key=key)
            return bool(deleted)
        except Exception as exc:
            log.warning(
                "llm_cache.invalidate.error", use_case=use_case, error=str(exc)
            )
            return False

    async def invalidate_by_use_case(self, use_case: str) -> int:
        """Remove all cache entries for a given use case.

        This performs a SCAN over keys matching the LLM cache prefix and
        checks if the use case matches. Since the hash doesn't preserve
        the use case in the key itself, this is a best-effort operation
        that clears ALL LLM cache entries (use with caution).

        For targeted invalidation, prefer :meth:`invalidate` with the
        exact parameters.

        Note:
            This method clears all LLM cache entries because the SHA-256
            key does not encode the use_case separately. If per-use-case
            invalidation is needed frequently, consider adding a secondary
            index or using a key prefix that includes the use_case.

        Returns:
            Number of keys deleted.
        """
        try:
            client = await self._get_redis()
            pattern = f"{LLM_CACHE_PREFIX}:*"
            deleted = 0
            async for key in client.scan_iter(match=pattern, count=100):
                await client.delete(key)
                deleted += 1
            if deleted:
                log.info(
                    "llm_cache.invalidate_all",
                    use_case=use_case,
                    keys_deleted=deleted,
                )
            return deleted
        except Exception as exc:
            log.warning(
                "llm_cache.invalidate_by_use_case.error",
                use_case=use_case,
                error=str(exc),
            )
            return 0

    async def close(self) -> None:
        """Close the Redis connection if owned by this instance."""
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

__all__ = [
    "DEFAULT_TTL_SECONDS",
    "LLM_CACHE_PREFIX",
    "LLMCache",
    "build_cache_key",
]
