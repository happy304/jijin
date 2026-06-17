"""Unit tests for the LLM cache module (app/ai/cache.py).

Tests use a mock Redis client to verify caching logic without requiring
a live Redis instance.
"""

from __future__ import annotations

import hashlib
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ai.cache import (
    DEFAULT_TTL_SECONDS,
    LLM_CACHE_PREFIX,
    LLMCache,
    build_cache_key,
)


# ---------------------------------------------------------------------------
# build_cache_key tests
# ---------------------------------------------------------------------------


class TestBuildCacheKey:
    """Tests for the cache key generation function."""

    def test_key_format(self) -> None:
        """Key should be prefixed with LLM_CACHE_PREFIX and a sha256 hex."""
        key = build_cache_key("test_case", "hello prompt")
        assert key.startswith(f"{LLM_CACHE_PREFIX}:")
        # The hash part should be 64 hex characters
        hash_part = key.split(":", maxsplit=2)[2]
        assert len(hash_part) == 64

    def test_deterministic(self) -> None:
        """Same inputs should always produce the same key."""
        key1 = build_cache_key("parse", "prompt text", {"type": "object"})
        key2 = build_cache_key("parse", "prompt text", {"type": "object"})
        assert key1 == key2

    def test_different_use_case_different_key(self) -> None:
        """Different use_case should produce different keys."""
        key1 = build_cache_key("case_a", "same prompt")
        key2 = build_cache_key("case_b", "same prompt")
        assert key1 != key2

    def test_different_prompt_different_key(self) -> None:
        """Different prompts should produce different keys."""
        key1 = build_cache_key("case", "prompt A")
        key2 = build_cache_key("case", "prompt B")
        assert key1 != key2

    def test_different_schema_different_key(self) -> None:
        """Different schemas should produce different keys."""
        key1 = build_cache_key("case", "prompt", {"type": "object"})
        key2 = build_cache_key("case", "prompt", {"type": "array"})
        assert key1 != key2

    def test_none_schema_vs_empty_dict(self) -> None:
        """None schema and empty dict schema should produce different keys."""
        key1 = build_cache_key("case", "prompt", None)
        key2 = build_cache_key("case", "prompt", {})
        assert key1 != key2

    def test_schema_key_order_independent(self) -> None:
        """Schema with different key order should produce the same key."""
        schema1 = {"type": "object", "properties": {"a": {"type": "string"}}}
        schema2 = {"properties": {"a": {"type": "string"}}, "type": "object"}
        key1 = build_cache_key("case", "prompt", schema1)
        key2 = build_cache_key("case", "prompt", schema2)
        assert key1 == key2

    def test_hash_matches_manual_computation(self) -> None:
        """Verify the hash matches a manual sha256 computation."""
        use_case = "announcement_parse"
        prompt = "Parse this announcement"
        schema = {"type": "object", "properties": {"category": {"type": "string"}}}

        schema_str = json.dumps(schema, sort_keys=True)
        raw = use_case + prompt + schema_str
        expected_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        expected_key = f"{LLM_CACHE_PREFIX}:{expected_hash}"

        assert build_cache_key(use_case, prompt, schema) == expected_key

    def test_no_schema_uses_empty_string(self) -> None:
        """When schema is None, the hash input uses empty string for schema."""
        use_case = "test"
        prompt = "hello"

        raw = use_case + prompt + ""
        expected_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        expected_key = f"{LLM_CACHE_PREFIX}:{expected_hash}"

        assert build_cache_key(use_case, prompt, None) == expected_key


# ---------------------------------------------------------------------------
# LLMCache tests
# ---------------------------------------------------------------------------


