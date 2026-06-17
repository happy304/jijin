"""Monte Carlo 滚动回测服务单元测试。

覆盖：
- MonteCarloConfig: 配置验证
- iid_bootstrap: IID 重采样
- block_bootstrap: 区块重采样
- compute_path_metrics: 路径指标计算
- MonteCarloSimulator: 核心模拟器
- run_monte_carlo_from_equity: 便捷函数
- 边界情况：短序列、极端值、可重复性
"""

from __future__ import annotations

import numpy as np
import pytest

from app.services.monte_carlo import (
    DistributionStats,
    MonteCarloConfig,
    MonteCarloResult,
    MonteCarloSimulator,
    SimulationPath,
    block_bootstrap,
    compute_path_metrics,
    iid_bootstrap,
    run_monte_carlo_from_equity,
)


# ---------------------------------------------------------------------------
# MonteCarloConfig Tests
# ---------------------------------------------------------------------------


class TestMonteCarloConfig:
    """MonteCarloConfig 配置测试。"""

    def test_default_config(self):
        """默认配置正确。"""
        config = MonteCarloConfig()
        assert config.n_simulations == 1000
        assert config.method == "iid"
        assert config.block_size is None
        assert config.confidence_level == 0.95
        assert config.seed is None
        assert 50.0 in config.percentiles

    def test_custom_config(self):
        """自定义配置正确创建。"""
        config = MonteCarloConfig(
            n_simulations=500,
            method="block",
            block_size=10,
            confidence_level=0.99,
            seed=42,
            percentiles=[10.0, 50.0, 90.0],
        )
        assert config.n_simulations == 500
        assert config.method == "block"
        assert config.block_size == 10
        assert config.confidence_level == 0.99
        assert config.seed == 42

    def test_invalid_n_simulations(self):
        """n_simulations <= 0 时应报错。"""
        with pytest.raises(ValueError, match="n_simulations must be positive"):
            MonteCarloConfig(n_simulations=0)

    def test_invalid_method(self):
        """无效 method 应报错。"""
        with pytest.raises(ValueError, match="method must be"):
            MonteCarloConfig(method="invalid")

    def test_invalid_block_size(self):
        """block_size <= 0 时应报错。"""
        with pytest.raises(ValueError, match="block_size must be positive"):
            MonteCarloConfig(block_size=0)

    def test_invalid_confidence_level_low(self):
        """confidence_level <= 0 时应报错。"""
        with pytest.raises(ValueError, match="confidence_level must be in"):
            MonteCarloConfig(confidence_level=0.0)

    def test_invalid_confidence_level_high(self):
        """confidence_level >= 1 时应报错。"""
        with pytest.raises(ValueError, match="confidence_level must be in"):
            MonteCarloConfig(confidence_level=1.0)


# ---------------------------------------------------------------------------
# IID Bootstrap Tests
# ---------------------------------------------------------------------------


class TestIIDBootstrap:
    """IID Bootstrap 重采样测试。"""

    def test_output_shape(self):
        """输出形状正确。"""
        rng = np.random.default_rng(42)
        returns = np.array([0.01, -0.005, 0.003, 0.002, -0.001])
        result = iid_bootstrap(returns, n_simulations=100, rng=rng)
        assert result.shape == (100, 5)

    def test_values_from_original(self):
        """重采样值来自原始序列。"""
        rng = np.random.default_rng(42)
        returns = np.array([0.01, -0.02, 0.03])
        result = iid_bootstrap(returns, n_simulations=50, rng=rng)
        # 所有值应该在原始值集合中
        original_set = set(returns.tolist())
        for row in result:
            for val in row:
                assert val in original_set

    def test_reproducibility(self):
        """相同种子产生相同结果。"""
        returns = np.array([0.01, -0.005, 0.003, 0.002, -0.001])
        rng1 = np.random.default_rng(123)
        rng2 = np.random.default_rng(123)
        result1 = iid_bootstrap(returns, n_simulations=10, rng=rng1)
        result2 = iid_bootstrap(returns, n_simulations=10, rng=rng2)
        np.testing.assert_array_equal(result1, result2)

    def test_different_seeds_different_results(self):
        """不同种子产生不同结果。"""
        returns = np.array([0.01, -0.005, 0.003, 0.002, -0.001])
        rng1 = np.random.default_rng(1)
        rng2 = np.random.default_rng(2)
        result1 = iid_bootstrap(returns, n_simulations=10, rng=rng1)
        result2 = iid_bootstrap(returns, n_simulations=10, rng=rng2)
        # 极不可能完全相同
        assert not np.array_equal(result1, result2)


