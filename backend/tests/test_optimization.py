"""参数优化服务单元测试。

覆盖：
- ParamSpace: 网格点生成、Sobol 采样
- GridSearchOptimizer: 网格搜索优化
- SobolSearchOptimizer: Sobol 随机搜索优化
- ParallelOptimizer: 本地串行模式（不依赖 Celery）
- 边界情况：空参数空间、单维度、失败试验处理
"""

from __future__ import annotations

import math
from datetime import date
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.services.optimization import (
    BaselineComparisonConfig,
    GridSearchOptimizer,
    MultiObjectiveConfig,
    OptimizationResult,
    OptimizationTrial,
    ParallelOptimizer,
    ParamDimension,
    ParamSpace,
    ParamType,
    SobolSearchOptimizer,
    compare_against_baselines,
    compute_multi_objective_score,
    create_optimizer,
)




# ---------------------------------------------------------------------------
# Multi-objective scoring Tests
# ---------------------------------------------------------------------------


def test_compute_multi_objective_score_rewards_oos_and_penalizes_overfit():
    good = compute_multi_objective_score({
        "total_return": 0.12,
        "sharpe": 1.2,
        "win_rate": 0.62,
        "max_drawdown": -0.08,
        "turnover": 1.0,
        "fee_drag": 0.005,
        "pbo": 0.25,
        "ic_degradation": 0.8,
        "sample_count": 120,
    })
    bad = compute_multi_objective_score({
        "total_return": -0.08,
        "sharpe": -0.4,
        "win_rate": 0.42,
        "max_drawdown": -0.42,
        "turnover": 5.0,
        "fee_drag": 0.05,
        "pbo": 0.75,
        "ic_degradation": 0.2,
        "sample_count": 12,
    })

    assert good.score > bad.score
    assert good.eliminated is False
    assert bad.eliminated is True
    assert bad.components["overfit_penalty"] > good.components["overfit_penalty"]
    assert any("PBO" in reason or "最大回撤" in reason for reason in bad.reasons)


def test_grid_search_can_rank_by_multi_objective_score():
    space = ParamSpace(dimensions=[ParamDimension("n", ParamType.DISCRETE, low=1, high=3, step=1)])

    def runner(params: dict[str, Any]) -> dict[str, float]:
        n = params["n"]
        if n == 1:
            return {"total_return": 0.50, "sharpe": 0.2, "max_drawdown": -0.6, "pbo": 0.8, "sample_count": 120}
        if n == 2:
            return {"total_return": 0.12, "sharpe": 1.1, "max_drawdown": -0.08, "pbo": 0.25, "win_rate": 0.62, "sample_count": 120}
        return {"total_return": 0.03, "sharpe": 0.4, "max_drawdown": -0.05, "pbo": 0.2, "sample_count": 120}

    result = GridSearchOptimizer(space, objective="multi_objective_score").optimize(runner)

    assert result.best_params == {"n": 2}
    assert result.trials[0].metrics["multi_objective_score"] == result.best_metric
    assert any(trial.metrics.get("multi_objective_eliminated") for trial in result.trials)


def test_create_optimizer_accepts_multi_objective_config():
    space = ParamSpace(dimensions=[ParamDimension("n", ParamType.DISCRETE, low=1, high=2, step=1)])
    cfg = MultiObjectiveConfig(max_drawdown_limit=0.05)
    opt = create_optimizer(
        space,
        method="grid",
        objective="multi_objective",
        multi_objective_config=cfg,
    )

    assert isinstance(opt, GridSearchOptimizer)
    assert opt.multi_objective_config is cfg


def test_baseline_comparison_penalizes_candidate_below_simple_models():
    candidate = {
        "total_return": 0.08,
        "sharpe": 0.7,
        "max_drawdown": -0.12,
        "sample_count": 120,
    }
    baselines = {
        "dca": {"total_return": 0.05, "sharpe": 0.5, "max_drawdown": -0.10, "sample_count": 120},
        "risk_parity": {"total_return": 0.10, "sharpe": 0.9, "max_drawdown": -0.08, "sample_count": 120},
        "simple_momentum": {"total_return": 0.06, "sharpe": 0.6, "max_drawdown": -0.09, "sample_count": 120},
    }

    result = compare_against_baselines(candidate, baselines)

    assert result.passed is False
    assert result.best_baseline is not None
    assert result.best_baseline.name == "risk_parity"
    assert result.adjusted_score < candidate.get("multi_objective_score", 1)
    assert any("baseline" in reason for reason in result.reasons)


