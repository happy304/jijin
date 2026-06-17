"""LLM provider adapters."""

from __future__ import annotations

from app.ai.providers.anthropic import AnthropicProvider
from app.ai.providers.openai_compat import OpenAICompatProvider


def build_default_providers() -> list:
    """Build default provider configurations from application settings.

    Returns a list of ProviderConfig instances suitable for LLMService.
    Falls back gracefully if API keys are not configured.
    """
    from app.ai.service import ProviderConfig
    from app.core.config import get_settings

    settings = get_settings()
    providers: list[ProviderConfig] = []

    # OpenAI-compatible provider (covers OpenAI, DeepSeek, Zhipu, MiMo, etc.)
    openai_api_key = getattr(settings, "openai_api_key", None) or ""
    if openai_api_key:
        openai_base_url = getattr(settings, "openai_base_url", None) or "https://api.openai.com/v1"
        openai_model = getattr(settings, "openai_model", None) or "gpt-4o-mini"
        # 根据 base_url 推断 provider 名称，便于日志和审计追踪
        if "xiaomimimo" in openai_base_url:
            provider_name = "mimo"
        elif "deepseek" in openai_base_url:
            provider_name = "deepseek"
        elif "zhipuai" in openai_base_url:
            provider_name = "zhipu"
        elif "moonshot" in openai_base_url:
            provider_name = "moonshot"
        else:
            provider_name = "openai"
        providers.append(
            ProviderConfig(
                provider=OpenAICompatProvider(
                    name=provider_name,
                    api_key=openai_api_key,
                    base_url=openai_base_url,
                ),
                model=openai_model,
                priority=1,
            )
        )

    # Anthropic provider
    anthropic_api_key = getattr(settings, "anthropic_api_key", None) or ""
    if anthropic_api_key:
        anthropic_model = getattr(settings, "anthropic_model", None) or "claude-3-5-sonnet-20241022"
        providers.append(
            ProviderConfig(
                provider=AnthropicProvider(
                    api_key=anthropic_api_key,
                ),
                model=anthropic_model,
                priority=2,
            )
        )

    return providers


__all__ = ["AnthropicProvider", "OpenAICompatProvider", "build_default_providers"]