# ---------------------------------------------------------------------------
# Block Bootstrap Tests
# ---------------------------------------------------------------------------


class TestBlockBootstrap:
    """Block Bootstrap 区块重采样测试。"""

    def test_output_shape(self):
        """输出形状正确。"""
        rng = np.random.default_rng(42)
        returns = np.array([0.01, -0.005, 0.003, 0.002, -0.001, 0.004, 0.001, -0.003, 0.002, 0.005])
        result = block_bootstrap(returns, n_simulations=50, block_size=3, rng=rng)
        assert result.shape == (50, 10)

    def test_preserves_local_structure(self):
        """区块内保留原始序列的局部结构。"""
        rng = np.random.default_rng(42)
        # 创建一个有明显模式的序列
        returns = np.array([0.1, 0.2, 0.3, -0.1, -0.2, -0.3, 0.4, 0.5, 0.6])
        block_size = 3
        result = block_bootstrap(returns, n_simulations=100, block_size=block_size, rng=rng)

        # 检查每条路径中是否存在连续的区块
        # 原始区块: [0.1, 0.2, 0.3], [-0.1, -0.2, -0.3], [0.4, 0.5, 0.6]
        # 以及其他起始位置的区块
        possible_blocks = []
        for start in range(len(returns) - block_size + 1):
            possible_blocks.append(tuple(returns[start:start + block_size]))

        # 至少有一些路径包含可识别的区块
        found_block = False
        for row in result:
            for i in range(0, len(row) - block_size + 1, block_size):
                chunk = tuple(row[i:i + block_size])
                if chunk in possible_blocks:
                    found_block = True
                    break
            if found_block:
                break
        assert found_block

    def test_block_size_larger_than_series(self):
        """block_size > 序列长度时退化为 iid bootstrap。"""
        rng = np.random.default_rng(42)
        returns = np.array([0.01, -0.005, 0.003])
        result = block_bootstrap(returns, n_simulations=10, block_size=5, rng=rng)
        assert result.shape == (10, 3)

    def test_reproducibility(self):
        """相同种子产生相同结果。"""
        returns = np.array([0.01, -0.005, 0.003, 0.002, -0.001, 0.004])
        rng1 = np.random.default_rng(99)
        rng2 = np.random.default_rng(99)
        result1 = block_bootstrap(returns, n_simulations=20, block_size=2, rng=rng1)
        result2 = block_bootstrap(returns, n_simulations=20, block_size=2, rng=rng2)
        np.testing.assert_array_equal(result1, result2)

    def test_values_from_original(self):
        """重采样值来自原始序列。"""
        rng = np.random.default_rng(42)
        returns = np.array([0.01, -0.02, 0.03, 0.04, -0.05])
        result = block_bootstrap(returns, n_simulations=30, block_size=2, rng=rng)
        original_set = set(returns.tolist())
        for row in result:
            for val in row:
                assert val in original_set


# ---------------------------------------------------------------------------
# compute_path_metrics Tests
# ---------------------------------------------------------------------------


class TestComputePathMetrics:
    """compute_path_metrics 路径指标计算测试。"""

    def test_constant_growth(self):
        """恒定增长路径的指标计算。"""
        # 每天涨 1%，252 天
        daily_return = 0.01
        n_days = 252
        cumulative = np.cumprod(np.full(n_days, 1.0 + daily_return))
        cumulative = np.insert(cumulative, 0, 1.0)

        metrics = compute_path_metrics(cumulative)

        # 总收益约 (1.01)^252 - 1 ≈ 11.27
        expected_total = (1.01**252) - 1
        assert pytest.approx(metrics["total_return"], rel=1e-4) == expected_total

        # 年化收益约等于总收益（因为恰好 1 年）
        assert pytest.approx(metrics["annualized_return"], rel=1e-3) == expected_total

        # 最大回撤应为 0（持续上涨）
        assert metrics["max_drawdown"] == 0.0

        # 波动率应很低（恒定收益）
        assert metrics["volatility"] == pytest.approx(0.0, abs=1e-10)

    def test_constant_decline(self):
        """恒定下跌路径的指标计算。"""
        daily_return = -0.01
        n_days = 50
        cumulative = np.cumprod(np.full(n_days, 1.0 + daily_return))
        cumulative = np.insert(cumulative, 0, 1.0)

        metrics = compute_path_metrics(cumulative)

        assert metrics["total_return"] < 0
        assert metrics["max_drawdown"] < 0
        assert metrics["annualized_return"] < 0

    def test_short_series(self):
        """短序列（< 2 个点）返回零值。"""
        metrics = compute_path_metrics(np.array([1.0]))
        assert metrics["total_return"] == 0.0
        assert metrics["sharpe"] == 0.0

    def test_realistic_returns(self):
        """模拟真实收益序列的指标计算。"""
        rng = np.random.default_rng(42)
        # 模拟年化 10% 收益、15% 波动率
        daily_mean = 0.10 / 252
        daily_std = 0.15 / np.sqrt(252)
        daily_returns = rng.normal(daily_mean, daily_std, size=252)
        cumulative = np.cumprod(1.0 + daily_returns)
        cumulative = np.insert(cumulative, 0, 1.0)

        metrics = compute_path_metrics(cumulative)

        # 基本合理性检查
        assert -1.0 < metrics["total_return"] < 5.0
        assert metrics["volatility"] > 0
        assert metrics["max_drawdown"] <= 0

    def test_max_drawdown_calculation(self):
        """最大回撤计算正确性。"""
        # 构造已知回撤的序列：1.0 -> 1.5 -> 1.0 -> 1.2
        cumulative = np.array([1.0, 1.2, 1.5, 1.2, 1.0, 1.1, 1.2])
        metrics = compute_path_metrics(cumulative)

        # 最大回撤从 1.5 跌到 1.0 = -33.3%
        expected_dd = (1.0 - 1.5) / 1.5
        assert pytest.approx(metrics["max_drawdown"], rel=1e-4) == expected_dd