def test_grid_search_can_rank_by_baseline_adjusted_score():
    space = ParamSpace(dimensions=[ParamDimension("n", ParamType.DISCRETE, low=1, high=2, step=1)])
    baselines = {
        "dca": {"total_return": 0.08, "sharpe": 0.75, "max_drawdown": -0.10, "sample_count": 120},
        "risk_parity": {"total_return": 0.07, "sharpe": 0.7, "max_drawdown": -0.09, "sample_count": 120},
        "simple_momentum": {"total_return": 0.06, "sharpe": 0.65, "max_drawdown": -0.08, "sample_count": 120},
    }

    def runner(params: dict[str, Any]) -> dict[str, float]:
        if params["n"] == 1:
            return {"total_return": 0.09, "sharpe": 0.8, "max_drawdown": -0.11, "sample_count": 120}
        return {"total_return": 0.14, "sharpe": 1.2, "max_drawdown": -0.07, "sample_count": 120}

    opt = GridSearchOptimizer(
        space,
        objective="baseline_adjusted_score",
        baseline_metrics=baselines,
        baseline_config=BaselineComparisonConfig(min_multi_objective_uplift=0.01),
    )
    result = opt.optimize(runner)

    assert result.best_params == {"n": 2}
    assert result.trials[0].metrics["baseline_passed"] is True
    assert "baseline_adjusted_score" in result.trials[0].metrics


# ---------------------------------------------------------------------------
# ParamDimension Tests
# ---------------------------------------------------------------------------


class TestParamDimension:
    """ParamDimension 参数维度定义测试。"""

    def test_continuous_dimension(self):
        """连续参数维度正常创建。"""
        dim = ParamDimension("weight", ParamType.CONTINUOUS, low=0.0, high=1.0, step=0.1)
        assert dim.name == "weight"
        assert dim.param_type == ParamType.CONTINUOUS
        assert dim.low == 0.0
        assert dim.high == 1.0

    def test_discrete_dimension(self):
        """离散参数维度正常创建。"""
        dim = ParamDimension("lookback", ParamType.DISCRETE, low=3, high=12, step=3)
        assert dim.name == "lookback"
        assert dim.param_type == ParamType.DISCRETE

    def test_categorical_dimension(self):
        """分类参数维度正常创建。"""
        dim = ParamDimension(
            "freq", ParamType.CATEGORICAL, choices=["weekly", "monthly"]
        )
        assert dim.name == "freq"
        assert dim.choices == ["weekly", "monthly"]

    def test_continuous_requires_bounds(self):
        """连续参数缺少边界时应报错。"""
        with pytest.raises(ValueError, match="require low and high"):
            ParamDimension("x", ParamType.CONTINUOUS, low=None, high=1.0)

    def test_discrete_requires_bounds(self):
        """离散参数缺少边界时应报错。"""
        with pytest.raises(ValueError, match="require low and high"):
            ParamDimension("x", ParamType.DISCRETE, low=1, high=None)

    def test_low_greater_than_high_raises(self):
        """low > high 时应报错。"""
        with pytest.raises(ValueError, match="must be <= high"):
            ParamDimension("x", ParamType.CONTINUOUS, low=10.0, high=5.0)

    def test_categorical_requires_choices(self):
        """分类参数缺少 choices 时应报错。"""
        with pytest.raises(ValueError, match="requires non-empty choices"):
            ParamDimension("x", ParamType.CATEGORICAL, choices=[])


# ---------------------------------------------------------------------------
# ParamSpace Tests
# ---------------------------------------------------------------------------


