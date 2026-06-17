"""Walk-forward 分析服务单元测试。

覆盖：
- WalkForwardConfig: 配置验证
- WalkForwardAnalyzer: 窗口生成、训练+测试流程、指标聚合
- 边界情况：数据不足、单窗口、多窗口滚动
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from app.domain.backtest.calendar import trading_days
from app.domain.backtest.engine_event import FundMeta
from app.domain.backtest.order import OrderIntent
from app.domain.strategy.base import BaseStrategy, StrategyParams
from app.services.optimization import (
    ParamDimension,
    ParamSpace,
    ParamType,
)
from app.services.walk_forward import (
    WalkForwardAnalyzer,
    WalkForwardConfig,
    WalkForwardResult,
    WalkForwardWindow,
)


# ---------------------------------------------------------------------------
# Test Helpers
# ---------------------------------------------------------------------------


class SimpleParams(StrategyParams):
    """简单测试策略参数。"""

    invest_amount: float = 1000.0


class SimpleStrategy(BaseStrategy):
    """简单测试策略：每个交易日定额申购。"""

    name = "simple_dca"

    def on_bar(self, context) -> list[OrderIntent]:
        if context.cash >= Decimal(str(self.params.invest_amount)):
            return [
                OrderIntent(
                    fund_code=self.universe[0],
                    direction="subscribe",
                    amount=Decimal(str(self.params.invest_amount)),
                )
            ]
        return []


def _make_nav_data(
    start: date, end: date, base_nav: float = 1.0, daily_growth: float = 0.001
) -> dict[str, dict[date, Decimal]]:
    """生成简单的测试净值数据（线性增长）。"""
    dates = trading_days(start, end)
    nav_data: dict[str, dict[date, Decimal]] = {"000001": {}}
    for i, d in enumerate(dates):
        nav = base_nav + i * daily_growth
        nav_data["000001"][d] = Decimal(str(round(nav, 6)))
    return nav_data


def _strategy_factory(params: dict[str, Any]) -> SimpleStrategy:
    """策略工厂函数。"""
    strategy_params = SimpleParams(**params)
    return SimpleStrategy(params=strategy_params, universe=["000001"])


# ---------------------------------------------------------------------------
# WalkForwardConfig Tests
# ---------------------------------------------------------------------------


class TestWalkForwardConfig:
    """WalkForwardConfig 配置测试。"""

    def test_default_config(self):
        """默认配置正确。"""
        config = WalkForwardConfig()
        assert config.train_days == 252
        assert config.test_days == 63
        assert config.step_days == 63  # 默认等于 test_days
        assert config.objective == "sharpe"
        assert config.maximize is True
        assert config.method == "grid"

    def test_step_days_defaults_to_test_days(self):
        """step_days 默认等于 test_days。"""
        config = WalkForwardConfig(train_days=100, test_days=50)
        assert config.step_days == 50

    def test_custom_step_days(self):
        """自定义 step_days。"""
        config = WalkForwardConfig(train_days=100, test_days=50, step_days=25)
        assert config.step_days == 25

    def test_invalid_train_days(self):
        """train_days <= 0 应报错。"""
        with pytest.raises(ValueError, match="train_days must be positive"):
            WalkForwardConfig(train_days=0)

    def test_invalid_test_days(self):
        """test_days <= 0 应报错。"""
        with pytest.raises(ValueError, match="test_days must be positive"):
            WalkForwardConfig(test_days=-1)

    def test_invalid_step_days(self):
        """step_days <= 0 应报错。"""
        with pytest.raises(ValueError, match="step_days must be positive"):
            WalkForwardConfig(step_days=0)


# ---------------------------------------------------------------------------
# WalkForwardAnalyzer Window Generation Tests
# ---------------------------------------------------------------------------


class TestWindowGeneration:
    """窗口生成逻辑测试。"""

    def test_single_window(self):
        """数据刚好够一个窗口。"""
        # 使用较短的窗口以适应测试数据
        start = date(2024, 1, 2)
        end = date(2024, 6, 28)
        nav_data = _make_nav_data(start, end)

        all_dates = trading_days(start, end)
        train_days = len(all_dates) // 2
        test_days = len(all_dates) - train_days

        config = WalkForwardConfig(
            train_days=train_days,
            test_days=test_days,
            objective="total_return",
        )

        space = ParamSpace(
            dimensions=[
                ParamDimension(
                    "invest_amount", ParamType.DISCRETE, low=500, high=1000, step=500
                ),
            ]
        )

        analyzer = WalkForwardAnalyzer(
            param_space=space,
            strategy_factory=_strategy_factory,
            nav_data=nav_data,
            initial_capital=Decimal("100000"),
            config=config,
        )

        result = analyzer.run(start, end)
        assert result.total_windows == 1
        assert len(result.windows) == 1

    def test_multiple_windows(self):
        """多个滚动窗口。"""
        start = date(2024, 1, 2)
        end = date(2024, 12, 31)
        nav_data = _make_nav_data(start, end)

        all_dates = trading_days(start, end)
        # 使用较小的窗口确保能生成多个
        train_days = 40
        test_days = 20
        step_days = 20

        config = WalkForwardConfig(
            train_days=train_days,
            test_days=test_days,
            step_days=step_days,
            objective="total_return",
        )

        space = ParamSpace(
            dimensions=[
                ParamDimension(
                    "invest_amount", ParamType.DISCRETE, low=500, high=1000, step=500
                ),
            ]
        )

        analyzer = WalkForwardAnalyzer(
            param_space=space,
            strategy_factory=_strategy_factory,
            nav_data=nav_data,
            initial_capital=Decimal("100000"),
            config=config,
        )

        result = analyzer.run(start, end)
        # 应该有多个窗口
        assert result.total_windows > 1
        # 每个窗口都应有测试指标
        for window in result.windows:
            assert window.test_metrics
            assert "total_return" in window.test_metrics

    def test_overlapping_windows(self):
        """step_days < test_days 时窗口有重叠。"""
        start = date(2024, 1, 2)
        end = date(2024, 12, 31)
        nav_data = _make_nav_data(start, end)

        config = WalkForwardConfig(
            train_days=40,
            test_days=20,
            step_days=10,  # 小于 test_days，窗口有重叠
            objective="total_return",
        )

        space = ParamSpace(
            dimensions=[
                ParamDimension(
                    "invest_amount", ParamType.DISCRETE, low=500, high=1000, step=500
                ),
            ]
        )

        analyzer = WalkForwardAnalyzer(
            param_space=space,
            strategy_factory=_strategy_factory,
            nav_data=nav_data,
            initial_capital=Decimal("100000"),
            config=config,
        )

        result = analyzer.run(start, end)
        # 重叠窗口应该比非重叠产生更多窗口
        assert result.total_windows > 5

    def test_insufficient_data_raises(self):
        """数据不足时应报错。"""
        start = date(2024, 1, 2)
        end = date(2024, 1, 31)  # 只有约 20 个交易日
        nav_data = _make_nav_data(start, end)

        config = WalkForwardConfig(
            train_days=252,  # 需要 252 + 63 = 315 个交易日
            test_days=63,
            objective="total_return",
        )

        space = ParamSpace(
            dimensions=[
                ParamDimension(
                    "invest_amount", ParamType.DISCRETE, low=500, high=1000, step=500
                ),
            ]
        )

        analyzer = WalkForwardAnalyzer(
            param_space=space,
            strategy_factory=_strategy_factory,
            nav_data=nav_data,
            initial_capital=Decimal("100000"),
            config=config,
        )

        with pytest.raises(ValueError, match="Insufficient trading days"):
            analyzer.run(start, end)


# ---------------------------------------------------------------------------
# WalkForwardAnalyzer Execution Tests
# ---------------------------------------------------------------------------


class TestWalkForwardExecution:
    """Walk-forward 执行逻辑测试。"""

    def _create_analyzer(
        self,
        start: date = date(2024, 1, 2),
        end: date = date(2024, 12, 31),
        train_days: int = 40,
        test_days: int = 20,
    ) -> tuple[WalkForwardAnalyzer, date, date]:
        """创建测试用分析器。"""
        nav_data = _make_nav_data(start, end)

        config = WalkForwardConfig(
            train_days=train_days,
            test_days=test_days,
            objective="total_return",
            method="grid",
        )

        space = ParamSpace(
            dimensions=[
                ParamDimension(
                    "invest_amount", ParamType.DISCRETE, low=500, high=1000, step=500
                ),
            ]
        )

        analyzer = WalkForwardAnalyzer(
            param_space=space,
            strategy_factory=_strategy_factory,
            nav_data=nav_data,
            initial_capital=Decimal("100000"),
            config=config,
        )

        return analyzer, start, end

    def test_windows_have_best_params(self):
        """每个窗口都应找到最优参数。"""
        analyzer, start, end = self._create_analyzer()
        result = analyzer.run(start, end)

        for window in result.windows:
            assert window.best_params
            assert "invest_amount" in window.best_params

    def test_windows_have_test_metrics(self):
        """每个窗口都应有测试指标。"""
        analyzer, start, end = self._create_analyzer()
        result = analyzer.run(start, end)

        for window in result.windows:
            assert window.test_metrics
            assert "total_return" in window.test_metrics
            assert "sharpe" in window.test_metrics
            assert "max_drawdown" in window.test_metrics

    def test_aggregated_metrics(self):
        """聚合指标正确计算。"""
        analyzer, start, end = self._create_analyzer()
        result = analyzer.run(start, end)

        # 聚合指标应包含所有测试窗口中出现的指标
        assert "total_return" in result.aggregated_metrics
        assert "sharpe" in result.aggregated_metrics
        assert "max_drawdown" in result.aggregated_metrics

        # 标准差也应存在
        assert "total_return" in result.metric_std
        assert "sharpe" in result.metric_std

    def test_aggregated_metrics_are_mean_of_windows(self):
        """聚合指标是各窗口测试指标的均值。"""
        import numpy as np

        analyzer, start, end = self._create_analyzer()
        result = analyzer.run(start, end)

        # 手动计算均值验证
        total_returns = [
            w.test_metrics["total_return"] for w in result.windows
        ]
        expected_mean = float(np.mean(total_returns))

        assert pytest.approx(
            result.aggregated_metrics["total_return"], rel=1e-6
        ) == expected_mean

    def test_window_dates_are_sequential(self):
        """窗口日期应按顺序排列。"""
        analyzer, start, end = self._create_analyzer()
        result = analyzer.run(start, end)

        for window in result.windows:
            # 训练窗口在测试窗口之前
            assert window.train_start <= window.train_end
            assert window.train_end < window.test_start
            assert window.test_start <= window.test_end

        # 窗口按时间顺序
        for i in range(1, len(result.windows)):
            assert result.windows[i].train_start > result.windows[i - 1].train_start

    def test_sobol_method(self):
        """Sobol 优化方法正常工作。"""
        start = date(2024, 1, 2)
        end = date(2024, 12, 31)
        nav_data = _make_nav_data(start, end)

        config = WalkForwardConfig(
            train_days=40,
            test_days=20,
            objective="total_return",
            method="sobol",
            n_samples=4,
            seed=42,
        )

        space = ParamSpace(
            dimensions=[
                ParamDimension(
                    "invest_amount", ParamType.CONTINUOUS, low=500.0, high=2000.0
                ),
            ]
        )

        def factory(params: dict[str, Any]) -> SimpleStrategy:
            # 将连续值转为合理的参数
            p = SimpleParams(invest_amount=params["invest_amount"])
            return SimpleStrategy(params=p, universe=["000001"])

        analyzer = WalkForwardAnalyzer(
            param_space=space,
            strategy_factory=factory,
            nav_data=nav_data,
            initial_capital=Decimal("100000"),
            config=config,
        )

        result = analyzer.run(start, end)
        assert result.total_windows > 0
        assert result.aggregated_metrics

    def test_result_config_preserved(self):
        """结果中保留了使用的配置。"""
        analyzer, start, end = self._create_analyzer()
        result = analyzer.run(start, end)

        assert result.config.train_days == 40
        assert result.config.test_days == 20
        assert result.objective == "total_return"

    def test_train_metric_recorded(self):
        """训练阶段的最优指标被记录。"""
        analyzer, start, end = self._create_analyzer()
        result = analyzer.run(start, end)

        for window in result.windows:
            # 训练指标应该是有限值（策略在上涨数据上应有正收益）
            assert window.train_metric != float("-inf")

    def test_with_fund_meta(self):
        """带基金元数据的 Walk-forward 分析。"""
        start = date(2024, 1, 2)
        end = date(2024, 12, 31)
        nav_data = _make_nav_data(start, end)

        fund_meta = {
            "000001": FundMeta(code="000001", fund_type="stock"),
        }

        config = WalkForwardConfig(
            train_days=40,
            test_days=20,
            objective="total_return",
        )

        space = ParamSpace(
            dimensions=[
                ParamDimension(
                    "invest_amount", ParamType.DISCRETE, low=500, high=1000, step=500
                ),
            ]
        )

        analyzer = WalkForwardAnalyzer(
            param_space=space,
            strategy_factory=_strategy_factory,
            nav_data=nav_data,
            initial_capital=Decimal("100000"),
            fund_meta=fund_meta,
            config=config,
        )

        result = analyzer.run(start, end)
        assert result.total_windows > 0


# ---------------------------------------------------------------------------
# Metric Extraction Tests
# ---------------------------------------------------------------------------


class TestMetricExtraction:
    """指标提取逻辑测试。"""

    def test_positive_return_data(self):
        """上涨数据应产生正收益指标。"""
        start = date(2024, 1, 2)
        end = date(2024, 12, 31)
        # 使用较大的日增长率确保正收益
        nav_data = _make_nav_data(start, end, daily_growth=0.005)

        config = WalkForwardConfig(
            train_days=40,
            test_days=20,
            objective="total_return",
        )

        space = ParamSpace(
            dimensions=[
                ParamDimension(
                    "invest_amount", ParamType.DISCRETE, low=500, high=1000, step=500
                ),
            ]
        )

        analyzer = WalkForwardAnalyzer(
            param_space=space,
            strategy_factory=_strategy_factory,
            nav_data=nav_data,
            initial_capital=Decimal("100000"),
            config=config,
        )

        result = analyzer.run(start, end)

        # 在持续上涨的数据上，样本外收益应为正
        for window in result.windows:
            assert window.test_metrics.get("total_return", 0) >= 0

    def test_metrics_include_all_standard_fields(self):
        """指标应包含所有标准字段。"""
        start = date(2024, 1, 2)
        end = date(2024, 12, 31)
        nav_data = _make_nav_data(start, end)

        config = WalkForwardConfig(
            train_days=40,
            test_days=20,
            objective="total_return",
        )

        space = ParamSpace(
            dimensions=[
                ParamDimension(
                    "invest_amount", ParamType.DISCRETE, low=500, high=1000, step=500
                ),
            ]
        )

        analyzer = WalkForwardAnalyzer(
            param_space=space,
            strategy_factory=_strategy_factory,
            nav_data=nav_data,
            initial_capital=Decimal("100000"),
            config=config,
        )

        result = analyzer.run(start, end)

        expected_fields = {
            "total_return",
            "annualized_return",
            "volatility",
            "sharpe",
            "max_drawdown",
            "calmar",
            "sortino",
        }

        for window in result.windows:
            assert expected_fields.issubset(set(window.test_metrics.keys()))


# ---------------------------------------------------------------------------
# Purge & Embargo Tests
# ---------------------------------------------------------------------------


class TestPurgeAndEmbargo:
    """Verify Purge & Embargo (Lopez de Prado) behaviour."""

    def test_default_no_purge_no_embargo(self):
        """Default config has purge_days=0 and embargo_days=0 (backward compatible)."""
        config = WalkForwardConfig()
        assert config.purge_days == 0
        assert config.embargo_days == 0

    def test_invalid_purge_days(self):
        """Negative purge_days raises."""
        with pytest.raises(ValueError, match="purge_days must be non-negative"):
            WalkForwardConfig(purge_days=-1)

    def test_invalid_embargo_days(self):
        """Negative embargo_days raises."""
        with pytest.raises(ValueError, match="embargo_days must be non-negative"):
            WalkForwardConfig(embargo_days=-5)

    def test_purge_must_be_smaller_than_train(self):
        """purge_days >= train_days raises."""
        with pytest.raises(ValueError, match="purge_days .* must be less than"):
            WalkForwardConfig(train_days=20, purge_days=20)

    def test_embargo_creates_gap_between_train_and_test(self):
        """With embargo_days=5, test_start should be 5 days after train_end."""
        from app.domain.backtest.calendar import trading_days

        start = date(2024, 1, 2)
        end = date(2024, 12, 31)
        nav_data = _make_nav_data(start, end)

        config = WalkForwardConfig(
            train_days=40,
            test_days=20,
            step_days=20,
            purge_days=0,
            embargo_days=5,
            objective="total_return",
        )
        space = ParamSpace(
            dimensions=[
                ParamDimension(
                    "invest_amount", ParamType.DISCRETE, low=500, high=1000, step=500
                ),
            ]
        )
        analyzer = WalkForwardAnalyzer(
            param_space=space,
            strategy_factory=_strategy_factory,
            nav_data=nav_data,
            initial_capital=Decimal("100000"),
            config=config,
        )
        result = analyzer.run(start, end)

        all_trade_dates = trading_days(start, end)

        for window in result.windows:
            train_end_idx = all_trade_dates.index(window.train_end)
            test_start_idx = all_trade_dates.index(window.test_start)
            # Embargo of 5 days: test_start should be at least 5 days after train_end
            assert test_start_idx - train_end_idx >= 5

    def test_purge_shrinks_effective_train_window(self):
        """With purge_days=10, train_end should be 10 days earlier than train_days suggests."""
        from app.domain.backtest.calendar import trading_days

        start = date(2024, 1, 2)
        end = date(2024, 12, 31)
        nav_data = _make_nav_data(start, end)

        config = WalkForwardConfig(
            train_days=40,
            test_days=20,
            step_days=40,
            purge_days=10,
            embargo_days=0,
            objective="total_return",
        )
        space = ParamSpace(
            dimensions=[
                ParamDimension(
                    "invest_amount", ParamType.DISCRETE, low=500, high=1000, step=500
                ),
            ]
        )
        analyzer = WalkForwardAnalyzer(
            param_space=space,
            strategy_factory=_strategy_factory,
            nav_data=nav_data,
            initial_capital=Decimal("100000"),
            config=config,
        )
        result = analyzer.run(start, end)

        all_trade_dates = trading_days(start, end)
        first_window = result.windows[0]
        train_start_idx = all_trade_dates.index(first_window.train_start)
        train_end_idx = all_trade_dates.index(first_window.train_end)
        # Effective train length = train_days - purge_days = 40 - 10 = 30 days
        # train_end_idx - train_start_idx + 1 = 30 → train_end_idx - train_start_idx = 29
        assert train_end_idx - train_start_idx == 29

    def test_insufficient_data_with_embargo_raises(self):
        """Embargo increases minimum required data."""
        start = date(2024, 1, 2)
        end = date(2024, 3, 31)  # ~60 trading days
        nav_data = _make_nav_data(start, end)

        config = WalkForwardConfig(
            train_days=40,
            test_days=20,
            embargo_days=10,  # 40+10+20=70 > 60 available
            objective="total_return",
        )
        space = ParamSpace(
            dimensions=[
                ParamDimension(
                    "invest_amount", ParamType.DISCRETE, low=500, high=1000, step=500
                ),
            ]
        )
        analyzer = WalkForwardAnalyzer(
            param_space=space,
            strategy_factory=_strategy_factory,
            nav_data=nav_data,
            initial_capital=Decimal("100000"),
            config=config,
        )
        with pytest.raises(ValueError, match="Insufficient trading days"):
            analyzer.run(start, end)
