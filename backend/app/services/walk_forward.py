"""Walk-forward 分析服务模块。

实现滚动训练/测试窗口的 Walk-forward 分析：
- WalkForwardAnalyzer: 核心分析器，按滚动窗口执行训练+测试
- WalkForwardConfig: 分析配置（窗口大小、步长等）
- WalkForwardResult: 分析结果，聚合样本外指标

设计要点：
- 将时间序列划分为多个滚动窗口（训练窗口 + 测试窗口）
- 在每个训练窗口上执行参数优化，找到最优参数
- 用最优参数在紧随其后的测试窗口上运行回测
- 聚合所有测试窗口的样本外指标，评估策略稳健性

需求: 5.8
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Callable

from app.domain.backtest.calendar import trading_days
from app.domain.backtest.engine_event import (
    BacktestResult,
    DividendInfo,
    EventDrivenEngine,
    FundMeta,
)
from app.services.optimization import (
    BacktestRunner,
    BaselineComparisonConfig,
    GridSearchOptimizer,
    MultiObjectiveConfig,
    OptimizationResult,
    ParamSpace,
    SobolSearchOptimizer,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class WalkForwardConfig:
    """Walk-forward 分析配置。

    Attributes:
        train_days: 训练窗口交易日数量
        test_days: 测试窗口交易日数量
        step_days: 窗口滚动步长（交易日数量），默认等于 test_days
        purge_days: 训练窗口结尾需要清除的交易日数（防止 label 与 test 重叠的 look-ahead）
        embargo_days: 训练窗口结束到测试窗口起始之间留出的 gap（消除自相关泄漏）
        objective: 优化目标指标名称
        maximize: 是否最大化目标
        method: 优化方法 (grid/sobol)
        n_samples: Sobol 采样数量（仅 sobol 方法使用）
        seed: 随机种子

    Purge & Embargo
    ---------------
    源自 Lopez de Prado《Advances in Financial Machine Learning》:
    - **Purge**: 当 label 由未来 N 天收益构成（如 5 日动量、20 日 vol target），
      训练集尾部最后 N 个观测的 label 时间区间会和测试集起始重叠，必须清除。
      ``purge_days`` 应当 ≥ 用于构造 label 的最长 forward 窗口。
    - **Embargo**: 即使 label 不重叠，金融序列的自相关也会让训练集末尾的特征
      与测试集起始的 label 之间存在信息泄漏。在 train 和 test 之间留 K 天 gap。
      经验值 5–10 个交易日。

    数学等价于：实际训练区间 = [offset, offset + train_days - purge_days)
                测试区间起点 = offset + train_days + embargo_days
    """

    train_days: int = 252  # 约 1 年交易日
    test_days: int = 63  # 约 1 季度交易日
    step_days: int | None = None  # 默认等于 test_days（无重叠）
    purge_days: int = 0  # 默认不清除（向后兼容）
    embargo_days: int = 0  # 默认无 gap（向后兼容）
    objective: str = "multi_objective_score"
    maximize: bool = True
    method: str = "grid"  # grid 或 sobol
    n_samples: int = 64
    seed: int | None = None
    multi_objective_config: MultiObjectiveConfig | None = None
    baseline_metrics: dict[str, dict[str, Any]] | None = None
    baseline_config: BaselineComparisonConfig | None = None

    def __post_init__(self) -> None:
        if self.step_days is None:
            self.step_days = self.test_days
        if self.train_days <= 0:
            raise ValueError(f"train_days must be positive, got {self.train_days}")
        if self.test_days <= 0:
            raise ValueError(f"test_days must be positive, got {self.test_days}")
        if self.step_days <= 0:
            raise ValueError(f"step_days must be positive, got {self.step_days}")
        if self.purge_days < 0:
            raise ValueError(f"purge_days must be non-negative, got {self.purge_days}")
        if self.embargo_days < 0:
            raise ValueError(
                f"embargo_days must be non-negative, got {self.embargo_days}"
            )
        if self.purge_days >= self.train_days:
            raise ValueError(
                f"purge_days ({self.purge_days}) must be less than "
                f"train_days ({self.train_days})"
            )


# ---------------------------------------------------------------------------
# Window
# ---------------------------------------------------------------------------


@dataclass
class WalkForwardWindow:
    """单个 Walk-forward 窗口。

    Attributes:
        window_index: 窗口序号（从 0 开始）
        train_start: 训练窗口起始日期
        train_end: 训练窗口结束日期
        test_start: 测试窗口起始日期
        test_end: 测试窗口结束日期
        best_params: 训练阶段找到的最优参数
        train_metric: 训练阶段最优指标值
        test_metrics: 测试阶段的指标字典
        optimization_result: 完整的优化结果（可选保留）
    """

    window_index: int
    train_start: date
    train_end: date
    test_start: date
    test_end: date
    best_params: dict[str, Any] = field(default_factory=dict)
    train_metric: float = float("-inf")
    train_metrics: dict[str, float] = field(default_factory=dict)
    test_metrics: dict[str, float] = field(default_factory=dict)
    optimization_result: OptimizationResult | None = None


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class WalkForwardResult:
    """Walk-forward 分析结果。

    Attributes:
        windows: 所有窗口的详细结果
        aggregated_metrics: 聚合的样本外指标（均值）
        metric_std: 样本外指标的标准差
        total_windows: 总窗口数
        config: 使用的配置
        objective: 优化目标名称
    """

    windows: list[WalkForwardWindow]
    aggregated_metrics: dict[str, float]
    metric_std: dict[str, float]
    total_windows: int
    config: WalkForwardConfig
    objective: str


# ---------------------------------------------------------------------------
# Strategy Factory Type
# ---------------------------------------------------------------------------

# 策略工厂：接收参数字典，返回策略实例
StrategyFactory = Callable[[dict[str, Any]], Any]


# ---------------------------------------------------------------------------
# Walk-forward Analyzer
# ---------------------------------------------------------------------------


class WalkForwardAnalyzer:
    """Walk-forward 分析器。

    按滚动窗口执行训练（参数优化）+ 测试（样本外回测），
    聚合所有测试窗口的样本外指标，评估策略稳健性。

    流程：
    1. 将总时间段划分为多个 [train_window | test_window] 对
    2. 在每个 train_window 上执行参数优化
    3. 用最优参数在 test_window 上运行回测
    4. 收集所有 test_window 的指标
    5. 聚合（均值、标准差）样本外指标

    Example::

        analyzer = WalkForwardAnalyzer(
            param_space=space,
            strategy_factory=lambda params: MyStrategy(params=MyParams(**params), universe=["000001"]),
            nav_data=nav_data,
            initial_capital=Decimal("100000"),
            config=WalkForwardConfig(train_days=252, test_days=63),
        )
        result = analyzer.run(start=date(2020, 1, 2), end=date(2023, 12, 29))
    """

    def __init__(
        self,
        param_space: ParamSpace,
        strategy_factory: StrategyFactory,
        nav_data: dict[str, dict[date, Decimal]],
        initial_capital: Decimal = Decimal("100000"),
        fund_meta: dict[str, FundMeta] | None = None,
        dividends: list[DividendInfo] | None = None,
        config: WalkForwardConfig | None = None,
    ) -> None:
        """初始化 Walk-forward 分析器。

        Args:
            param_space: 参数搜索空间
            strategy_factory: 策略工厂函数，接收参数字典返回策略实例
            nav_data: 净值数据 {fund_code: {date: nav}}
            initial_capital: 初始资金
            fund_meta: 基金元数据
            dividends: 分红/拆分事件列表
            config: Walk-forward 配置
        """
        self.param_space = param_space
        self.strategy_factory = strategy_factory
        self.nav_data = nav_data
        self.initial_capital = initial_capital
        self.fund_meta = fund_meta
        self.dividends = dividends or []
        self.config = config or WalkForwardConfig()

    def run(self, start: date, end: date) -> WalkForwardResult:
        """执行 Walk-forward 分析。

        Args:
            start: 分析起始日期
            end: 分析结束日期

        Returns:
            WalkForwardResult 包含所有窗口结果和聚合指标

        Raises:
            ValueError: 如果时间段不足以形成至少一个完整窗口
        """
        # 获取交易日列表
        all_trade_dates = trading_days(start, end)

        if len(all_trade_dates) == 0:
            raise ValueError(
                f"No trading days found between {start} and {end}"
            )

        min_required = self.config.train_days + self.config.embargo_days + self.config.test_days
        if len(all_trade_dates) < min_required:
            raise ValueError(
                f"Insufficient trading days: need at least {min_required} "
                f"(train={self.config.train_days} + embargo={self.config.embargo_days} "
                f"+ test={self.config.test_days}), "
                f"but only {len(all_trade_dates)} available between {start} and {end}"
            )

        # 划分窗口
        windows = self._generate_windows(all_trade_dates)

        if not windows:
            raise ValueError(
                f"Cannot generate any walk-forward windows with the given configuration"
            )

        logger.info(
            "Walk-forward analysis: %d windows, train=%d days, test=%d days, "
            "step=%d days, purge=%d, embargo=%d",
            len(windows), self.config.train_days, self.config.test_days,
            self.config.step_days, self.config.purge_days, self.config.embargo_days,
        )

        # 逐窗口执行
        for window in windows:
            self._run_window(window)

        # 聚合样本外指标
        aggregated, std = self._aggregate_metrics(windows)

        return WalkForwardResult(
            windows=windows,
            aggregated_metrics=aggregated,
            metric_std=std,
            total_windows=len(windows),
            config=self.config,
            objective=self.config.objective,
        )

    def _generate_windows(
        self, all_trade_dates: list[date]
    ) -> list[WalkForwardWindow]:
        """生成滚动窗口列表。

        从起始位置开始，每次滑动 step_days 个交易日，
        直到剩余交易日不足以形成完整的 train + test 窗口。

        窗口结构（含 purge & embargo）::

            [─────── train_days ───────][purge][embargo][── test_days ──]
            ^                            ^                ^
            train_start    train_end_effective         test_start    test_end

        - train_end_effective = train_start + (train_days - purge_days) - 1
        - test_start = train_start + train_days + embargo_days
        - test_end = test_start + test_days - 1

        Args:
            all_trade_dates: 全部交易日列表（已排序）

        Returns:
            WalkForwardWindow 列表
        """
        windows: list[WalkForwardWindow] = []
        total_dates = len(all_trade_dates)
        train_days = self.config.train_days
        test_days = self.config.test_days
        step_days = self.config.step_days  # type: ignore[assignment]
        purge_days = self.config.purge_days
        embargo_days = self.config.embargo_days

        # 单个窗口完整跨度（含 embargo）
        window_span = train_days + embargo_days + test_days

        window_idx = 0
        offset = 0

        while offset + window_span <= total_dates:
            train_start_idx = offset
            # train 末尾需要 purge: 实际训练数据截止到 (offset + train_days - purge_days - 1)
            train_end_idx = offset + train_days - purge_days - 1
            # test 起始要在 train_days 之后再加 embargo
            test_start_idx = offset + train_days + embargo_days
            test_end_idx = test_start_idx + test_days - 1

            window = WalkForwardWindow(
                window_index=window_idx,
                train_start=all_trade_dates[train_start_idx],
                train_end=all_trade_dates[train_end_idx],
                test_start=all_trade_dates[test_start_idx],
                test_end=all_trade_dates[test_end_idx],
            )
            windows.append(window)

            window_idx += 1
            offset += step_days

        return windows

    def _run_window(self, window: WalkForwardWindow) -> None:
        """执行单个窗口的训练+测试。

        1. 在训练窗口上执行参数优化
        2. 用最优参数在测试窗口上运行回测
        3. 记录结果到 window 对象

        Args:
            window: 待执行的窗口
        """
        logger.debug(
            "Window %d: train [%s, %s], test [%s, %s]",
            window.window_index,
            window.train_start, window.train_end,
            window.test_start, window.test_end,
        )

        # 1. 训练阶段：参数优化
        opt_result = self._optimize_on_window(
            window.train_start, window.train_end
        )
        window.best_params = opt_result.best_params
        window.train_metric = opt_result.best_metric
        window.optimization_result = opt_result
        if opt_result.trials:
            window.train_metrics = dict(opt_result.trials[0].metrics)

        logger.debug(
            "Window %d: best_params=%s, train_%s=%.6f",
            window.window_index, window.best_params,
            self.config.objective, window.train_metric,
        )

        # 2. 测试阶段：用最优参数在测试窗口回测
        test_metrics = self._backtest_on_window(
            window.best_params, window.test_start, window.test_end
        )
        window.test_metrics = test_metrics

        test_obj_value = test_metrics.get(self.config.objective, float("nan"))
        logger.debug(
            "Window %d: test_%s=%.6f",
            window.window_index, self.config.objective, test_obj_value,
        )

    def _optimize_on_window(
        self, train_start: date, train_end: date
    ) -> OptimizationResult:
        """在训练窗口上执行参数优化。

        Args:
            train_start: 训练起始日期
            train_end: 训练结束日期

        Returns:
            OptimizationResult
        """

        def runner(params: dict[str, Any]) -> dict[str, float]:
            return self._backtest_on_window(params, train_start, train_end)

        if self.config.method == "grid":
            optimizer = GridSearchOptimizer(
                param_space=self.param_space,
                objective=self.config.objective,
                maximize=self.config.maximize,
                multi_objective_config=self.config.multi_objective_config,
                baseline_metrics=self.config.baseline_metrics,
                baseline_config=self.config.baseline_config,
            )
        else:
            optimizer = SobolSearchOptimizer(  # type: ignore[assignment]
                param_space=self.param_space,
                n_samples=self.config.n_samples,
                objective=self.config.objective,
                maximize=self.config.maximize,
                seed=self.config.seed,
                multi_objective_config=self.config.multi_objective_config,
                baseline_metrics=self.config.baseline_metrics,
                baseline_config=self.config.baseline_config,
            )

        return optimizer.optimize(runner)

    def _backtest_on_window(
        self,
        params: dict[str, Any],
        start: date,
        end: date,
    ) -> dict[str, float]:
        """在指定窗口上运行回测并返回指标。

        Args:
            params: 策略参数
            start: 回测起始日期
            end: 回测结束日期

        Returns:
            指标字典
        """
        # 创建策略实例
        strategy = self.strategy_factory(params)

        # 过滤净值数据到窗口范围（包含窗口前的数据供策略使用）
        # 策略可能需要 lookback 数据，所以不裁剪 nav_data
        engine = EventDrivenEngine()

        # 过滤分红事件到窗口范围
        window_dividends = [
            d for d in self.dividends
            if start <= d.ex_date <= end
        ]

        result = engine.run(
            start=start,
            end=end,
            strategy=strategy,
            nav_data=self.nav_data,
            initial_capital=self.initial_capital,
            fund_meta=self.fund_meta,
            dividends=window_dividends,
        )

        return self._extract_metrics(result)

    def _extract_metrics(self, result: BacktestResult) -> dict[str, float]:
        """从回测结果中提取指标。

        Args:
            result: 回测结果

        Returns:
            指标字典
        """
        if not result.equity_curve:
            return {}

        # 计算基本指标
        equities = [float(s.equity) for s in result.equity_curve]
        initial = equities[0]
        final = equities[-1]

        if initial <= 0:
            return {}

        total_return = (final - initial) / initial
        n_days = len(equities)

        # 年化收益
        years = n_days / 252.0
        annualized_return = (
            ((1 + total_return) ** (1 / years) - 1) if years > 0 else 0.0
        )

        # 日收益率序列
        daily_returns: list[float] = []
        for i in range(1, len(equities)):
            if equities[i - 1] > 0:
                daily_returns.append(equities[i] / equities[i - 1] - 1)
            else:
                daily_returns.append(0.0)

        # 波动率
        if len(daily_returns) > 1:
            import numpy as np

            returns_arr = np.array(daily_returns)
            volatility = float(np.std(returns_arr, ddof=1) * np.sqrt(252))
        else:
            volatility = 0.0

        # Sharpe（假设无风险利率为 0）
        sharpe = annualized_return / volatility if volatility > 0 else 0.0

        # 最大回撤
        max_drawdown = 0.0
        peak = equities[0]
        for eq in equities:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak if peak > 0 else 0.0
            if dd > max_drawdown:
                max_drawdown = dd

        # Calmar
        calmar = annualized_return / max_drawdown if max_drawdown > 0 else 0.0

        # Sortino（下行波动率）
        if daily_returns:
            import numpy as np

            negative_returns = [r for r in daily_returns if r < 0]
            if negative_returns:
                downside_vol = float(
                    np.std(negative_returns, ddof=1) * np.sqrt(252)
                )
            else:
                downside_vol = 0.0
        else:
            downside_vol = 0.0

        sortino = annualized_return / downside_vol if downside_vol > 0 else 0.0

        traded_amount = sum(float(getattr(trade, "amount", 0) or 0) for trade in result.trades)
        total_fees = sum(float(getattr(trade, "fee", 0) or 0) for trade in result.trades)
        turnover = traded_amount / initial if initial > 0 else 0.0
        fee_drag = total_fees / initial if initial > 0 else 0.0

        return {
            "total_return": total_return,
            "annualized_return": annualized_return,
            "volatility": volatility,
            "sharpe": sharpe,
            "max_drawdown": max_drawdown,
            "calmar": calmar,
            "sortino": sortino,
            "turnover": turnover,
            "fee_drag": fee_drag,
            "sample_count": len(daily_returns),
        }

    def _aggregate_metrics(
        self, windows: list[WalkForwardWindow]
    ) -> tuple[dict[str, float], dict[str, float]]:
        """聚合所有窗口的样本外指标。

        计算每个指标在所有测试窗口上的均值和标准差。

        Args:
            windows: 已执行完毕的窗口列表

        Returns:
            (均值字典, 标准差字典)
        """
        import numpy as np

        # 收集所有测试窗口的指标
        all_metrics: dict[str, list[float]] = {}
        for window in windows:
            for key, value in window.test_metrics.items():
                if key not in all_metrics:
                    all_metrics[key] = []
                all_metrics[key].append(value)

        # 计算均值和标准差
        aggregated: dict[str, float] = {}
        std: dict[str, float] = {}

        for key, values in all_metrics.items():
            arr = np.array(values)
            aggregated[key] = float(np.mean(arr))
            std[key] = float(np.std(arr, ddof=1)) if len(arr) > 1 else 0.0

        return aggregated, std