class TestParamSpace:
    """ParamSpace 参数空间测试。"""

    def test_grid_points_discrete(self):
        """离散参数网格点生成。"""
        space = ParamSpace(
            dimensions=[
                ParamDimension("n", ParamType.DISCRETE, low=1, high=3, step=1),
            ]
        )
        points = space.grid_points()
        assert len(points) == 3
        values = [p["n"] for p in points]
        assert 1 in values
        assert 2 in values
        assert 3 in values

    def test_grid_points_continuous(self):
        """连续参数网格点生成。"""
        space = ParamSpace(
            dimensions=[
                ParamDimension("w", ParamType.CONTINUOUS, low=0.0, high=1.0, step=0.5),
            ]
        )
        points = space.grid_points()
        values = [p["w"] for p in points]
        assert len(values) == 3  # 0.0, 0.5, 1.0
        assert pytest.approx(values[0], abs=1e-9) == 0.0
        assert pytest.approx(values[1], abs=1e-9) == 0.5
        assert pytest.approx(values[2], abs=1e-9) == 1.0

    def test_grid_points_categorical(self):
        """分类参数网格点生成。"""
        space = ParamSpace(
            dimensions=[
                ParamDimension("freq", ParamType.CATEGORICAL, choices=["w", "m"]),
            ]
        )
        points = space.grid_points()
        assert len(points) == 2
        values = [p["freq"] for p in points]
        assert "w" in values
        assert "m" in values

    def test_grid_points_multi_dimension(self):
        """多维参数笛卡尔积。"""
        space = ParamSpace(
            dimensions=[
                ParamDimension("a", ParamType.DISCRETE, low=1, high=2, step=1),
                ParamDimension("b", ParamType.CATEGORICAL, choices=["x", "y"]),
            ]
        )
        points = space.grid_points()
        # 2 * 2 = 4 combinations
        assert len(points) == 4

    def test_grid_points_empty_space(self):
        """空参数空间返回单个空字典。"""
        space = ParamSpace(dimensions=[])
        points = space.grid_points()
        assert points == [{}]

    def test_sobol_sample_count(self):
        """Sobol 采样返回正确数量的点。"""
        space = ParamSpace(
            dimensions=[
                ParamDimension("x", ParamType.CONTINUOUS, low=0.0, high=10.0),
                ParamDimension("y", ParamType.CONTINUOUS, low=-1.0, high=1.0),
            ]
        )
        points = space.sobol_sample(16, seed=42)
        assert len(points) == 16

    def test_sobol_sample_bounds(self):
        """Sobol 采样值在参数边界内。"""
        space = ParamSpace(
            dimensions=[
                ParamDimension("x", ParamType.CONTINUOUS, low=2.0, high=8.0),
                ParamDimension("n", ParamType.DISCRETE, low=1, high=10),
            ]
        )
        points = space.sobol_sample(32, seed=123)
        for p in points:
            assert 2.0 <= p["x"] <= 8.0
            assert 1 <= p["n"] <= 10
            assert isinstance(p["n"], int)

    def test_sobol_sample_categorical(self):
        """Sobol 采样正确处理分类参数。"""
        space = ParamSpace(
            dimensions=[
                ParamDimension("mode", ParamType.CATEGORICAL, choices=["a", "b", "c"]),
            ]
        )
        points = space.sobol_sample(8, seed=0)
        for p in points:
            assert p["mode"] in ["a", "b", "c"]

    def test_sobol_reproducibility(self):
        """相同种子产生相同结果。"""
        space = ParamSpace(
            dimensions=[
                ParamDimension("x", ParamType.CONTINUOUS, low=0.0, high=1.0),
            ]
        )
        points1 = space.sobol_sample(8, seed=99)
        points2 = space.sobol_sample(8, seed=99)
        assert points1 == points2

    def test_sobol_empty_space(self):
        """空参数空间 Sobol 采样。"""
        space = ParamSpace(dimensions=[])
        points = space.sobol_sample(4)
        assert len(points) == 4
        for p in points:
            assert p == {}


# ---------------------------------------------------------------------------
# GridSearchOptimizer Tests
# ---------------------------------------------------------------------------


def _mock_runner(params: dict[str, Any]) -> dict[str, float]:
    """模拟回测运行器：sharpe = n * 0.5。"""
    n = params.get("n", 1)
    return {
        "sharpe": n * 0.5,
        "total_return": n * 0.1,
        "max_drawdown": -0.05 * n,
    }


