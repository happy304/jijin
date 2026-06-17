"""LLMProvider Protocol and core data types for the AI layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol


@dataclass(frozen=True, slots=True)
class Message:
    """A single message in a chat conversation."""

    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """Structured response from an LLM provider."""

    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float = 0.0
    finish_reason: str = "stop"
    raw: dict = field(default_factory=dict)


class LLMProvider(Protocol):
    """Protocol that all LLM provider adapters must implement.

    Supports OpenAI-compatible APIs, Anthropic, and other providers.
    Switching providers requires only configuration changes, not code changes.
    """

    name: str

    async def chat(
        self,
        messages: list[Message],
        *,
        model: str,
        temperature: float = 0.1,
        response_format: Literal["text", "json"] = "text",
        json_schema: dict | None = None,
        max_tokens: int = 2000,
    ) -> LLMResponse:
        """Send a chat completion request and return the response.

        Args:
            messages: Conversation messages.
            model: Model identifier (e.g. "gpt-4o", "claude-3-5-sonnet-20241022").
            temperature: Sampling temperature.
            response_format: Whether to request plain text or JSON output.
            json_schema: Optional JSON Schema for structured output validation.
            max_tokens: Maximum tokens in the completion.

        Returns:
            LLMResponse with content and usage metadata.

        Raises:
            LLMProviderError: On network, auth, or API errors.
        """
        ...

    def estimate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        """Estimate the cost in USD for a given token usage.

        Args:
            prompt_tokens: Number of input tokens.
            completion_tokens: Number of output tokens.

        Returns:
            Estimated cost in USD.
        """
        ...


class LLMProviderError(Exception):
    """Base exception for LLM provider errors."""

    def __init__(self, provider: str, message: str, status_code: int | None = None) -> None:
        self.provider = provider
        self.status_code = status_code
        super().__init__(f"[{provider}] {message}")
