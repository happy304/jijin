"""Tests for LLMService unified pipeline (task 7.5).

Covers the full pipeline: budget check → cache → provider selection →
call → schema validation → audit → cache write → fallback.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ai.provider import LLMProviderError, LLMResponse, Message
from app.ai.service import (
    AllProvidersFailedError,
    BudgetExhaustedError,
    LLMResult,
    LLMService,
    ProviderConfig,
    SchemaValidationError,
)


# ---------------------------------------------------------------------------
# Fixtures / Helpers
# ---------------------------------------------------------------------------


class FakeProvider:
    """A fake LLM provider for testing."""

    def __init__(self, name: str = "fake", response_content: str = "hello"):
        self.name = name
        self._response_content = response_content
        self.chat_calls: list[dict] = []

    async def chat(
        self,
        messages: list[Message],
        *,
        model: str = "test-model",
        temperature: float = 0.1,
        response_format: Literal["text", "json"] = "text",
        json_schema: dict | None = None,
        max_tokens: int = 2000,
    ) -> LLMResponse:
        self.chat_calls.append(
            {
                "messages": messages,
                "model": model,
                "temperature": temperature,
                "response_format": response_format,
                "json_schema": json_schema,
                "max_tokens": max_tokens,
            }
        )
        return LLMResponse(
            content=self._response_content,
            model=model,
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            cost_usd=0.001,
        )

    def estimate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        return 0.001


class FailingProvider:
    """A provider that always raises LLMProviderError."""

    def __init__(self, name: str = "failing"):
        self.name = name

    async def chat(self, messages, **kwargs) -> LLMResponse:
        raise LLMProviderError(self.name, "Service unavailable", status_code=503)

    def estimate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        return 0.0


def make_service(
    providers: list[ProviderConfig] | None = None,
    cache: Any = None,
    budget: Any = None,
    audit: Any = None,
) -> LLMService:
    """Create an LLMService with sensible test defaults."""
    if providers is None:
        providers = [
            ProviderConfig(
                provider=FakeProvider("primary", "response text"),
                model="test-model",
                priority=1,
            )
        ]
    return LLMService(
        providers=providers,
        cache=cache,
        budget=budget,
        audit=audit,
        default_cache_ttl=3600,
    )


# ---------------------------------------------------------------------------
# Tests: Basic call flow
# ---------------------------------------------------------------------------


class TestBasicCallFlow:
    """Test the happy path of the LLM call pipeline."""

    @pytest.mark.asyncio
    async def test_simple_text_call(self):
        """A simple text call should return the provider's response."""
        provider = FakeProvider("test", "Hello world")
        service = make_service(
            providers=[ProviderConfig(provider=provider, model="m1", priority=1)]
        )

        result = await service.call("test_case", "Say hello")

        assert isinstance(result, LLMResult)
        assert result.content == "Hello world"
        assert result.provider == "test"
        assert result.model == "m1"
        assert result.cached is False
        assert result.cost_usd == 0.001
        assert result.latency_ms >= 0

    @pytest.mark.asyncio
    async def test_json_schema_validation_success(self):
        """When schema is provided and response is valid JSON, parse it."""
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }
        provider = FakeProvider("test", json.dumps({"name": "Alice"}))
        service = make_service(
            providers=[ProviderConfig(provider=provider, model="m1", priority=1)]
        )

        result = await service.call("test_case", "Get name", schema=schema)

        assert result.content == {"name": "Alice"}
        assert result.cached is False

    @pytest.mark.asyncio
    async def test_json_schema_validation_failure_triggers_fallback(self):
        """Invalid JSON should trigger fallback to next provider."""
        schema = {
            "type": "object",
            "properties": {"count": {"type": "integer"}},
            "required": ["count"],
        }
        # First provider returns invalid JSON
        bad_provider = FakeProvider("bad", "not json at all")
        # Second provider returns valid JSON
        good_provider = FakeProvider("good", json.dumps({"count": 42}))

        service = make_service(
            providers=[
                ProviderConfig(provider=bad_provider, model="m1", priority=1),
                ProviderConfig(provider=good_provider, model="m2", priority=2),
            ]
        )

        result = await service.call("test_case", "Count", schema=schema)

        assert result.content == {"count": 42}
        assert result.provider == "good"

    @pytest.mark.asyncio
    async def test_messages_built_correctly(self):
        """Messages should include system prompt if provided."""
        provider = FakeProvider("test", "ok")
        service = make_service(
            providers=[ProviderConfig(provider=provider, model="m1", priority=1)]
        )

        await service.call(
            "test_case", "user msg", system_prompt="system msg"
        )

        call = provider.chat_calls[0]
        messages = call["messages"]
        assert len(messages) == 2
        assert messages[0].role == "system"
        assert messages[0].content == "system msg"
        assert messages[1].role == "user"
        assert messages[1].content == "user msg"

    @pytest.mark.asyncio
    async def test_messages_without_system_prompt(self):
        """Without system prompt, only user message should be sent."""
        provider = FakeProvider("test", "ok")
        service = make_service(
            providers=[ProviderConfig(provider=provider, model="m1", priority=1)]
        )

        await service.call("test_case", "user msg")

        call = provider.chat_calls[0]
        messages = call["messages"]
        assert len(messages) == 1
        assert messages[0].role == "user"