class TestGridSearchOptimizer:
    """GridSearchOptimizer 网格搜索测试。"""

    def test_basic_optimization(self):
        """基本网格搜索找到最优参数。"""
        space = ParamSpace(
            dimensions=[
                ParamDimension("n", ParamType.DISCRETE, low=1, high=5, step=1),
            ]
        )
        optimizer = GridSearchOptimizer(param_space=space, objective="sharpe")
        result = optimizer.optimize(_mock_runner)

        assert result.best_params == {"n": 5}
        assert result.best_metric == 2.5
        assert result.total_trials == 5
        assert result.method == "grid"
        assert result.objective == "sharpe"

    def test_minimize_objective(self):
        """最小化目标时找到最小值。"""
        space = ParamSpace(
            dimensions=[
                ParamDimension("n", ParamType.DISCRETE, low=1, high=5, step=1),
            ]
        )
        optimizer = GridSearchOptimizer(
            param_space=space, objective="max_drawdown", maximize=False
        )
        result = optimizer.optimize(_mock_runner)

        # max_drawdown = -0.05 * n, minimize means most negative = n=5
        assert result.best_params == {"n": 5}
        assert result.best_metric == -0.25

    def test_trials_sorted_descending(self):
        """试验结果按目标指标降序排列。"""
        space = ParamSpace(
            dimensions=[
                ParamDimension("n", ParamType.DISCRETE, low=1, high=3, step=1),
            ]
        )
        optimizer = GridSearchOptimizer(param_space=space, objective="sharpe")
        result = optimizer.optimize(_mock_runner)

        metric_values = [t.metric_value for t in result.trials]
        assert metric_values == sorted(metric_values, reverse=True)

    def test_failed_trial_handling(self):
        """失败的试验不影响整体优化。"""

        def failing_runner(params: dict[str, Any]) -> dict[str, float]:
            if params["n"] == 2:
                raise RuntimeError("Backtest failed")
            return {"sharpe": params["n"] * 0.5}

        space = ParamSpace(
            dimensions=[
                ParamDimension("n", ParamType.DISCRETE, low=1, high=3, step=1),
            ]
        )
        optimizer = GridSearchOptimizer(param_space=space, objective="sharpe")
        result = optimizer.optimize(failing_runner)

        assert result.total_trials == 3
        assert result.best_params == {"n": 3}
        assert result.best_metric == 1.5

    def test_multi_dimension_grid(self):
        """多维网格搜索。"""

        def runner(params: dict[str, Any]) -> dict[str, float]:
            return {"sharpe": params["a"] + params["b"]}

        space = ParamSpace(
            dimensions=[
                ParamDimension("a", ParamType.DISCRETE, low=1, high=2, step=1),
                ParamDimension("b", ParamType.DISCRETE, low=10, high=20, step=10),
            ]
        )
        optimizer = GridSearchOptimizer(param_space=space, objective="sharpe")
        result = optimizer.optimize(runner)

        assert result.best_params == {"a": 2, "b": 20}
        assert result.best_metric == 22
        assert result.total_trials == 4


# ---------------------------------------------------------------------------
# SobolSearchOptimizer Tests
# ---------------------------------------------------------------------------