# ---------------------------------------------------------------------------
# MonteCarloSimulator Tests
# ---------------------------------------------------------------------------


class TestMonteCarloSimulator:
    """MonteCarloSimulator 核心模拟器测试。"""

    def _make_returns(self, n_days: int = 252, seed: int = 42) -> np.ndarray:
        """生成测试用收益序列。"""
        rng = np.random.default_rng(seed)
        daily_mean = 0.08 / 252
        daily_std = 0.12 / np.sqrt(252)
        return rng.normal(daily_mean, daily_std, size=n_days)

    def test_iid_simulation(self):
        """IID 方法模拟正常运行。"""
        returns = self._make_returns()
        config = MonteCarloConfig(
            n_simulations=100, method="iid", seed=42
        )
        simulator = MonteCarloSimulator(returns=returns, config=config)
        result = simulator.run()

        assert result.n_simulations == 100
        assert result.n_original_returns == 252
        assert len(result.all_metrics) == 100
        assert "sharpe" in result.metric_distributions
        assert "total_return" in result.metric_distributions

    def test_block_simulation(self):
        """Block 方法模拟正常运行。"""
        returns = self._make_returns()
        config = MonteCarloConfig(
            n_simulations=100, method="block", block_size=20, seed=42
        )
        simulator = MonteCarloSimulator(returns=returns, config=config)
        result = simulator.run()

        assert result.n_simulations == 100
        assert result.config.method == "block"
        assert len(result.all_metrics) == 100

    def test_block_auto_size(self):
        """Block 方法自动计算区块大小。"""
        returns = self._make_returns(n_days=100)
        config = MonteCarloConfig(
            n_simulations=50, method="block", block_size=None, seed=42
        )
        simulator = MonteCarloSimulator(returns=returns, config=config)
        result = simulator.run()

        # 自动 block_size = sqrt(100) = 10
        assert result.n_simulations == 50

    def test_distribution_stats_structure(self):
        """分布统计结构完整。"""
        returns = self._make_returns()
        config = MonteCarloConfig(
            n_simulations=200, method="iid", seed=42,
            percentiles=[5.0, 25.0, 50.0, 75.0, 95.0],
        )
        simulator = MonteCarloSimulator(returns=returns, config=config)
        result = simulator.run()

        for name, dist in result.metric_distributions.items():
            assert isinstance(dist, DistributionStats)
            assert isinstance(dist.mean, float)
            assert isinstance(dist.std, float)
            assert isinstance(dist.median, float)
            assert dist.ci_lower <= dist.ci_upper
            assert dist.min_value <= dist.max_value
            assert 5.0 in dist.percentiles
            assert 50.0 in dist.percentiles
            assert 95.0 in dist.percentiles

    def test_equity_percentiles(self):
        """权益曲线百分位数正确生成。"""
        returns = self._make_returns()
        config = MonteCarloConfig(
            n_simulations=100, method="iid", seed=42,
            percentiles=[10.0, 50.0, 90.0],
        )
        simulator = MonteCarloSimulator(returns=returns, config=config)
        result = simulator.run()

        assert 10.0 in result.equity_percentiles
        assert 50.0 in result.equity_percentiles
        assert 90.0 in result.equity_percentiles

        # 每条百分位曲线长度 = n_days + 1（含初始值）
        for p, curve in result.equity_percentiles.items():
            assert len(curve) == 253  # 252 + 1

        # 百分位数应有序：p10 <= p50 <= p90（在每个时间点）
        p10 = result.equity_percentiles[10.0]
        p50 = result.equity_percentiles[50.0]
        p90 = result.equity_percentiles[90.0]
        # 检查最后一个时间点
        assert p10[-1] <= p50[-1] <= p90[-1]

    def test_reproducibility(self):
        """相同种子产生相同结果。"""
        returns = self._make_returns()
        config = MonteCarloConfig(n_simulations=50, method="iid", seed=77)

        result1 = MonteCarloSimulator(returns=returns, config=config).run()
        result2 = MonteCarloSimulator(returns=returns, config=config).run()

        # 所有指标应完全相同
        for i in range(50):
            assert result1.all_metrics[i] == result2.all_metrics[i]

    def test_short_returns_raises(self):
        """收益序列过短时应报错。"""
        with pytest.raises(ValueError, match="at least 2 elements"):
            MonteCarloSimulator(returns=np.array([0.01]))

    def test_confidence_interval_coverage(self):
        """置信区间合理性检查。"""
        returns = self._make_returns(n_days=252)
        config = MonteCarloConfig(
            n_simulations=500, method="iid", seed=42,
            confidence_level=0.90,
        )
        simulator = MonteCarloSimulator(returns=returns, config=config)
        result = simulator.run()

        sharpe_dist = result.metric_distributions["sharpe"]
        # CI 下界应小于均值，上界应大于均值
        assert sharpe_dist.ci_lower <= sharpe_dist.mean
        assert sharpe_dist.ci_upper >= sharpe_dist.mean

    def test_many_simulations_mean_converges(self):
        """大量模拟时均值应接近原始序列的指标。"""
        rng = np.random.default_rng(42)
        # 生成有明确正收益的序列
        returns = rng.normal(0.0005, 0.01, size=252)

        config = MonteCarloConfig(
            n_simulations=2000, method="iid", seed=42
        )
        simulator = MonteCarloSimulator(returns=returns, config=config)
        result = simulator.run()

        # 原始序列的总收益
        original_total = float(np.prod(1.0 + returns) - 1.0)

        # 模拟均值应接近原始值（IID bootstrap 的期望等于原始均值）
        sim_mean = result.metric_distributions["total_return"].mean
        # 允许较大容差，因为 bootstrap 有方差
        assert abs(sim_mean - original_total) < abs(original_total) * 0.5 + 0.05