# ---------------------------------------------------------------------------
# Tests: Budget control
# ---------------------------------------------------------------------------


class TestBudgetControl:
    """Test budget check integration."""

    @pytest.mark.asyncio
    async def test_budget_exhausted_raises(self):
        """When budget is exhausted, BudgetExhaustedError is raised."""
        budget = AsyncMock()
        budget.is_exhausted = AsyncMock(return_value=True)

        service = make_service(budget=budget)

        with pytest.raises(BudgetExhaustedError) as exc_info:
            await service.call("non_critical", "prompt")

        assert exc_info.value.use_case == "non_critical"

    @pytest.mark.asyncio
    async def test_budget_not_exhausted_proceeds(self):
        """When budget is not exhausted, call proceeds normally."""
        budget = AsyncMock()
        budget.is_exhausted = AsyncMock(return_value=False)
        budget.consume = AsyncMock()

        service = make_service(budget=budget)

        result = await service.call("test_case", "prompt")

        assert result.content == "response text"
        budget.consume.assert_called_once_with(0.001)

    @pytest.mark.asyncio
    async def test_budget_consume_called_on_success(self):
        """Budget.consume should be called with the cost after success."""
        budget = AsyncMock()
        budget.is_exhausted = AsyncMock(return_value=False)
        budget.consume = AsyncMock()

        service = make_service(budget=budget)
        await service.call("test_case", "prompt")

        budget.consume.assert_called_once_with(0.001)


# ---------------------------------------------------------------------------
# Tests: Cache
# ---------------------------------------------------------------------------


class TestCacheIntegration:
    """Test cache hit/miss behavior."""

    @pytest.mark.asyncio
    async def test_cache_hit_returns_cached_result(self):
        """On cache hit, return cached content without calling provider."""
        cache = AsyncMock()
        cache.get = AsyncMock(return_value="cached response")
        cache.set = AsyncMock()

        provider = FakeProvider("test", "fresh response")
        service = make_service(
            providers=[ProviderConfig(provider=provider, model="m1", priority=1)],
            cache=cache,
        )

        result = await service.call("test_case", "prompt")

        assert result.content == "cached response"
        assert result.cached is True
        assert len(provider.chat_calls) == 0  # Provider not called

    @pytest.mark.asyncio
    async def test_cache_hit_with_schema_parses_json(self):
        """Cached JSON string should be parsed when schema is provided."""
        schema = {"type": "object", "properties": {"x": {"type": "integer"}}}
        cache = AsyncMock()
        cache.get = AsyncMock(return_value=json.dumps({"x": 5}))
        cache.set = AsyncMock()

        service = make_service(cache=cache)

        result = await service.call("test_case", "prompt", schema=schema)

        assert result.content == {"x": 5}
        assert result.cached is True

    @pytest.mark.asyncio
    async def test_cache_miss_calls_provider_and_writes_cache(self):
        """On cache miss, call provider and write result to cache."""
        cache = AsyncMock()
        cache.get = AsyncMock(return_value=None)
        cache.set = AsyncMock()

        provider = FakeProvider("test", "new response")
        service = make_service(
            providers=[ProviderConfig(provider=provider, model="m1", priority=1)],
            cache=cache,
        )

        result = await service.call("test_case", "prompt")

        assert result.content == "new response"
        assert result.cached is False
        cache.set.assert_called_once()

    @pytest.mark.asyncio
    async def test_cache_ttl_zero_skips_cache(self):
        """When cache_ttl=0, cache should not be consulted."""
        cache = AsyncMock()
        cache.get = AsyncMock(return_value="should not be used")
        cache.set = AsyncMock()

        provider = FakeProvider("test", "direct response")
        service = make_service(
            providers=[ProviderConfig(provider=provider, model="m1", priority=1)],
            cache=cache,
        )

        result = await service.call("test_case", "prompt", cache_ttl=0)

        assert result.content == "direct response"
        cache.get.assert_not_called()
        cache.set.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: Provider selection and fallback
# ---------------------------------------------------------------------------


