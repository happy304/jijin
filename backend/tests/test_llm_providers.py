"""Unit tests for LLM provider adapters (mock HTTP)."""

from __future__ import annotations

import json

import httpx
import pytest

from app.ai.provider import LLMProviderError, LLMResponse, Message
from app.ai.providers.anthropic import AnthropicProvider
from app.ai.providers.openai_compat import OpenAICompatProvider


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _openai_success_response(
    content: str = "Hello!",
    model: str = "gpt-4o-mini",
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
) -> dict:
    """Build a mock OpenAI-compatible chat completion response."""
    return {
        "id": "chatcmpl-test123",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def _anthropic_success_response(
    content: str = "Hello!",
    model: str = "claude-3-5-sonnet-20241022",
    input_tokens: int = 12,
    output_tokens: int = 8,
) -> dict:
    """Build a mock Anthropic Messages API response."""
    return {
        "id": "msg_test123",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": content}],
        "stop_reason": "end_turn",
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
    }


# ---------------------------------------------------------------------------
# OpenAICompatProvider Tests
# ---------------------------------------------------------------------------


class TestOpenAICompatProvider:
    """Tests for the OpenAI-compatible provider adapter."""

    @pytest.fixture
    def messages(self) -> list[Message]:
        return [
            Message(role="system", content="You are a helpful assistant."),
            Message(role="user", content="Hi"),
        ]

    @pytest.mark.asyncio
    async def test_chat_success(self, messages: list[Message]) -> None:
        """Test successful chat completion with mocked response."""
        mock_resp = _openai_success_response(content="Hi there!", prompt_tokens=15, completion_tokens=3)

        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json=mock_resp)
        )
        client = httpx.AsyncClient(transport=transport)

        provider = OpenAICompatProvider(
            name="test-openai",
            base_url="https://api.openai.com/v1",
            api_key="sk-test",
            default_model="gpt-4o-mini",
            http_client=client,
        )

        result = await provider.chat(messages, model="gpt-4o-mini")

        assert isinstance(result, LLMResponse)
        assert result.content == "Hi there!"
        assert result.model == "gpt-4o-mini"
        assert result.prompt_tokens == 15
        assert result.completion_tokens == 3
        assert result.total_tokens == 18
        assert result.finish_reason == "stop"
        assert result.cost_usd > 0

    @pytest.mark.asyncio
    async def test_chat_json_format(self, messages: list[Message]) -> None:
        """Test JSON response format request."""
        json_content = json.dumps({"answer": "42"})
        mock_resp = _openai_success_response(content=json_content)

        captured_request: httpx.Request | None = None

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return httpx.Response(200, json=mock_resp)

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)

        provider = OpenAICompatProvider(
            name="test-openai",
            base_url="https://api.openai.com/v1",
            api_key="sk-test",
            http_client=client,
        )

        result = await provider.chat(messages, response_format="json")

        assert result.content == json_content
        # Verify the request payload includes response_format
        assert captured_request is not None
        body = json.loads(captured_request.content)
        assert body["response_format"] == {"type": "json_object"}

    @pytest.mark.asyncio
    async def test_chat_json_with_schema(self, messages: list[Message]) -> None:
        """Test JSON response format with explicit schema."""
        schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        mock_resp = _openai_success_response(content='{"name": "test"}')

        captured_request: httpx.Request | None = None

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return httpx.Response(200, json=mock_resp)

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)

        provider = OpenAICompatProvider(
            name="test-openai",
            base_url="https://api.openai.com/v1",
            api_key="sk-test",
            http_client=client,
        )

        result = await provider.chat(messages, response_format="json", json_schema=schema)

        assert result.content == '{"name": "test"}'
        assert captured_request is not None
        body = json.loads(captured_request.content)
        assert body["response_format"]["type"] == "json_schema"
        assert body["response_format"]["json_schema"]["schema"] == schema

    @pytest.mark.asyncio
    async def test_chat_api_error(self, messages: list[Message]) -> None:
        """Test handling of API error responses."""
        error_resp = {
            "error": {
                "message": "Invalid API key",
                "type": "invalid_request_error",
            }
        }

        transport = httpx.MockTransport(
            lambda request: httpx.Response(401, json=error_resp)
        )
        client = httpx.AsyncClient(transport=transport)

        provider = OpenAICompatProvider(
            name="test-openai",
            base_url="https://api.openai.com/v1",
            api_key="sk-bad",
            http_client=client,
        )

        with pytest.raises(LLMProviderError) as exc_info:
            await provider.chat(messages)

        assert exc_info.value.provider == "test-openai"
        assert exc_info.value.status_code == 401
        assert "Invalid API key" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_chat_network_error(self, messages: list[Message]) -> None:
        """Test handling of network errors."""

        def raise_error(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused")

        transport = httpx.MockTransport(raise_error)
        client = httpx.AsyncClient(transport=transport)

        provider = OpenAICompatProvider(
            name="test-openai",
            base_url="https://api.openai.com/v1",
            api_key="sk-test",
            http_client=client,
        )

        with pytest.raises(LLMProviderError) as exc_info:
            await provider.chat(messages)

        assert "HTTP error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_default_model_used(self, messages: list[Message]) -> None:
        """Test that default model is used when not specified."""
        mock_resp = _openai_success_response(model="deepseek-chat")

        captured_request: httpx.Request | None = None

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return httpx.Response(200, json=mock_resp)

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)

        provider = OpenAICompatProvider(
            name="deepseek",
            base_url="https://api.deepseek.com/v1",
            api_key="sk-test",
            default_model="deepseek-chat",
            http_client=client,
        )

        result = await provider.chat(messages)

        assert result.model == "deepseek-chat"
        assert captured_request is not None
        body = json.loads(captured_request.content)
        assert body["model"] == "deepseek-chat"

    @pytest.mark.asyncio
    async def test_request_headers(self, messages: list[Message]) -> None:
        """Test that correct authorization headers are sent."""
        mock_resp = _openai_success_response()

        captured_request: httpx.Request | None = None

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return httpx.Response(200, json=mock_resp)

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)

        provider = OpenAICompatProvider(
            name="test",
            base_url="https://api.example.com/v1",
            api_key="sk-mykey123",
            http_client=client,
        )

        await provider.chat(messages)

        assert captured_request is not None
        assert captured_request.headers["authorization"] == "Bearer sk-mykey123"
        assert captured_request.headers["content-type"] == "application/json"

    def test_estimate_cost_known_model(self) -> None:
        """Test cost estimation for a known model."""
        provider = OpenAICompatProvider(
            name="test",
            base_url="https://api.openai.com/v1",
            api_key="sk-test",
        )

        # gpt-4o-mini: $0.15/1M prompt, $0.60/1M completion
        cost = provider.estimate_cost(1000, 500, model="gpt-4o-mini")
        expected = (1000 * 0.15 + 500 * 0.60) / 1_000_000
        assert abs(cost - expected) < 1e-10

    def test_estimate_cost_unknown_model(self) -> None:
        """Test cost estimation returns 0 for unknown models."""
        provider = OpenAICompatProvider(
            name="test",
            base_url="https://api.openai.com/v1",
            api_key="sk-test",
        )

        cost = provider.estimate_cost(1000, 500, model="unknown-model-xyz")
        assert cost == 0.0

    @pytest.mark.asyncio
    async def test_deepseek_provider(self, messages: list[Message]) -> None:
        """Test that DeepSeek works via OpenAI-compatible adapter."""
        mock_resp = _openai_success_response(
            content="DeepSeek response", model="deepseek-chat"
        )

        captured_request: httpx.Request | None = None

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return httpx.Response(200, json=mock_resp)

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)

        provider = OpenAICompatProvider(
            name="deepseek",
            base_url="https://api.deepseek.com/v1",
            api_key="sk-deepseek-test",
            default_model="deepseek-chat",
            http_client=client,
        )

        result = await provider.chat(messages)

        assert result.content == "DeepSeek response"
        assert captured_request is not None
        assert "api.deepseek.com" in str(captured_request.url)

    @pytest.mark.asyncio
    async def test_zhipu_provider(self, messages: list[Message]) -> None:
        """Test that Zhipu (智谱) works via OpenAI-compatible adapter."""
        mock_resp = _openai_success_response(content="智谱回复", model="glm-4")

        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json=mock_resp)
        )
        client = httpx.AsyncClient(transport=transport)

        provider = OpenAICompatProvider(
            name="zhipu",
            base_url="https://open.bigmodel.cn/api/paas/v4",
            api_key="zhipu-test-key",
            default_model="glm-4",
            http_client=client,
        )

        result = await provider.chat(messages)

        assert result.content == "智谱回复"
        assert result.model == "glm-4"

    @pytest.mark.asyncio
    async def test_moonshot_provider(self, messages: list[Message]) -> None:
        """Test that Moonshot (月之暗面) works via OpenAI-compatible adapter."""
        mock_resp = _openai_success_response(content="Moonshot回复", model="moonshot-v1-8k")

        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json=mock_resp)
        )
        client = httpx.AsyncClient(transport=transport)

        provider = OpenAICompatProvider(
            name="moonshot",
            base_url="https://api.moonshot.cn/v1",
            api_key="moonshot-test-key",
            default_model="moonshot-v1-8k",
            http_client=client,
        )

        result = await provider.chat(messages)

        assert result.content == "Moonshot回复"
        assert result.model == "moonshot-v1-8k"