# ---------------------------------------------------------------------------
# run_monte_carlo_from_equity Tests
# ---------------------------------------------------------------------------


class TestRunMonteCarloFromEquity:
    """run_monte_carlo_from_equity 便捷函数测试。"""

    def test_basic_usage(self):
        """基本使用正常。"""
        # 模拟一条简单的权益曲线
        equity = [100000.0 + i * 100 for i in range(100)]
        config = MonteCarloConfig(n_simulations=50, method="iid", seed=42)
        result = run_monte_carlo_from_equity(equity, config=config)

        assert result.n_simulations == 50
        assert result.n_original_returns == 99  # 100 点 -> 99 个收益率
        assert "sharpe" in result.metric_distributions

    def test_short_equity_raises(self):
        """权益曲线过短时应报错。"""
        with pytest.raises(ValueError, match="at least 3 points"):
            run_monte_carlo_from_equity([100000.0, 100100.0])

    def test_non_positive_equity_raises(self):
        """权益曲线包含非正值时应报错。"""
        with pytest.raises(ValueError, match="only positive values"):
            run_monte_carlo_from_equity([100000.0, 0.0, 100100.0])

    def test_negative_equity_raises(self):
        """权益曲线包含负值时应报错。"""
        with pytest.raises(ValueError, match="only positive values"):
            run_monte_carlo_from_equity([100000.0, -1000.0, 100100.0])

    def test_default_config(self):
        """不传 config 时使用默认配置。"""
        equity = [100000.0 + i * 50 for i in range(50)]
        result = run_monte_carlo_from_equity(equity)
        assert result.config.n_simulations == 1000
        assert result.config.method == "iid"

    def test_block_method_from_equity(self):
        """从权益曲线使用 block 方法。"""
        rng = np.random.default_rng(42)
        equity = [100000.0]
        for _ in range(99):
            equity.append(equity[-1] * (1 + rng.normal(0.0003, 0.01)))

        config = MonteCarloConfig(
            n_simulations=50, method="block", block_size=5, seed=42
        )
        result = run_monte_carlo_from_equity(equity, config=config)

        assert result.n_simulations == 50
        assert result.config.method == "block"