class TestSobolSearchOptimizer:
    """SobolSearchOptimizer Sobol 搜索测试。"""

    def test_basic_sobol_optimization(self):
        """Sobol 搜索能找到较优参数。"""

        def runner(params: dict[str, Any]) -> dict[str, float]:
            # Sharpe peaks at x=5
            x = params["x"]
            return {"sharpe": -(x - 5) ** 2 + 25}

        space = ParamSpace(
            dimensions=[
                ParamDimension("x", ParamType.CONTINUOUS, low=0.0, high=10.0),
            ]
        )
        optimizer = SobolSearchOptimizer(
            param_space=space, n_samples=32, objective="sharpe", seed=42
        )
        result = optimizer.optimize(runner)

        # With 32 Sobol samples in [0, 10], should find something close to x=5
        assert result.best_metric > 20  # Close to peak of 25
        assert result.method == "sobol"
        assert result.total_trials == 32

    def test_sobol_with_discrete_params(self):
        """Sobol 搜索处理离散参数。"""

        def runner(params: dict[str, Any]) -> dict[str, float]:
            return {"sharpe": float(params["n"])}

        space = ParamSpace(
            dimensions=[
                ParamDimension("n", ParamType.DISCRETE, low=1, high=10),
            ]
        )
        optimizer = SobolSearchOptimizer(
            param_space=space, n_samples=16, objective="sharpe", seed=0
        )
        result = optimizer.optimize(runner)

        assert result.best_params["n"] >= 1
        assert result.best_params["n"] <= 10
        assert isinstance(result.best_params["n"], int)

    def test_sobol_reproducibility(self):
        """相同种子产生相同优化结果。"""

        def runner(params: dict[str, Any]) -> dict[str, float]:
            return {"sharpe": params["x"] * 2}

        space = ParamSpace(
            dimensions=[
                ParamDimension("x", ParamType.CONTINUOUS, low=0.0, high=1.0),
            ]
        )

        opt1 = SobolSearchOptimizer(
            param_space=space, n_samples=8, objective="sharpe", seed=77
        )
        opt2 = SobolSearchOptimizer(
            param_space=space, n_samples=8, objective="sharpe", seed=77
        )

        r1 = opt1.optimize(runner)
        r2 = opt2.optimize(runner)

        assert r1.best_params == r2.best_params
        assert r1.best_metric == r2.best_metric

    def test_sobol_failed_trials(self):
        """Sobol 搜索处理失败试验。"""
        call_count = 0

        def flaky_runner(params: dict[str, Any]) -> dict[str, float]:
            nonlocal call_count
            call_count += 1
            if call_count % 3 == 0:
                raise RuntimeError("Random failure")
            return {"sharpe": params["x"]}

        space = ParamSpace(
            dimensions=[
                ParamDimension("x", ParamType.CONTINUOUS, low=0.0, high=10.0),
            ]
        )
        optimizer = SobolSearchOptimizer(
            param_space=space, n_samples=8, objective="sharpe", seed=42
        )
        result = optimizer.optimize(flaky_runner)

        # Should still produce a result despite some failures
        assert result.total_trials == 8
        assert result.best_metric > 0


# ---------------------------------------------------------------------------
# ParallelOptimizer Tests (local mode)
# ---------------------------------------------------------------------------


class TestParallelOptimizer:
    """ParallelOptimizer 并行优化器测试（本地模式）。"""

    def _make_nav_data(self) -> dict[str, dict[date, Decimal]]:
        """生成简单的测试净值数据。"""
        from app.domain.backtest.calendar import trading_days

        dates = trading_days(date(2024, 1, 2), date(2024, 3, 29))
        nav_data: dict[str, dict[date, Decimal]] = {}

        # Generate simple uptrend NAV for one fund
        nav_data["000001"] = {}
        base_nav = Decimal("1.0000")
        for i, d in enumerate(dates):
            nav_data["000001"][d] = base_nav + Decimal(str(i * 0.001))

        return nav_data

    def test_local_grid_optimization(self):
        """本地网格搜索优化（不使用 Celery）。"""
        nav_data = self._make_nav_data()

        space = ParamSpace(
            dimensions=[
                ParamDimension("lookback", ParamType.DISCRETE, low=5, high=10, step=5),
            ]
        )

        optimizer = ParallelOptimizer(
            param_space=space,
            strategy_class_path="app.domain.strategy.dca.FixedAmountDCA",
            nav_data=nav_data,
            start_date=date(2024, 1, 2),
            end_date=date(2024, 3, 29),
            initial_capital=Decimal("100000"),
            objective="sharpe",
            method="grid",
        )

        result = optimizer.optimize_local()

        assert result.total_trials == 2
        assert result.method == "grid"
        assert result.objective == "sharpe"
        # Both trials should have valid metrics
        for trial in result.trials:
            assert "sharpe" in trial.metrics or trial.metric_value == float("-inf")

    def test_local_sobol_optimization(self):
        """本地 Sobol 搜索优化。"""
        nav_data = self._make_nav_data()

        space = ParamSpace(
            dimensions=[
                ParamDimension("lookback", ParamType.DISCRETE, low=3, high=20),
            ]
        )

        optimizer = ParallelOptimizer(
            param_space=space,
            strategy_class_path="app.domain.strategy.dca.FixedAmountDCA",
            nav_data=nav_data,
            start_date=date(2024, 1, 2),
            end_date=date(2024, 3, 29),
            initial_capital=Decimal("100000"),
            objective="total_return",
            method="sobol",
            n_samples=4,
            seed=42,
        )

        result = optimizer.optimize_local()

        assert result.total_trials == 4
        assert result.method == "sobol"

    @patch("app.tasks.celery_app.celery_app")
    def test_parallel_dispatches_tasks(self, mock_celery):
        """并行模式正确分发 Celery 任务。"""
        nav_data = self._make_nav_data()

        # Mock async result
        mock_result = MagicMock()
        mock_result.get.return_value = {
            "params": {"lookback": 5},
            "metrics": {"sharpe": 1.5, "total_return": 0.1},
            "objective_value": 1.5,
        }
        mock_celery.send_task.return_value = mock_result

        space = ParamSpace(
            dimensions=[
                ParamDimension("lookback", ParamType.DISCRETE, low=5, high=10, step=5),
            ]
        )

        optimizer = ParallelOptimizer(
            param_space=space,
            strategy_class_path="app.domain.strategy.dca.FixedAmountDCA",
            nav_data=nav_data,
            start_date=date(2024, 1, 2),
            end_date=date(2024, 3, 29),
            initial_capital=Decimal("100000"),
            objective="sharpe",
            method="grid",
        )

        result = optimizer.optimize()

        # Should have dispatched 2 tasks (grid: lookback=5, lookback=10)
        assert mock_celery.send_task.call_count == 2
        assert result.total_trials == 2

        # Verify task was sent to backtest queue
        call_kwargs = mock_celery.send_task.call_args_list[0]
        assert call_kwargs[1]["queue"] == "backtest"

    @patch("app.tasks.celery_app.celery_app")
    def test_parallel_handles_task_failure(self, mock_celery):
        """并行模式处理任务失败。"""
        nav_data = self._make_nav_data()

        mock_result = MagicMock()
        mock_result.get.side_effect = TimeoutError("Task timed out")
        mock_celery.send_task.return_value = mock_result

        space = ParamSpace(
            dimensions=[
                ParamDimension("n", ParamType.DISCRETE, low=1, high=2, step=1),
            ]
        )

        optimizer = ParallelOptimizer(
            param_space=space,
            strategy_class_path="app.domain.strategy.dca.FixedAmountDCA",
            nav_data=nav_data,
            start_date=date(2024, 1, 2),
            end_date=date(2024, 3, 29),
            initial_capital=Decimal("100000"),
            objective="sharpe",
            method="grid",
        )

        result = optimizer.optimize()

        # All trials should be recorded as failed
        assert result.total_trials == 2
        for trial in result.trials:
            assert trial.metric_value == float("-inf")