# ---------------------------------------------------------------------------
# AnthropicProvider Tests
# ---------------------------------------------------------------------------


class TestAnthropicProvider:
    """Tests for the Anthropic Claude provider adapter."""

    @pytest.fixture
    def messages(self) -> list[Message]:
        return [
            Message(role="system", content="You are a helpful assistant."),
            Message(role="user", content="Hi"),
        ]

    @pytest.mark.asyncio
    async def test_chat_success(self, messages: list[Message]) -> None:
        """Test successful chat completion with Anthropic."""
        mock_resp = _anthropic_success_response(
            content="Hello from Claude!", input_tokens=20, output_tokens=10
        )

        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json=mock_resp)
        )
        client = httpx.AsyncClient(transport=transport)

        provider = AnthropicProvider(
            api_key="sk-ant-test",
            default_model="claude-3-5-sonnet-20241022",
            http_client=client,
        )

        result = await provider.chat(messages)

        assert isinstance(result, LLMResponse)
        assert result.content == "Hello from Claude!"
        assert result.model == "claude-3-5-sonnet-20241022"
        assert result.prompt_tokens == 20
        assert result.completion_tokens == 10
        assert result.total_tokens == 30
        assert result.cost_usd > 0

    @pytest.mark.asyncio
    async def test_system_message_separated(self, messages: list[Message]) -> None:
        """Test that system messages are sent as the 'system' parameter."""
        mock_resp = _anthropic_success_response()

        captured_request: httpx.Request | None = None

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return httpx.Response(200, json=mock_resp)

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)

        provider = AnthropicProvider(api_key="sk-ant-test", http_client=client)

        await provider.chat(messages)

        assert captured_request is not None
        body = json.loads(captured_request.content)
        # System should be a top-level parameter, not in messages
        assert body["system"] == "You are a helpful assistant."
        # Messages should only contain user/assistant messages
        assert len(body["messages"]) == 1
        assert body["messages"][0]["role"] == "user"

    @pytest.mark.asyncio
    async def test_no_system_message(self) -> None:
        """Test request without system message."""
        messages = [Message(role="user", content="Hello")]
        mock_resp = _anthropic_success_response()

        captured_request: httpx.Request | None = None

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return httpx.Response(200, json=mock_resp)

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)

        provider = AnthropicProvider(api_key="sk-ant-test", http_client=client)

        await provider.chat(messages)

        assert captured_request is not None
        body = json.loads(captured_request.content)
        assert "system" not in body

    @pytest.mark.asyncio
    async def test_json_format_adds_instruction(self, messages: list[Message]) -> None:
        """Test that JSON format adds instruction to system prompt."""
        mock_resp = _anthropic_success_response(content='{"result": "ok"}')

        captured_request: httpx.Request | None = None

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return httpx.Response(200, json=mock_resp)

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)

        provider = AnthropicProvider(api_key="sk-ant-test", http_client=client)

        schema = {"type": "object", "properties": {"result": {"type": "string"}}}
        result = await provider.chat(messages, response_format="json", json_schema=schema)

        assert result.content == '{"result": "ok"}'
        assert captured_request is not None
        body = json.loads(captured_request.content)
        # System should contain both original system message and JSON instruction
        assert "You are a helpful assistant." in body["system"]
        assert "valid JSON" in body["system"]
        assert "json_schema" in body["system"].lower() or "JSON Schema" in body["system"]

    @pytest.mark.asyncio
    async def test_api_error(self, messages: list[Message]) -> None:
        """Test handling of Anthropic API errors."""
        error_resp = {
            "type": "error",
            "error": {
                "type": "authentication_error",
                "message": "Invalid API key",
            },
        }

        transport = httpx.MockTransport(
            lambda request: httpx.Response(401, json=error_resp)
        )
        client = httpx.AsyncClient(transport=transport)

        provider = AnthropicProvider(api_key="sk-ant-bad", http_client=client)

        with pytest.raises(LLMProviderError) as exc_info:
            await provider.chat(messages)

        assert exc_info.value.provider == "anthropic"
        assert exc_info.value.status_code == 401
        assert "Invalid API key" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_network_error(self, messages: list[Message]) -> None:
        """Test handling of network errors."""

        def raise_error(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused")

        transport = httpx.MockTransport(raise_error)
        client = httpx.AsyncClient(transport=transport)

        provider = AnthropicProvider(api_key="sk-ant-test", http_client=client)

        with pytest.raises(LLMProviderError) as exc_info:
            await provider.chat(messages)

        assert "HTTP error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_request_headers(self, messages: list[Message]) -> None:
        """Test that correct Anthropic headers are sent."""
        mock_resp = _anthropic_success_response()

        captured_request: httpx.Request | None = None

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return httpx.Response(200, json=mock_resp)

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)

        provider = AnthropicProvider(api_key="sk-ant-mykey", http_client=client)

        await provider.chat(messages)

        assert captured_request is not None
        assert captured_request.headers["x-api-key"] == "sk-ant-mykey"
        assert captured_request.headers["anthropic-version"] == "2023-06-01"
        assert captured_request.headers["content-type"] == "application/json"

    @pytest.mark.asyncio
    async def test_empty_messages_raises(self) -> None:
        """Test that empty conversation (only system) raises error."""
        messages = [Message(role="system", content="System only")]

        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json=_anthropic_success_response())
        )
        client = httpx.AsyncClient(transport=transport)

        provider = AnthropicProvider(api_key="sk-ant-test", http_client=client)

        with pytest.raises(LLMProviderError) as exc_info:
            await provider.chat(messages)

        assert "At least one user or assistant message" in str(exc_info.value)

    def test_estimate_cost_known_model(self) -> None:
        """Test cost estimation for Claude models."""
        provider = AnthropicProvider(api_key="sk-ant-test")

        # claude-3-5-sonnet: $3/1M input, $15/1M output
        cost = provider.estimate_cost(1000, 500, model="claude-3-5-sonnet-20241022")
        expected = (1000 * 3.0 + 500 * 15.0) / 1_000_000
        assert abs(cost - expected) < 1e-10

    def test_estimate_cost_unknown_model(self) -> None:
        """Test cost estimation returns 0 for unknown models."""
        provider = AnthropicProvider(api_key="sk-ant-test")

        cost = provider.estimate_cost(1000, 500, model="unknown-model")
        assert cost == 0.0

    @pytest.mark.asyncio
    async def test_multiple_content_blocks(self) -> None:
        """Test handling of multiple content blocks in response."""
        messages = [Message(role="user", content="Hi")]
        mock_resp = {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "claude-3-5-sonnet-20241022",
            "content": [
                {"type": "text", "text": "Part 1. "},
                {"type": "text", "text": "Part 2."},
            ],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 5, "output_tokens": 10},
        }

        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json=mock_resp)
        )
        client = httpx.AsyncClient(transport=transport)

        provider = AnthropicProvider(api_key="sk-ant-test", http_client=client)

        result = await provider.chat(messages)

        assert result.content == "Part 1. Part 2."