class TestLLMCache:
    """Tests for the LLMCache class."""

    @pytest.fixture
    def mock_redis(self) -> AsyncMock:
        """Create a mock async Redis client."""
        client = AsyncMock()
        client.get = AsyncMock(return_value=None)
        client.set = AsyncMock(return_value=True)
        client.delete = AsyncMock(return_value=1)
        client.scan_iter = MagicMock(return_value=self._async_iter([]))
        client.aclose = AsyncMock()
        return client

    @pytest.fixture
    def cache(self, mock_redis: AsyncMock) -> LLMCache:
        """Create an LLMCache instance with a mock Redis client."""
        return LLMCache(redis=mock_redis)

    @staticmethod
    async def _async_iter(items: list):
        """Helper to create an async iterator from a list."""
        for item in items:
            yield item

    # --- get() tests ---

    @pytest.mark.asyncio
    async def test_get_cache_miss(self, cache: LLMCache, mock_redis: AsyncMock) -> None:
        """get() should return None on cache miss."""
        mock_redis.get.return_value = None

        result = await cache.get("parse", "some prompt")

        assert result is None
        mock_redis.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_cache_hit(self, cache: LLMCache, mock_redis: AsyncMock) -> None:
        """get() should return the cached value on hit."""
        mock_redis.get.return_value = '{"category": "DIVIDEND"}'

        result = await cache.get("parse", "some prompt", {"type": "object"})

        assert result == '{"category": "DIVIDEND"}'

    @pytest.mark.asyncio
    async def test_get_uses_correct_key(
        self, cache: LLMCache, mock_redis: AsyncMock
    ) -> None:
        """get() should query Redis with the correct cache key."""
        use_case = "nl_query"
        prompt = "find top funds"
        schema = {"type": "object"}

        expected_key = build_cache_key(use_case, prompt, schema)

        await cache.get(use_case, prompt, schema)

        mock_redis.get.assert_called_once_with(expected_key)

    @pytest.mark.asyncio
    async def test_get_graceful_on_redis_error(
        self, cache: LLMCache, mock_redis: AsyncMock
    ) -> None:
        """get() should return None and not raise on Redis errors."""
        mock_redis.get.side_effect = ConnectionError("Redis down")

        result = await cache.get("parse", "prompt")

        assert result is None

    # --- set() tests ---

    @pytest.mark.asyncio
    async def test_set_stores_with_default_ttl(
        self, cache: LLMCache, mock_redis: AsyncMock
    ) -> None:
        """set() should store the response with the default 7-day TTL."""
        use_case = "parse"
        prompt = "test prompt"
        response = "parsed result"

        await cache.set(use_case, prompt, response=response)

        expected_key = build_cache_key(use_case, prompt, None)
        mock_redis.set.assert_called_once_with(
            expected_key, response, ex=DEFAULT_TTL_SECONDS
        )

    @pytest.mark.asyncio
    async def test_set_stores_with_custom_ttl(
        self, cache: LLMCache, mock_redis: AsyncMock
    ) -> None:
        """set() should respect a custom TTL."""
        custom_ttl = 3600  # 1 hour

        await cache.set("case", "prompt", response="result", ttl=custom_ttl)

        expected_key = build_cache_key("case", "prompt", None)
        mock_redis.set.assert_called_once_with(expected_key, "result", ex=custom_ttl)

    @pytest.mark.asyncio
    async def test_set_with_schema(
        self, cache: LLMCache, mock_redis: AsyncMock
    ) -> None:
        """set() should include schema in key computation."""
        schema = {"type": "object", "properties": {"name": {"type": "string"}}}

        await cache.set("case", "prompt", schema, response="result")

        expected_key = build_cache_key("case", "prompt", schema)
        mock_redis.set.assert_called_once_with(
            expected_key, "result", ex=DEFAULT_TTL_SECONDS
        )

    @pytest.mark.asyncio
    async def test_set_graceful_on_redis_error(
        self, cache: LLMCache, mock_redis: AsyncMock
    ) -> None:
        """set() should not raise on Redis errors."""
        mock_redis.set.side_effect = ConnectionError("Redis down")

        # Should not raise
        await cache.set("case", "prompt", response="result")

    # --- invalidate() tests ---

    @pytest.mark.asyncio
    async def test_invalidate_existing_key(
        self, cache: LLMCache, mock_redis: AsyncMock
    ) -> None:
        """invalidate() should return True when key existed."""
        mock_redis.delete.return_value = 1

        result = await cache.invalidate("case", "prompt")

        assert result is True
        expected_key = build_cache_key("case", "prompt", None)
        mock_redis.delete.assert_called_once_with(expected_key)

    @pytest.mark.asyncio
    async def test_invalidate_nonexistent_key(
        self, cache: LLMCache, mock_redis: AsyncMock
    ) -> None:
        """invalidate() should return False when key didn't exist."""
        mock_redis.delete.return_value = 0

        result = await cache.invalidate("case", "prompt")

        assert result is False

    @pytest.mark.asyncio
    async def test_invalidate_graceful_on_redis_error(
        self, cache: LLMCache, mock_redis: AsyncMock
    ) -> None:
        """invalidate() should return False on Redis errors."""
        mock_redis.delete.side_effect = ConnectionError("Redis down")

        result = await cache.invalidate("case", "prompt")

        assert result is False

    # --- invalidate_by_use_case() tests ---

    @pytest.mark.asyncio
    async def test_invalidate_by_use_case_deletes_matching_keys(
        self, mock_redis: AsyncMock
    ) -> None:
        """invalidate_by_use_case() should delete all LLM cache keys."""
        keys = [
            f"{LLM_CACHE_PREFIX}:abc123",
            f"{LLM_CACHE_PREFIX}:def456",
        ]
        mock_redis.scan_iter = MagicMock(
            return_value=self._async_iter(keys)
        )
        mock_redis.delete.return_value = 1

        cache = LLMCache(redis=mock_redis)
        deleted = await cache.invalidate_by_use_case("parse")

        assert deleted == 2
        assert mock_redis.delete.call_count == 2

    @pytest.mark.asyncio
    async def test_invalidate_by_use_case_no_keys(
        self, cache: LLMCache, mock_redis: AsyncMock
    ) -> None:
        """invalidate_by_use_case() should return 0 when no keys exist."""
        mock_redis.scan_iter = MagicMock(return_value=self._async_iter([]))

        deleted = await cache.invalidate_by_use_case("parse")

        assert deleted == 0

    # --- Custom default_ttl ---

    @pytest.mark.asyncio
    async def test_custom_default_ttl(self, mock_redis: AsyncMock) -> None:
        """LLMCache should use a custom default_ttl when provided."""
        custom_ttl = 86400  # 1 day
        cache = LLMCache(redis=mock_redis, default_ttl=custom_ttl)

        await cache.set("case", "prompt", response="result")

        expected_key = build_cache_key("case", "prompt", None)
        mock_redis.set.assert_called_once_with(expected_key, "result", ex=custom_ttl)

    @pytest.mark.asyncio
    async def test_per_call_ttl_overrides_default(
        self, mock_redis: AsyncMock
    ) -> None:
        """Per-call ttl should override the instance default_ttl."""
        cache = LLMCache(redis=mock_redis, default_ttl=86400)

        await cache.set("case", "prompt", response="result", ttl=3600)

        expected_key = build_cache_key("case", "prompt", None)
        mock_redis.set.assert_called_once_with(expected_key, "result", ex=3600)

    # --- close() ---

    @pytest.mark.asyncio
    async def test_close(self, cache: LLMCache, mock_redis: AsyncMock) -> None:
        """close() should close the Redis connection."""
        await cache.close()

        mock_redis.aclose.assert_called_once()

    # --- Auto-create Redis from settings ---

    @pytest.mark.asyncio
    async def test_auto_creates_redis_from_settings(self) -> None:
        """LLMCache should create a Redis client from settings if none provided."""
        cache = LLMCache()  # No redis argument

        with patch("app.ai.cache.get_settings") as mock_settings, \
             patch("app.ai.cache.aioredis") as mock_aioredis:
            mock_settings.return_value = MagicMock(redis_url="redis://localhost:6379/0")
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=None)
            mock_aioredis.from_url.return_value = mock_client

            result = await cache.get("case", "prompt")

            assert result is None
            mock_aioredis.from_url.assert_called_once_with(
                "redis://localhost:6379/0",
                decode_responses=True,
                encoding="utf-8",
            )

    # --- Round-trip integration test (with mock) ---

    @pytest.mark.asyncio
    async def test_set_then_get_round_trip(self, mock_redis: AsyncMock) -> None:
        """Verify that set() and get() use the same key for the same inputs."""
        stored: dict[str, str] = {}

        async def mock_set(key: str, value: str, ex: int) -> bool:
            stored[key] = value
            return True

        async def mock_get(key: str) -> str | None:
            return stored.get(key)

        mock_redis.set = AsyncMock(side_effect=mock_set)
        mock_redis.get = AsyncMock(side_effect=mock_get)

        cache = LLMCache(redis=mock_redis)

        use_case = "announcement_parse"
        prompt = "Parse: 本基金暂停大额申购"
        schema = {"type": "object", "properties": {"category": {"type": "string"}}}
        response = '{"category": "LIMIT_PURCHASE"}'

        # Set
        await cache.set(use_case, prompt, schema, response=response)

        # Get with same params should return the cached value
        result = await cache.get(use_case, prompt, schema)
        assert result == response

        # Get with different params should miss
        result2 = await cache.get(use_case, "different prompt", schema)
        assert result2 is None
