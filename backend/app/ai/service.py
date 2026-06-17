"""LLMService — 统一 LLM 调用管道.

将预算检查、缓存、provider 选择、调用、Schema 校验、审计、缓存写入、
降级等步骤串联为一个统一的异步管道（需求 11.3, 11.8）。

管道流程
--------
1. 预算检查 — 超限时非关键用例抛 BudgetExhaustedError
2. 缓存查询 — 命中则直接返回
3. Provider 选择 — 按偏好 + 健康状态排序
4. 逐个调用 — 失败后自动降级到下一个 provider
5. Schema 校验 — JSON 响应需通过 json_schema 验证
6. 审计记录 — 每次调用（成功/失败）都落库
7. 缓存写入 — 成功响应写入缓存
8. 降级处理 — 所有 provider 失败时抛 AllProvidersFailedError

Design notes
------------
* 所有组件通过构造函数注入，便于测试。
* 优雅降级：缓存/审计/预算的 Redis/DB 故障不阻塞主流程。
* Schema 校验失败视为 provider 失败，触发降级到下一个 provider。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import jsonschema

from app.ai.audit import LLMAuditLog
from app.ai.budget import LLMBudget
from app.ai.cache import LLMCache, build_cache_key
from app.ai.provider import LLMProvider, LLMProviderError, LLMResponse, Message
from app.core.logging import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class BudgetExhaustedError(Exception):
    """Raised when the LLM budget is exhausted for a non-critical use case."""

    def __init__(self, use_case: str) -> None:
        self.use_case = use_case
        super().__init__(
            f"LLM budget exhausted; non-critical use case '{use_case}' blocked"
        )


class SchemaValidationError(Exception):
    """Raised when LLM response fails JSON Schema validation."""

    def __init__(self, message: str, errors: list[str] | None = None) -> None:
        self.errors = errors or []
        super().__init__(message)


class AllProvidersFailedError(Exception):
    """Raised when all configured LLM providers fail."""

    def __init__(self, use_case: str, errors: list[str]) -> None:
        self.use_case = use_case
        self.errors = errors
        super().__init__(
            f"All LLM providers failed for use case '{use_case}': "
            + "; ".join(errors)
        )


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LLMResult:
    """Result returned by LLMService.call().

    Attributes:
        content: The parsed response content (str or dict depending on schema).
        raw_response: The raw LLMResponse from the provider.
        provider: Name of the provider that served the response.
        model: Model identifier used.
        cached: Whether the result came from cache.
        cost_usd: Estimated cost in USD (0 if cached).
        latency_ms: End-to-end latency in milliseconds.
    """

    content: str | dict[str, Any]
    raw_response: LLMResponse | None = None
    provider: str = ""
    model: str = ""
    cached: bool = False
    cost_usd: float = 0.0
    latency_ms: int = 0


# ---------------------------------------------------------------------------
# Provider configuration
# ---------------------------------------------------------------------------


@dataclass
class ProviderConfig:
    """Configuration for a single LLM provider instance.

    Attributes:
        provider: The LLMProvider adapter instance.
        model: Default model to use with this provider.
        priority: Lower number = higher priority (tried first).
        healthy: Whether this provider is currently considered healthy.
    """

    provider: LLMProvider
    model: str
    priority: int = 0
    healthy: bool = True


# ---------------------------------------------------------------------------
# LLMService
# ---------------------------------------------------------------------------


class LLMService:
    """Unified LLM call pipeline.

    Orchestrates budget → cache → provider selection → call → schema
    validation → audit → cache write → fallback across all configured
    providers.

    Args:
        providers: List of provider configurations, ordered by priority.
        cache: LLMCache instance for response caching.
        budget: LLMBudget instance for spend control.
        audit: LLMAuditLog instance for call logging.
        default_cache_ttl: Default cache TTL in seconds (7 days).
    """

    def __init__(
        self,
        *,
        providers: list[ProviderConfig],
        cache: LLMCache | None = None,
        budget: LLMBudget | None = None,
        audit: LLMAuditLog | None = None,
        default_cache_ttl: int = 7 * 86400,
    ) -> None:
        self._providers = providers
        self._cache = cache
        self._budget = budget
        self._audit = audit
        self._default_cache_ttl = default_cache_ttl

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def call(
        self,
        use_case: str,
        prompt: str,
        *,
        system_prompt: str | None = None,
        schema: dict[str, Any] | None = None,
        cache_ttl: int | None = None,
        preferred_providers: list[str] | None = None,
        temperature: float = 0.1,
        max_tokens: int = 2000,
    ) -> LLMResult:
        """Execute the unified LLM call pipeline.

        Args:
            use_case: Identifier for the calling use case (e.g.
                "announcement_parse", "nl_query").
            prompt: The user prompt text.
            system_prompt: Optional system prompt prepended to messages.
            schema: Optional JSON Schema dict for structured output.
                When provided, the response is validated against this
                schema and parsed as JSON.
            cache_ttl: Cache TTL in seconds. Defaults to instance default
                (7 days). Set to 0 to skip caching.
            preferred_providers: Optional list of provider names to prefer.
                Providers not in this list are still used as fallbacks.
            temperature: Sampling temperature for the LLM.
            max_tokens: Maximum tokens in the completion.

        Returns:
            LLMResult with the parsed content and metadata.

        Raises:
            BudgetExhaustedError: If budget is exhausted for non-critical
                use cases.
            AllProvidersFailedError: If all providers fail.
        """
        start_time = time.perf_counter()
        effective_ttl = cache_ttl if cache_ttl is not None else self._default_cache_ttl

        # ----------------------------------------------------------
        # Step 1: Budget check
        # ----------------------------------------------------------
        if self._budget is not None:
            exhausted = await self._budget.is_exhausted(use_case)
            if exhausted:
                log.warning(
                    "llm_service.budget_exhausted",
                    use_case=use_case,
                )
                raise BudgetExhaustedError(use_case)

        # ----------------------------------------------------------
        # Step 2: Cache lookup
        # ----------------------------------------------------------
        if self._cache is not None and effective_ttl > 0:
            cached_value = await self._cache.get(use_case, prompt, schema)
            if cached_value is not None:
                log.info("llm_service.cache_hit", use_case=use_case)
                elapsed_ms = int((time.perf_counter() - start_time) * 1000)
                # Parse cached JSON if schema was provided
                content: str | dict[str, Any] = cached_value
                if schema is not None:
                    try:
                        content = json.loads(cached_value)
                    except (json.JSONDecodeError, TypeError):
                        content = cached_value
                return LLMResult(
                    content=content,
                    cached=True,
                    latency_ms=elapsed_ms,
                )

        # ----------------------------------------------------------
        # Step 3: Provider selection
        # ----------------------------------------------------------
        ordered_providers = self._select_providers(preferred_providers)

        if not ordered_providers:
            raise AllProvidersFailedError(use_case, ["No providers configured"])

        # ----------------------------------------------------------
        # Step 4: Call providers with fallback
        # ----------------------------------------------------------
        messages = self._build_messages(system_prompt, prompt)
        response_format = "json" if schema else "text"
        errors: list[str] = []

        for provider_cfg in ordered_providers:
            provider = provider_cfg.provider
            model = provider_cfg.model
            call_start = time.perf_counter()
            latency_ms = 0

            try:
                # 4a. Call the provider
                response = await provider.chat(
                    messages,
                    model=model,
                    temperature=temperature,
                    response_format=response_format,
                    json_schema=schema,
                    max_tokens=max_tokens,
                )
                latency_ms = int((time.perf_counter() - call_start) * 1000)

                # --------------------------------------------------
                # Step 5: Schema validation
                # --------------------------------------------------
                parsed_content: str | dict[str, Any]
                if schema is not None:
                    parsed_content = self._validate_json(response.content, schema)
                else:
                    parsed_content = response.content

                # --------------------------------------------------
                # Step 6: Audit (success)
                # --------------------------------------------------
                prompt_hash = build_cache_key(use_case, prompt, schema).split(":")[-1]
                await self._record_audit(
                    provider=provider.name,
                    model=model,
                    use_case=use_case,
                    prompt_hash=prompt_hash,
                    prompt_text=prompt,
                    response_text=response.content,
                    prompt_tokens=response.prompt_tokens,
                    completion_tokens=response.completion_tokens,
                    cost_usd=response.cost_usd,
                    latency_ms=latency_ms,
                    success=True,
                )

                # --------------------------------------------------
                # Step 7: Cache write
                # --------------------------------------------------
                if self._cache is not None and effective_ttl > 0:
                    cache_value = (
                        json.dumps(parsed_content, ensure_ascii=False)
                        if isinstance(parsed_content, dict)
                        else response.content
                    )
                    await self._cache.set(
                        use_case,
                        prompt,
                        schema,
                        response=cache_value,
                        ttl=effective_ttl,
                    )

                # --------------------------------------------------
                # Step 8: Budget consume
                # --------------------------------------------------
                if self._budget is not None and response.cost_usd > 0:
                    await self._budget.consume(response.cost_usd)

                # Success — return result
                total_latency_ms = int((time.perf_counter() - start_time) * 1000)
                return LLMResult(
                    content=parsed_content,
                    raw_response=response,
                    provider=provider.name,
                    model=model,
                    cached=False,
                    cost_usd=response.cost_usd,
                    latency_ms=total_latency_ms,
                )

            except (LLMProviderError, SchemaValidationError) as exc:
                # Provider or validation failure — record and try next
                latency_ms = int((time.perf_counter() - call_start) * 1000)
                error_msg = str(exc)
                errors.append(f"{provider.name}/{model}: {error_msg}")

                log.warning(
                    "llm_service.provider_failed",
                    provider=provider.name,
                    model=model,
                    use_case=use_case,
                    error=error_msg,
                )

                # Audit the failure
                await self._record_audit(
                    provider=provider.name,
                    model=model,
                    use_case=use_case,
                    prompt_hash=build_cache_key(use_case, prompt, schema).split(":")[-1],
                    prompt_text=prompt,
                    latency_ms=latency_ms,
                    success=False,
                    error_msg=error_msg,
                )

                continue

        # ----------------------------------------------------------
        # All providers failed
        # ----------------------------------------------------------
        raise AllProvidersFailedError(use_case, errors)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _select_providers(
        self, preferred: list[str] | None
    ) -> list[ProviderConfig]:
        """Select and order providers by preference and health.

        Preferred providers come first (in the order specified), followed
        by remaining healthy providers sorted by priority.
        """
        healthy = [p for p in self._providers if p.healthy]

        if not preferred:
            return sorted(healthy, key=lambda p: p.priority)

        # Split into preferred (in order) and others
        preferred_set = set(preferred)
        preferred_list: list[ProviderConfig] = []
        others: list[ProviderConfig] = []

        for cfg in healthy:
            if cfg.provider.name in preferred_set:
                preferred_list.append(cfg)
            else:
                others.append(cfg)

        # Sort preferred by the order in the input list
        preferred_list.sort(
            key=lambda p: preferred.index(p.provider.name)
            if p.provider.name in preferred
            else 999
        )
        others.sort(key=lambda p: p.priority)

        return preferred_list + others

    @staticmethod
    def _build_messages(
        system_prompt: str | None, prompt: str
    ) -> list[Message]:
        """Build the message list for the provider."""
        messages: list[Message] = []
        if system_prompt:
            messages.append(Message(role="system", content=system_prompt))
        messages.append(Message(role="user", content=prompt))
        return messages

    @staticmethod
    def _validate_json(content: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Parse and validate JSON content against a schema.

        Args:
            content: Raw string content from the LLM.
            schema: JSON Schema to validate against.

        Returns:
            Parsed dict if validation passes.

        Raises:
            SchemaValidationError: If parsing or validation fails.
        """
        # 清理 LLM 返回的 markdown 代码块包裹（如 ```json ... ```）
        cleaned = content.strip()
        if cleaned.startswith("```"):
            # 移除开头的 ```json 或 ``` 行
            first_newline = cleaned.find("\n")
            if first_newline != -1:
                cleaned = cleaned[first_newline + 1:]
            # 移除结尾的 ```
            if cleaned.rstrip().endswith("```"):
                cleaned = cleaned.rstrip()[:-3].rstrip()

        try:
            parsed = json.loads(cleaned)
        except (json.JSONDecodeError, TypeError) as exc:
            raise SchemaValidationError(
                f"Invalid JSON from LLM: {exc}",
                errors=[str(exc)],
            ) from exc

        try:
            jsonschema.validate(instance=parsed, schema=schema)
        except jsonschema.ValidationError as exc:
            raise SchemaValidationError(
                f"Schema validation failed: {exc.message}",
                errors=[exc.message],
            ) from exc

        return parsed

    async def _record_audit(self, **kwargs: Any) -> None:
        """Record an audit entry, swallowing errors."""
        if self._audit is None:
            return
        try:
            await self._audit.record(**kwargs)
        except Exception as exc:
            log.warning("llm_service.audit_error", error=str(exc))


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

__all__ = [
    "AllProvidersFailedError",
    "BudgetExhaustedError",
    "LLMResult",
    "LLMService",
    "ProviderConfig",
    "SchemaValidationError",
]
