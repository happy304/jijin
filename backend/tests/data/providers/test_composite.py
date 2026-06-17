"""Unit tests for app.data.providers.composite.CompositeProvider.

测试覆盖：
- 主源成功时直接返回结果和 source 名称
- 主源失败时自动降级到备源
- 全部失败时抛出 AllProvidersFailedError
- 熔断器 OPEN 状态跳过 provider
- 按 priority 排序调用
- 成功时记录 circuit_breaker success
- 失败时记录 circuit_breaker failure
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.data.fetchers.circuit_breaker import CircuitBreakerRegistry, CircuitState
from app.data.providers.base import (
    AllProvidersFailedError,
    HealthStatus,
    ProviderError,
    ProviderNotFoundError,
    ProviderTimeoutError,
)
from app.data.providers.composite import CompositeProvider
from app.data.schemas.funds import (
    Announcement,
    DividendRecord,
    FundMeta,
    FundStatus,
    FundType,
    HoldingPosition,
    HoldingSnapshot,
    NavRecord,
    NavStatus,
)


# ---------------------------------------------------------------------------
# Fixtures: Mock Providers
# ---------------------------------------------------------------------------


def _make_mock_provider(name: str, priority: int) -> MagicMock:
    """创建一个 mock provider，满足 FundDataProvider Protocol。"""
    provider = MagicMock()
    provider.name = name
    provider.priority = priority
    # 默认所有方法为 AsyncMock
    provider.fetch_fund_meta = AsyncMock()
    provider.fetch_nav_history = AsyncMock()
    provider.fetch_holdings = AsyncMock()
    provider.fetch_dividends = AsyncMock()
    provider.fetch_announcements = AsyncMock()
    provider.health_check = AsyncMock()
    return provider


def _make_nav_records(source: str) -> list[NavRecord]:
    """创建测试用 NavRecord 列表。"""
    return [
        NavRecord(
            fund_code="000001",
            trade_date=date(2024, 1, 2),
            unit_nav=Decimal("1.5000"),
            accum_nav=Decimal("3.2000"),
            adj_nav=None,
            daily_return=Decimal("0.0100"),
            status=NavStatus.NORMAL,
            source=source,
        ),
        NavRecord(
            fund_code="000001",
            trade_date=date(2024, 1, 3),
            unit_nav=Decimal("1.5200"),
            accum_nav=Decimal("3.2200"),
            adj_nav=None,
            daily_return=Decimal("0.0133"),
            status=NavStatus.NORMAL,
            source=source,
        ),
    ]


@pytest.fixture
def primary_provider() -> MagicMock:
    """主源 provider（priority=1）。"""
    return _make_mock_provider("eastmoney", priority=1)


@pytest.fixture
def secondary_provider() -> MagicMock:
    """备源 provider（priority=2）。"""
    return _make_mock_provider("akshare", priority=2)


@pytest.fixture
def circuit_breaker() -> CircuitBreakerRegistry:
    """熔断器注册表。"""
    return CircuitBreakerRegistry(
        default_failure_threshold=3,
        default_recovery_timeout=60.0,
    )


# ---------------------------------------------------------------------------
# 测试：初始化
# ---------------------------------------------------------------------------


class TestCompositeProviderInit:
    def test_empty_providers_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="至少需要提供一个 provider"):
            CompositeProvider(providers=[])

    def test_providers_sorted_by_priority(
        self, primary_provider: MagicMock, secondary_provider: MagicMock
    ) -> None:
        # 故意倒序传入
        composite = CompositeProvider(
            providers=[secondary_provider, primary_provider]
        )
        assert composite.providers[0].name == "eastmoney"
        assert composite.providers[1].name == "akshare"

    def test_repr(
        self, primary_provider: MagicMock, secondary_provider: MagicMock
    ) -> None:
        composite = CompositeProvider(
            providers=[primary_provider, secondary_provider]
        )
        assert "eastmoney" in repr(composite)
        assert "akshare" in repr(composite)


# ---------------------------------------------------------------------------
# 测试：主源成功
# ---------------------------------------------------------------------------


class TestPrimarySuccess:
    @pytest.mark.asyncio
    async def test_fetch_nav_history_returns_data_and_source(
        self,
        primary_provider: MagicMock,
        secondary_provider: MagicMock,
    ) -> None:
        """主源成功时返回数据和 source 名称。"""
        expected_data = _make_nav_records("eastmoney")
        primary_provider.fetch_nav_history.return_value = expected_data

        composite = CompositeProvider(
            providers=[primary_provider, secondary_provider]
        )
        data, source = await composite.fetch_nav_history(
            "000001", date(2024, 1, 1), date(2024, 1, 31)
        )

        assert data == expected_data
        assert source == "eastmoney"
        # 备源不应被调用
        secondary_provider.fetch_nav_history.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_fund_meta_returns_data_and_source(
        self,
        primary_provider: MagicMock,
        secondary_provider: MagicMock,
    ) -> None:
        """主源成功时 fetch_fund_meta 返回数据和 source。"""
        from datetime import datetime, timezone

        expected = FundMeta(
            code="000001",
            name="华夏成长",
            fund_type=FundType.MIXED,
            sub_type="混合型",
            inception_date=date(2001, 12, 18),
            management_fee=Decimal("0.015"),
            custodian_fee=Decimal("0.0025"),
            status=FundStatus.ACTIVE,
            is_purchasable=True,
            purchase_limit=None,
            source="eastmoney",
            updated_at=datetime.now(tz=timezone.utc),
        )
        primary_provider.fetch_fund_meta.return_value = expected

        composite = CompositeProvider(
            providers=[primary_provider, secondary_provider]
        )
        data, source = await composite.fetch_fund_meta("000001")

        assert data == expected
        assert source == "eastmoney"

    @pytest.mark.asyncio
    async def test_success_records_circuit_breaker_success(
        self,
        primary_provider: MagicMock,
        secondary_provider: MagicMock,
        circuit_breaker: CircuitBreakerRegistry,
    ) -> None:
        """成功调用后熔断器记录 success。"""
        primary_provider.fetch_nav_history.return_value = []

        composite = CompositeProvider(
            providers=[primary_provider, secondary_provider],
            circuit_breaker=circuit_breaker,
        )
        await composite.fetch_nav_history("000001", date(2024, 1, 1), date(2024, 1, 31))

        # 熔断器应处于 CLOSED 状态
        assert circuit_breaker.get_state("eastmoney") == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_fetch_nav_history_all_sources_collects_available_sources(
        self,
        primary_provider: MagicMock,
        secondary_provider: MagicMock,
    ) -> None:
        """多源 NAV 对照入口应收集所有可用来源。"""
        primary_provider.fetch_nav_history.return_value = _make_nav_records("eastmoney")
        secondary_provider.fetch_nav_history.return_value = _make_nav_records("akshare")
        composite = CompositeProvider(providers=[primary_provider, secondary_provider])

        data_by_source, errors = await composite.fetch_nav_history_all_sources(
            "000001", date(2024, 1, 1), date(2024, 1, 31)
        )

        assert set(data_by_source) == {"eastmoney", "akshare"}
        assert errors == {}
        assert len(data_by_source["eastmoney"]) == 2
        assert len(data_by_source["akshare"]) == 2

    @pytest.mark.asyncio
    async def test_fetch_nav_history_all_sources_keeps_errors_per_source(
        self,
        primary_provider: MagicMock,
        secondary_provider: MagicMock,
    ) -> None:
        """单个来源失败时，多源对照应继续收集其他来源并返回错误。"""
        primary_provider.fetch_nav_history.side_effect = ProviderError(
            "解析失败", provider_name="eastmoney", fund_code="000001"
        )
        secondary_provider.fetch_nav_history.return_value = _make_nav_records("akshare")
        composite = CompositeProvider(providers=[primary_provider, secondary_provider])

        data_by_source, errors = await composite.fetch_nav_history_all_sources(
            "000001", date(2024, 1, 1), date(2024, 1, 31)
        )

        assert set(data_by_source) == {"akshare"}
        assert "eastmoney" in errors
        assert "解析失败" in errors["eastmoney"]


# ---------------------------------------------------------------------------
# 测试：主源失败自动降级
# ---------------------------------------------------------------------------


class TestFallbackOnPrimaryFailure:
    @pytest.mark.asyncio
    async def test_primary_timeout_falls_back_to_secondary(
        self,
        primary_provider: MagicMock,
        secondary_provider: MagicMock,
    ) -> None:
        """主源超时时自动降级到备源。"""
        primary_provider.fetch_nav_history.side_effect = ProviderTimeoutError(
            "请求超时", provider_name="eastmoney", fund_code="000001"
        )
        expected_data = _make_nav_records("akshare")
        secondary_provider.fetch_nav_history.return_value = expected_data

        composite = CompositeProvider(
            providers=[primary_provider, secondary_provider]
        )
        data, source = await composite.fetch_nav_history(
            "000001", date(2024, 1, 1), date(2024, 1, 31)
        )

        assert data == expected_data
        assert source == "akshare"

    @pytest.mark.asyncio
    async def test_primary_error_falls_back_to_secondary(
        self,
        primary_provider: MagicMock,
        secondary_provider: MagicMock,
    ) -> None:
        """主源一般错误时自动降级到备源。"""
        primary_provider.fetch_nav_history.side_effect = ProviderError(
            "解析失败", provider_name="eastmoney", fund_code="000001"
        )
        expected_data = _make_nav_records("akshare")
        secondary_provider.fetch_nav_history.return_value = expected_data

        composite = CompositeProvider(
            providers=[primary_provider, secondary_provider]
        )
        data, source = await composite.fetch_nav_history(
            "000001", date(2024, 1, 1), date(2024, 1, 31)
        )

        assert data == expected_data
        assert source == "akshare"

    @pytest.mark.asyncio
    async def test_primary_not_found_falls_back_to_secondary(
        self,
        primary_provider: MagicMock,
        secondary_provider: MagicMock,
    ) -> None:
        """主源 NotFound 时也降级到备源（备源可能有数据）。"""
        primary_provider.fetch_dividends.side_effect = ProviderNotFoundError(
            "基金不存在", provider_name="eastmoney", fund_code="999999"
        )
        expected_data = [
            DividendRecord(
                fund_code="999999",
                ex_date=date(2024, 6, 15),
                record_date=date(2024, 6, 14),
                pay_date=date(2024, 6, 17),
                dividend_per_share=Decimal("0.05"),
                split_ratio=Decimal("1"),
            )
        ]
        secondary_provider.fetch_dividends.return_value = expected_data

        composite = CompositeProvider(
            providers=[primary_provider, secondary_provider]
        )
        data, source = await composite.fetch_dividends("999999")

        assert data == expected_data
        assert source == "akshare"

    @pytest.mark.asyncio
    async def test_failure_records_circuit_breaker_failure(
        self,
        primary_provider: MagicMock,
        secondary_provider: MagicMock,
        circuit_breaker: CircuitBreakerRegistry,
    ) -> None:
        """失败调用后熔断器记录 failure。"""
        primary_provider.fetch_nav_history.side_effect = ProviderError(
            "error", provider_name="eastmoney"
        )
        secondary_provider.fetch_nav_history.return_value = []

        composite = CompositeProvider(
            providers=[primary_provider, secondary_provider],
            circuit_breaker=circuit_breaker,
        )
        await composite.fetch_nav_history("000001", date(2024, 1, 1), date(2024, 1, 31))

        # eastmoney 应有 1 次失败记录
        breaker = circuit_breaker._get("eastmoney")
        assert breaker.consecutive_failures == 1


# ---------------------------------------------------------------------------
# 测试：全部失败抛 AllProvidersFailedError
# ---------------------------------------------------------------------------


class TestAllProvidersFailed:
    @pytest.mark.asyncio
    async def test_all_fail_raises_all_providers_failed_error(
        self,
        primary_provider: MagicMock,
        secondary_provider: MagicMock,
    ) -> None:
        """所有 provider 都失败时抛出 AllProvidersFailedError。"""
        primary_provider.fetch_nav_history.side_effect = ProviderTimeoutError(
            "超时", provider_name="eastmoney", fund_code="000001"
        )
        secondary_provider.fetch_nav_history.side_effect = ProviderError(
            "解析失败", provider_name="akshare", fund_code="000001"
        )

        composite = CompositeProvider(
            providers=[primary_provider, secondary_provider]
        )

        with pytest.raises(AllProvidersFailedError) as exc_info:
            await composite.fetch_nav_history(
                "000001", date(2024, 1, 1), date(2024, 1, 31)
            )

        err = exc_info.value
        assert err.fund_code == "000001"
        assert len(err.errors) == 2
        assert err.provider_names == ["eastmoney", "akshare"]

    @pytest.mark.asyncio
    async def test_all_fail_with_single_provider(
        self,
        primary_provider: MagicMock,
    ) -> None:
        """只有一个 provider 且失败时也抛出 AllProvidersFailedError。"""
        primary_provider.fetch_fund_meta.side_effect = ProviderError(
            "网络错误", provider_name="eastmoney", fund_code="000001"
        )

        composite = CompositeProvider(providers=[primary_provider])

        with pytest.raises(AllProvidersFailedError) as exc_info:
            await composite.fetch_fund_meta("000001")

        err = exc_info.value
        assert len(err.errors) == 1
        assert err.provider_names == ["eastmoney"]


# ---------------------------------------------------------------------------
# 测试：熔断器 OPEN 状态跳过 provider
# ---------------------------------------------------------------------------


class TestCircuitBreakerIntegration:
    @pytest.mark.asyncio
    async def test_open_circuit_skips_provider(
        self,
        primary_provider: MagicMock,
        secondary_provider: MagicMock,
        circuit_breaker: CircuitBreakerRegistry,
    ) -> None:
        """熔断器 OPEN 时跳过该 provider，直接尝试下一个。"""
        # 手动将 eastmoney 熔断器设为 OPEN
        # 连续失败 3 次触发熔断（threshold=3）
        circuit_breaker.record_failure("eastmoney")
        circuit_breaker.record_failure("eastmoney")
        circuit_breaker.record_failure("eastmoney")
        assert circuit_breaker.is_open("eastmoney") is True

        expected_data = _make_nav_records("akshare")
        secondary_provider.fetch_nav_history.return_value = expected_data

        composite = CompositeProvider(
            providers=[primary_provider, secondary_provider],
            circuit_breaker=circuit_breaker,
        )
        data, source = await composite.fetch_nav_history(
            "000001", date(2024, 1, 1), date(2024, 1, 31)
        )

        # 主源不应被调用（被熔断跳过）
        primary_provider.fetch_nav_history.assert_not_called()
        assert data == expected_data
        assert source == "akshare"

    @pytest.mark.asyncio
    async def test_all_circuits_open_raises_all_providers_failed(
        self,
        primary_provider: MagicMock,
        secondary_provider: MagicMock,
        circuit_breaker: CircuitBreakerRegistry,
    ) -> None:
        """所有 provider 都被熔断时抛出 AllProvidersFailedError。"""
        # 将两个 provider 都熔断
        for _ in range(3):
            circuit_breaker.record_failure("eastmoney")
            circuit_breaker.record_failure("akshare")

        assert circuit_breaker.is_open("eastmoney") is True
        assert circuit_breaker.is_open("akshare") is True

        composite = CompositeProvider(
            providers=[primary_provider, secondary_provider],
            circuit_breaker=circuit_breaker,
        )

        with pytest.raises(AllProvidersFailedError) as exc_info:
            await composite.fetch_nav_history(
                "000001", date(2024, 1, 1), date(2024, 1, 31)
            )

        # 两个 provider 都被跳过，errors 列表为空（没有实际调用失败）
        err = exc_info.value
        assert len(err.errors) == 0

    @pytest.mark.asyncio
    async def test_repeated_failures_trip_circuit_breaker(
        self,
        primary_provider: MagicMock,
        secondary_provider: MagicMock,
        circuit_breaker: CircuitBreakerRegistry,
    ) -> None:
        """连续失败达到阈值后熔断器跳闸。"""
        primary_provider.fetch_nav_history.side_effect = ProviderError(
            "error", provider_name="eastmoney"
        )
        secondary_provider.fetch_nav_history.return_value = []

        composite = CompositeProvider(
            providers=[primary_provider, secondary_provider],
            circuit_breaker=circuit_breaker,
        )

        # 连续调用 3 次（threshold=3）
        for _ in range(3):
            await composite.fetch_nav_history(
                "000001", date(2024, 1, 1), date(2024, 1, 31)
            )

        # eastmoney 应该被熔断
        assert circuit_breaker.is_open("eastmoney") is True

        # 第 4 次调用应跳过 eastmoney
        primary_provider.fetch_nav_history.reset_mock()
        await composite.fetch_nav_history(
            "000001", date(2024, 1, 1), date(2024, 1, 31)
        )
        primary_provider.fetch_nav_history.assert_not_called()


# ---------------------------------------------------------------------------
# 测试：fetch_holdings 和 fetch_announcements
# ---------------------------------------------------------------------------


class TestOtherMethods:
    @pytest.mark.asyncio
    async def test_fetch_holdings_fallback(
        self,
        primary_provider: MagicMock,
        secondary_provider: MagicMock,
    ) -> None:
        """fetch_holdings 主源失败降级到备源。"""
        primary_provider.fetch_holdings.side_effect = ProviderError(
            "error", provider_name="eastmoney"
        )
        expected = HoldingSnapshot(
            fund_code="000001",
            report_date=date(2024, 3, 31),
            positions=[
                HoldingPosition(
                    stock_code="600519",
                    stock_name="贵州茅台",
                    weight=Decimal("0.0823"),
                    shares=Decimal("50000"),
                    market_value=Decimal("90000000"),
                    industry="食品饮料",
                )
            ],
        )
        secondary_provider.fetch_holdings.return_value = expected

        composite = CompositeProvider(
            providers=[primary_provider, secondary_provider]
        )
        data, source = await composite.fetch_holdings("000001", "2024-Q1")

        assert data == expected
        assert source == "akshare"

    @pytest.mark.asyncio
    async def test_fetch_announcements_primary_success(
        self,
        primary_provider: MagicMock,
        secondary_provider: MagicMock,
    ) -> None:
        """fetch_announcements 主源成功。"""
        expected = [
            Announcement(
                fund_code="000001",
                title="关于暂停大额申购的公告",
                category="LIMIT_PURCHASE",
                publish_date=date(2024, 5, 1),
                content_url="http://example.com/ann1",
            )
        ]
        primary_provider.fetch_announcements.return_value = expected

        composite = CompositeProvider(
            providers=[primary_provider, secondary_provider]
        )
        data, source = await composite.fetch_announcements("000001", date(2024, 1, 1))

        assert data == expected
        assert source == "eastmoney"


# ---------------------------------------------------------------------------
# 测试：health_check
# ---------------------------------------------------------------------------


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_health_check_returns_all_providers(
        self,
        primary_provider: MagicMock,
        secondary_provider: MagicMock,
    ) -> None:
        """health_check 返回所有 provider 的健康状态。"""
        primary_provider.health_check.return_value = HealthStatus(
            healthy=True, message="OK", latency_ms=50.0
        )
        secondary_provider.health_check.return_value = HealthStatus(
            healthy=False, message="timeout", latency_ms=5000.0
        )

        composite = CompositeProvider(
            providers=[primary_provider, secondary_provider]
        )
        results = await composite.health_check()

        assert "eastmoney" in results
        assert "akshare" in results
        assert results["eastmoney"].healthy is True
        assert results["akshare"].healthy is False

    @pytest.mark.asyncio
    async def test_health_check_handles_exception(
        self,
        primary_provider: MagicMock,
        secondary_provider: MagicMock,
    ) -> None:
        """health_check 中某个 provider 抛异常时不影响其他。"""
        primary_provider.health_check.side_effect = RuntimeError("unexpected")
        secondary_provider.health_check.return_value = HealthStatus(
            healthy=True, message="OK"
        )

        composite = CompositeProvider(
            providers=[primary_provider, secondary_provider]
        )
        results = await composite.health_check()

        assert results["eastmoney"].healthy is False
        assert "异常" in results["eastmoney"].message
        assert results["akshare"].healthy is True