# ---------------------------------------------------------------------------
# Message & LLMResponse dataclass tests
# ---------------------------------------------------------------------------


class TestDataModels:
    """Tests for the core data models."""

    def test_message_creation(self) -> None:
        """Test Message dataclass creation."""
        msg = Message(role="user", content="Hello")
        assert msg.role == "user"
        assert msg.content == "Hello"

    def test_message_immutable(self) -> None:
        """Test that Message is frozen (immutable)."""
        msg = Message(role="user", content="Hello")
        with pytest.raises(AttributeError):
            msg.content = "Changed"  # type: ignore[misc]

    def test_llm_response_creation(self) -> None:
        """Test LLMResponse dataclass creation."""
        resp = LLMResponse(
            content="Hi",
            model="gpt-4o",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            cost_usd=0.001,
        )
        assert resp.content == "Hi"
        assert resp.model == "gpt-4o"
        assert resp.total_tokens == 15
        assert resp.cost_usd == 0.001
        assert resp.finish_reason == "stop"
        assert resp.raw == {}

    def test_llm_response_immutable(self) -> None:
        """Test that LLMResponse is frozen (immutable)."""
        resp = LLMResponse(
            content="Hi",
            model="gpt-4o",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
        )
        with pytest.raises(AttributeError):
            resp.content = "Changed"  # type: ignore[misc]

    def test_llm_provider_error(self) -> None:
        """Test LLMProviderError exception."""
        err = LLMProviderError("openai", "Rate limited", status_code=429)
        assert err.provider == "openai"
        assert err.status_code == 429
        assert "[openai]" in str(err)
        assert "Rate limited" in str(err)
