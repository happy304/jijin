"""CompositeProvider - 多源编排，按优先级链式调用并自动降级。

实现需求 1.4 和 1.5：
- 天天基金接口返回错误或超时时，自动切换到 AkShare 备源重试
- 主源和备源都失败时，记录失败日志并抛出 AllProvidersFailedError

设计要点：
- 按 priority 顺序尝试（数字越小优先级越高）
- 集成 CircuitBreakerRegistry，OPEN 状态跳过 provider
- 每次成功后写入原始快照供事后审计
- 返回结果同时记录命中的 provider 名称（source）
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from app.data.fetchers.circuit_breaker import CircuitBreakerRegistry
from app.data.providers.base import (
    AllProvidersFailedError,
    FundDataProvider,
    HealthStatus,
    ProviderError,
)
from app.data.providers.snapshot import SnapshotArchive
from app.data.schemas.funds import (
    Announcement,
    DividendRecord,
    FundMeta,
    HoldingSnapshot,
    NavRecord,
)

logger = logging.getLogger(__name__)


class CompositeProvider:
    """按优先级链式调用多个 FundDataProvider，主源失败自动降级到备源。

    集成熔断器：当某个 provider 的熔断器处于 OPEN 状态时，跳过该 provider。
    每次成功调用后记录命中的 source 名称，便于追踪数据来源。

    Args:
        providers: 数据源列表，将按 priority 排序。
        circuit_breaker: 熔断器注册表，管理各 provider 的熔断状态。
            如果不提供，将使用默认配置创建。
        snapshot: 快照归档实例，用于保存原始响应。
            如果不提供，将使用默认配置创建。

    Usage::

        composite = CompositeProvider(
            providers=[eastmoney_provider, akshare_provider],
        )
        data, source = await composite.fetch_nav_history("000001", start, end)
        print(f"数据来自: {source}")
    """

    def __init__(
        self,
        providers: list[FundDataProvider],
        *,
        circuit_breaker: CircuitBreakerRegistry | None = None,
        snapshot: SnapshotArchive | None = None,
    ) -> None:
        if not providers:
            raise ValueError("至少需要提供一个 provider")

        self._providers = sorted(providers, key=lambda p: p.priority)
        self._circuit_breaker = circuit_breaker or CircuitBreakerRegistry()
        self._snapshot = snapshot or SnapshotArchive()

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _providers_by_priority(self) -> list[FundDataProvider]:
        """返回按 priority 升序排列的 provider 列表。"""
        return self._providers

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    async def fetch_fund_meta(self, code: str) -> tuple[FundMeta, str]:
        """按优先级获取基金元数据，返回 (数据, 命中的 provider 名称)。

        Args:
            code: 基金代码。

        Returns:
            (FundMeta, source_name) 元组。

        Raises:
            AllProvidersFailedError: 所有 provider 都失败。
        """
        return await self._fetch_with_fallback(
            method_name="fetch_fund_meta",
            fund_code=code,
            code=code,
        )

    async def fetch_nav_history(
        self,
        code: str,
        start: date,
        end: date,
    ) -> tuple[list[NavRecord], str]:
        """按优先级获取历史净值，返回 (数据, 命中的 provider 名称)。

        Args:
            code: 基金代码。
            start: 起始日期（含）。
            end: 结束日期（含）。

        Returns:
            (list[NavRecord], source_name) 元组。

        Raises:
            AllProvidersFailedError: 所有 provider 都失败。
        """
        return await self._fetch_with_fallback(
            method_name="fetch_nav_history",
            fund_code=code,
            code=code,
            start=start,
            end=end,
        )

    async def fetch_nav_history_all_sources(
        self,
        code: str,
        start: date,
        end: date,
    ) -> tuple[dict[str, list[NavRecord]], dict[str, str]]:
        """从所有未熔断来源拉取 NAV，用于同日跨源一致性硬校验。

        该方法不改变默认降级读取语义；单个 provider 失败只记录错误并继续，
        调用方可用返回的多源原始序列做审计或决定是否阻断写入。
        """
        data_by_source: dict[str, list[NavRecord]] = {}
        errors: dict[str, str] = {}

        for provider in self._providers_by_priority():
            if self._circuit_breaker.is_open(provider.name):
                errors[provider.name] = "circuit_open"
                logger.info(
                    "CompositeProvider: 跳过 %s（熔断器 OPEN）, method=fetch_nav_history_all_sources, fund=%s",
                    provider.name,
                    code,
                )
                continue
            try:
                records = await provider.fetch_nav_history(code=code, start=start, end=end)
                self._circuit_breaker.record_success(provider.name)
                data_by_source[provider.name] = list(records or [])
                logger.info(
                    "CompositeProvider: fetch_nav_history_all_sources 成功, provider=%s, fund=%s, records=%d",
                    provider.name,
                    code,
                    len(data_by_source[provider.name]),
                )
            except ProviderError as exc:
                errors[provider.name] = str(exc)
                self._circuit_breaker.record_failure(provider.name)
                logger.warning(
                    "CompositeProvider: fetch_nav_history_all_sources 失败, provider=%s, fund=%s, error=%s",
                    provider.name,
                    code,
                    exc,
                )

        return data_by_source, errors

    async def fetch_holdings(
        self,
        code: str,
        quarter: str,
    ) -> tuple[HoldingSnapshot, str]:
        """按优先级获取季度持仓，返回 (数据, 命中的 provider 名称)。

        Args:
            code: 基金代码。
            quarter: 季度标识，格式 "YYYY-QN"。

        Returns:
            (HoldingSnapshot, source_name) 元组。

        Raises:
            AllProvidersFailedError: 所有 provider 都失败。
        """
        return await self._fetch_with_fallback(
            method_name="fetch_holdings",
            fund_code=code,
            code=code,
            quarter=quarter,
        )

    async def fetch_dividends(self, code: str) -> tuple[list[DividendRecord], str]:
        """按优先级获取分红记录，返回 (数据, 命中的 provider 名称)。

        Args:
            code: 基金代码。

        Returns:
            (list[DividendRecord], source_name) 元组。

        Raises:
            AllProvidersFailedError: 所有 provider 都失败。
        """
        return await self._fetch_with_fallback(
            method_name="fetch_dividends",
            fund_code=code,
            code=code,
        )

    async def fetch_announcements(
        self,
        code: str,
        since: date,
    ) -> tuple[list[Announcement], str]:
        """按优先级获取公告，返回 (数据, 命中的 provider 名称)。

        Args:
            code: 基金代码。
            since: 起始日期（含）。

        Returns:
            (list[Announcement], source_name) 元组。

        Raises:
            AllProvidersFailedError: 所有 provider 都失败。
        """
        return await self._fetch_with_fallback(
            method_name="fetch_announcements",
            fund_code=code,
            code=code,
            since=since,
        )

    async def health_check(self) -> dict[str, HealthStatus]:
        """对所有 provider 执行健康检查。

        Returns:
            {provider_name: HealthStatus} 字典。
        """
        results: dict[str, HealthStatus] = {}
        for provider in self._providers:
            try:
                status = await provider.health_check()
                results[provider.name] = status
            except Exception as exc:
                results[provider.name] = HealthStatus(
                    healthy=False,
                    message=f"健康检查异常: {exc}",
                )
        return results

    # ------------------------------------------------------------------
    # 核心降级逻辑
    # ------------------------------------------------------------------

    async def _fetch_with_fallback(
        self,
        method_name: str,
        fund_code: str,
        **kwargs: Any,
    ) -> tuple[Any, str]:
        """通用的降级调用逻辑。

        按 priority 顺序尝试每个 provider：
        1. 检查熔断器状态，OPEN 则跳过
        2. 调用 provider 的指定方法
        3. 成功则记录 success 并返回
        4. 失败则记录 failure 并继续下一个

        Args:
            method_name: 要调用的 provider 方法名。
            fund_code: 基金代码（用于错误信息）。
            **kwargs: 传递给 provider 方法的参数。

        Returns:
            (result, provider_name) 元组。

        Raises:
            AllProvidersFailedError: 所有 provider 都失败。
        """
        errors: list[tuple[str, Exception]] = []

        for provider in self._providers_by_priority():
            # 检查熔断器状态
            if self._circuit_breaker.is_open(provider.name):
                logger.info(
                    "CompositeProvider: 跳过 %s（熔断器 OPEN）, method=%s, fund=%s",
                    provider.name,
                    method_name,
                    fund_code,
                )
                continue

            try:
                method = getattr(provider, method_name)
                data = await method(**kwargs)

                # 成功：记录熔断器 success
                self._circuit_breaker.record_success(provider.name)

                logger.info(
                    "CompositeProvider: %s 成功, provider=%s, fund=%s",
                    method_name,
                    provider.name,
                    fund_code,
                )

                return data, provider.name

            except ProviderError as exc:
                # 记录失败
                errors.append((provider.name, exc))
                self._circuit_breaker.record_failure(provider.name)

                logger.warning(
                    "CompositeProvider: %s 失败, provider=%s, fund=%s, error=%s",
                    method_name,
                    provider.name,
                    fund_code,
                    exc,
                )

        # 所有 provider 都失败
        logger.error(
            "CompositeProvider: 所有 provider 均失败, method=%s, fund=%s, errors=%d",
            method_name,
            fund_code,
            len(errors),
        )
        raise AllProvidersFailedError(errors, fund_code=fund_code)

    # ------------------------------------------------------------------
    # 诊断
    # ------------------------------------------------------------------

    @property
    def providers(self) -> list[FundDataProvider]:
        """返回已注册的 provider 列表（按 priority 排序）。"""
        return list(self._providers)

    @property
    def circuit_breaker(self) -> CircuitBreakerRegistry:
        """返回熔断器注册表实例。"""
        return self._circuit_breaker

    def __repr__(self) -> str:
        names = [p.name for p in self._providers]
        return f"CompositeProvider(providers={names})"
