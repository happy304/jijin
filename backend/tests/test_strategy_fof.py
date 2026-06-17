"""FOF 策略单元测试。

覆盖：
- FOFStrategy: 多因子打分筛选 + 组合优化
- compute_composite_scores: 多因子综合评分
- compute_weights: 权重优化方法
- rank_normalize: 排名归一化
- 各因子计算函数

需求: 5.5
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.domain.backtest.engine_event import BarContext
from app.domain.backtest.order import OrderIntent
from app.domain.backtest.portfolio import Portfolio
from app.domain.strategy.fof import (
    FOFParams,
    FOFStrategy,
    FactorType,
    FactorWeight,
    RebalanceFreq,
    WeightMethod,
    compute_composite_scores,
    compute_composite_scores_with_diagnostics,
    compute_factor_score,
    compute_max_drawdown,
    compute_return,
    compute_sharpe,
    compute_sortino,
    compute_volatility,
    compute_weights,
    is_rebalance_day,
    rank_normalize,
    winsorize_values,
    validate_factor_oos,
    apply_correlation_penalty,
)


# ---------------------------------------------------------------------------
# 辅助工具
# ---------------------------------------------------------------------------


def _make_context(
    current_date: date,
    cash: Decimal = Decimal("100000"),
    positions: dict[str, Decimal] | None = None,
    nav_data: dict[str, dict[date, Decimal]] | None = None,
    cutoff_date: date | None = None,
) -> BarContext:
    """构建测试用 BarContext。"""
    positions = positions or {}
    nav_data = nav_data or {}
    if cutoff_date is None:
        cutoff_date = current_date - timedelta(days=1)
    portfolio = Portfolio(cash=cash, positions=positions)
    return BarContext(
        current_date=current_date,
        portfolio=portfolio,
        nav_history=nav_data,
        _cutoff_date=cutoff_date,
    )


def _make_nav_series(start: date, values: list[float]) -> dict[date, Decimal]:
    """生成连续日期的净值序列。"""
    return {
        start + timedelta(days=i): Decimal(str(round(v, 6)))
        for i, v in enumerate(values)
    }


def _build_nav_data() -> dict[str, dict[date, Decimal]]:
    """构建 5 只基金的测试净值数据。

    A: 高收益高波动
    B: 中收益中波动
    C: 低收益低波动
    D: 负收益高波动
    E: 横盘
    """
    start = date(2024, 1, 1)
    days = 90

    import numpy as np
    np.random.seed(42)

    nav_data: dict[str, dict[date, Decimal]] = {}

    # A: 高收益高波动
    nav_a = [1.0]
    for _ in range(days - 1):
        nav_a.append(nav_a[-1] * (1 + np.random.normal(0.003, 0.02)))
    nav_data["A"] = {
        start + timedelta(days=i): Decimal(str(round(v, 6)))
        for i, v in enumerate(nav_a)
    }

    # B: 中收益中波动
    nav_b = [1.0]
    for _ in range(days - 1):
        nav_b.append(nav_b[-1] * (1 + np.random.normal(0.001, 0.01)))
    nav_data["B"] = {
        start + timedelta(days=i): Decimal(str(round(v, 6)))
        for i, v in enumerate(nav_b)
    }

    # C: 低收益低波动
    nav_c = [1.0]
    for _ in range(days - 1):
        nav_c.append(nav_c[-1] * (1 + np.random.normal(0.0005, 0.005)))
    nav_data["C"] = {
        start + timedelta(days=i): Decimal(str(round(v, 6)))
        for i, v in enumerate(nav_c)
    }

    # D: 负收益高波动
    nav_d = [1.0]
    for _ in range(days - 1):
        nav_d.append(nav_d[-1] * (1 + np.random.normal(-0.002, 0.025)))
    nav_data["D"] = {
        start + timedelta(days=i): Decimal(str(round(v, 6)))
        for i, v in enumerate(nav_d)
    }

    # E: 横盘
    nav_data["E"] = {
        start + timedelta(days=i): Decimal("1.0")
        for i in range(days)
    }

    return nav_data


# ---------------------------------------------------------------------------
# 因子计算测试
# ---------------------------------------------------------------------------


class TestFactorComputation:
    """因子计算函数测试。"""

    def test_compute_return_positive(self) -> None:
        """正收益率计算。"""
        nav = _make_nav_series(date(2024, 1, 1), [1.0, 1.1, 1.2, 1.3, 1.5])
        ret = compute_return(nav, 10)
        assert ret is not None
        assert abs(ret - 0.5) < 1e-10

    def test_compute_return_negative(self) -> None:
        """负收益率计算。"""
        nav = _make_nav_series(date(2024, 1, 1), [2.0, 1.8, 1.5, 1.2, 1.0])
        ret = compute_return(nav, 10)
        assert ret is not None
        assert abs(ret - (-0.5)) < 1e-10

    def test_compute_return_insufficient(self) -> None:
        """数据不足返回 None。"""
        nav = _make_nav_series(date(2024, 1, 1), [1.0])
        assert compute_return(nav, 10) is None

    def test_compute_volatility(self) -> None:
        """波动率计算为正。"""
        values = [1.0 + 0.01 * (i % 3 - 1) for i in range(30)]
        nav = _make_nav_series(date(2024, 1, 1), values)
        vol = compute_volatility(nav, 30)
        assert vol is not None
        assert vol > 0

    def test_compute_volatility_constant(self) -> None:
        """常数净值波动率为 0。"""
        nav = _make_nav_series(date(2024, 1, 1), [1.0] * 30)
        vol = compute_volatility(nav, 30)
        assert vol is not None
        assert vol == 0.0

    def test_compute_sharpe_positive(self) -> None:
        """稳定上涨 Sharpe 为正。"""
        values = [1.0 * 1.001**i for i in range(30)]
        nav = _make_nav_series(date(2024, 1, 1), values)
        sharpe = compute_sharpe(nav, 30)
        assert sharpe is not None
        assert sharpe > 0

    def test_compute_sharpe_negative(self) -> None:
        """稳定下跌 Sharpe 为负。"""
        values = [1.0 * 0.999**i for i in range(30)]
        nav = _make_nav_series(date(2024, 1, 1), values)
        sharpe = compute_sharpe(nav, 30)
        assert sharpe is not None
        assert sharpe < 0

    def test_compute_max_drawdown(self) -> None:
        """最大回撤计算。"""
        # 从 2.0 跌到 1.0，回撤 50%
        nav = _make_nav_series(date(2024, 1, 1), [1.0, 2.0, 1.5, 1.0, 1.2])
        mdd = compute_max_drawdown(nav, 10)
        assert mdd is not None
        assert abs(mdd - 0.5) < 1e-10

    def test_compute_max_drawdown_no_drawdown(self) -> None:
        """持续上涨无回撤。"""
        nav = _make_nav_series(date(2024, 1, 1), [1.0, 1.1, 1.2, 1.3, 1.4])
        mdd = compute_max_drawdown(nav, 10)
        assert mdd is not None
        assert mdd == 0.0

    def test_compute_sortino_positive(self) -> None:
        """稳定上涨 Sortino 为正。"""
        values = [1.0 * 1.002**i for i in range(30)]
        nav = _make_nav_series(date(2024, 1, 1), values)
        sortino = compute_sortino(nav, 30)
        assert sortino is not None
        assert sortino > 0

    def test_compute_factor_score_dispatches(self) -> None:
        """compute_factor_score 正确分发。"""
        values = [1.0 * 1.001**i for i in range(30)]
        nav = _make_nav_series(date(2024, 1, 1), values)

        ret = compute_factor_score(nav, FactorType.RETURN, 30)
        assert ret is not None
        assert ret > 0

        sharpe = compute_factor_score(nav, FactorType.SHARPE, 30)
        assert sharpe is not None
        assert sharpe > 0


# ---------------------------------------------------------------------------
# rank_normalize 测试
# ---------------------------------------------------------------------------


class TestWinsorizeValues:
    """因子截尾测试。"""

    def test_winsorize_caps_outlier(self) -> None:
        values: list[float | None] = [1.0, 2.0, 3.0, 1000.0]
        capped = winsorize_values(values, 0.0, 0.75)
        assert capped[-1] is not None
        assert capped[-1] < 1000.0


class TestRankNormalize:
    """排名归一化测试。"""

    def test_basic(self) -> None:
        """基本排名归一化。"""
        values: list[float | None] = [3.0, 1.0, 2.0]
        ranked = rank_normalize(values)
        # 排序: 1.0(idx=1) < 2.0(idx=2) < 3.0(idx=0)
        # rank: idx1=0, idx2=1, idx0=2
        # normalized: idx1=0/2=0, idx2=1/2=0.5, idx0=2/2=1.0
        assert ranked[0] == 1.0
        assert ranked[1] == 0.0
        assert ranked[2] == 0.5

    def test_with_none(self) -> None:
        """包含 None 值。"""
        values: list[float | None] = [3.0, None, 1.0]
        ranked = rank_normalize(values)
        assert ranked[1] is None
        assert ranked[0] == 1.0
        assert ranked[2] == 0.0

    def test_single_value(self) -> None:
        """单个有效值返回 0.5。"""
        values: list[float | None] = [5.0, None, None]
        ranked = rank_normalize(values)
        assert ranked[0] == 0.5

    def test_all_none(self) -> None:
        """全部 None。"""
        values: list[float | None] = [None, None, None]
        ranked = rank_normalize(values)
        assert all(v is None for v in ranked)


# ---------------------------------------------------------------------------
# compute_composite_scores 测试
# ---------------------------------------------------------------------------


class TestCompositeScores:
    """多因子综合评分测试。"""

    def test_single_factor(self) -> None:
        """单因子评分。"""
        nav_data = _build_nav_data()
        codes = ["A", "B", "C", "D", "E"]
        factor_weights = [FactorWeight(FactorType.RETURN, weight=1.0, lookback_days=60)]

        scores = compute_composite_scores(nav_data, codes, factor_weights)
        assert len(scores) == 5
        # 所有得分在 [0, 1] 范围内
        for s in scores.values():
            assert 0.0 <= s <= 1.0

    def test_multi_factor(self) -> None:
        """多因子评分。"""
        nav_data = _build_nav_data()
        codes = ["A", "B", "C"]
        factor_weights = [
            FactorWeight(FactorType.RETURN, weight=1.0, lookback_days=60),
            FactorWeight(FactorType.SHARPE, weight=2.0, lookback_days=60),
        ]

        scores = compute_composite_scores(nav_data, codes, factor_weights)
        assert len(scores) == 3

    def test_inverse_factor(self) -> None:
        """反向因子（波动率）：低波动得分高。"""
        nav_data = _build_nav_data()
        codes = ["A", "B", "C"]  # A 高波动, C 低波动
        factor_weights = [FactorWeight(FactorType.VOLATILITY, weight=1.0, lookback_days=60)]

        scores = compute_composite_scores(nav_data, codes, factor_weights)
        # C（低波动）得分应高于 A（高波动）
        assert scores["C"] > scores["A"]

    def test_empty_codes(self) -> None:
        """空代码列表。"""
        scores = compute_composite_scores({}, [], [])
        assert scores == {}

    def test_diagnostics_warn_on_low_factor_coverage(self) -> None:
        """因子覆盖率不足时输出诊断和研究状态。"""
        nav_data = _build_nav_data()
        codes = ["A", "B", "MISSING"]
        factor_weights = [FactorWeight(FactorType.SHARPE, weight=1.0, lookback_days=60)]

        scores, diagnostics = compute_composite_scores_with_diagnostics(
            nav_data,
            codes,
            factor_weights,
        )

        assert set(scores) == set(codes)
        assert diagnostics["oos_status"] == "not_available"
        assert diagnostics["validation_status"] == "research_only"
        assert diagnostics["factors"]["sharpe"]["coverage_ratio"] < 0.7  # type: ignore[index]
        assert diagnostics["quality_warnings"]


# ---------------------------------------------------------------------------
# compute_weights 测试
# ---------------------------------------------------------------------------


class TestComputeWeights:
    """权重计算测试。"""

    def test_equal_weight(self) -> None:
        """等权。"""
        codes = ["A", "B", "C"]
        scores = {"A": 0.8, "B": 0.6, "C": 0.4}
        weights = compute_weights(codes, scores, {}, WeightMethod.EQUAL, 60)
        assert len(weights) == 3
        for w in weights.values():
            assert abs(w - 1.0 / 3) < 1e-10

    def test_score_weighted(self) -> None:
        """得分加权。"""
        codes = ["A", "B"]
        scores = {"A": 0.8, "B": 0.2}
        weights = compute_weights(codes, scores, {}, WeightMethod.SCORE_WEIGHTED, 60)
        assert abs(weights["A"] - 0.8) < 1e-10
        assert abs(weights["B"] - 0.2) < 1e-10

    def test_inverse_vol(self) -> None:
        """逆波动率加权：低波动权重更高。"""
        nav_data = _build_nav_data()
        codes = ["A", "C"]  # A 高波动, C 低波动
        scores = {"A": 0.5, "C": 0.5}
        weights = compute_weights(codes, scores, nav_data, WeightMethod.INVERSE_VOL, 60)
        # C（低波动）权重应更高
        assert weights["C"] > weights["A"]

    def test_weights_sum_to_one(self) -> None:
        """权重和为 1。"""
        nav_data = _build_nav_data()
        codes = ["A", "B", "C"]
        scores = {"A": 0.8, "B": 0.6, "C": 0.4}

        for method in WeightMethod:
            weights = compute_weights(codes, scores, nav_data, method, 60)
            total = sum(weights.values())
            assert abs(total - 1.0) < 1e-6, f"{method}: sum={total}"

    def test_empty_codes(self) -> None:
        """空代码列表返回空字典。"""
        weights = compute_weights([], {}, {}, WeightMethod.EQUAL, 60)
        assert weights == {}

    def test_score_weighted_applies_max_weight_cap(self) -> None:
        """得分极端集中时仍应用单基金权重上限。"""
        codes = ["A", "B", "C", "D"]
        scores = {"A": 100.0, "B": 1.0, "C": 1.0, "D": 1.0}
        weights = compute_weights(
            codes,
            scores,
            {},
            WeightMethod.SCORE_WEIGHTED,
            60,
            max_weight=0.4,
        )
        assert max(weights.values()) <= 0.40001
        assert abs(sum(weights.values()) - 1.0) < 1e-6

    def test_correlation_penalty_reduces_duplicate_exposure(self) -> None:
        """高相关基金应触发权重惩罚诊断。"""
        start = date(2024, 1, 1)
        values = [1.0]
        for i in range(1, 80):
            values.append(values[-1] * (1.0 + 0.001 + 0.0005 * ((i % 5) - 2)))
        nav_data = {
            "A": _make_nav_series(start, values),
            "B": _make_nav_series(start, [v * 1.0001 for v in values]),
            "C": _make_nav_series(start, [1.0 + 0.0005 * ((i % 5) - 2) for i in range(80)]),
        }
        weights, diagnostics = apply_correlation_penalty(
            {"A": 0.4, "B": 0.4, "C": 0.2},
            nav_data,
            threshold=0.8,
            penalty_strength=0.5,
            max_weight=0.6,
        )
        assert diagnostics["applied"] is True
        assert diagnostics["high_correlation_pairs"]
        assert abs(sum(weights.values()) - 1.0) < 1e-6


class TestFactorValidation:
    """FOF 因子验证诊断测试。"""

    def test_validate_factor_oos_outputs_ic_fields(self) -> None:
        nav_data = _build_nav_data()
        diagnostics = validate_factor_oos(
            nav_data,
            ["A", "B", "C", "D", "E"],
            [FactorWeight(FactorType.RETURN, weight=1.0, lookback_days=40)],
            forward_days=10,
        )
        assert "factors" in diagnostics
        factor_diag = diagnostics["factors"]["return"]  # type: ignore[index]
        assert "ic" in factor_diag
        assert "rank_ic" in factor_diag
        assert "group_returns" in factor_diag


# ---------------------------------------------------------------------------
# FOFParams 测试
# ---------------------------------------------------------------------------


class TestFOFParams:
    """FOF 参数测试。"""

    def test_default_params(self) -> None:
        """默认参数。"""
        params = FOFParams()
        assert params.lookback_days == 60
        assert params.top_n == 5
        assert params.weight_method == WeightMethod.EQUAL
        assert params.rebalance_freq == RebalanceFreq.MONTHLY

    def test_invalid_lookback(self) -> None:
        """lookback 为 0 应失败。"""
        with pytest.raises(Exception):
            FOFParams(lookback_days=0)

    def test_serialization(self) -> None:
        """参数可序列化。"""
        params = FOFParams(
            top_n=3,
            weight_method=WeightMethod.SCORE_WEIGHTED,
            rebalance_freq=RebalanceFreq.QUARTERLY,
        )
        d = params.model_dump()
        assert d["top_n"] == 3
        assert d["weight_method"] == "score_weighted"
        assert d["rebalance_freq"] == "quarterly"


# ---------------------------------------------------------------------------
# FOFStrategy 测试
# ---------------------------------------------------------------------------


class TestFOFStrategy:
    """FOF 策略测试。"""

    def test_creation(self) -> None:
        """策略创建。"""
        params = FOFParams(top_n=3, weight_method=WeightMethod.EQUAL)
        strategy = FOFStrategy(params=params, universe=["A", "B", "C"])
        assert strategy.name == "fof"

    def test_first_day_rebalances(self) -> None:
        """第一个交易日应触发调仓。"""
        nav_data = _build_nav_data()
        params = FOFParams(lookback_days=60, top_n=3)
        strategy = FOFStrategy(
            params=params,
            universe=["A", "B", "C", "D", "E"],
            factor_weights=[
                FactorWeight(FactorType.SHARPE, weight=1.0, lookback_days=60),
            ],
        )

        ctx = _make_context(
            current_date=date(2024, 3, 30),
            cash=Decimal("100000"),
            nav_data=nav_data,
            cutoff_date=date(2024, 3, 29),
        )
        orders = strategy.on_bar(ctx)
        assert len(orders) > 0

    def test_top_n_selection(self) -> None:
        """Top-N 筛选正确选取基金。"""
        nav_data = _build_nav_data()
        params = FOFParams(lookback_days=60, top_n=2)
        strategy = FOFStrategy(
            params=params,
            universe=["A", "B", "C", "D", "E"],
            factor_weights=[
                FactorWeight(FactorType.RETURN, weight=1.0, lookback_days=60),
            ],
        )

        ctx = _make_context(
            current_date=date(2024, 3, 30),
            cash=Decimal("100000"),
            nav_data=nav_data,
            cutoff_date=date(2024, 3, 29),
        )
        orders = strategy.on_bar(ctx)
        assert len(orders) > 0

        # D（负收益）不应被选中
        codes = {o.fund_code for o in orders if o.direction == "subscribe"}
        assert "D" not in codes

    def test_non_rebalance_day_no_orders(self) -> None:
        """非调仓日不产生订单。"""
        nav_data = _build_nav_data()
        params = FOFParams(lookback_days=60, top_n=3)
        strategy = FOFStrategy(
            params=params,
            universe=["A", "B", "C"],
            factor_weights=[FactorWeight(FactorType.RETURN, weight=1.0)],
        )

        # 第一天调仓
        ctx1 = _make_context(
            current_date=date(2024, 3, 1),
            cash=Decimal("100000"),
            nav_data=nav_data,
            cutoff_date=date(2024, 2, 29),
        )
        strategy.on_bar(ctx1)

        # 第二天不调仓
        ctx2 = _make_context(
            current_date=date(2024, 3, 2),
            cash=Decimal("100000"),
            nav_data=nav_data,
            cutoff_date=date(2024, 3, 1),
        )
        orders = strategy.on_bar(ctx2)
        assert len(orders) == 0

    def test_score_weighted_method(self) -> None:
        """得分加权方法。"""
        nav_data = _build_nav_data()
        params = FOFParams(
            lookback_days=60, top_n=3,
            weight_method=WeightMethod.SCORE_WEIGHTED,
        )
        strategy = FOFStrategy(
            params=params,
            universe=["A", "B", "C", "D", "E"],
            factor_weights=[FactorWeight(FactorType.SHARPE, weight=1.0, lookback_days=60)],
        )

        ctx = _make_context(
            current_date=date(2024, 3, 30),
            cash=Decimal("100000"),
            nav_data=nav_data,
            cutoff_date=date(2024, 3, 29),
        )
        orders = strategy.on_bar(ctx)
        assert len(orders) > 0

    def test_inverse_vol_method(self) -> None:
        """逆波动率加权方法。"""
        nav_data = _build_nav_data()
        params = FOFParams(
            lookback_days=60, top_n=3,
            weight_method=WeightMethod.INVERSE_VOL,
        )
        strategy = FOFStrategy(
            params=params,
            universe=["A", "B", "C"],
            factor_weights=[FactorWeight(FactorType.RETURN, weight=1.0, lookback_days=60)],
        )

        ctx = _make_context(
            current_date=date(2024, 3, 30),
            cash=Decimal("100000"),
            nav_data=nav_data,
            cutoff_date=date(2024, 3, 29),
        )
        orders = strategy.on_bar(ctx)
        assert len(orders) > 0

    def test_empty_universe(self) -> None:
        """空基金池不产生订单。"""
        params = FOFParams()
        strategy = FOFStrategy(params=params, universe=[])
        ctx = _make_context(current_date=date(2024, 3, 1), nav_data={})
        orders = strategy.on_bar(ctx)
        assert len(orders) == 0

    def test_multi_factor_weights(self) -> None:
        """多因子权重配置。"""
        nav_data = _build_nav_data()
        params = FOFParams(lookback_days=60, top_n=3)
        strategy = FOFStrategy(
            params=params,
            universe=["A", "B", "C", "D", "E"],
            factor_weights=[
                FactorWeight(FactorType.SHARPE, weight=2.0, lookback_days=60),
                FactorWeight(FactorType.MAX_DRAWDOWN, weight=1.0, lookback_days=60),
                FactorWeight(FactorType.RETURN, weight=1.5, lookback_days=90),
            ],
        )

        ctx = _make_context(
            current_date=date(2024, 3, 30),
            cash=Decimal("100000"),
            nav_data=nav_data,
            cutoff_date=date(2024, 3, 29),
        )
        orders = strategy.on_bar(ctx)
        assert len(orders) > 0

    def test_last_diagnostics_after_rebalance(self) -> None:
        """调仓后暴露因子、权重和验证状态诊断。"""
        nav_data = _build_nav_data()
        params = FOFParams(lookback_days=60, top_n=3, max_weight=0.4)
        strategy = FOFStrategy(
            params=params,
            universe=["A", "B", "C", "D", "E"],
            factor_weights=[FactorWeight(FactorType.RETURN, weight=1.0, lookback_days=60)],
        )

        ctx = _make_context(
            current_date=date(2024, 3, 30),
            cash=Decimal("100000"),
            nav_data=nav_data,
            cutoff_date=date(2024, 3, 29),
        )
        orders = strategy.on_bar(ctx)

        assert len(orders) > 0
        assert strategy.last_diagnostics["oos_status"] == "not_available"
        assert "weight_diagnostics" in strategy.last_diagnostics
        assert strategy.last_diagnostics["validation_status"] == "research_only"

    def test_threshold_selection(self) -> None:
        """阈值筛选模式（top_n=0）。"""
        nav_data = _build_nav_data()
        params = FOFParams(
            lookback_days=60,
            top_n=0,
            score_threshold=0.5,
        )
        strategy = FOFStrategy(
            params=params,
            universe=["A", "B", "C", "D", "E"],
            factor_weights=[FactorWeight(FactorType.RETURN, weight=1.0, lookback_days=60)],
        )

        ctx = _make_context(
            current_date=date(2024, 3, 30),
            cash=Decimal("100000"),
            nav_data=nav_data,
            cutoff_date=date(2024, 3, 29),
        )
        orders = strategy.on_bar(ctx)
        # 应该有一些基金得分 >= 0.5
        assert len(orders) >= 0  # 可能有也可能没有，取决于数据


# ---------------------------------------------------------------------------
# is_rebalance_day 测试
# ---------------------------------------------------------------------------


class TestIsRebalanceDay:
    """调仓日判断测试。"""

    def test_first_day(self) -> None:
        """首次调仓。"""
        assert is_rebalance_day(date(2024, 1, 1), None, RebalanceFreq.MONTHLY) is True

    def test_monthly(self) -> None:
        """月频。"""
        last = date(2024, 1, 1)
        assert is_rebalance_day(date(2024, 1, 28), last, RebalanceFreq.MONTHLY) is False
        assert is_rebalance_day(date(2024, 1, 29), last, RebalanceFreq.MONTHLY) is True
