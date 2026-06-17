"""回测结果模型与指标计算测试。

验证：
1. BacktestMetrics 各指标计算正确性
2. BacktestResult.compute_metrics() 方法
3. BacktestResult.to_dict() 序列化
4. BacktestResult.from_engine_result() 工厂方法
5. 边界条件处理（空数据、单日数据等）

需求: 4.11
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.domain.backtest.engine_event import (
    BacktestResult as EngineBacktestResult,
    EquitySnapshot,
)
from app.domain.backtest.order import Fill
from app.domain.backtest.portfolio import Portfolio
from app.domain.backtest.result import (
    BacktestMetrics,
    BacktestQuality,
    BacktestResult,
    HoldingSnapshot,
    _annualize_return,
    _annualized_volatility,
    _calmar_ratio,
    _max_drawdown,
    _profit_factor,
    _sharpe_ratio,
    _sortino_ratio,
    _win_rate,
    calc_cvar,
    calc_var,
    compute_rolling_metrics,
)
from app.domain.performance.metrics import (
    METRIC_VERSION,
    historical_cvar,
    historical_var,
    sharpe_ratio_from_returns,
    sortino_ratio_from_returns,
)


# ---------------------------------------------------------------------------
# Fixtures / Helpers
# ---------------------------------------------------------------------------


def _make_equity_curve_rising() -> list[EquitySnapshot]:
    """构建稳定上涨的权益曲线（10 个交易日）。"""
    base = 100000.0
    daily_return = 0.001  # 每天涨 0.1%
    curve = []
    for i in range(10):
        equity = Decimal(str(round(base * (1 + daily_return) ** i, 2)))
        curve.append(
            EquitySnapshot(
                trade_date=date(2024, 1, 2 + i),
                equity=equity,
                cash=Decimal("50000"),
                position_value=equity - Decimal("50000"),
            )
        )
    return curve


def _make_equity_curve_with_drawdown() -> list[EquitySnapshot]:
    """构建包含回撤的权益曲线。

    100000 -> 110000 -> 105000 -> 100000 -> 108000 -> 115000
    最大回撤从 110000 到 100000 = -9.09%
    """
    values = [100000, 110000, 105000, 100000, 108000, 115000]
    curve = []
    for i, v in enumerate(values):
        curve.append(
            EquitySnapshot(
                trade_date=date(2024, 1, 2 + i),
                equity=Decimal(str(v)),
                cash=Decimal("30000"),
                position_value=Decimal(str(v - 30000)),
            )
        )
    return curve


def _make_trades() -> list[Fill]:
    """构建测试用成交记录。"""
    return [
        Fill(
            order_id="ORD-001",
            fund_code="000001",
            direction="subscribe",
            shares=Decimal("33333.33"),
            amount=Decimal("50000"),
            nav=Decimal("1.5000"),
            fee=Decimal("0"),
            confirm_date=date(2024, 1, 3),
        ),
        Fill(
            order_id="ORD-002",
            fund_code="000001",
            direction="redeem",
            shares=Decimal("10000"),
            amount=Decimal("15800"),
            nav=Decimal("1.5800"),
            fee=Decimal("79"),
            confirm_date=date(2024, 1, 10),
        ),
    ]


# ---------------------------------------------------------------------------
# 指标计算辅助函数测试
# ---------------------------------------------------------------------------


class TestAnnualizeReturn:
    """年化收益率计算测试。"""

    def test_one_year_100_percent(self) -> None:
        """1 年翻倍 -> 年化 100%。"""
        result = _annualize_return(1.0, 1.0)
        assert abs(result - 1.0) < 1e-10

    def test_two_years_100_percent(self) -> None:
        """2 年翻倍 -> 年化约 41.4%。"""
        result = _annualize_return(1.0, 2.0)
        expected = math.sqrt(2) - 1  # ≈ 0.4142
        assert abs(result - expected) < 1e-6

    def test_zero_return(self) -> None:
        """零收益 -> 年化 0%。"""
        result = _annualize_return(0.0, 1.0)
        assert result == 0.0

    def test_negative_return(self) -> None:
        """负收益年化。"""
        result = _annualize_return(-0.5, 1.0)
        assert abs(result - (-0.5)) < 1e-10

    def test_zero_years(self) -> None:
        """0 年 -> 返回 0。"""
        result = _annualize_return(0.5, 0.0)
        assert result == 0.0

    def test_total_loss(self) -> None:
        """全部亏损 -> 返回 -1。"""
        result = _annualize_return(-1.0, 1.0)
        assert result == -1.0


class TestMaxDrawdown:
    """最大回撤计算测试。"""

    def test_no_drawdown(self) -> None:
        """持续上涨无回撤。"""
        curve = _make_equity_curve_rising()
        dd, start, end = _max_drawdown(curve)
        assert dd == 0.0
        assert start is None
        assert end is None

    def test_drawdown_with_recovery(self) -> None:
        """有回撤且恢复。"""
        curve = _make_equity_curve_with_drawdown()
        dd, start, end = _max_drawdown(curve)

        # 最大回撤从 110000 到 100000 = -10000/110000 ≈ -0.0909
        expected_dd = (100000 - 110000) / 110000
        assert abs(dd - expected_dd) < 1e-6
        assert start == date(2024, 1, 3)  # peak at 110000
        assert end == date(2024, 1, 5)  # trough at 100000

    def test_single_point(self) -> None:
        """单点数据无回撤。"""
        curve = [
            EquitySnapshot(
                trade_date=date(2024, 1, 2),
                equity=Decimal("100000"),
                cash=Decimal("100000"),
                position_value=Decimal("0"),
            )
        ]
        dd, start, end = _max_drawdown(curve)
        assert dd == 0.0

    def test_continuous_decline(self) -> None:
        """持续下跌。"""
        values = [100000, 95000, 90000, 85000, 80000]
        curve = [
            EquitySnapshot(
                trade_date=date(2024, 1, 2 + i),
                equity=Decimal(str(v)),
                cash=Decimal("50000"),
                position_value=Decimal(str(v - 50000)),
            )
            for i, v in enumerate(values)
        ]
        dd, start, end = _max_drawdown(curve)
        expected_dd = (80000 - 100000) / 100000  # -20%
        assert abs(dd - expected_dd) < 1e-6
        assert start == date(2024, 1, 2)
        assert end == date(2024, 1, 6)


class TestVolatility:
    """年化波动率测试。"""

    def test_zero_volatility(self) -> None:
        """收益率恒定 -> 波动率为 0。"""
        returns = [0.01] * 10
        result = _annualized_volatility(returns)
        assert result == 0.0

    def test_positive_volatility(self) -> None:
        """正常收益率序列有正波动率。"""
        returns = [0.01, -0.005, 0.02, -0.01, 0.015]
        result = _annualized_volatility(returns)
        assert result > 0

    def test_single_return(self) -> None:
        """单个收益率 -> 波动率为 0。"""
        returns = [0.01]
        result = _annualized_volatility(returns)
        assert result == 0.0

    def test_annualization_factor(self) -> None:
        """验证年化因子 sqrt(252)。"""
        returns = [0.01, -0.01, 0.01, -0.01, 0.01]
        vol = _annualized_volatility(returns)

        # 手动计算
        n = len(returns)
        mean = sum(returns) / n
        var = sum((r - mean) ** 2 for r in returns) / (n - 1)
        daily_std = math.sqrt(var)
        expected = daily_std * math.sqrt(252)

        assert abs(vol - expected) < 1e-10


class TestSharpeRatio:
    """夏普比率测试。"""

    def test_positive_sharpe(self) -> None:
        """正收益正夏普。"""
        returns = [0.01, 0.005, 0.008, 0.012, 0.003]
        result = _sharpe_ratio(returns, risk_free_rate=0.0)
        assert result > 0

    def test_negative_sharpe(self) -> None:
        """负收益负夏普。"""
        returns = [-0.01, -0.005, -0.008, -0.012, -0.003]
        result = _sharpe_ratio(returns, risk_free_rate=0.0)
        assert result < 0

    def test_zero_volatility_returns_zero(self) -> None:
        """零波动率返回 0。"""
        returns = [0.0, 0.0, 0.0, 0.0]
        result = _sharpe_ratio(returns, risk_free_rate=0.0)
        assert result == 0.0

    def test_sharpe_matches_shared_metric_zero_risk_free(self) -> None:
        """回测 Sharpe 在 rf=0 时应与统一指标工具口径一致。"""
        returns = [0.02, -0.01, 0.03, -0.02, 0.01]
        assert _sharpe_ratio(returns, risk_free_rate=0.0) == pytest.approx(
            sharpe_ratio_from_returns(returns, risk_free_rate=0.0)
        )

    def test_risk_free_rate_effect(self) -> None:
        """无风险利率降低夏普。"""
        returns = [0.01, 0.005, 0.008, 0.012, 0.003]
        sharpe_no_rf = _sharpe_ratio(returns, risk_free_rate=0.0)
        sharpe_with_rf = _sharpe_ratio(returns, risk_free_rate=0.03)
        assert sharpe_with_rf < sharpe_no_rf


class TestSortinoRatio:
    """索提诺比率测试。"""

    def test_no_downside_returns_zero(self) -> None:
        """全部正收益，下行偏差为 0 -> 返回 0。"""
        returns = [0.01, 0.02, 0.03, 0.04]
        result = _sortino_ratio(returns, risk_free_rate=0.0)
        # 下行偏差为 0 时返回 0
        assert result == 0.0

    def test_mixed_returns(self) -> None:
        """混合收益率有正索提诺。"""
        returns = [0.02, -0.01, 0.03, -0.005, 0.01]
        result = _sortino_ratio(returns, risk_free_rate=0.0)
        assert result > 0

    def test_sortino_greater_than_sharpe_for_positive_skew(self) -> None:
        """正偏态收益率下，索提诺 > 夏普。"""
        # 大部分正收益，少量小负收益
        returns = [0.02, 0.03, 0.01, -0.001, 0.025, 0.015, -0.002]
        sharpe = _sharpe_ratio(returns)
        sortino = _sortino_ratio(returns)
        assert sortino > sharpe

    def test_sortino_matches_shared_metric_zero_risk_free(self) -> None:
        """回测 Sortino 在 rf=0 时应与统一指标工具的下行偏差口径一致。"""
        returns = [0.02, -0.01, 0.03, -0.02, 0.01]
        assert _sortino_ratio(returns, risk_free_rate=0.0) == pytest.approx(
            sortino_ratio_from_returns(returns, risk_free_rate=0.0)
        )


class TestCalmarRatio:
    """卡尔玛比率测试。"""

    def test_positive_calmar(self) -> None:
        """正收益正回撤 -> 正卡尔玛。"""
        result = _calmar_ratio(0.15, -0.10)
        assert abs(result - 1.5) < 1e-10

    def test_zero_drawdown(self) -> None:
        """零回撤 -> 返回 0。"""
        result = _calmar_ratio(0.15, 0.0)
        assert result == 0.0

    def test_negative_return(self) -> None:
        """负收益 -> 负卡尔玛。"""
        result = _calmar_ratio(-0.10, -0.20)
        assert abs(result - (-0.5)) < 1e-10


class TestWinRate:
    """胜率测试。"""

    def test_all_wins(self) -> None:
        """全部正收益 -> 100%。"""
        returns = [0.01, 0.02, 0.03]
        assert _win_rate(returns) == 1.0

    def test_all_losses(self) -> None:
        """全部负收益 -> 0%。"""
        returns = [-0.01, -0.02, -0.03]
        assert _win_rate(returns) == 0.0

    def test_mixed(self) -> None:
        """混合 -> 正确比例。"""
        returns = [0.01, -0.01, 0.02, -0.02, 0.03]
        assert abs(_win_rate(returns) - 0.6) < 1e-10

    def test_empty(self) -> None:
        """空列表 -> 0。"""
        assert _win_rate([]) == 0.0

    def test_zero_not_counted_as_win(self) -> None:
        """零收益不算赢。"""
        returns = [0.0, 0.01, -0.01]
        assert abs(_win_rate(returns) - 1 / 3) < 1e-10


class TestProfitFactor:
    """盈亏比测试。"""

    def test_equal_gains_losses(self) -> None:
        """盈亏相等 -> 1.0。"""
        returns = [0.01, -0.01, 0.02, -0.02]
        assert abs(_profit_factor(returns) - 1.0) < 1e-10

    def test_more_gains(self) -> None:
        """盈大于亏 -> > 1。"""
        returns = [0.03, -0.01, 0.02, -0.01]
        result = _profit_factor(returns)
        expected = 0.05 / 0.02  # 2.5
        assert abs(result - expected) < 1e-10

    def test_no_losses(self) -> None:
        """无亏损 -> inf。"""
        returns = [0.01, 0.02, 0.03]
        assert _profit_factor(returns) == float("inf")

    def test_no_gains_no_losses(self) -> None:
        """全部为零 -> 0。"""
        returns = [0.0, 0.0, 0.0]
        assert _profit_factor(returns) == 0.0


# ---------------------------------------------------------------------------
# BacktestResult 测试
# ---------------------------------------------------------------------------


class TestVarCvar:
    """VaR / CVaR 风险度量测试。"""

    def test_calc_var_and_cvar_match_shared_metrics(self) -> None:
        """回测 VaR/CVaR 应复用统一指标工具的正数损失口径。"""
        returns = [-0.05, -0.04, -0.03, -0.02, -0.01, 0, 0.01, 0.02, 0.03, 0.04]

        assert calc_var(returns, confidence=0.90) == pytest.approx(
            historical_var(returns, confidence=0.90)
        )
        assert calc_cvar(returns, confidence=0.90) == pytest.approx(
            historical_cvar(returns, confidence=0.90)
        )

    def test_calc_var_and_cvar_insufficient_data_return_zero(self) -> None:
        """回测结果层数据不足时继续返回 0.0，避免 API 输出 NaN。"""
        returns = [0.01] * 9

        assert calc_var(returns) == 0.0
        assert calc_cvar(returns) == 0.0


class TestBacktestResultComputeMetrics:
    """BacktestResult.compute_metrics() 测试。"""

    def test_compute_metrics_rising_curve(self) -> None:
        """上涨曲线的指标计算。"""
        result = BacktestResult(
            equity_curve=_make_equity_curve_rising(),
            trades=_make_trades(),
            holdings_history=[],
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 11),
            initial_capital=Decimal("100000"),
        )

        metrics = result.compute_metrics()

        assert metrics.total_return > 0
        assert metrics.annualized_return > 0
        assert metrics.metric_version == METRIC_VERSION
        assert metrics.metrics_status == "ok"
        assert metrics.max_drawdown == 0.0  # 持续上涨无回撤
        assert metrics.volatility >= 0
        assert metrics.sharpe > 0
        assert metrics.win_rate == 1.0  # 每天都涨
        assert metrics.total_trades == 2
        assert metrics.trading_days == 10

    def test_compute_metrics_with_drawdown(self) -> None:
        """包含回撤的曲线指标计算。"""
        result = BacktestResult(
            equity_curve=_make_equity_curve_with_drawdown(),
            trades=[],
            holdings_history=[],
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 7),
            initial_capital=Decimal("100000"),
        )

        metrics = result.compute_metrics()

        assert metrics.total_return > 0  # 最终 115000 > 100000
        assert metrics.max_drawdown < 0  # 有回撤
        assert abs(metrics.max_drawdown - (-10000 / 110000)) < 1e-4
        assert metrics.max_drawdown_start == date(2024, 1, 3)
        assert metrics.max_drawdown_end == date(2024, 1, 5)
        assert metrics.max_drawdown_recovery_date == date(2024, 1, 7)
        assert metrics.max_drawdown_recovery_days == 2
        assert metrics.calmar > 0  # 正收益 / 正回撤绝对值

    def test_compute_metrics_empty_curve(self) -> None:
        """空权益曲线返回零指标。"""
        result = BacktestResult(
            equity_curve=[],
            trades=[],
            holdings_history=[],
        )

        metrics = result.compute_metrics()

        assert metrics.total_return == 0.0
        assert metrics.annualized_return == 0.0
        assert metrics.max_drawdown == 0.0
        assert metrics.sharpe == 0.0
        assert metrics.trading_days == 0
        assert metrics.metric_version == METRIC_VERSION
        assert metrics.metrics_status == "insufficient_data"

    def test_compute_metrics_single_day(self) -> None:
        """单日数据返回零指标。"""
        curve = [
            EquitySnapshot(
                trade_date=date(2024, 1, 2),
                equity=Decimal("100000"),
                cash=Decimal("100000"),
                position_value=Decimal("0"),
            )
        ]
        result = BacktestResult(
            equity_curve=curve,
            trades=[],
            holdings_history=[],
        )

        metrics = result.compute_metrics()

        assert metrics.total_return == 0.0
        assert metrics.trading_days == 1

    def test_compute_metrics_with_risk_free_rate(self) -> None:
        """指定无风险利率影响夏普和索提诺。"""
        result = BacktestResult(
            equity_curve=_make_equity_curve_rising(),
            trades=[],
            holdings_history=[],
        )

        metrics_no_rf = result.compute_metrics(risk_free_rate=0.0)
        metrics_with_rf = result.compute_metrics(risk_free_rate=0.05)

        assert metrics_with_rf.sharpe < metrics_no_rf.sharpe


class TestBacktestResultToDict:
    """BacktestResult.to_dict() 序列化测试。"""

    def test_to_dict_structure(self) -> None:
        """to_dict 返回正确的结构。"""
        result = BacktestResult(
            equity_curve=_make_equity_curve_rising()[:3],
            trades=_make_trades()[:1],
            holdings_history=[
                HoldingSnapshot(
                    trade_date=date(2024, 1, 2),
                    positions={"000001": Decimal("10000")},
                    weights={"000001": 0.5},
                ),
            ],
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 4),
            initial_capital=Decimal("100000"),
        )
        result.compute_metrics()

        d = result.to_dict()

        assert d["start_date"] == "2024-01-02"
        assert d["end_date"] == "2024-01-04"
        assert d["initial_capital"] == "100000"
        assert d["metrics"] is not None
        assert "total_return" in d["metrics"]
        assert "annualized_return" in d["metrics"]
        assert "max_drawdown" in d["metrics"]
        assert "sharpe" in d["metrics"]

    def test_to_dict_equity_curve(self) -> None:
        """to_dict 正确序列化权益曲线。"""
        curve = _make_equity_curve_rising()[:2]
        result = BacktestResult(
            equity_curve=curve,
            trades=[],
            holdings_history=[],
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 3),
            initial_capital=Decimal("100000"),
        )

        d = result.to_dict()

        assert len(d["equity_curve"]) == 2
        assert d["equity_curve"][0]["trade_date"] == "2024-01-02"
        assert "equity" in d["equity_curve"][0]
        assert "cash" in d["equity_curve"][0]
        assert "position_value" in d["equity_curve"][0]

    def test_to_dict_trades(self) -> None:
        """to_dict 正确序列化成交记录。"""
        trades = _make_trades()
        result = BacktestResult(
            equity_curve=_make_equity_curve_rising()[:2],
            trades=trades,
            holdings_history=[],
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 3),
            initial_capital=Decimal("100000"),
        )

        d = result.to_dict()

        assert len(d["trades"]) == 2
        assert d["trades"][0]["order_id"] == "ORD-001"
        assert d["trades"][0]["fund_code"] == "000001"
        assert d["trades"][0]["direction"] == "subscribe"
        assert d["trades"][0]["shares"] == "33333.33"
        assert d["trades"][0]["confirm_date"] == "2024-01-03"

    def test_to_dict_holdings_history(self) -> None:
        """to_dict 正确序列化持仓历史。"""
        result = BacktestResult(
            equity_curve=_make_equity_curve_rising()[:2],
            trades=[],
            holdings_history=[
                HoldingSnapshot(
                    trade_date=date(2024, 1, 2),
                    positions={"000001": Decimal("10000"), "110011": Decimal("5000")},
                    weights={"000001": 0.6, "110011": 0.4},
                ),
            ],
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 3),
            initial_capital=Decimal("100000"),
        )

        d = result.to_dict()

        assert len(d["holdings_history"]) == 1
        h = d["holdings_history"][0]
        assert h["trade_date"] == "2024-01-02"
        assert h["positions"]["000001"] == "10000"
        assert h["positions"]["110011"] == "5000"
        assert h["weights"]["000001"] == 0.6

    def test_to_dict_no_metrics(self) -> None:
        """未计算 metrics 时 to_dict 中 metrics 为 None。"""
        result = BacktestResult(
            equity_curve=[],
            trades=[],
            holdings_history=[],
        )

        d = result.to_dict()
        assert d["metrics"] is None


class TestRollingMetrics:
    """滚动指标计算测试。"""

    def test_compute_rolling_return_and_drawdown(self) -> None:
        """滚动收益、回撤和其他序列与日期对齐。"""
        values = [100000 + i * 1000 for i in range(31)]
        values[25] = 110000  # 制造一次从前高回落的回撤
        start = date(2024, 1, 2)
        curve = [
            EquitySnapshot(
                trade_date=start + timedelta(days=i),
                equity=Decimal(str(value)),
                cash=Decimal("30000"),
                position_value=Decimal(str(value - 30000)),
            )
            for i, value in enumerate(values)
        ]

        rolling = compute_rolling_metrics(
            curve,
            sharpe_window=5,
            vol_window=5,
            return_window=20,
        )

        assert rolling is not None
        assert len(rolling.dates) == len(curve) - 1
        assert len(rolling.rolling_return) == len(rolling.dates)
        assert len(rolling.rolling_sharpe) == len(rolling.dates)
        assert len(rolling.rolling_drawdown) == len(rolling.dates)
        assert len(rolling.rolling_volatility) == len(rolling.dates)

        expected_20d_return = values[20] / values[0] - 1.0
        assert rolling.rolling_return[19] == pytest.approx(expected_20d_return)
        assert rolling.rolling_drawdown[20] == 0.0
        assert rolling.rolling_drawdown[24] < 0.0

        payload = rolling.to_dict()
        assert "rolling_return" in payload
        assert len(payload["rolling_return"]) == len(payload["dates"])


class TestBacktestResultFromEngineResult:
    """BacktestResult.from_engine_result() 测试。"""

    def test_from_engine_result(self) -> None:
        """从引擎结果构建增强版结果。"""
        engine_result = EngineBacktestResult(
            equity_curve=_make_equity_curve_rising(),
            trades=_make_trades(),
            final_portfolio=Portfolio(cash=Decimal("50000")),
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 11),
            initial_capital=Decimal("100000"),
        )

        result = BacktestResult.from_engine_result(engine_result)

        assert result.start_date == date(2024, 1, 2)
        assert result.end_date == date(2024, 1, 11)
        assert result.initial_capital == Decimal("100000")
        assert len(result.equity_curve) == 10
        assert len(result.trades) == 2
        assert result.metrics is not None
        assert result.metrics.total_return > 0

    def test_from_engine_result_with_holdings(self) -> None:
        """从引擎结果构建时传入持仓历史。"""
        engine_result = EngineBacktestResult(
            equity_curve=_make_equity_curve_rising()[:3],
            trades=[],
            final_portfolio=Portfolio(cash=Decimal("100000")),
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 4),
            initial_capital=Decimal("100000"),
        )

        holdings = [
            HoldingSnapshot(
                trade_date=date(2024, 1, 2),
                positions={"000001": Decimal("5000")},
                weights={"000001": 0.5},
            ),
        ]

        result = BacktestResult.from_engine_result(engine_result, holdings_history=holdings)

        assert len(result.holdings_history) == 1
        assert result.holdings_history[0].positions["000001"] == Decimal("5000")


class TestBacktestMetricsToDict:
    """BacktestMetrics.to_dict() 测试。"""

    def test_metrics_to_dict(self) -> None:
        """指标序列化为字典。"""
        metrics = BacktestMetrics(
            total_return=0.15,
            annualized_return=0.12,
            max_drawdown=-0.08,
            max_drawdown_start=date(2024, 3, 1),
            max_drawdown_end=date(2024, 4, 15),
            sharpe=1.5,
            sortino=2.1,
            volatility=0.12,
            calmar=1.5,
            win_rate=0.55,
            profit_factor=1.3,
            total_trades=20,
            trading_days=252,
        )

        d = metrics.to_dict()

        assert d["total_return"] == 0.15
        assert d["annualized_return"] == 0.12
        assert d["max_drawdown"] == -0.08
        assert d["max_drawdown_start"] == "2024-03-01"
        assert d["max_drawdown_end"] == "2024-04-15"
        assert d["sharpe"] == 1.5
        assert d["sortino"] == 2.1
        assert d["volatility"] == 0.12
        assert d["calmar"] == 1.5
        assert d["win_rate"] == 0.55
        assert d["profit_factor"] == 1.3
        assert d["total_trades"] == 20
        assert d["trading_days"] == 252
        assert d["metric_version"] == METRIC_VERSION
        assert d["metrics_status"] == "ok"

    def test_metrics_to_dict_none_dates(self) -> None:
        """无回撤时日期为 None。"""
        metrics = BacktestMetrics(
            total_return=0.1,
            annualized_return=0.1,
            max_drawdown=0.0,
            max_drawdown_start=None,
            max_drawdown_end=None,
            sharpe=2.0,
            sortino=3.0,
            volatility=0.05,
            calmar=0.0,
            win_rate=1.0,
            profit_factor=float("inf"),
            total_trades=5,
            trading_days=100,
        )

        d = metrics.to_dict()

        assert d["max_drawdown_start"] is None
        assert d["max_drawdown_end"] is None


class TestBacktestQuality:
    def test_default_quality_is_research_approximation_without_strict_pit(self) -> None:
        quality = BacktestQuality()
        payload = quality.to_dict()

        assert payload["cash_arrival_delay_modelled"] is True
        assert payload["lot_level_fee_modelled"] is True
        assert payload["pit_data_quality"] == "missing"
        assert payload["decision_grade"] == "research_approximation"
        assert any("PIT" in warning for warning in payload["warnings"])

    def test_strict_quality_can_be_decision_support(self) -> None:
        quality = BacktestQuality(pit_data_quality="strict", survivorship_bias_control="full")
        payload = quality.to_dict()

        assert payload["decision_grade"] == "decision_support"

    def test_result_to_dict_includes_quality_gate(self) -> None:
        result = BacktestResult(
            equity_curve=_make_equity_curve_rising(),
            trades=[],
            holdings_history=[],
            quality=BacktestQuality(pit_data_quality="fallback"),
        )

        payload = result.to_dict()

        assert payload["quality"]["pit_data_quality"] == "fallback"
        assert payload["quality"]["decision_grade"] == "research_approximation"
