"""向量化回测引擎单元测试 + 性能测试。

覆盖：
- 基本功能：信号矩阵 → 权重归一化 → 扣除成本 → 权益曲线
- 权重归一化逻辑
- 前视偏差防护（shift(1)）
- 交易成本扣除
- 汇总指标计算
- 边界条件
- 性能测试：100 基金 10 年 < 5 秒

需求: 4.12
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd
import pytest

from app.domain.backtest.engine_vector import VectorBacktest, VectorBacktestResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def simple_dates() -> pd.DatetimeIndex:
    """5 个交易日。"""
    return pd.date_range("2024-01-01", periods=5, freq="B")


@pytest.fixture
def simple_signals(simple_dates: pd.DatetimeIndex) -> pd.DataFrame:
    """简单等权信号：2 只基金各 0.5。"""
    return pd.DataFrame(
        {"fund_a": [0.5] * 5, "fund_b": [0.5] * 5},
        index=simple_dates,
    )


@pytest.fixture
def simple_returns(simple_dates: pd.DatetimeIndex) -> pd.DataFrame:
    """简单收益率：每天各 1%。"""
    return pd.DataFrame(
        {"fund_a": [0.01] * 5, "fund_b": [0.01] * 5},
        index=simple_dates,
    )


@pytest.fixture
def engine() -> VectorBacktest:
    """默认引擎：100 万初始资金，10bps 成本。"""
    return VectorBacktest(initial_capital=1_000_000, cost_bps=10)


@pytest.fixture
def zero_cost_engine() -> VectorBacktest:
    """零成本引擎。"""
    return VectorBacktest(initial_capital=1_000_000, cost_bps=0)


# ---------------------------------------------------------------------------
# 基本功能测试
# ---------------------------------------------------------------------------


class TestVectorBacktestBasic:
    """基本功能测试。"""

    def test_returns_correct_type(
        self, engine: VectorBacktest, simple_signals: pd.DataFrame, simple_returns: pd.DataFrame
    ) -> None:
        """结果应为 VectorBacktestResult 类型。"""
        result = engine.run(simple_signals, simple_returns)
        assert isinstance(result, VectorBacktestResult)

    def test_result_has_all_fields(
        self, engine: VectorBacktest, simple_signals: pd.DataFrame, simple_returns: pd.DataFrame
    ) -> None:
        """结果应包含所有必要字段。"""
        result = engine.run(simple_signals, simple_returns)
        assert isinstance(result.equity, pd.Series)
        assert isinstance(result.returns, pd.Series)
        assert isinstance(result.turnover, pd.Series)
        assert isinstance(result.total_return, float)
        assert isinstance(result.annualized_return, float)
        assert isinstance(result.max_drawdown, float)

    def test_equity_length_matches_input(
        self, engine: VectorBacktest, simple_signals: pd.DataFrame, simple_returns: pd.DataFrame
    ) -> None:
        """权益曲线长度应与输入一致。"""
        result = engine.run(simple_signals, simple_returns)
        assert len(result.equity) == len(simple_signals)
        assert len(result.returns) == len(simple_signals)
        assert len(result.turnover) == len(simple_signals)

    def test_equity_starts_near_initial_capital(
        self, engine: VectorBacktest, simple_signals: pd.DataFrame, simple_returns: pd.DataFrame
    ) -> None:
        """第一天权益应接近初始资金（扣除建仓成本后）。"""
        result = engine.run(simple_signals, simple_returns)
        # 第一天建仓换手率 = 1.0（0.5 + 0.5），成本 = 1.0 * 10/10000 = 0.001
        # 第一天权益 = 1_000_000 * (1 - 0.001) = 999_000
        assert result.equity.iloc[0] == pytest.approx(999_000, rel=1e-6)


# ---------------------------------------------------------------------------
# 权重归一化测试
# ---------------------------------------------------------------------------


class TestWeightNormalization:
    """权重归一化逻辑测试。"""

    def test_weights_sum_to_one_when_exceeds(self, zero_cost_engine: VectorBacktest) -> None:
        """权重和 > 1 时应缩放到 1。"""
        dates = pd.date_range("2024-01-01", periods=3, freq="B")
        # 权重和为 1.5，应缩放
        signals = pd.DataFrame(
            {"a": [0.75, 0.75, 0.75], "b": [0.75, 0.75, 0.75]},
            index=dates,
        )
        returns = pd.DataFrame(
            {"a": [0.01, 0.01, 0.01], "b": [0.01, 0.01, 0.01]},
            index=dates,
        )
        result = zero_cost_engine.run(signals, returns)
        # 归一化后每只 0.5，组合收益 = 0.5*0.01 + 0.5*0.01 = 0.01
        # 第二天收益应为 0.01（第一天 lagged_weights 为 NaN）
        assert result.returns.iloc[1] == pytest.approx(0.01, abs=1e-10)

    def test_weights_preserved_when_sum_less_than_one(
        self, zero_cost_engine: VectorBacktest
    ) -> None:
        """权重和 ≤ 1 时保持不变（差额为现金）。"""
        dates = pd.date_range("2024-01-01", periods=3, freq="B")
        # 权重和为 0.6，不缩放
        signals = pd.DataFrame(
            {"a": [0.3, 0.3, 0.3], "b": [0.3, 0.3, 0.3]},
            index=dates,
        )
        returns = pd.DataFrame(
            {"a": [0.01, 0.01, 0.01], "b": [0.02, 0.02, 0.02]},
            index=dates,
        )
        result = zero_cost_engine.run(signals, returns)
        # 第二天收益 = 0.3*0.01 + 0.3*0.02 = 0.009
        assert result.returns.iloc[1] == pytest.approx(0.009, abs=1e-10)

    def test_negative_weights_clipped_to_zero(self, zero_cost_engine: VectorBacktest) -> None:
        """负权重应被裁剪为 0。"""
        dates = pd.date_range("2024-01-01", periods=3, freq="B")
        signals = pd.DataFrame(
            {"a": [-0.5, -0.5, -0.5], "b": [0.8, 0.8, 0.8]},
            index=dates,
        )
        returns = pd.DataFrame(
            {"a": [0.01, 0.01, 0.01], "b": [0.02, 0.02, 0.02]},
            index=dates,
        )
        result = zero_cost_engine.run(signals, returns)
        # a 被裁剪为 0，b 保持 0.8（和 ≤ 1）
        # 第二天收益 = 0*0.01 + 0.8*0.02 = 0.016
        assert result.returns.iloc[1] == pytest.approx(0.016, abs=1e-10)


# ---------------------------------------------------------------------------
# 前视偏差防护测试
# ---------------------------------------------------------------------------


class TestLookaheadPrevention:
    """前视偏差防护测试（shift(1) 逻辑）。"""

    def test_first_day_no_position_return(self, zero_cost_engine: VectorBacktest) -> None:
        """第一天应无持仓收益（因为 shift(1) 使 lagged_weights 为 NaN）。"""
        dates = pd.date_range("2024-01-01", periods=3, freq="B")
        signals = pd.DataFrame({"a": [1.0, 1.0, 1.0]}, index=dates)
        returns = pd.DataFrame({"a": [0.05, 0.01, 0.01]}, index=dates)
        result = zero_cost_engine.run(signals, returns)
        # 第一天：lagged_weights 为 NaN → 持仓收益为 0，只扣建仓成本（0 bps）
        assert result.returns.iloc[0] == pytest.approx(0.0, abs=1e-10)

    def test_second_day_uses_first_day_weights(self, zero_cost_engine: VectorBacktest) -> None:
        """第二天应使用第一天的权重。"""
        dates = pd.date_range("2024-01-01", periods=3, freq="B")
        # 第一天权重 0.5，第二天权重 1.0
        signals = pd.DataFrame({"a": [0.5, 1.0, 1.0]}, index=dates)
        returns = pd.DataFrame({"a": [0.00, 0.02, 0.01]}, index=dates)
        result = zero_cost_engine.run(signals, returns)
        # 第二天收益 = 0.5 * 0.02 = 0.01（使用第一天权重 0.5）
        # 换手率 = |1.0 - 0.5| = 0.5，无成本
        assert result.returns.iloc[1] == pytest.approx(0.01, abs=1e-10)


# ---------------------------------------------------------------------------
# 交易成本测试
# ---------------------------------------------------------------------------


class TestTransactionCosts:
    """交易成本扣除测试。"""

    def test_zero_cost_no_deduction(self, zero_cost_engine: VectorBacktest) -> None:
        """零成本时不扣除费用。"""
        dates = pd.date_range("2024-01-01", periods=3, freq="B")
        signals = pd.DataFrame({"a": [1.0, 1.0, 1.0]}, index=dates)
        returns = pd.DataFrame({"a": [0.0, 0.01, 0.01]}, index=dates)
        result = zero_cost_engine.run(signals, returns)
        # 第二天：权重不变，换手率 0，收益 = 1.0 * 0.01 = 0.01
        assert result.returns.iloc[1] == pytest.approx(0.01, abs=1e-10)

    def test_cost_deducted_on_turnover(self) -> None:
        """有换手时应扣除成本。"""
        engine = VectorBacktest(initial_capital=1_000_000, cost_bps=100)  # 1% 成本
        dates = pd.date_range("2024-01-01", periods=3, freq="B")
        # 第一天全仓 a，第二天全仓 b → 换手率 = |0-1| + |1-0| = 2
        signals = pd.DataFrame(
            {"a": [1.0, 0.0, 0.0], "b": [0.0, 1.0, 1.0]},
            index=dates,
        )
        returns = pd.DataFrame(
            {"a": [0.0, 0.0, 0.0], "b": [0.0, 0.0, 0.0]},
            index=dates,
        )
        result = engine.run(signals, returns)
        # 第二天：换手率 = 2.0，成本 = 2.0 * 100/10000 = 0.02
        # 持仓收益 = 1.0*0.0 = 0（使用第一天权重 a=1.0）
        # 净收益 = 0 - 0.02 = -0.02
        assert result.returns.iloc[1] == pytest.approx(-0.02, abs=1e-10)

    def test_no_turnover_no_cost(self) -> None:
        """无换手时不扣除成本。"""
        engine = VectorBacktest(initial_capital=1_000_000, cost_bps=100)
        dates = pd.date_range("2024-01-01", periods=4, freq="B")
        # 权重不变
        signals = pd.DataFrame({"a": [0.5, 0.5, 0.5, 0.5]}, index=dates)
        returns = pd.DataFrame({"a": [0.0, 0.01, 0.01, 0.01]}, index=dates)
        result = engine.run(signals, returns)
        # 第三天和第四天：换手率 = 0，成本 = 0
        # 收益 = 0.5 * 0.01 = 0.005
        assert result.returns.iloc[2] == pytest.approx(0.005, abs=1e-10)
        assert result.returns.iloc[3] == pytest.approx(0.005, abs=1e-10)


# ---------------------------------------------------------------------------
# 换手率计算测试
# ---------------------------------------------------------------------------


class TestTurnover:
    """换手率计算测试。"""

    def test_initial_turnover_equals_initial_weights(
        self, zero_cost_engine: VectorBacktest
    ) -> None:
        """第一天换手率应等于初始权重之和。"""
        dates = pd.date_range("2024-01-01", periods=3, freq="B")
        signals = pd.DataFrame({"a": [0.3, 0.3, 0.3], "b": [0.4, 0.4, 0.4]}, index=dates)
        returns = pd.DataFrame({"a": [0.01] * 3, "b": [0.01] * 3}, index=dates)
        result = zero_cost_engine.run(signals, returns)
        assert result.turnover.iloc[0] == pytest.approx(0.7, abs=1e-10)

    def test_constant_weights_zero_turnover_after_first(
        self, zero_cost_engine: VectorBacktest
    ) -> None:
        """权重不变时，第一天之后换手率为 0。"""
        dates = pd.date_range("2024-01-01", periods=5, freq="B")
        signals = pd.DataFrame({"a": [0.5] * 5, "b": [0.5] * 5}, index=dates)
        returns = pd.DataFrame({"a": [0.01] * 5, "b": [0.01] * 5}, index=dates)
        result = zero_cost_engine.run(signals, returns)
        # 第 2-5 天换手率应为 0
        assert result.turnover.iloc[1:].abs().max() < 1e-10


# ---------------------------------------------------------------------------
# 汇总指标测试
# ---------------------------------------------------------------------------


class TestMetrics:
    """汇总指标计算测试。"""

    def test_total_return_positive(self, zero_cost_engine: VectorBacktest) -> None:
        """正收益时总收益率为正。"""
        dates = pd.date_range("2024-01-01", periods=10, freq="B")
        signals = pd.DataFrame({"a": [1.0] * 10}, index=dates)
        returns = pd.DataFrame({"a": [0.01] * 10}, index=dates)
        result = zero_cost_engine.run(signals, returns)
        assert result.total_return > 0

    def test_total_return_calculation(self, zero_cost_engine: VectorBacktest) -> None:
        """总收益率应等于 (最终权益/初始权益 - 1)。"""
        dates = pd.date_range("2024-01-01", periods=10, freq="B")
        signals = pd.DataFrame({"a": [1.0] * 10}, index=dates)
        returns = pd.DataFrame({"a": [0.01] * 10}, index=dates)
        result = zero_cost_engine.run(signals, returns)
        expected = result.equity.iloc[-1] / result.equity.iloc[0] - 1
        assert result.total_return == pytest.approx(float(expected), rel=1e-10)

    def test_max_drawdown_zero_for_monotone_increase(
        self, zero_cost_engine: VectorBacktest
    ) -> None:
        """单调递增的权益曲线最大回撤为 0。"""
        dates = pd.date_range("2024-01-01", periods=10, freq="B")
        signals = pd.DataFrame({"a": [1.0] * 10}, index=dates)
        returns = pd.DataFrame({"a": [0.01] * 10}, index=dates)
        result = zero_cost_engine.run(signals, returns)
        # 第一天收益为 0（shift），之后每天 +1%，单调递增
        assert result.max_drawdown == pytest.approx(0.0, abs=1e-10)

    def test_max_drawdown_positive_for_decline(self) -> None:
        """有下跌时最大回撤为正数。"""
        engine = VectorBacktest(initial_capital=1_000_000, cost_bps=0)
        dates = pd.date_range("2024-01-01", periods=5, freq="B")
        signals = pd.DataFrame({"a": [1.0] * 5}, index=dates)
        # 先涨后跌
        returns = pd.DataFrame({"a": [0.0, 0.10, -0.20, 0.05, 0.05]}, index=dates)
        result = engine.run(signals, returns)
        assert result.max_drawdown > 0

    def test_annualized_return_reasonable(self, zero_cost_engine: VectorBacktest) -> None:
        """年化收益率应在合理范围内。"""
        # 252 天，每天 0.04%，年化约 10.6%
        dates = pd.date_range("2024-01-01", periods=252, freq="B")
        signals = pd.DataFrame({"a": [1.0] * 252}, index=dates)
        returns = pd.DataFrame({"a": [0.0004] * 252}, index=dates)
        result = zero_cost_engine.run(signals, returns)
        # 年化收益应接近 (1.0004)^252 - 1 ≈ 10.6%
        # 但第一天无收益，所以略低
        assert 0.08 < result.annualized_return < 0.12

    def test_annualized_return_uses_return_intervals_not_points(
        self, zero_cost_engine: VectorBacktest
    ) -> None:
        """253 个权益点对应 252 个收益区间，年化应按区间数计算。"""
        dates = pd.date_range("2024-01-01", periods=253, freq="B")
        signals = pd.DataFrame({"a": [1.0] * 253}, index=dates)
        returns = pd.DataFrame({"a": [0.0] + [0.001] * 252}, index=dates)
        result = zero_cost_engine.run(signals, returns)

        expected_total_ratio = result.equity.iloc[-1] / result.equity.iloc[0]
        expected_annualized = float(expected_total_ratio - 1)
        assert result.annualized_return == pytest.approx(expected_annualized, rel=1e-10)


# ---------------------------------------------------------------------------
# 输入校验测试
# ---------------------------------------------------------------------------


class TestInputValidation:
    """输入校验测试。"""

    def test_empty_signals_raises(self, engine: VectorBacktest) -> None:
        """空信号矩阵应抛出 ValueError。"""
        signals = pd.DataFrame()
        returns = pd.DataFrame({"a": [0.01]})
        with pytest.raises(ValueError, match="empty"):
            engine.run(signals, returns)

    def test_empty_returns_raises(self, engine: VectorBacktest) -> None:
        """空收益率矩阵应抛出 ValueError。"""
        signals = pd.DataFrame({"a": [0.5]})
        returns = pd.DataFrame()
        with pytest.raises(ValueError, match="empty"):
            engine.run(signals, returns)

    def test_no_common_columns_raises(self, engine: VectorBacktest) -> None:
        """无共同列时应抛出 ValueError。"""
        dates = pd.date_range("2024-01-01", periods=3, freq="B")
        signals = pd.DataFrame({"a": [0.5] * 3}, index=dates)
        returns = pd.DataFrame({"b": [0.01] * 3}, index=dates)
        with pytest.raises(ValueError, match="no common columns"):
            engine.run(signals, returns)

    def test_no_common_index_raises(self, engine: VectorBacktest) -> None:
        """无共同索引时应抛出 ValueError。"""
        dates1 = pd.date_range("2024-01-01", periods=3, freq="B")
        dates2 = pd.date_range("2025-01-01", periods=3, freq="B")
        signals = pd.DataFrame({"a": [0.5] * 3}, index=dates1)
        returns = pd.DataFrame({"a": [0.01] * 3}, index=dates2)
        with pytest.raises(ValueError, match="no common index"):
            engine.run(signals, returns)

    def test_invalid_initial_capital_raises(self) -> None:
        """非正初始资金应抛出 ValueError。"""
        with pytest.raises(ValueError, match="initial_capital"):
            VectorBacktest(initial_capital=0)
        with pytest.raises(ValueError, match="initial_capital"):
            VectorBacktest(initial_capital=-100)

    def test_negative_cost_bps_raises(self) -> None:
        """负成本应抛出 ValueError。"""
        with pytest.raises(ValueError, match="cost_bps"):
            VectorBacktest(cost_bps=-1)

    def test_non_dataframe_signals_raises(self, engine: VectorBacktest) -> None:
        """非 DataFrame 信号应抛出 TypeError。"""
        with pytest.raises(TypeError, match="signals"):
            engine.run("not a dataframe", pd.DataFrame({"a": [0.01]}))  # type: ignore

    def test_non_dataframe_returns_raises(self, engine: VectorBacktest) -> None:
        """非 DataFrame 收益率应抛出 TypeError。"""
        with pytest.raises(TypeError, match="returns"):
            engine.run(pd.DataFrame({"a": [0.5]}), "not a dataframe")  # type: ignore


# ---------------------------------------------------------------------------
# 部分重叠测试
# ---------------------------------------------------------------------------


class TestPartialOverlap:
    """信号和收益率部分重叠时的处理。"""

    def test_partial_column_overlap(self, zero_cost_engine: VectorBacktest) -> None:
        """只有部分基金重叠时，应只使用重叠部分。"""
        dates = pd.date_range("2024-01-01", periods=3, freq="B")
        signals = pd.DataFrame(
            {"a": [0.5, 0.5, 0.5], "b": [0.5, 0.5, 0.5], "c": [0.3, 0.3, 0.3]},
            index=dates,
        )
        returns = pd.DataFrame(
            {"a": [0.01, 0.01, 0.01], "b": [0.02, 0.02, 0.02]},
            index=dates,
        )
        result = zero_cost_engine.run(signals, returns)
        # 只使用 a 和 b，权重 0.5+0.5=1.0，不缩放
        # 第二天收益 = 0.5*0.01 + 0.5*0.02 = 0.015
        assert result.returns.iloc[1] == pytest.approx(0.015, abs=1e-10)

    def test_partial_index_overlap(self, zero_cost_engine: VectorBacktest) -> None:
        """只有部分日期重叠时，应只使用重叠部分。"""
        dates1 = pd.date_range("2024-01-01", periods=5, freq="B")
        dates2 = pd.date_range("2024-01-03", periods=5, freq="B")
        signals = pd.DataFrame({"a": [1.0] * 5}, index=dates1)
        returns = pd.DataFrame({"a": [0.01] * 5}, index=dates2)
        result = zero_cost_engine.run(signals, returns)
        # 重叠日期为 3 天
        assert len(result.equity) == 3


# ---------------------------------------------------------------------------
# 性能测试
# ---------------------------------------------------------------------------


@pytest.mark.slow
class TestPerformance:
    """性能测试：100 基金 10 年 < 5 秒。"""

    def test_100_funds_10_years_under_5_seconds(self) -> None:
        """100 只基金 10 年日频数据回测应在 5 秒内完成。"""
        n_funds = 100
        n_days = 252 * 10  # 10 年交易日

        dates = pd.date_range("2014-01-01", periods=n_days, freq="B")
        fund_codes = [f"fund_{i:03d}" for i in range(n_funds)]

        # 生成随机信号和收益率
        rng = np.random.default_rng(42)
        signals_data = rng.dirichlet(np.ones(n_funds), size=n_days)
        returns_data = rng.normal(0.0003, 0.015, size=(n_days, n_funds))

        signals = pd.DataFrame(signals_data, index=dates, columns=fund_codes)
        returns = pd.DataFrame(returns_data, index=dates, columns=fund_codes)

        engine = VectorBacktest(initial_capital=10_000_000, cost_bps=10)

        start_time = time.perf_counter()
        result = engine.run(signals, returns)
        elapsed = time.perf_counter() - start_time

        assert elapsed < 5.0, f"Performance test failed: took {elapsed:.2f}s (limit: 5s)"
        assert len(result.equity) == n_days
        assert result.total_return != 0.0  # 确保有实际计算

    def test_500_funds_5_years_under_5_seconds(self) -> None:
        """500 只基金 5 年日频数据回测也应在 5 秒内完成。"""
        n_funds = 500
        n_days = 252 * 5

        dates = pd.date_range("2019-01-01", periods=n_days, freq="B")
        fund_codes = [f"fund_{i:04d}" for i in range(n_funds)]

        rng = np.random.default_rng(123)
        signals_data = rng.dirichlet(np.ones(n_funds), size=n_days)
        returns_data = rng.normal(0.0002, 0.012, size=(n_days, n_funds))

        signals = pd.DataFrame(signals_data, index=dates, columns=fund_codes)
        returns = pd.DataFrame(returns_data, index=dates, columns=fund_codes)

        engine = VectorBacktest(initial_capital=10_000_000, cost_bps=15)

        start_time = time.perf_counter()
        result = engine.run(signals, returns)
        elapsed = time.perf_counter() - start_time

        assert elapsed < 5.0, f"Performance test failed: took {elapsed:.2f}s (limit: 5s)"
        assert len(result.equity) == n_days
