"""均值-方差与 Black-Litterman 策略单元测试。

覆盖：
- MeanVarianceStrategy: 均值-方差优化策略
- BlackLittermanStrategy: Black-Litterman 策略
- mv_optimize: 优化求解
- compute_equilibrium_returns: 均衡收益率
- black_litterman_posterior: BL 后验计算
- is_rebalance_day: 调仓日判断

需求: 5.3
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pytest

from app.domain.backtest.engine_event import BarContext
from app.domain.backtest.order import OrderIntent
from app.domain.backtest.portfolio import Portfolio
from app.domain.strategy.mean_variance import (
    BlackLittermanParams,
    BlackLittermanStrategy,
    MeanVarianceParams,
    MeanVarianceStrategy,
    OptObjective,
    RebalanceFreq,
    View,
    black_litterman_posterior,
    build_omega_from_views,
    compute_equilibrium_returns,
    is_rebalance_day,
    mv_optimize,
    mv_optimize_with_diagnostics,
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


def _build_nav_data(n_days: int = 90) -> dict[str, dict[date, Decimal]]:
    """构建 3 只基金的测试净值数据。

    基金 A: 高收益高波动
    基金 B: 中收益中波动
    基金 C: 低收益低波动
    """
    np.random.seed(42)
    start = date(2024, 1, 1)

    nav_data: dict[str, dict[date, Decimal]] = {}

    # 基金 A: 高收益高波动
    nav_a = [1.0]
    for _ in range(n_days - 1):
        nav_a.append(nav_a[-1] * (1 + np.random.normal(0.002, 0.02)))
    nav_data["A"] = {
        start + timedelta(days=i): Decimal(str(round(v, 6)))
        for i, v in enumerate(nav_a)
    }

    # 基金 B: 中收益中波动
    nav_b = [1.0]
    for _ in range(n_days - 1):
        nav_b.append(nav_b[-1] * (1 + np.random.normal(0.001, 0.01)))
    nav_data["B"] = {
        start + timedelta(days=i): Decimal(str(round(v, 6)))
        for i, v in enumerate(nav_b)
    }

    # 基金 C: 低收益低波动
    nav_c = [1.0]
    for _ in range(n_days - 1):
        nav_c.append(nav_c[-1] * (1 + np.random.normal(0.0005, 0.005)))
    nav_data["C"] = {
        start + timedelta(days=i): Decimal(str(round(v, 6)))
        for i, v in enumerate(nav_c)
    }

    return nav_data


# ---------------------------------------------------------------------------
# is_rebalance_day 测试
# ---------------------------------------------------------------------------


class TestIsRebalanceDay:
    """调仓日判断逻辑测试。"""

    def test_first_day_always_rebalance(self) -> None:
        """首次调仓应返回 True。"""
        assert is_rebalance_day(date(2024, 1, 2), None, RebalanceFreq.WEEKLY) is True
        assert is_rebalance_day(date(2024, 1, 2), None, RebalanceFreq.MONTHLY) is True
        assert is_rebalance_day(date(2024, 1, 2), None, RebalanceFreq.QUARTERLY) is True

    def test_weekly(self) -> None:
        """周频调仓。"""
        last = date(2024, 1, 1)
        assert is_rebalance_day(date(2024, 1, 7), last, RebalanceFreq.WEEKLY) is False
        assert is_rebalance_day(date(2024, 1, 8), last, RebalanceFreq.WEEKLY) is True

    def test_monthly(self) -> None:
        """月频调仓。"""
        last = date(2024, 1, 1)
        assert is_rebalance_day(date(2024, 1, 28), last, RebalanceFreq.MONTHLY) is False
        assert is_rebalance_day(date(2024, 1, 29), last, RebalanceFreq.MONTHLY) is True

    def test_quarterly(self) -> None:
        """季频调仓。"""
        last = date(2024, 1, 1)
        assert is_rebalance_day(date(2024, 3, 24), last, RebalanceFreq.QUARTERLY) is False
        assert is_rebalance_day(date(2024, 3, 25), last, RebalanceFreq.QUARTERLY) is True


# ---------------------------------------------------------------------------
# mv_optimize 测试
# ---------------------------------------------------------------------------


class TestMvOptimize:
    """均值-方差优化求解测试。"""

    def test_max_sharpe_basic(self) -> None:
        """最大 Sharpe 优化基本功能。"""
        # 两只不相关资产，A 收益高波动高，B 收益低波动低
        expected_returns = np.array([0.10, 0.05])
        cov_matrix = np.array([[0.04, 0.0], [0.0, 0.01]])

        weights = mv_optimize(
            expected_returns, cov_matrix, OptObjective.MAX_SHARPE,
            risk_free_rate=0.02,
        )
        assert weights is not None
        np.testing.assert_allclose(weights.sum(), 1.0, atol=1e-6)
        assert np.all(weights >= -1e-10)

    def test_min_variance(self) -> None:
        """最小方差优化：低波动资产权重更高。"""
        expected_returns = np.array([0.10, 0.05])
        cov_matrix = np.array([[0.04, 0.0], [0.0, 0.01]])

        weights = mv_optimize(
            expected_returns, cov_matrix, OptObjective.MIN_VARIANCE,
        )
        assert weights is not None
        # 低波动资产 B 权重更高
        assert weights[1] > weights[0]
        np.testing.assert_allclose(weights.sum(), 1.0, atol=1e-6)

    def test_min_variance_equal_assets(self) -> None:
        """两只相同资产最小方差应等权。"""
        expected_returns = np.array([0.10, 0.10])
        cov_matrix = np.array([[0.04, 0.0], [0.0, 0.04]])

        weights = mv_optimize(
            expected_returns, cov_matrix, OptObjective.MIN_VARIANCE,
        )
        assert weights is not None
        np.testing.assert_allclose(weights, [0.5, 0.5], atol=1e-4)

    def test_target_return(self) -> None:
        """目标收益率优化。"""
        expected_returns = np.array([0.15, 0.05])
        cov_matrix = np.array([[0.04, 0.005], [0.005, 0.01]])

        weights = mv_optimize(
            expected_returns, cov_matrix, OptObjective.TARGET_RETURN,
            target_return=0.10,
        )
        assert weights is not None
        # 组合收益率应 >= 目标
        port_return = weights @ expected_returns
        assert port_return >= 0.10 - 1e-4
        np.testing.assert_allclose(weights.sum(), 1.0, atol=1e-6)

    def test_single_asset(self) -> None:
        """单只资产权重为 1。"""
        expected_returns = np.array([0.10])
        cov_matrix = np.array([[0.04]])

        weights = mv_optimize(
            expected_returns, cov_matrix, OptObjective.MAX_SHARPE,
        )
        assert weights is not None
        np.testing.assert_allclose(weights, [1.0], atol=1e-6)

    def test_empty_returns_none(self) -> None:
        """空输入返回 None。"""
        weights = mv_optimize(
            np.array([]), np.array([]).reshape(0, 0), OptObjective.MAX_SHARPE,
        )
        assert weights is None

    def test_weights_non_negative_no_short(self) -> None:
        """不允许做空时权重非负。"""
        np.random.seed(99)
        n = 4
        returns = np.random.randn(100, n)
        cov = np.cov(returns, rowvar=False)
        mu = returns.mean(axis=0) * 252

        weights = mv_optimize(mu, cov * 252, OptObjective.MAX_SHARPE, allow_short=False)
        assert weights is not None
        assert np.all(weights >= -1e-10)

    def test_max_weight_constraint_limits_concentration(self) -> None:
        """极端收益输入下仍受单基金上限约束。"""
        expected_returns = np.array([0.50, 0.02, 0.01])
        cov_matrix = np.diag([0.01, 0.01, 0.01])

        weights = mv_optimize(
            expected_returns,
            cov_matrix,
            OptObjective.MAX_SHARPE,
            max_weight=0.4,
        )

        assert weights is not None
        np.testing.assert_allclose(weights.sum(), 1.0, atol=1e-6)
        assert np.max(weights) <= 0.40001

    def test_small_sample_fallback_avoids_full_concentration(self) -> None:
        """样本不足时降级，不产生单基金 100% 集中。"""
        expected_returns = np.array([0.40, 0.01, 0.01])
        cov_matrix = np.diag([0.02, 0.02, 0.02])

        weights = mv_optimize(
            expected_returns,
            cov_matrix,
            OptObjective.MAX_SHARPE,
            max_weight=0.4,
            min_observations=60,
            n_observations=10,
        )

        assert weights is not None
        assert np.max(weights) <= 0.40001
        np.testing.assert_allclose(weights.sum(), 1.0, atol=1e-6)

    def test_diagnostics_report_small_sample_fallback(self) -> None:
        """带诊断接口应暴露 fallback 原因和约束。"""
        weights, diagnostics = mv_optimize_with_diagnostics(
            np.array([0.40, 0.01, 0.01]),
            np.diag([0.02, 0.02, 0.02]),
            OptObjective.MAX_SHARPE,
            max_weight=0.4,
            min_observations=60,
            n_observations=10,
        )

        assert weights is not None
        assert diagnostics["fallback"] is True
        assert diagnostics["fallback_method"] == "equal_weight"
        assert diagnostics["validation_status"] == "research_only"
        assert diagnostics["constraints"]["effective_max_weight"] == 0.4  # type: ignore[index]
        assert diagnostics["warnings"]



# ---------------------------------------------------------------------------
# Black-Litterman 核心函数测试
# ---------------------------------------------------------------------------


class TestBlackLitterman:
    """Black-Litterman 模型核心函数测试。"""

    def test_equilibrium_returns(self) -> None:
        """均衡收益率计算：Π = δΣw。"""
        cov = np.array([[0.04, 0.01], [0.01, 0.09]])
        market_weights = np.array([0.6, 0.4])
        risk_aversion = 2.5

        pi = compute_equilibrium_returns(cov, market_weights, risk_aversion)

        # 手动计算
        expected = risk_aversion * cov @ market_weights
        np.testing.assert_allclose(pi, expected, atol=1e-10)

    def test_equilibrium_returns_equal_weight(self) -> None:
        """等权市场权重的均衡收益率。"""
        cov = np.diag([0.04, 0.09, 0.16])
        market_weights = np.ones(3) / 3
        risk_aversion = 2.5

        pi = compute_equilibrium_returns(cov, market_weights, risk_aversion)
        assert pi.shape == (3,)
        # 高波动资产隐含收益率更高
        assert pi[2] > pi[1] > pi[0]

    def test_posterior_no_views_equals_prior(self) -> None:
        """无观点时后验应接近先验（极弱观点）。"""
        n = 3
        cov = np.diag([0.04, 0.09, 0.16])
        pi = np.array([0.05, 0.08, 0.12])
        tau = 0.05

        # 构造一个极弱观点（Ω 极大）
        P = np.eye(n)
        Q = pi.copy()
        omega = np.eye(n) * 1e10  # 极大不确定性

        posterior_returns, _ = black_litterman_posterior(
            cov, pi, P, Q, omega, tau
        )

        # 后验应接近先验
        np.testing.assert_allclose(posterior_returns, pi, atol=0.01)

    def test_posterior_strong_view_dominates(self) -> None:
        """强观点应主导后验收益率。"""
        n = 2
        cov = np.diag([0.04, 0.09])
        pi = np.array([0.05, 0.08])
        tau = 0.05

        # 强观点：资产 0 收益率为 0.20
        P = np.array([[1.0, 0.0]])
        Q = np.array([0.20])
        omega = np.array([[1e-8]])  # 极高置信度

        posterior_returns, _ = black_litterman_posterior(
            cov, pi, P, Q, omega, tau
        )

        # 资产 0 的后验收益率应接近观点值 0.20
        assert posterior_returns[0] > 0.15

    def test_relative_view(self) -> None:
        """相对观点：资产 A 比资产 B 多涨 5%。"""
        n = 2
        cov = np.diag([0.04, 0.04])
        pi = np.array([0.06, 0.06])  # 均衡收益率相同
        tau = 0.05

        # 相对观点：A - B = 0.05
        P = np.array([[1.0, -1.0]])
        Q = np.array([0.05])
        # omega 标量 = tau * p' Σ p * (1/confidence - 1)
        view_var = (P @ (tau * cov) @ P.T).item()
        omega = np.array([[view_var]])  # 中等置信度

        posterior_returns, _ = black_litterman_posterior(
            cov, pi, P, Q, omega, tau
        )

        # A 的后验收益率应高于 B
        assert posterior_returns[0] > posterior_returns[1]

    def test_build_omega_from_views(self) -> None:
        """Ω 矩阵构建正确。"""
        cov = np.diag([0.04, 0.09])
        tau = 0.05
        views = [
            View(p_row=[1.0, 0.0], q=0.10, confidence=0.8),
            View(p_row=[0.0, 1.0], q=0.05, confidence=0.5),
        ]
        P = np.array([v.p_row for v in views])

        omega = build_omega_from_views(views, P, cov, tau)

        # Ω 应为对角矩阵
        assert omega.shape == (2, 2)
        assert omega[0, 1] == 0.0
        assert omega[1, 0] == 0.0
        # 对角元素为正
        assert omega[0, 0] > 0
        assert omega[1, 1] > 0
        # 高置信度的 ω 更小
        # view 0: confidence=0.8, p=[1,0], var = 0.04*0.05 = 0.002
        # ω_0 = (1/0.8 - 1) * 0.002 = 0.25 * 0.002 = 0.0005
        # view 1: confidence=0.5, p=[0,1], var = 0.09*0.05 = 0.0045
        # ω_1 = (1/0.5 - 1) * 0.0045 = 1.0 * 0.0045 = 0.0045
        np.testing.assert_allclose(omega[0, 0], 0.25 * 0.04 * tau, atol=1e-10)
        np.testing.assert_allclose(omega[1, 1], 1.0 * 0.09 * tau, atol=1e-10)


# ---------------------------------------------------------------------------
# MeanVarianceParams 测试
# ---------------------------------------------------------------------------


class TestMeanVarianceParams:
    """均值-方差参数验证测试。"""

    def test_valid_params(self) -> None:
        """有效参数创建成功。"""
        params = MeanVarianceParams(
            lookback_days=90,
            rebalance_freq=RebalanceFreq.WEEKLY,
            objective=OptObjective.MIN_VARIANCE,
            risk_free_rate=0.03,
        )
        assert params.lookback_days == 90
        assert params.objective == OptObjective.MIN_VARIANCE
        assert params.risk_free_rate == 0.03

    def test_default_params(self) -> None:
        """默认参数值正确。"""
        params = MeanVarianceParams()
        assert params.lookback_days == 60
        assert params.rebalance_freq == RebalanceFreq.MONTHLY
        assert params.objective == OptObjective.MAX_SHARPE
        assert params.risk_free_rate == 0.02
        assert params.allow_short is False

    def test_invalid_lookback(self) -> None:
        """lookback_days 为 0 应失败。"""
        with pytest.raises(Exception):
            MeanVarianceParams(lookback_days=0)

    def test_serialization(self) -> None:
        """参数可序列化。"""
        params = MeanVarianceParams(
            lookback_days=90,
            objective=OptObjective.TARGET_RETURN,
            target_annual_return=0.12,
        )
        d = params.model_dump()
        assert d["lookback_days"] == 90
        assert d["objective"] == "target_return"
        assert d["target_annual_return"] == 0.12


# ---------------------------------------------------------------------------
# BlackLittermanParams 测试
# ---------------------------------------------------------------------------


class TestBlackLittermanParams:
    """BL 参数验证测试。"""

    def test_valid_params(self) -> None:
        """有效参数创建成功。"""
        params = BlackLittermanParams(
            lookback_days=90,
            tau=0.1,
            risk_aversion=3.0,
        )
        assert params.tau == 0.1
        assert params.risk_aversion == 3.0

    def test_default_params(self) -> None:
        """默认参数值正确。"""
        params = BlackLittermanParams()
        assert params.lookback_days == 60
        assert params.tau == 0.05
        assert params.risk_aversion == 2.5
        assert params.risk_free_rate == 0.02

    def test_invalid_tau_zero(self) -> None:
        """tau 为 0 应失败。"""
        with pytest.raises(Exception):
            BlackLittermanParams(tau=0)

    def test_invalid_risk_aversion_zero(self) -> None:
        """risk_aversion 为 0 应失败。"""
        with pytest.raises(Exception):
            BlackLittermanParams(risk_aversion=0)


# ---------------------------------------------------------------------------
# MeanVarianceStrategy 策略测试
# ---------------------------------------------------------------------------


class TestMeanVarianceStrategy:
    """均值-方差策略测试。"""

    def test_creation(self) -> None:
        """策略创建。"""
        params = MeanVarianceParams(
            lookback_days=60,
            objective=OptObjective.MAX_SHARPE,
        )
        strategy = MeanVarianceStrategy(params=params, universe=["A", "B", "C"])
        assert strategy.name == "mean_variance"
        assert strategy.mv_params.objective == OptObjective.MAX_SHARPE

    def test_first_day_rebalances(self) -> None:
        """第一个交易日应触发调仓。"""
        nav_data = _build_nav_data()
        params = MeanVarianceParams(lookback_days=60)
        strategy = MeanVarianceStrategy(
            params=params, universe=["A", "B", "C"]
        )

        ctx = _make_context(
            current_date=date(2024, 3, 30),
            cash=Decimal("100000"),
            nav_data=nav_data,
            cutoff_date=date(2024, 3, 29),
        )
        orders = strategy.on_bar(ctx)
        assert len(orders) > 0

    def test_non_rebalance_day_no_orders(self) -> None:
        """非调仓日不产生订单。"""
        nav_data = _build_nav_data()
        params = MeanVarianceParams(lookback_days=60)
        strategy = MeanVarianceStrategy(
            params=params, universe=["A", "B", "C"]
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

    def test_min_variance_objective(self) -> None:
        """最小方差目标产生有效调仓。"""
        nav_data = _build_nav_data()
        params = MeanVarianceParams(
            lookback_days=60,
            objective=OptObjective.MIN_VARIANCE,
        )
        strategy = MeanVarianceStrategy(
            params=params, universe=["A", "B", "C"]
        )

        ctx = _make_context(
            current_date=date(2024, 3, 30),
            cash=Decimal("100000"),
            nav_data=nav_data,
            cutoff_date=date(2024, 3, 29),
        )
        orders = strategy.on_bar(ctx)
        assert len(orders) > 0

    def test_insufficient_data_no_orders(self) -> None:
        """数据不足时不产生订单。"""
        start = date(2024, 1, 1)
        nav_data: dict[str, dict[date, Decimal]] = {
            "A": {start + timedelta(days=i): Decimal("1.0") for i in range(5)},
            "B": {start + timedelta(days=i): Decimal("1.0") for i in range(5)},
        }

        params = MeanVarianceParams(lookback_days=60)
        strategy = MeanVarianceStrategy(params=params, universe=["A", "B"])

        ctx = _make_context(
            current_date=date(2024, 1, 6),
            cash=Decimal("100000"),
            nav_data=nav_data,
            cutoff_date=date(2024, 1, 5),
        )
        orders = strategy.on_bar(ctx)
        assert len(orders) == 0

    def test_empty_universe(self) -> None:
        """空基金池不产生订单。"""
        params = MeanVarianceParams(lookback_days=60)
        strategy = MeanVarianceStrategy(params=params, universe=[])

        ctx = _make_context(
            current_date=date(2024, 3, 1),
            cash=Decimal("100000"),
            nav_data={},
        )
        orders = strategy.on_bar(ctx)
        assert len(orders) == 0

    def test_last_diagnostics_after_rebalance(self) -> None:
        """调仓后可读取优化约束和权重诊断。"""
        nav_data = _build_nav_data()
        params = MeanVarianceParams(
            lookback_days=60,
            objective=OptObjective.MAX_SHARPE,
            max_weight=0.4,
        )
        strategy = MeanVarianceStrategy(params=params, universe=["A", "B", "C"])

        ctx = _make_context(
            current_date=date(2024, 3, 30),
            cash=Decimal("100000"),
            nav_data=nav_data,
            cutoff_date=date(2024, 3, 29),
        )
        orders = strategy.on_bar(ctx)

        assert len(orders) > 0
        assert strategy.last_diagnostics["constraints"]["max_weight"] == 0.4  # type: ignore[index]
        assert "weight_diagnostics" in strategy.last_diagnostics

    def test_turnover_limit_caps_rebalance_amount(self) -> None:
        """turnover_limit 应限制单次调仓规模。"""
        nav_data = _build_nav_data()
        params = MeanVarianceParams(
            lookback_days=60,
            objective=OptObjective.MAX_SHARPE,
            max_weight=0.4,
            turnover_limit=0.05,
        )
        strategy = MeanVarianceStrategy(params=params, universe=["A", "B", "C"])
        ctx = _make_context(
            current_date=date(2024, 3, 30),
            cash=Decimal("50000"),
            positions={"A": Decimal("50000")},
            nav_data=nav_data,
            cutoff_date=date(2024, 3, 29),
        )
        orders = strategy.on_bar(ctx)
        traded = Decimal("0")
        for order in orders:
            nav = ctx.nav(order.fund_code) or Decimal("0")
            if order.amount is not None:
                traded += order.amount
            elif order.shares is not None:
                traded += order.shares * nav
        total_value = ctx.cash + ctx.positions["A"] * (ctx.nav("A") or Decimal("0"))
        assert traded <= total_value * Decimal("0.101")
        assert strategy.last_diagnostics["turnover_limit"] == 0.05


# ---------------------------------------------------------------------------
# BlackLittermanStrategy 策略测试
# ---------------------------------------------------------------------------


class TestBlackLittermanStrategy:
    """Black-Litterman 策略测试。"""

    def test_creation(self) -> None:
        """策略创建。"""
        params = BlackLittermanParams(tau=0.05, risk_aversion=2.5)
        strategy = BlackLittermanStrategy(
            params=params, universe=["A", "B", "C"]
        )
        assert strategy.name == "black_litterman"
        assert strategy.bl_params.tau == 0.05

    def test_no_views_produces_orders(self) -> None:
        """无观点时仍应产生调仓指令（基于均衡收益率）。"""
        nav_data = _build_nav_data()
        params = BlackLittermanParams(lookback_days=60)
        strategy = BlackLittermanStrategy(
            params=params, universe=["A", "B", "C"]
        )

        ctx = _make_context(
            current_date=date(2024, 3, 30),
            cash=Decimal("100000"),
            nav_data=nav_data,
            cutoff_date=date(2024, 3, 29),
        )
        orders = strategy.on_bar(ctx)
        assert len(orders) > 0

    def test_black_litterman_respects_max_weight(self) -> None:
        """Black-Litterman 也应受单基金权重上限约束。"""
        nav_data = _build_nav_data()
        params = BlackLittermanParams(lookback_days=60, max_weight=0.4)
        strategy = BlackLittermanStrategy(params=params, universe=["A", "B", "C"])
        strategy.set_views([View(p_row=[1.0, 0.0, 0.0], q=1.0, confidence=0.95)])
        ctx = _make_context(
            current_date=date(2024, 3, 30),
            cash=Decimal("100000"),
            nav_data=nav_data,
            cutoff_date=date(2024, 3, 29),
        )
        orders = strategy.on_bar(ctx)
        assert len(orders) > 0
        assert strategy.last_diagnostics["constraints"]["max_weight"] == 0.4  # type: ignore[index]
        assert strategy.last_diagnostics["weight_diagnostics"]["top1_weight"] <= 0.4001  # type: ignore[index]

    def test_with_absolute_view(self) -> None:
        """设置绝对观点后产生调仓指令。"""
        nav_data = _build_nav_data()
        params = BlackLittermanParams(lookback_days=60)
        strategy = BlackLittermanStrategy(
            params=params, universe=["A", "B", "C"]
        )

        # 绝对观点：看好基金 A（年化 15%）
        strategy.set_views([
            View(p_row=[1.0, 0.0, 0.0], q=0.15, confidence=0.8),
        ])

        ctx = _make_context(
            current_date=date(2024, 3, 30),
            cash=Decimal("100000"),
            nav_data=nav_data,
            cutoff_date=date(2024, 3, 29),
        )
        orders = strategy.on_bar(ctx)
        assert len(orders) > 0

    def test_with_relative_view(self) -> None:
        """设置相对观点后产生调仓指令。"""
        nav_data = _build_nav_data()
        params = BlackLittermanParams(lookback_days=60)
        strategy = BlackLittermanStrategy(
            params=params, universe=["A", "B", "C"]
        )

        # 相对观点：A 比 C 多涨 8%
        strategy.set_views([
            View(p_row=[1.0, 0.0, -1.0], q=0.08, confidence=0.7),
        ])

        ctx = _make_context(
            current_date=date(2024, 3, 30),
            cash=Decimal("100000"),
            nav_data=nav_data,
            cutoff_date=date(2024, 3, 29),
        )
        orders = strategy.on_bar(ctx)
        assert len(orders) > 0

    def test_non_rebalance_day_no_orders(self) -> None:
        """非调仓日不产生订单。"""
        nav_data = _build_nav_data()
        params = BlackLittermanParams(lookback_days=60)
        strategy = BlackLittermanStrategy(
            params=params, universe=["A", "B", "C"]
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

    def test_invalid_view_dimension_ignored(self) -> None:
        """观点维度不匹配时被忽略。"""
        nav_data = _build_nav_data()
        params = BlackLittermanParams(lookback_days=60)
        strategy = BlackLittermanStrategy(
            params=params, universe=["A", "B", "C"]
        )

        # 观点维度为 2，但有 3 只基金 → 被忽略
        strategy.set_views([
            View(p_row=[1.0, 0.0], q=0.10, confidence=0.8),
        ])

        ctx = _make_context(
            current_date=date(2024, 3, 30),
            cash=Decimal("100000"),
            nav_data=nav_data,
            cutoff_date=date(2024, 3, 29),
        )
        # 应仍能产生订单（退化为无观点模式）
        orders = strategy.on_bar(ctx)
        assert len(orders) > 0

    def test_empty_universe(self) -> None:
        """空基金池不产生订单。"""
        params = BlackLittermanParams(lookback_days=60)
        strategy = BlackLittermanStrategy(params=params, universe=[])

        ctx = _make_context(
            current_date=date(2024, 3, 1),
            cash=Decimal("100000"),
            nav_data={},
        )
        orders = strategy.on_bar(ctx)
        assert len(orders) == 0

    def test_insufficient_data_no_orders(self) -> None:
        """数据不足时不产生订单。"""
        start = date(2024, 1, 1)
        nav_data: dict[str, dict[date, Decimal]] = {
            "A": {start + timedelta(days=i): Decimal("1.0") for i in range(5)},
            "B": {start + timedelta(days=i): Decimal("1.0") for i in range(5)},
        }

        params = BlackLittermanParams(lookback_days=60)
        strategy = BlackLittermanStrategy(params=params, universe=["A", "B"])

        ctx = _make_context(
            current_date=date(2024, 1, 6),
            cash=Decimal("100000"),
            nav_data=nav_data,
            cutoff_date=date(2024, 1, 5),
        )
        orders = strategy.on_bar(ctx)
        assert len(orders) == 0
