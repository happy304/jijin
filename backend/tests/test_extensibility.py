"""可扩展性验证测试。

验证平台的核心扩展点无需修改核心代码即可使用：
1. 新增数据源：实现 FundDataProvider 接口，注册到 CompositeProvider
2. 新增策略：继承 BaseStrategy，实现 on_bar 方法
3. 新增因子：使用 @factor 装饰器注册

需求: 10.1, 10.2, 10.4, 10.5
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.data.providers.base import (
    FundDataProvider,
    HealthStatus,
    ProviderError,
)
from app.data.providers.composite import CompositeProvider
from app.data.schemas.funds import (
    Announcement,
    DividendRecord,
    FundMeta,
    HoldingSnapshot,
    NavRecord,
)
from app.domain.backtest.order import OrderIntent
from app.domain.factors.registry import (
    FactorDef,
    _FACTOR_REGISTRY,
    factor,
    get_factor,
    list_factors,
)
from app.domain.strategy.base import BaseStrategy, StrategyParams
from app.domain.strategy.registry import StrategyRegistry


# ===========================================================================
# 1. 假数据源 - 验证 FundDataProvider 可扩展性（需求 10.1）
# ===========================================================================


class FakeProvider:
    """一个假数据源，实现 FundDataProvider 协议。

    无需继承任何基类，只需满足 Protocol 的结构化子类型要求。
    """

    name: str = "fake_provider"
    priority: int = 99

    async def fetch_fund_meta(self, code: str) -> FundMeta:
        return FundMeta(
            code=code,
            name=f"Fake Fund {code}",
            source=self.name,
        )

    async def fetch_nav_history(
        self, code: str, start: date, end: date
    ) -> list[NavRecord]:
        return [
            NavRecord(
                fund_code=code,
                trade_date=start,
                unit_nav=Decimal("1.5000"),
                accum_nav=Decimal("2.0000"),
                source=self.name,
            )
        ]

    async def fetch_holdings(self, code: str, quarter: str) -> HoldingSnapshot:
        return HoldingSnapshot(fund_code=code, report_date=date(2024, 3, 31))

    async def fetch_dividends(self, code: str) -> list[DividendRecord]:
        return [
            DividendRecord(
                fund_code=code,
                ex_date=date(2024, 6, 15),
                dividend_per_share=Decimal("0.5"),
            )
        ]

    async def fetch_announcements(
        self, code: str, since: date
    ) -> list[Announcement]:
        return []

    async def health_check(self) -> HealthStatus:
        return HealthStatus(healthy=True, message="fake provider is healthy")


class TestDataProviderExtensibility:
    """验证新增数据源无需修改核心代码。"""

    def test_fake_provider_satisfies_protocol(self):
        """FakeProvider 满足 FundDataProvider 协议（结构化子类型）。"""
        provider = FakeProvider()
        assert isinstance(provider, FundDataProvider)

    @pytest.mark.asyncio
    async def test_fake_provider_works_with_composite(self):
        """FakeProvider 可以直接注册到 CompositeProvider 并正常工作。"""
        fake = FakeProvider()
        composite = CompositeProvider(providers=[fake])

        # 调用 fetch_fund_meta
        meta, source = await composite.fetch_fund_meta("999999")
        assert meta.code == "999999"
        assert meta.name == "Fake Fund 999999"
        assert source == "fake_provider"

    @pytest.mark.asyncio
    async def test_fake_provider_fallback_in_composite(self):
        """FakeProvider 作为备源，主源失败时自动降级到 FakeProvider。"""

        class FailingProvider:
            """总是失败的主源。"""

            name = "failing_provider"
            priority = 1

            async def fetch_fund_meta(self, code: str) -> FundMeta:
                raise ProviderError(
                    "模拟失败", provider_name=self.name, fund_code=code
                )

            async def fetch_nav_history(self, code, start, end):
                raise ProviderError("模拟失败", provider_name=self.name)

            async def fetch_holdings(self, code, quarter):
                raise ProviderError("模拟失败", provider_name=self.name)

            async def fetch_dividends(self, code):
                raise ProviderError("模拟失败", provider_name=self.name)

            async def fetch_announcements(self, code, since):
                raise ProviderError("模拟失败", provider_name=self.name)

            async def health_check(self):
                return HealthStatus(healthy=False, message="always fails")

        failing = FailingProvider()
        fake = FakeProvider()
        composite = CompositeProvider(providers=[failing, fake])

        # 主源失败，自动降级到 FakeProvider
        meta, source = await composite.fetch_fund_meta("000001")
        assert source == "fake_provider"
        assert meta.code == "000001"

    @pytest.mark.asyncio
    async def test_fake_provider_nav_history(self):
        """FakeProvider 的 fetch_nav_history 通过 CompositeProvider 正常返回。"""
        fake = FakeProvider()
        composite = CompositeProvider(providers=[fake])

        navs, source = await composite.fetch_nav_history(
            "000001", date(2024, 1, 1), date(2024, 1, 31)
        )
        assert source == "fake_provider"
        assert len(navs) == 1
        assert navs[0].unit_nav == Decimal("1.5000")


# ===========================================================================
# 2. 假策略 - 验证 BaseStrategy 可扩展性（需求 10.5）
# ===========================================================================


class FakeStrategyParams(StrategyParams):
    """假策略的参数。"""

    buy_threshold: float = 0.5
    sell_threshold: float = -0.3


class FakeStrategy(BaseStrategy):
    """一个假策略，继承 BaseStrategy 并实现 on_bar。

    策略逻辑：如果有现金就全部申购第一只基金。
    """

    name = "fake_strategy"

    def __init__(
        self,
        params: FakeStrategyParams | None = None,
        universe: list[str] | None = None,
    ) -> None:
        super().__init__(params=params or FakeStrategyParams(), universe=universe)

    def on_bar(self, context) -> list[OrderIntent]:
        """简单策略：有现金就买入第一只基金。"""
        if not self.universe:
            return []

        cash = context.cash
        if cash > Decimal("100"):
            return [
                OrderIntent(
                    fund_code=self.universe[0],
                    direction="subscribe",
                    amount=cash,
                )
            ]
        return []


class TestStrategyExtensibility:
    """验证新增策略无需修改核心代码。"""

    def test_fake_strategy_is_base_strategy_subclass(self):
        """FakeStrategy 是 BaseStrategy 的子类。"""
        assert issubclass(FakeStrategy, BaseStrategy)

    def test_fake_strategy_instantiation(self):
        """FakeStrategy 可以正常实例化。"""
        strategy = FakeStrategy(
            params=FakeStrategyParams(buy_threshold=0.8),
            universe=["000001", "000002"],
        )
        assert strategy.name == "fake_strategy"
        assert strategy.universe == ["000001", "000002"]
        assert strategy.params.buy_threshold == 0.8

    def test_fake_strategy_registers_in_registry(self):
        """FakeStrategy 可以注册到策略注册表。"""
        registry = StrategyRegistry()
        registry.register(FakeStrategy)

        assert registry.get("fake_strategy") is FakeStrategy
        assert "fake_strategy" in registry.list_names()

    def test_fake_strategy_on_bar_returns_order_intents(self):
        """FakeStrategy.on_bar 返回有效的 OrderIntent 列表。"""
        from unittest.mock import MagicMock

        strategy = FakeStrategy(universe=["000001"])

        # 模拟 BarContext
        mock_context = MagicMock()
        mock_context.cash = Decimal("10000")

        orders = strategy.on_bar(mock_context)
        assert len(orders) == 1
        assert orders[0].fund_code == "000001"
        assert orders[0].direction == "subscribe"
        assert orders[0].amount == Decimal("10000")

    def test_fake_strategy_no_cash_no_orders(self):
        """现金不足时不产生订单。"""
        from unittest.mock import MagicMock

        strategy = FakeStrategy(universe=["000001"])

        mock_context = MagicMock()
        mock_context.cash = Decimal("50")  # 低于 100 阈值

        orders = strategy.on_bar(mock_context)
        assert orders == []


# ===========================================================================
# 3. 假因子 - 验证 @factor 装饰器可扩展性（需求 10.4）
# ===========================================================================


# 使用唯一名称避免与其他测试冲突
_TEST_FACTOR_NAME = "_test_extensibility_fake_momentum"


@factor(_TEST_FACTOR_NAME, category="custom", window=20, return_type="scalar")
def fake_momentum_factor(nav_series, window: int = 20) -> float:
    """假动量因子：计算窗口期内的简单收益率。"""
    if len(nav_series) < 2:
        return float("nan")
    return (nav_series[-1] / nav_series[0]) - 1.0


class TestFactorExtensibility:
    """验证新增因子无需修改核心代码。"""

    def test_fake_factor_registered_in_registry(self):
        """使用 @factor 装饰器注册的因子出现在全局注册表中。"""
        assert _TEST_FACTOR_NAME in _FACTOR_REGISTRY

    def test_fake_factor_metadata_correct(self):
        """注册的因子元数据正确。"""
        factor_def = get_factor(_TEST_FACTOR_NAME)
        assert isinstance(factor_def, FactorDef)
        assert factor_def.name == _TEST_FACTOR_NAME
        assert factor_def.category == "custom"
        assert factor_def.window == 20
        assert factor_def.return_type == "scalar"

    def test_fake_factor_appears_in_list(self):
        """注册的因子可以通过 list_factors 查询到。"""
        all_factors = list_factors()
        names = [f.name for f in all_factors]
        assert _TEST_FACTOR_NAME in names

    def test_fake_factor_filtered_by_category(self):
        """注册的因子可以按自定义类别过滤。"""
        custom_factors = list_factors(category="custom")
        names = [f.name for f in custom_factors]
        assert _TEST_FACTOR_NAME in names

    def test_fake_factor_callable(self):
        """注册的因子函数可以正常调用。"""
        factor_def = get_factor(_TEST_FACTOR_NAME)
        nav = [1.0, 1.05, 1.10, 1.08, 1.15]
        result = factor_def.fn(nav)
        expected = (1.15 / 1.0) - 1.0
        assert abs(result - expected) < 1e-10

    def test_fake_factor_handles_empty_input(self):
        """因子函数对空输入返回 NaN。"""
        import math

        factor_def = get_factor(_TEST_FACTOR_NAME)
        result = factor_def.fn([1.0])  # 少于 2 个数据点
        assert math.isnan(result)
