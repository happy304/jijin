"""OpenAI-compatible LLM provider adapter.

Works with OpenAI, DeepSeek, Zhipu (智谱), Moonshot (月之暗面), and any other
provider that implements the OpenAI Chat Completions API.
"""

from __future__ import annotations

import json
from typing import Any, Literal

import httpx

from app.ai.provider import LLMProviderError, LLMResponse, Message

# Default pricing per 1M tokens (USD) — can be overridden via constructor
_DEFAULT_PRICING: dict[str, tuple[float, float]] = {
    # (prompt_price_per_1M, completion_price_per_1M)
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4-turbo": (10.00, 30.00),
    "gpt-3.5-turbo": (0.50, 1.50),
    "deepseek-chat": (0.14, 0.28),
    "deepseek-reasoner": (0.55, 2.19),
    "glm-4": (1.00, 1.00),
    "glm-4-flash": (0.01, 0.01),
    "moonshot-v1-8k": (0.85, 0.85),
    "moonshot-v1-32k": (1.70, 1.70),
    "moonshot-v1-128k": (4.25, 4.25),
    "MiMo-V2.5": (0.14, 0.28),
    "mimo-v2.5": (0.14, 0.28),
    "mimo-v2.5-pro": (0.28, 0.56),
    "mimo-v2-pro": (0.28, 0.56),
}


class OpenAICompatProvider:
    """Adapter for any OpenAI-compatible Chat Completions API.

    Supports: OpenAI, DeepSeek, Zhipu (智谱), Moonshot (月之暗面), etc.

    Usage:
        provider = OpenAICompatProvider(
            name="deepseek",
            base_url="https://api.deepseek.com/v1",
            api_key="sk-...",
            default_model="deepseek-chat",
        )
        response = await provider.chat(messages, model="deepseek-chat")
    """

    def __init__(
        self,
        *,
        name: str = "openai",
        base_url: str = "https://api.openai.com/v1",
        api_key: str,
        default_model: str = "gpt-4o-mini",
        pricing: dict[str, tuple[float, float]] | None = None,
        timeout: float = 120.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.name = name
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._default_model = default_model
        self._pricing = pricing or _DEFAULT_PRICING
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
        """Send a chat completion request to the OpenAI-compatible API."""
        model = model or self._default_model

        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        # Handle response format
        if response_format == "json":
            if json_schema:
                # Use structured output if schema provided (OpenAI-style)
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "response", "schema": json_schema},
                }
            else:
                payload["response_format"] = {"type": "json_object"}

        url = f"{self._base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
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
        choice = data["choices"][0]
        usage = data.get("usage", {})

        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)

        content = choice.get("message", {}).get("content", "")
        # 兼容推理模型（如 MiMo、DeepSeek-R1）：
        # 这类模型可能将实际回复放在 reasoning_content 中，content 为空
        if not content:
            reasoning_content = choice.get("message", {}).get("reasoning_content", "")
            if reasoning_content:
                content = reasoning_content
        finish_reason = choice.get("finish_reason", "stop")

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
        prompt_price, completion_price = pricing
        return (prompt_tokens * prompt_price + completion_tokens * completion_price) / 1_000_000