class TestProviderSelection:
    """Test provider ordering and fallback behavior."""

    @pytest.mark.asyncio
    async def test_providers_ordered_by_priority(self):
        """Providers should be tried in priority order."""
        p1 = FakeProvider("low_priority", "p1")
        p2 = FakeProvider("high_priority", "p2")

        service = make_service(
            providers=[
                ProviderConfig(provider=p1, model="m1", priority=10),
                ProviderConfig(provider=p2, model="m2", priority=1),
            ]
        )

        result = await service.call("test_case", "prompt")

        # p2 has higher priority (lower number), should be called first
        assert result.provider == "high_priority"
        assert len(p1.chat_calls) == 0

    @pytest.mark.asyncio
    async def test_preferred_providers_come_first(self):
        """Preferred providers should be tried before others."""
        p1 = FakeProvider("alpha", "from alpha")
        p2 = FakeProvider("beta", "from beta")

        service = make_service(
            providers=[
                ProviderConfig(provider=p1, model="m1", priority=1),
                ProviderConfig(provider=p2, model="m2", priority=2),
            ]
        )

        result = await service.call(
            "test_case", "prompt", preferred_providers=["beta"]
        )

        assert result.provider == "beta"

    @pytest.mark.asyncio
    async def test_fallback_on_provider_error(self):
        """If first provider fails, fallback to next."""
        failing = FailingProvider("primary")
        backup = FakeProvider("backup", "backup response")

        service = make_service(
            providers=[
                ProviderConfig(provider=failing, model="m1", priority=1),
                ProviderConfig(provider=backup, model="m2", priority=2),
            ]
        )

        result = await service.call("test_case", "prompt")

        assert result.content == "backup response"
        assert result.provider == "backup"

    @pytest.mark.asyncio
    async def test_all_providers_fail_raises(self):
        """When all providers fail, AllProvidersFailedError is raised."""
        failing1 = FailingProvider("p1")
        failing2 = FailingProvider("p2")

        service = make_service(
            providers=[
                ProviderConfig(provider=failing1, model="m1", priority=1),
                ProviderConfig(provider=failing2, model="m2", priority=2),
            ]
        )

        with pytest.raises(AllProvidersFailedError) as exc_info:
            await service.call("test_case", "prompt")

        assert exc_info.value.use_case == "test_case"
        assert len(exc_info.value.errors) == 2

    @pytest.mark.asyncio
    async def test_unhealthy_providers_skipped(self):
        """Unhealthy providers should be skipped."""
        unhealthy = FakeProvider("unhealthy", "should not be used")
        healthy = FakeProvider("healthy", "healthy response")

        service = make_service(
            providers=[
                ProviderConfig(
                    provider=unhealthy, model="m1", priority=1, healthy=False
                ),
                ProviderConfig(provider=healthy, model="m2", priority=2, healthy=True),
            ]
        )

        result = await service.call("test_case", "prompt")

        assert result.provider == "healthy"
        assert len(unhealthy.chat_calls) == 0

    @pytest.mark.asyncio
    async def test_no_providers_configured_raises(self):
        """If no providers are configured, raise AllProvidersFailedError."""
        service = make_service(providers=[])

        with pytest.raises(AllProvidersFailedError):
            await service.call("test_case", "prompt")


# ---------------------------------------------------------------------------
# Tests: Audit logging
# ---------------------------------------------------------------------------


class TestAuditLogging:
    """Test audit record creation."""

    @pytest.mark.asyncio
    async def test_audit_recorded_on_success(self):
        """Successful calls should be audited."""
        audit = AsyncMock()
        audit.record = AsyncMock()

        service = make_service(audit=audit)
        await service.call("test_case", "prompt")

        audit.record.assert_called_once()
        call_kwargs = audit.record.call_args[1]
        assert call_kwargs["success"] is True
        assert call_kwargs["use_case"] == "test_case"
        assert call_kwargs["provider"] == "primary"

    @pytest.mark.asyncio
    async def test_audit_recorded_on_failure(self):
        """Failed provider calls should also be audited."""
        audit = AsyncMock()
        audit.record = AsyncMock()

        failing = FailingProvider("p1")
        backup = FakeProvider("backup", "ok")

        service = LLMService(
            providers=[
                ProviderConfig(provider=failing, model="m1", priority=1),
                ProviderConfig(provider=backup, model="m2", priority=2),
            ],
            audit=audit,
            default_cache_ttl=0,
        )

        await service.call("test_case", "prompt")

        # Should have 2 audit calls: 1 failure + 1 success
        assert audit.record.call_count == 2
        first_call = audit.record.call_args_list[0][1]
        assert first_call["success"] is False
        second_call = audit.record.call_args_list[1][1]
        assert second_call["success"] is True

    @pytest.mark.asyncio
    async def test_audit_error_does_not_break_pipeline(self):
        """If audit raises, the pipeline should still succeed."""
        audit = AsyncMock()
        audit.record = AsyncMock(side_effect=Exception("DB down"))

        service = make_service(audit=audit)
        result = await service.call("test_case", "prompt")

        # Should still return successfully
        assert result.content == "response text"


