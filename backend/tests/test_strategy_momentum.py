"""基金轮动策略单元测试。

覆盖：
- MomentumRotation: 动量轮动策略
- MomentumParams: 参数验证
- compute_return_score: 收益率评分
- compute_sharpe_score: Sharpe 评分
- is_rebalance_day: 调仓日判断

需求: 5.2
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.domain.backtest.engine_event import BarContext
from app.domain.backtest.order import OrderIntent
from app.domain.backtest.portfolio import Portfolio
from app.domain.strategy.momentum import (
    MomentumParams,
    MomentumRotation,
    RebalanceFreq,
    ScoreMethod,
    compute_return_score,
    compute_score,
    compute_sharpe_score,
    is_rebalance_day,
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

    def test_weekly_frequency(self) -> None:
        """周频：距上次 >= 7 天才调仓。"""
        last = date(2024, 1, 1)
        assert is_rebalance_day(date(2024, 1, 7), last, RebalanceFreq.WEEKLY) is False
        assert is_rebalance_day(date(2024, 1, 8), last, RebalanceFreq.WEEKLY) is True
        assert is_rebalance_day(date(2024, 1, 15), last, RebalanceFreq.WEEKLY) is True

    def test_monthly_frequency(self) -> None:
        """月频：距上次 >= 28 天才调仓。"""
        last = date(2024, 1, 1)
        assert is_rebalance_day(date(2024, 1, 28), last, RebalanceFreq.MONTHLY) is False
        assert is_rebalance_day(date(2024, 1, 29), last, RebalanceFreq.MONTHLY) is True
        assert is_rebalance_day(date(2024, 2, 15), last, RebalanceFreq.MONTHLY) is True


# ---------------------------------------------------------------------------
# compute_return_score 测试
# ---------------------------------------------------------------------------


class TestComputeReturnScore:
    """收益率评分计算测试。"""

    def test_empty_series(self) -> None:
        """空序列返回 None。"""
        assert compute_return_score({}, 20) is None

    def test_single_point(self) -> None:
        """单个数据点返回 None。"""
        nav = {date(2024, 1, 1): Decimal("1.0")}
        assert compute_return_score(nav, 20) is None

    def test_positive_return(self) -> None:
        """正收益率计算正确（数据足够完整窗口）。"""
        # 从 1.0 涨到 1.5，中间有足够数据点覆盖 lookback=5
        values = [Decimal("1.0"), Decimal("1.1"), Decimal("1.2"),
                  Decimal("1.3"), Decimal("1.4"), Decimal("1.5")]
        nav = _make_nav_series(date(2024, 1, 1), values)
        score = compute_return_score(nav, 4)  # lookback=4, 需要 >4 个点
        assert score is not None
        # start_idx = 6 - 4 - 1 = 1, start_nav = 1.1, latest_nav = 1.5
        expected = (1.5 / 1.1) - 1.0
        assert abs(score - expected) < 1e-10

    def test_negative_return(self) -> None:
        """负收益率计算正确（数据足够完整窗口）。"""
        # 从 2.0 跌到 1.0，中间有足够数据点
        values = [Decimal("2.0"), Decimal("1.8"), Decimal("1.6"),
                  Decimal("1.4"), Decimal("1.2"), Decimal("1.0")]
        nav = _make_nav_series(date(2024, 1, 1), values)
        score = compute_return_score(nav, 4)  # lookback=4, 需要 >4 个点
        assert score is not None
        # start_idx = 6 - 4 - 1 = 1, start_nav = 1.8, latest_nav = 1.0
        expected = (1.0 / 1.8) - 1.0
        assert abs(score - expected) < 1e-10

    def test_lookback_window(self) -> None:
        """回看窗口正确截取数据。"""
        # 10 个数据点，lookback=5
        # 应该从 index=4 (第5个) 到 index=9 (最后一个) 计算
        values = [Decimal(str(1.0 + i * 0.1)) for i in range(10)]
        nav = _make_nav_series(date(2024, 1, 1), values)
        score = compute_return_score(nav, 5)
        assert score is not None
        # start_idx = 10 - 5 - 1 = 4, start_nav = 1.4, latest_nav = 1.9
        expected = (1.9 / 1.4) - 1.0
        assert abs(score - expected) < 1e-10

    def test_insufficient_data_returns_none(self) -> None:
        """数据不足窗口长度时返回 None（避免不同基金评分窗口不一致）。"""
        # 3 个数据点，lookback=10，数据不足
        nav = _make_nav_series(
            date(2024, 1, 1),
            [Decimal("1.0"), Decimal("1.2"), Decimal("1.5")],
        )
        score = compute_return_score(nav, 10)
        assert score is None


# ---------------------------------------------------------------------------
# compute_sharpe_score 测试
# ---------------------------------------------------------------------------


class TestComputeSharpeScore:
    """Sharpe 评分计算测试。"""

    def test_empty_series(self) -> None:
        """空序列返回 None。"""
        assert compute_sharpe_score({}, 20) is None

    def test_insufficient_data(self) -> None:
        """数据不足 lookback 窗口返回 None。"""
        nav = _make_nav_series(date(2024, 1, 1), [Decimal("1.0"), Decimal("1.1")])
        assert compute_sharpe_score(nav, 20) is None

    def test_constant_nav_returns_none(self) -> None:
        """净值不变（标准差为 0）返回 None。"""
        # 需要 >20 个点才能通过窗口检查
        nav = _make_nav_series(
            date(2024, 1, 1),
            [Decimal("1.0")] * 25,
        )
        assert compute_sharpe_score(nav, 20) is None

    def test_positive_sharpe(self) -> None:
        """稳定上涨的基金应有正 Sharpe。"""
        # 每天涨 0.1%，生成 35 个点以满足 lookback=30 的要求
        values = [Decimal(str(round(1.0 * 1.001**i, 6))) for i in range(35)]
        nav = _make_nav_series(date(2024, 1, 1), values)
        score = compute_sharpe_score(nav, 30)
        assert score is not None
        assert score > 0

    def test_negative_sharpe(self) -> None:
        """稳定下跌的基金应有负 Sharpe。"""
        # 每天跌 0.1%，生成 35 个点以满足 lookback=30 的要求
        values = [Decimal(str(round(1.0 * 0.999**i, 6))) for i in range(35)]
        nav = _make_nav_series(date(2024, 1, 1), values)
        score = compute_sharpe_score(nav, 30)
        assert score is not None
        assert score < 0

    def test_lookback_window_limits_data(self) -> None:
        """Sharpe 计算应受 lookback 窗口限制。"""
        # 前 50 天跌，后 10 天涨
        values_down = [Decimal(str(round(2.0 * 0.99**i, 6))) for i in range(50)]
        values_up = [Decimal(str(round(float(values_down[-1]) * 1.02**i, 6))) for i in range(10)]
        all_values = values_down + values_up
        nav = _make_nav_series(date(2024, 1, 1), all_values)

        # lookback=10 只看最近 10 天（上涨期），Sharpe 应为正
        score = compute_sharpe_score(nav, 10)
        assert score is not None
        assert score > 0


# ---------------------------------------------------------------------------
# compute_score 测试
# ---------------------------------------------------------------------------


class TestComputeScore:
    """统一评分函数测试。"""

    def test_dispatches_to_return(self) -> None:
        """ScoreMethod.RETURN 调用收益率计算。"""
        # 需要 >5 个数据点以满足 lookback=5 的要求
        values = [Decimal("1.0"), Decimal("1.04"), Decimal("1.08"),
                  Decimal("1.12"), Decimal("1.16"), Decimal("1.2")]
        nav = _make_nav_series(date(2024, 1, 1), values)
        score = compute_score(nav, 4, ScoreMethod.RETURN)
        assert score is not None
        # start_idx = 6 - 4 - 1 = 1, start_nav = 1.04, latest_nav = 1.2
        expected = (1.2 / 1.04) - 1.0
        assert abs(score - expected) < 1e-10

    def test_dispatches_to_sharpe(self) -> None:
        """ScoreMethod.SHARPE 调用 Sharpe 计算。"""
        # 需要 >30 个数据点以满足 lookback=30 的要求
        values = [Decimal(str(round(1.0 * 1.001**i, 6))) for i in range(35)]
        nav = _make_nav_series(date(2024, 1, 1), values)
        score = compute_score(nav, 30, ScoreMethod.SHARPE)
        assert score is not None
        assert score > 0


# ---------------------------------------------------------------------------
# MomentumParams 测试
# ---------------------------------------------------------------------------


class TestMomentumParams:
    """动量轮动参数验证测试。"""

    def test_valid_params(self) -> None:
        """有效参数创建成功。"""
        params = MomentumParams(
            lookback_days=60,
            top_n=5,
            rebalance_freq=RebalanceFreq.WEEKLY,
            score_method=ScoreMethod.SHARPE,
        )
        assert params.lookback_days == 60
        assert params.top_n == 5
        assert params.rebalance_freq == RebalanceFreq.WEEKLY
        assert params.score_method == ScoreMethod.SHARPE

    def test_default_params(self) -> None:
        """默认参数值正确。"""
        params = MomentumParams()
        assert params.lookback_days == 120
        assert params.top_n == 3
        assert params.rebalance_freq == RebalanceFreq.MONTHLY
        assert params.score_method == ScoreMethod.RETURN

    def test_invalid_lookback_zero(self) -> None:
        """lookback_days 为 0 应失败。"""
        with pytest.raises(Exception):
            MomentumParams(lookback_days=0)

    def test_invalid_lookback_negative(self) -> None:
        """lookback_days 为负应失败。"""
        with pytest.raises(Exception):
            MomentumParams(lookback_days=-10)

    def test_invalid_top_n_zero(self) -> None:
        """top_n 为 0 应失败。"""
        with pytest.raises(Exception):
            MomentumParams(top_n=0)

    def test_serialization(self) -> None:
        """参数可序列化。"""
        params = MomentumParams(
            lookback_days=90,
            top_n=4,
            rebalance_freq=RebalanceFreq.WEEKLY,
            score_method=ScoreMethod.SHARPE,
        )
        d = params.model_dump()
        assert d["lookback_days"] == 90
        assert d["top_n"] == 4
        assert d["rebalance_freq"] == "weekly"
        assert d["score_method"] == "sharpe"


# ---------------------------------------------------------------------------
# MomentumRotation 策略测试
# ---------------------------------------------------------------------------


class TestMomentumRotation:
    """动量轮动策略测试。"""

    def _build_nav_data(self) -> dict[str, dict[date, Decimal]]:
        """构建 5 只基金的测试净值数据。

        基金 A: 稳定上涨（最高收益）
        基金 B: 温和上涨
        基金 C: 横盘
        基金 D: 温和下跌
        基金 E: 大幅下跌（最低收益）
        """
        start = date(2024, 1, 1)
        days = 60

        nav_data: dict[str, dict[date, Decimal]] = {}

        # 基金 A: 每天涨 0.5%
        nav_data["A"] = {
            start + timedelta(days=i): Decimal(str(round(1.0 * 1.005**i, 6)))
            for i in range(days)
        }
        # 基金 B: 每天涨 0.2%
        nav_data["B"] = {
            start + timedelta(days=i): Decimal(str(round(1.0 * 1.002**i, 6)))
            for i in range(days)
        }
        # 基金 C: 横盘
        nav_data["C"] = {
            start + timedelta(days=i): Decimal("1.0")
            for i in range(days)
        }
        # 基金 D: 每天跌 0.2%
        nav_data["D"] = {
            start + timedelta(days=i): Decimal(str(round(1.0 * 0.998**i, 6)))
            for i in range(days)
        }
        # 基金 E: 每天跌 0.5%
        nav_data["E"] = {
            start + timedelta(days=i): Decimal(str(round(1.0 * 0.995**i, 6)))
            for i in range(days)
        }

        return nav_data

    def test_creation(self) -> None:
        """策略创建。"""
        params = MomentumParams(
            lookback_days=60,
            top_n=3,
            rebalance_freq=RebalanceFreq.MONTHLY,
            score_method=ScoreMethod.RETURN,
        )
        strategy = MomentumRotation(params=params, universe=["A", "B", "C"])
        assert strategy.name == "momentum_rotation"
        assert strategy.momentum_params.lookback_days == 60
        assert strategy.momentum_params.top_n == 3

    def test_first_day_rebalances(self) -> None:
        """第一个交易日应触发调仓。"""
        nav_data = self._build_nav_data()
        params = MomentumParams(
            lookback_days=30,
            top_n=2,
            rebalance_freq=RebalanceFreq.MONTHLY,
            score_method=ScoreMethod.RETURN,
        )
        strategy = MomentumRotation(
            params=params, universe=["A", "B", "C", "D", "E"]
        )

        ctx = _make_context(
            current_date=date(2024, 2, 29),
            cash=Decimal("100000"),
            nav_data=nav_data,
            cutoff_date=date(2024, 2, 28),
        )
        orders = strategy.on_bar(ctx)
        # 应该有调仓指令（选 Top-2: A 和 B）
        assert len(orders) > 0

        # 验证选中的是收益最高的基金
        subscribe_codes = {o.fund_code for o in orders if o.direction == "subscribe"}
        assert "A" in subscribe_codes
        assert "B" in subscribe_codes

    def test_non_rebalance_day_no_orders(self) -> None:
        """非调仓日不产生订单。"""
        nav_data = self._build_nav_data()
        params = MomentumParams(
            lookback_days=30,
            top_n=2,
            rebalance_freq=RebalanceFreq.MONTHLY,
            score_method=ScoreMethod.RETURN,
        )
        strategy = MomentumRotation(
            params=params, universe=["A", "B", "C", "D", "E"]
        )

        # 第一天调仓
        ctx1 = _make_context(
            current_date=date(2024, 2, 1),
            cash=Decimal("100000"),
            nav_data=nav_data,
            cutoff_date=date(2024, 1, 31),
        )
        strategy.on_bar(ctx1)

        # 第二天不应调仓
        ctx2 = _make_context(
            current_date=date(2024, 2, 2),
            cash=Decimal("100000"),
            nav_data=nav_data,
            cutoff_date=date(2024, 2, 1),
        )
        orders = strategy.on_bar(ctx2)
        assert len(orders) == 0

    def test_weekly_rebalance(self) -> None:
        """周频调仓：7 天后再次调仓。"""
        nav_data = self._build_nav_data()
        params = MomentumParams(
            lookback_days=30,
            top_n=2,
            rebalance_freq=RebalanceFreq.WEEKLY,
            score_method=ScoreMethod.RETURN,
        )
        strategy = MomentumRotation(
            params=params, universe=["A", "B", "C", "D", "E"]
        )

        # 第一天调仓
        ctx1 = _make_context(
            current_date=date(2024, 2, 1),
            cash=Decimal("100000"),
            nav_data=nav_data,
            cutoff_date=date(2024, 1, 31),
        )
        orders1 = strategy.on_bar(ctx1)
        assert len(orders1) > 0

        # 5 天后不调仓
        ctx2 = _make_context(
            current_date=date(2024, 2, 6),
            cash=Decimal("100000"),
            nav_data=nav_data,
            cutoff_date=date(2024, 2, 5),
        )
        orders2 = strategy.on_bar(ctx2)
        assert len(orders2) == 0

        # 8 天后调仓
        ctx3 = _make_context(
            current_date=date(2024, 2, 9),
            cash=Decimal("100000"),
            nav_data=nav_data,
            cutoff_date=date(2024, 2, 8),
        )
        orders3 = strategy.on_bar(ctx3)
        assert len(orders3) > 0

    def test_top_n_selection(self) -> None:
        """正确选取 Top-N 基金。"""
        nav_data = self._build_nav_data()
        params = MomentumParams(
            lookback_days=30,
            top_n=3,
            rebalance_freq=RebalanceFreq.MONTHLY,
            score_method=ScoreMethod.RETURN,
        )
        strategy = MomentumRotation(
            params=params, universe=["A", "B", "C", "D", "E"]
        )

        ctx = _make_context(
            current_date=date(2024, 2, 29),
            cash=Decimal("100000"),
            nav_data=nav_data,
            cutoff_date=date(2024, 2, 28),
        )
        orders = strategy.on_bar(ctx)

        # 应该选中 A, B, C（收益最高的 3 只）
        subscribe_codes = {o.fund_code for o in orders if o.direction == "subscribe"}
        # A 和 B 一定被选中（最高收益）
        assert "A" in subscribe_codes
        assert "B" in subscribe_codes
        # D 和 E 不应被选中（负收益）
        assert "D" not in subscribe_codes
        assert "E" not in subscribe_codes

    def test_equal_weight_allocation(self) -> None:
        """等权配置：每只基金权重 = 1/top_n。"""
        nav_data = self._build_nav_data()
        params = MomentumParams(
            lookback_days=30,
            top_n=2,
            rebalance_freq=RebalanceFreq.MONTHLY,
            score_method=ScoreMethod.RETURN,
        )
        strategy = MomentumRotation(
            params=params, universe=["A", "B", "C", "D", "E"]
        )

        ctx = _make_context(
            current_date=date(2024, 2, 29),
            cash=Decimal("100000"),
            nav_data=nav_data,
            cutoff_date=date(2024, 2, 28),
        )
        orders = strategy.on_bar(ctx)

        # 两只基金各 50% 权重，总资金 100000
        # 每只基金约 50000
        subscribe_orders = [o for o in orders if o.direction == "subscribe"]
        assert len(subscribe_orders) == 2
        for order in subscribe_orders:
            assert order.amount is not None
            # 每只基金约 50000（允许小误差因为 rebalance_to 有最小阈值）
            assert Decimal("49000") < order.amount < Decimal("51000")

    def test_sharpe_score_method(self) -> None:
        """使用 Sharpe 评分方法。"""
        nav_data = self._build_nav_data()
        params = MomentumParams(
            lookback_days=30,
            top_n=2,
            rebalance_freq=RebalanceFreq.MONTHLY,
            score_method=ScoreMethod.SHARPE,
        )
        strategy = MomentumRotation(
            params=params, universe=["A", "B", "C", "D", "E"]
        )

        ctx = _make_context(
            current_date=date(2024, 2, 29),
            cash=Decimal("100000"),
            nav_data=nav_data,
            cutoff_date=date(2024, 2, 28),
        )
        orders = strategy.on_bar(ctx)

        # Sharpe 方法也应选中 A 和 B（稳定上涨，Sharpe 最高）
        subscribe_codes = {o.fund_code for o in orders if o.direction == "subscribe"}
        assert "A" in subscribe_codes
        assert "B" in subscribe_codes

    def test_no_nav_data_no_orders(self) -> None:
        """基金池中所有基金无净值数据时不调仓。"""
        params = MomentumParams(
            lookback_days=30,
            top_n=2,
            rebalance_freq=RebalanceFreq.MONTHLY,
            score_method=ScoreMethod.RETURN,
        )
        strategy = MomentumRotation(
            params=params, universe=["X", "Y", "Z"]
        )

        ctx = _make_context(
            current_date=date(2024, 2, 1),
            cash=Decimal("100000"),
            nav_data={},  # 无数据
        )
        orders = strategy.on_bar(ctx)
        assert len(orders) == 0

    def test_top_n_larger_than_universe(self) -> None:
        """top_n 大于有效基金数时选取所有有效基金。"""
        nav_data = self._build_nav_data()
        params = MomentumParams(
            lookback_days=30,
            top_n=10,  # 大于基金池大小
            rebalance_freq=RebalanceFreq.MONTHLY,
            score_method=ScoreMethod.RETURN,
        )
        # 只有 A 和 B 有数据
        strategy = MomentumRotation(
            params=params, universe=["A", "B"]
        )

        ctx = _make_context(
            current_date=date(2024, 2, 29),
            cash=Decimal("100000"),
            nav_data=nav_data,
            cutoff_date=date(2024, 2, 28),
        )
        orders = strategy.on_bar(ctx)

        # 应选中所有有效基金（A 和 B），各 50%
        subscribe_codes = {o.fund_code for o in orders if o.direction == "subscribe"}
        assert subscribe_codes == {"A", "B"}

    def test_rebalance_sells_old_positions(self) -> None:
        """调仓时应卖出不再入选的基金。"""
        nav_data = self._build_nav_data()
        params = MomentumParams(
            lookback_days=30,
            top_n=2,
            rebalance_freq=RebalanceFreq.MONTHLY,
            score_method=ScoreMethod.RETURN,
        )
        strategy = MomentumRotation(
            params=params, universe=["A", "B", "C", "D", "E"]
        )

        # 当前持有 D 和 E（表现最差的）
        ctx = _make_context(
            current_date=date(2024, 2, 29),
            cash=Decimal("10000"),
            positions={"D": Decimal("50000"), "E": Decimal("50000")},
            nav_data=nav_data,
            cutoff_date=date(2024, 2, 28),
        )
        orders = strategy.on_bar(ctx)

        # 应该有赎回 D 和 E 的指令
        redeem_codes = {o.fund_code for o in orders if o.direction == "redeem"}
        assert "D" in redeem_codes or "E" in redeem_codes

        # 应该有申购 A 和 B 的指令
        subscribe_codes = {o.fund_code for o in orders if o.direction == "subscribe"}
        assert "A" in subscribe_codes
        assert "B" in subscribe_codes

    def test_partial_nav_data(self) -> None:
        """部分基金无净值数据时只选有数据的基金。"""
        # 只有 A 和 C 有数据
        nav_data: dict[str, dict[date, Decimal]] = {
            "A": _make_nav_series(
                date(2024, 1, 1),
                [Decimal(str(round(1.0 * 1.005**i, 6))) for i in range(60)],
            ),
            "C": _make_nav_series(
                date(2024, 1, 1),
                [Decimal("1.0")] * 60,
            ),
        }

        params = MomentumParams(
            lookback_days=30,
            top_n=3,
            rebalance_freq=RebalanceFreq.MONTHLY,
            score_method=ScoreMethod.RETURN,
        )
        strategy = MomentumRotation(
            params=params, universe=["A", "B", "C", "D", "E"]
        )

        ctx = _make_context(
            current_date=date(2024, 2, 29),
            cash=Decimal("100000"),
            nav_data=nav_data,
            cutoff_date=date(2024, 2, 28),
        )
        orders = strategy.on_bar(ctx)

        # 只有 A 和 C 有数据，应选中这两只
        subscribe_codes = {o.fund_code for o in orders if o.direction == "subscribe"}
        assert "A" in subscribe_codes
        # B, D, E 无数据不应被选中
        assert "B" not in subscribe_codes
        assert "D" not in subscribe_codes
        assert "E" not in subscribe_codes

    def test_empty_universe(self) -> None:
        """空基金池不产生订单。"""
        params = MomentumParams(
            lookback_days=30,
            top_n=2,
            rebalance_freq=RebalanceFreq.MONTHLY,
            score_method=ScoreMethod.RETURN,
        )
        strategy = MomentumRotation(params=params, universe=[])

        ctx = _make_context(
            current_date=date(2024, 2, 1),
            cash=Decimal("100000"),
            nav_data={},
        )
        orders = strategy.on_bar(ctx)
        assert len(orders) == 0
