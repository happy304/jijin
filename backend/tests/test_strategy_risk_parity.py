"""风险平价策略单元测试。

覆盖：
- RiskParityStrategy: 风险平价策略
- RiskParityParams: 参数验证
- estimate_covariance_*: 协方差估计方法
- risk_parity_weights: 权重求解
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
from app.domain.strategy.risk_parity import (
    CovMethod,
    RebalanceFreq,
    RiskParityParams,
    RiskParityStrategy,
    estimate_covariance,
    estimate_covariance_ewm,
    estimate_covariance_sample,
    estimate_covariance_shrinkage,
    is_rebalance_day,
    risk_parity_weights,
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


def _make_nav_series(
    start: date,
    values: list[Decimal],
) -> dict[date, Decimal]:
    """生成连续日期的净值序列。"""
    result: dict[date, Decimal] = {}
    for i, val in enumerate(values):
        result[start + timedelta(days=i)] = val
    return result


# ---------------------------------------------------------------------------
# is_rebalance_day 测试
# ---------------------------------------------------------------------------


class TestIsRebalanceDay:
    """调仓日判断逻辑测试。"""

    def test_first_day_always_rebalance(self) -> None:
        """首次调仓（无上次日期）应返回 True。"""
        assert is_rebalance_day(date(2024, 1, 2), None, RebalanceFreq.WEEKLY) is True
        assert is_rebalance_day(date(2024, 1, 2), None, RebalanceFreq.MONTHLY) is True
        assert is_rebalance_day(date(2024, 1, 2), None, RebalanceFreq.QUARTERLY) is True

    def test_weekly_frequency(self) -> None:
        """周频：距上次 >= 7 天才调仓。"""
        last = date(2024, 1, 1)
        assert is_rebalance_day(date(2024, 1, 7), last, RebalanceFreq.WEEKLY) is False
        assert is_rebalance_day(date(2024, 1, 8), last, RebalanceFreq.WEEKLY) is True

    def test_monthly_frequency(self) -> None:
        """月频：距上次 >= 28 天才调仓。"""
        last = date(2024, 1, 1)
        assert is_rebalance_day(date(2024, 1, 28), last, RebalanceFreq.MONTHLY) is False
        assert is_rebalance_day(date(2024, 1, 29), last, RebalanceFreq.MONTHLY) is True

    def test_quarterly_frequency(self) -> None:
        """季频：距上次 >= 84 天才调仓。"""
        last = date(2024, 1, 1)
        assert is_rebalance_day(date(2024, 3, 24), last, RebalanceFreq.QUARTERLY) is False
        assert is_rebalance_day(date(2024, 3, 25), last, RebalanceFreq.QUARTERLY) is True


# ---------------------------------------------------------------------------
# 协方差估计测试
# ---------------------------------------------------------------------------


class TestEstimateCovariance:
    """协方差矩阵估计方法测试。"""

    def _make_returns(self) -> np.ndarray:
        """构建已知的收益率矩阵用于测试。

        两只资产：
        - 资产 A: 波动率较低
        - 资产 B: 波动率较高
        """
        np.random.seed(42)
        n_days = 60
        # 资产 A: 均值 0.001, 标准差 0.01
        # 资产 B: 均值 0.002, 标准差 0.03
        returns_a = np.random.normal(0.001, 0.01, n_days)
        returns_b = np.random.normal(0.002, 0.03, n_days)
        return np.column_stack([returns_a, returns_b])

    def test_sample_covariance_shape(self) -> None:
        """样本协方差矩阵形状正确。"""
        returns = self._make_returns()
        cov = estimate_covariance_sample(returns)
        assert cov.shape == (2, 2)

    def test_sample_covariance_symmetric(self) -> None:
        """样本协方差矩阵对称。"""
        returns = self._make_returns()
        cov = estimate_covariance_sample(returns)
        np.testing.assert_allclose(cov, cov.T, atol=1e-15)

    def test_sample_covariance_positive_diagonal(self) -> None:
        """样本协方差矩阵对角线为正（方差 > 0）。"""
        returns = self._make_returns()
        cov = estimate_covariance_sample(returns)
        assert np.all(np.diag(cov) > 0)

    def test_ewm_covariance_shape(self) -> None:
        """指数加权协方差矩阵形状正确。"""
        returns = self._make_returns()
        cov = estimate_covariance_ewm(returns, halflife=30.0)
        assert cov.shape == (2, 2)

    def test_ewm_covariance_symmetric(self) -> None:
        """指数加权协方差矩阵对称。"""
        returns = self._make_returns()
        cov = estimate_covariance_ewm(returns, halflife=30.0)
        np.testing.assert_allclose(cov, cov.T, atol=1e-15)

    def test_ewm_default_halflife(self) -> None:
        """EWM 不传 halflife 时使用默认值（T/2）。"""
        returns = self._make_returns()
        cov = estimate_covariance_ewm(returns)
        assert cov.shape == (2, 2)
        assert np.all(np.isfinite(cov))

    def test_shrinkage_covariance_shape(self) -> None:
        """收缩协方差矩阵形状正确。"""
        returns = self._make_returns()
        cov = estimate_covariance_shrinkage(returns)
        assert cov.shape == (2, 2)

    def test_shrinkage_covariance_symmetric(self) -> None:
        """收缩协方差矩阵对称。"""
        returns = self._make_returns()
        cov = estimate_covariance_shrinkage(returns)
        np.testing.assert_allclose(cov, cov.T, atol=1e-15)

    def test_shrinkage_reduces_extreme_values(self) -> None:
        """收缩估计应减少极端值（向目标收缩）。"""
        returns = self._make_returns()
        sample_cov = estimate_covariance_sample(returns)
        shrunk_cov = estimate_covariance_shrinkage(returns)
        # 收缩后的非对角元素绝对值应 <= 样本协方差
        # （收缩向对角矩阵靠拢）
        off_diag_sample = abs(sample_cov[0, 1])
        off_diag_shrunk = abs(shrunk_cov[0, 1])
        assert off_diag_shrunk <= off_diag_sample + 1e-10

    def test_estimate_covariance_dispatches(self) -> None:
        """estimate_covariance 正确分发到各方法。"""
        returns = self._make_returns()
        cov_sample = estimate_covariance(returns, CovMethod.SAMPLE)
        cov_ewm = estimate_covariance(returns, CovMethod.EWM, halflife=30.0)
        cov_shrink = estimate_covariance(returns, CovMethod.SHRINKAGE)
        # 三种方法结果不同
        assert not np.allclose(cov_sample, cov_ewm)
        assert not np.allclose(cov_sample, cov_shrink)


# ---------------------------------------------------------------------------
# risk_parity_weights 测试
# ---------------------------------------------------------------------------


class TestRiskParityWeights:
    """风险平价权重求解测试。"""

    def test_two_equal_assets(self) -> None:
        """两只相同波动率且不相关的资产应等权。"""
        # 对角协方差矩阵，两只资产方差相同
        cov = np.array([[0.04, 0.0], [0.0, 0.04]])
        weights = risk_parity_weights(cov)
        assert weights is not None
        np.testing.assert_allclose(weights, [0.5, 0.5], atol=1e-4)

    def test_two_different_vol_assets(self) -> None:
        """两只不同波动率的不相关资产：低波资产权重更高。"""
        # 资产 A 方差 0.01（波动率 10%），资产 B 方差 0.04（波动率 20%）
        cov = np.array([[0.01, 0.0], [0.0, 0.04]])
        weights = risk_parity_weights(cov)
        assert weights is not None
        # 低波动资产权重更高
        assert weights[0] > weights[1]
        # 权重和为 1
        np.testing.assert_allclose(weights.sum(), 1.0, atol=1e-6)

    def test_three_assets_known_case(self) -> None:
        """三只不相关资产的已知案例对拍。

        对于不相关资产，风险平价权重 = 逆波动率权重（归一化）。
        σ = [0.1, 0.2, 0.3] → 1/σ = [10, 5, 3.33]
        归一化: [10/18.33, 5/18.33, 3.33/18.33] ≈ [0.545, 0.273, 0.182]
        """
        cov = np.diag([0.01, 0.04, 0.09])
        weights = risk_parity_weights(cov)
        assert weights is not None

        # 对于不相关资产，风险平价 = 逆波动率权重
        inv_vol = 1.0 / np.sqrt(np.diag(cov))
        expected = inv_vol / inv_vol.sum()
        np.testing.assert_allclose(weights, expected, atol=1e-3)

    def test_correlated_assets(self) -> None:
        """相关资产的风险平价权重验证。"""
        # 两只资产，相关系数 0.5
        # σ_A = 0.1, σ_B = 0.2, ρ = 0.5
        cov = np.array([
            [0.01, 0.01],  # 0.1 * 0.2 * 0.5 = 0.01
            [0.01, 0.04],
        ])
        weights = risk_parity_weights(cov)
        assert weights is not None
        # 权重和为 1
        np.testing.assert_allclose(weights.sum(), 1.0, atol=1e-6)
        # 低波动资产权重更高
        assert weights[0] > weights[1]

        # 验证风险贡献相等
        marginal = cov @ weights
        risk_contrib = weights * marginal
        # 各资产风险贡献应近似相等
        np.testing.assert_allclose(
            risk_contrib[0], risk_contrib[1], atol=1e-4
        )

    def test_single_asset(self) -> None:
        """单只资产权重为 1。"""
        cov = np.array([[0.04]])
        weights = risk_parity_weights(cov)
        assert weights is not None
        np.testing.assert_allclose(weights, [1.0], atol=1e-6)

    def test_empty_matrix(self) -> None:
        """空矩阵返回 None。"""
        cov = np.array([]).reshape(0, 0)
        weights = risk_parity_weights(cov)
        assert weights is None

    def test_weights_sum_to_one(self) -> None:
        """权重和为 1。"""
        np.random.seed(123)
        n = 5
        # 生成随机正定协方差矩阵
        a = np.random.randn(100, n)
        cov = np.cov(a, rowvar=False)
        weights = risk_parity_weights(cov)
        assert weights is not None
        np.testing.assert_allclose(weights.sum(), 1.0, atol=1e-6)

    def test_weights_non_negative(self) -> None:
        """权重非负。"""
        np.random.seed(456)
        n = 4
        a = np.random.randn(100, n)
        cov = np.cov(a, rowvar=False)
        weights = risk_parity_weights(cov)
        assert weights is not None
        assert np.all(weights >= -1e-10)

    def test_equal_risk_contribution(self) -> None:
        """验证求解结果满足等风险贡献条件。"""
        # 构建一个有相关性的 3 资产协方差矩阵
        cov = np.array([
            [0.04, 0.006, 0.002],
            [0.006, 0.09, 0.009],
            [0.002, 0.009, 0.16],
        ])
        weights = risk_parity_weights(cov)
        assert weights is not None

        # 计算各资产风险贡献
        marginal = cov @ weights
        risk_contrib = weights * marginal
        total_risk = risk_contrib.sum()

        # 各资产风险贡献占比应近似相等（1/3）
        rc_pct = risk_contrib / total_risk
        np.testing.assert_allclose(rc_pct, [1 / 3, 1 / 3, 1 / 3], atol=1e-3)


# ---------------------------------------------------------------------------
# RiskParityParams 测试
# ---------------------------------------------------------------------------


class TestRiskParityParams:
    """风险平价参数验证测试。"""

    def test_valid_params(self) -> None:
        """有效参数创建成功。"""
        params = RiskParityParams(
            lookback_days=90,
            rebalance_freq=RebalanceFreq.WEEKLY,
            cov_method=CovMethod.EWM,
        )
        assert params.lookback_days == 90
        assert params.rebalance_freq == RebalanceFreq.WEEKLY
        assert params.cov_method == CovMethod.EWM

    def test_default_params(self) -> None:
        """默认参数值正确。"""
        params = RiskParityParams()
        assert params.lookback_days == 60
        assert params.rebalance_freq == RebalanceFreq.MONTHLY
        assert params.cov_method == CovMethod.SAMPLE

    def test_invalid_lookback_zero(self) -> None:
        """lookback_days 为 0 应失败。"""
        with pytest.raises(Exception):
            RiskParityParams(lookback_days=0)

    def test_invalid_lookback_negative(self) -> None:
        """lookback_days 为负应失败。"""
        with pytest.raises(Exception):
            RiskParityParams(lookback_days=-10)

    def test_serialization(self) -> None:
        """参数可序列化。"""
        params = RiskParityParams(
            lookback_days=90,
            rebalance_freq=RebalanceFreq.QUARTERLY,
            cov_method=CovMethod.SHRINKAGE,
        )
        d = params.model_dump()
        assert d["lookback_days"] == 90
        assert d["rebalance_freq"] == "quarterly"
        assert d["cov_method"] == "shrinkage"


# ---------------------------------------------------------------------------
# RiskParityStrategy 策略测试
# ---------------------------------------------------------------------------


class TestRiskParityStrategy:
    """风险平价策略测试。"""

    def _build_nav_data(self, n_days: int = 90) -> dict[str, dict[date, Decimal]]:
        """构建 3 只基金的测试净值数据。

        基金 A: 低波动（日波动率 ~0.5%）
        基金 B: 中波动（日波动率 ~1.0%）
        基金 C: 高波动（日波动率 ~2.0%）
        """
        np.random.seed(42)
        start = date(2024, 1, 1)

        nav_data: dict[str, dict[date, Decimal]] = {}

        # 基金 A: 低波动
        nav_a = [1.0]
        for _ in range(n_days - 1):
            nav_a.append(nav_a[-1] * (1 + np.random.normal(0.0005, 0.005)))
        nav_data["A"] = {
            start + timedelta(days=i): Decimal(str(round(v, 6)))
            for i, v in enumerate(nav_a)
        }

        # 基金 B: 中波动
        nav_b = [1.0]
        for _ in range(n_days - 1):
            nav_b.append(nav_b[-1] * (1 + np.random.normal(0.001, 0.01)))
        nav_data["B"] = {
            start + timedelta(days=i): Decimal(str(round(v, 6)))
            for i, v in enumerate(nav_b)
        }

        # 基金 C: 高波动
        nav_c = [1.0]
        for _ in range(n_days - 1):
            nav_c.append(nav_c[-1] * (1 + np.random.normal(0.002, 0.02)))
        nav_data["C"] = {
            start + timedelta(days=i): Decimal(str(round(v, 6)))
            for i, v in enumerate(nav_c)
        }

        return nav_data

    def test_creation(self) -> None:
        """策略创建。"""
        params = RiskParityParams(
            lookback_days=60,
            rebalance_freq=RebalanceFreq.MONTHLY,
            cov_method=CovMethod.SAMPLE,
        )
        strategy = RiskParityStrategy(params=params, universe=["A", "B", "C"])
        assert strategy.name == "risk_parity"
        assert strategy.risk_parity_params.lookback_days == 60
        assert strategy.risk_parity_params.cov_method == CovMethod.SAMPLE

    def test_first_day_rebalances(self) -> None:
        """第一个交易日应触发调仓。"""
        nav_data = self._build_nav_data()
        params = RiskParityParams(
            lookback_days=60,
            rebalance_freq=RebalanceFreq.MONTHLY,
            cov_method=CovMethod.SAMPLE,
        )
        strategy = RiskParityStrategy(
            params=params, universe=["A", "B", "C"]
        )

        ctx = _make_context(
            current_date=date(2024, 3, 30),
            cash=Decimal("100000"),
            nav_data=nav_data,
            cutoff_date=date(2024, 3, 29),
        )
        orders = strategy.on_bar(ctx)
        # 应该有调仓指令
        assert len(orders) > 0

    def test_low_vol_gets_higher_weight(self) -> None:
        """低波动资产应获得更高权重。"""
        nav_data = self._build_nav_data()
        params = RiskParityParams(
            lookback_days=60,
            rebalance_freq=RebalanceFreq.MONTHLY,
            cov_method=CovMethod.SAMPLE,
        )
        strategy = RiskParityStrategy(
            params=params, universe=["A", "B", "C"]
        )

        ctx = _make_context(
            current_date=date(2024, 3, 30),
            cash=Decimal("100000"),
            nav_data=nav_data,
            cutoff_date=date(2024, 3, 29),
        )
        orders = strategy.on_bar(ctx)

        # 找到各基金的申购金额
        amounts: dict[str, Decimal] = {}
        for o in orders:
            if o.direction == "subscribe" and o.amount is not None:
                amounts[o.fund_code] = o.amount

        # 低波动基金 A 的配置金额应 >= 高波动基金 C
        if "A" in amounts and "C" in amounts:
            assert amounts["A"] >= amounts["C"]

    def test_non_rebalance_day_no_orders(self) -> None:
        """非调仓日不产生订单。"""
        nav_data = self._build_nav_data()
        params = RiskParityParams(
            lookback_days=60,
            rebalance_freq=RebalanceFreq.MONTHLY,
            cov_method=CovMethod.SAMPLE,
        )
        strategy = RiskParityStrategy(
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

        # 第二天不应调仓
        ctx2 = _make_context(
            current_date=date(2024, 3, 2),
            cash=Decimal("100000"),
            nav_data=nav_data,
            cutoff_date=date(2024, 3, 1),
        )
        orders = strategy.on_bar(ctx2)
        assert len(orders) == 0

    def test_insufficient_data_no_orders(self) -> None:
        """数据不足时不产生订单。"""
        # 只有 5 天数据，不足以计算协方差
        start = date(2024, 1, 1)
        nav_data: dict[str, dict[date, Decimal]] = {
            "A": {start + timedelta(days=i): Decimal("1.0") for i in range(5)},
            "B": {start + timedelta(days=i): Decimal("1.0") for i in range(5)},
        }

        params = RiskParityParams(lookback_days=60)
        strategy = RiskParityStrategy(
            params=params, universe=["A", "B"]
        )

        ctx = _make_context(
            current_date=date(2024, 1, 6),
            cash=Decimal("100000"),
            nav_data=nav_data,
            cutoff_date=date(2024, 1, 5),
        )
        orders = strategy.on_bar(ctx)
        assert len(orders) == 0

    def test_single_fund_no_orders(self) -> None:
        """单只基金无法计算协方差，不产生订单。"""
        nav_data = self._build_nav_data()
        params = RiskParityParams(lookback_days=60)
        strategy = RiskParityStrategy(params=params, universe=["A"])

        ctx = _make_context(
            current_date=date(2024, 3, 30),
            cash=Decimal("100000"),
            nav_data=nav_data,
            cutoff_date=date(2024, 3, 29),
        )
        orders = strategy.on_bar(ctx)
        assert len(orders) == 0

    def test_ewm_method(self) -> None:
        """使用 EWM 协方差估计方法。"""
        nav_data = self._build_nav_data()
        params = RiskParityParams(
            lookback_days=60,
            rebalance_freq=RebalanceFreq.MONTHLY,
            cov_method=CovMethod.EWM,
        )
        strategy = RiskParityStrategy(
            params=params, universe=["A", "B", "C"]
        )

        ctx = _make_context(
            current_date=date(2024, 3, 30),
            cash=Decimal("100000"),
            nav_data=nav_data,
            cutoff_date=date(2024, 3, 29),
        )
        orders = strategy.on_bar(ctx)
        # EWM 方法也应产生有效调仓指令
        assert len(orders) > 0

    def test_shrinkage_method(self) -> None:
        """使用收缩协方差估计方法。"""
        nav_data = self._build_nav_data()
        params = RiskParityParams(
            lookback_days=60,
            rebalance_freq=RebalanceFreq.MONTHLY,
            cov_method=CovMethod.SHRINKAGE,
        )
        strategy = RiskParityStrategy(
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

    def test_weekly_rebalance(self) -> None:
        """周频调仓：7 天后再次调仓。"""
        nav_data = self._build_nav_data()
        params = RiskParityParams(
            lookback_days=60,
            rebalance_freq=RebalanceFreq.WEEKLY,
            cov_method=CovMethod.SAMPLE,
        )
        strategy = RiskParityStrategy(
            params=params, universe=["A", "B", "C"]
        )

        # 第一天调仓
        ctx1 = _make_context(
            current_date=date(2024, 3, 1),
            cash=Decimal("100000"),
            nav_data=nav_data,
            cutoff_date=date(2024, 2, 29),
        )
        orders1 = strategy.on_bar(ctx1)
        assert len(orders1) > 0

        # 5 天后不调仓
        ctx2 = _make_context(
            current_date=date(2024, 3, 6),
            cash=Decimal("100000"),
            nav_data=nav_data,
            cutoff_date=date(2024, 3, 5),
        )
        orders2 = strategy.on_bar(ctx2)
        assert len(orders2) == 0

        # 8 天后调仓
        ctx3 = _make_context(
            current_date=date(2024, 3, 9),
            cash=Decimal("100000"),
            nav_data=nav_data,
            cutoff_date=date(2024, 3, 8),
        )
        orders3 = strategy.on_bar(ctx3)
        assert len(orders3) > 0

    def test_empty_universe(self) -> None:
        """空基金池不产生订单。"""
        params = RiskParityParams(lookback_days=60)
        strategy = RiskParityStrategy(params=params, universe=[])

        ctx = _make_context(
            current_date=date(2024, 3, 1),
            cash=Decimal("100000"),
            nav_data={},
        )
        orders = strategy.on_bar(ctx)
        assert len(orders) == 0

    def test_partial_nav_data(self) -> None:
        """部分基金无净值数据时只使用有数据的基金。"""
        nav_data = self._build_nav_data()
        # 只有 A 和 B 有数据，C 无数据
        del nav_data["C"]

        params = RiskParityParams(lookback_days=60)
        strategy = RiskParityStrategy(
            params=params, universe=["A", "B", "C"]
        )

        ctx = _make_context(
            current_date=date(2024, 3, 30),
            cash=Decimal("100000"),
            nav_data=nav_data,
            cutoff_date=date(2024, 3, 29),
        )
        orders = strategy.on_bar(ctx)

        # 应该只对 A 和 B 产生调仓指令
        codes = {o.fund_code for o in orders}
        assert "C" not in codes
        if len(orders) > 0:
            assert codes <= {"A", "B"}