# ---------------------------------------------------------------------------
# Tests: Schema validation
# ---------------------------------------------------------------------------


class TestSchemaValidation:
    """Test JSON Schema validation behavior."""

    @pytest.mark.asyncio
    async def test_invalid_json_triggers_fallback(self):
        """Non-JSON response with schema should trigger fallback."""
        schema = {"type": "object", "properties": {"x": {"type": "integer"}}}
        bad = FakeProvider("bad", "not json")
        good = FakeProvider("good", json.dumps({"x": 1}))

        service = make_service(
            providers=[
                ProviderConfig(provider=bad, model="m1", priority=1),
                ProviderConfig(provider=good, model="m2", priority=2),
            ]
        )

        result = await service.call("test_case", "prompt", schema=schema)
        assert result.content == {"x": 1}

    @pytest.mark.asyncio
    async def test_schema_mismatch_triggers_fallback(self):
        """Valid JSON that doesn't match schema should trigger fallback."""
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }
        # Returns valid JSON but missing required field
        bad = FakeProvider("bad", json.dumps({"age": 25}))
        good = FakeProvider("good", json.dumps({"name": "Bob"}))

        service = make_service(
            providers=[
                ProviderConfig(provider=bad, model="m1", priority=1),
                ProviderConfig(provider=good, model="m2", priority=2),
            ]
        )

        result = await service.call("test_case", "prompt", schema=schema)
        assert result.content == {"name": "Bob"}

    @pytest.mark.asyncio
    async def test_all_fail_schema_validation(self):
        """If all providers fail schema validation, raise error."""
        schema = {"type": "object", "required": ["key"]}
        bad1 = FakeProvider("bad1", "invalid")
        bad2 = FakeProvider("bad2", json.dumps({"wrong": True}))

        service = make_service(
            providers=[
                ProviderConfig(provider=bad1, model="m1", priority=1),
                ProviderConfig(provider=bad2, model="m2", priority=2),
            ]
        )

        with pytest.raises(AllProvidersFailedError):
            await service.call("test_case", "prompt", schema=schema)


# ---------------------------------------------------------------------------
# Tests: End-to-end pipeline
# ---------------------------------------------------------------------------


class TestEndToEndPipeline:
    """Integration-style tests covering the full pipeline."""

    @pytest.mark.asyncio
    async def test_full_pipeline_budget_cache_audit(self):
        """Full pipeline: budget OK → cache miss → call → audit → cache write."""
        budget = AsyncMock()
        budget.is_exhausted = AsyncMock(return_value=False)
        budget.consume = AsyncMock()

        cache = AsyncMock()
        cache.get = AsyncMock(return_value=None)
        cache.set = AsyncMock()

        audit = AsyncMock()
        audit.record = AsyncMock()

        provider = FakeProvider("main", "pipeline result")
        service = LLMService(
            providers=[ProviderConfig(provider=provider, model="m1", priority=1)],
            cache=cache,
            budget=budget,
            audit=audit,
            default_cache_ttl=3600,
        )

        result = await service.call("pipeline_test", "test prompt")

        # Verify full pipeline executed
        assert result.content == "pipeline result"
        assert result.cached is False
        budget.is_exhausted.assert_called_once_with("pipeline_test")
        cache.get.assert_called_once()
        cache.set.assert_called_once()
        audit.record.assert_called_once()
        budget.consume.assert_called_once_with(0.001)

    @pytest.mark.asyncio
    async def test_pipeline_with_none_components(self):
        """Pipeline works fine when cache/budget/audit are None."""
        provider = FakeProvider("solo", "solo result")
        service = LLMService(
            providers=[ProviderConfig(provider=provider, model="m1", priority=1)],
            cache=None,
            budget=None,
            audit=None,
        )

        result = await service.call("test_case", "prompt")

        assert result.content == "solo result"
        assert result.provider == "solo"

    @pytest.mark.asyncio
    async def test_temperature_and_max_tokens_passed(self):
        """Custom temperature and max_tokens should be forwarded."""
        provider = FakeProvider("test", "ok")
        service = make_service(
            providers=[ProviderConfig(provider=provider, model="m1", priority=1)]
        )

        await service.call(
            "test_case", "prompt", temperature=0.8, max_tokens=500
        )

        call = provider.chat_calls[0]
        assert call["temperature"] == 0.8
        assert call["max_tokens"] == 500