# ---------------------------------------------------------------------------
# Factory Function Tests
# ---------------------------------------------------------------------------


class TestCreateOptimizer:
    """create_optimizer 工厂函数测试。"""

    def test_create_grid_optimizer(self):
        """创建网格搜索优化器。"""
        space = ParamSpace(
            dimensions=[ParamDimension("x", ParamType.DISCRETE, low=1, high=5, step=1)]
        )
        opt = create_optimizer(space, method="grid", objective="sharpe")
        assert isinstance(opt, GridSearchOptimizer)
        assert opt.objective == "sharpe"
        assert opt.maximize is True

    def test_create_sobol_optimizer(self):
        """创建 Sobol 搜索优化器。"""
        space = ParamSpace(
            dimensions=[ParamDimension("x", ParamType.CONTINUOUS, low=0.0, high=1.0)]
        )
        opt = create_optimizer(
            space, method="sobol", objective="calmar", n_samples=32, seed=42
        )
        assert isinstance(opt, SobolSearchOptimizer)
        assert opt.objective == "calmar"
        assert opt.n_samples == 32
        assert opt.seed == 42


# ---------------------------------------------------------------------------
# OptimizationResult Tests
# ---------------------------------------------------------------------------


class TestOptimizationResult:
    """OptimizationResult 结果模型测试。"""

    def test_result_structure(self):
        """结果结构完整性。"""
        result = OptimizationResult(
            best_params={"n": 5},
            best_metric=2.5,
            trials=[
                OptimizationTrial(params={"n": 5}, metric_value=2.5),
                OptimizationTrial(params={"n": 3}, metric_value=1.5),
            ],
            objective="sharpe",
            method="grid",
            total_trials=2,
        )
        assert result.best_params == {"n": 5}
        assert result.best_metric == 2.5
        assert len(result.trials) == 2
        assert result.objective == "sharpe"
        assert result.method == "grid"
        assert result.total_trials == 2
