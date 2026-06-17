"""Anthropic Claude LLM provider adapter.

Handles Anthropic's Messages API which differs from the OpenAI format:
- Uses a separate `system` parameter instead of a system message in the array
- Different response structure
- Different pricing model
"""

from __future__ import annotations

import json
from typing import Any, Literal

import httpx

from app.ai.provider import LLMProviderError, LLMResponse, Message

# Pricing per 1M tokens (USD)
_ANTHROPIC_PRICING: dict[str, tuple[float, float]] = {
    # (input_price_per_1M, output_price_per_1M)
    "claude-sonnet-4-20250514": (3.00, 15.00),
    "claude-3-5-sonnet-20241022": (3.00, 15.00),
    "claude-3-5-haiku-20241022": (0.80, 4.00),
    "claude-3-opus-20240229": (15.00, 75.00),
    "claude-3-haiku-20240307": (0.25, 1.25),
}

_ANTHROPIC_API_VERSION = "2023-06-01"


class AnthropicProvider:
    """Adapter for Anthropic's Messages API (Claude models).

    Usage:
        provider = AnthropicProvider(
            api_key="sk-ant-...",
            default_model="claude-3-5-sonnet-20241022",
        )
        response = await provider.chat(messages, model="claude-3-5-sonnet-20241022")
    """

    def __init__(
        self,
        *,
        api_key: str,
        default_model: str = "claude-3-5-sonnet-20241022",
        base_url: str = "https://api.anthropic.com",
        pricing: dict[str, tuple[float, float]] | None = None,
        timeout: float = 60.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.name = "anthropic"
        self._api_key = api_key
        self._default_model = default_model
        self._base_url = base_url.rstrip("/")
        self._pricing = pricing or _ANTHROPIC_PRICING
        self._timeout = timeout
        self._client = http_client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = http_client is None

    async def close(self) -> None:
        """Close the underlying HTTP client if we own it."""
        if self._owns_client:
            await self._client.aclose()

    async def chat(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        temperature: float = 0.1,
        response_format: Literal["text", "json"] = "text",
        json_schema: dict | None = None,
        max_tokens: int = 2000,
    ) -> LLMResponse:
        """Send a chat completion request to Anthropic's Messages API."""
        model = model or self._default_model

        # Anthropic separates system message from the conversation
        system_text: str | None = None
        conversation: list[dict[str, str]] = []

        for msg in messages:
            if msg.role == "system":
                # Anthropic only supports one system prompt; concatenate if multiple
                if system_text is None:
                    system_text = msg.content
                else:
                    system_text += "\n\n" + msg.content
            else:
                conversation.append({"role": msg.role, "content": msg.content})

        # Anthropic requires at least one user message
        if not conversation:
            raise LLMProviderError(
                self.name, "At least one user or assistant message is required"
            )

        payload: dict[str, Any] = {
            "model": model,
            "messages": conversation,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        if system_text:
            payload["system"] = system_text

        # Handle JSON response format via system prompt instruction
        if response_format == "json":
            json_instruction = "You must respond with valid JSON only. No markdown, no explanation."
            if json_schema:
                json_instruction += f"\n\nYour response must conform to this JSON Schema:\n{json.dumps(json_schema, ensure_ascii=False)}"
            if system_text:
                payload["system"] = system_text + "\n\n" + json_instruction
            else:
                payload["system"] = json_instruction

        url = f"{self._base_url}/v1/messages"
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": _ANTHROPIC_API_VERSION,
            "Content-Type": "application/json",
        }

        try:
            resp = await self._client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise LLMProviderError(self.name, f"HTTP error: {exc}") from exc

        if resp.status_code != 200:
            error_body = resp.text
            try:
                error_data = resp.json()
                error_msg = error_data.get("error", {}).get("message", error_body)
            except (json.JSONDecodeError, KeyError):
                error_msg = error_body
            raise LLMProviderError(
                self.name,
                f"API error {resp.status_code}: {error_msg}",
                status_code=resp.status_code,
            )

        data = resp.json()

        # Extract content from Anthropic's response format
        content_blocks = data.get("content", [])
        content = ""
        for block in content_blocks:
            if block.get("type") == "text":
                content += block.get("text", "")

        usage = data.get("usage", {})
        prompt_tokens = usage.get("input_tokens", 0)
        completion_tokens = usage.get("output_tokens", 0)
        total_tokens = prompt_tokens + completion_tokens

        finish_reason = data.get("stop_reason", "end_turn")

        cost = self.estimate_cost(prompt_tokens, completion_tokens, model=model)

        return LLMResponse(
            content=content,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=cost,
            finish_reason=finish_reason,
            raw=data,
        )

    def estimate_cost(
        self,
        prompt_tokens: int,
        completion_tokens: int,
        *,
        model: str | None = None,
    ) -> float:
        """Estimate cost in USD based on token counts and model pricing."""
        model = model or self._default_model
        pricing = self._pricing.get(model)
        if pricing is None:
            return 0.0
        input_price, output_price = pricing
        return (prompt_tokens * input_price + completion_tokens * output_price) / 1_000_000
