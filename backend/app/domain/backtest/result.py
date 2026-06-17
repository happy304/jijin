"""回测结果模型与指标计算模块。

提供增强版 BacktestResult，包含：
- equity: 每日权益曲线
- trades: 成交记录列表
- holdings_history: 每日持仓快照
- metrics: 关键绩效指标

指标计算方法：
- total_return: 总收益率
- annualized_return: 年化收益率
- max_drawdown: 最大回撤
- sharpe: 夏普比率（年化）
- sortino: 索提诺比率
- volatility: 年化波动率
- calmar: 卡尔玛比率
- win_rate: 日频胜率（日收益为正的比例）
- profit_factor: 日频盈亏比
- cashflow_win_rate_estimate: 现金流估算胜率（deprecated trade_win_rate 兼容字段同值）
- cashflow_profit_factor_estimate: 现金流估算盈亏比（deprecated trade_profit_factor 兼容字段同值）
- var_95 / cvar_95: 95% VaR 和 CVaR

需求: 4.11
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

import pandas as pd

from app.domain.backtest.engine_event import BacktestResult as EngineBacktestResult
from app.domain.backtest.engine_event import EquitySnapshot
from app.domain.backtest.order import Fill
from app.domain.performance.metrics import (
    METRIC_VERSION,
    annualized_return_from_nav,
    annualized_volatility_from_returns,
    historical_cvar,
    historical_var,
    drawdown_details_from_nav,
    max_drawdown_from_nav,
    sharpe_ratio_from_returns,
    sortino_ratio_from_returns,
    total_return_from_nav,
)


# ---------------------------------------------------------------------------
# Quality Gate 数据类
# ---------------------------------------------------------------------------


@dataclass
class BacktestQuality:
    """回测可信度标签，帮助用户先理解假设再看收益曲线。"""

    lookahead_guard: bool = True
    cash_arrival_delay_modelled: bool = True
    lot_level_fee_modelled: bool = True
    pit_data_quality: str = "missing"  # strict / fallback / missing
    nav_publication_lag_modelled: bool = True
    survivorship_bias_control: str = "partial"  # full / partial / none
    vectorized_simplification: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def decision_grade(self) -> str:
        critical_ok = (
            self.lookahead_guard
            and self.cash_arrival_delay_modelled
            and self.lot_level_fee_modelled
            and self.nav_publication_lag_modelled
            and self.pit_data_quality == "strict"
            and not self.vectorized_simplification
        )
        return "decision_support" if critical_ok else "research_approximation"

    def to_dict(self) -> dict[str, Any]:
        warnings = list(self.warnings)
        if self.pit_data_quality != "strict":
            warnings.append("未使用严格 PIT 元数据，历史基金状态可能存在当前信息替代风险")
        if self.survivorship_bias_control != "full":
            warnings.append("幸存者偏差控制并非完整口径，需谨慎解读结果")
        if self.vectorized_simplification:
            warnings.append("该结果来自向量化简化回测，不应标记为决策级")
        return {
            "lookahead_guard": self.lookahead_guard,
            "cash_arrival_delay_modelled": self.cash_arrival_delay_modelled,
            "lot_level_fee_modelled": self.lot_level_fee_modelled,
            "pit_data_quality": self.pit_data_quality,
            "nav_publication_lag_modelled": self.nav_publication_lag_modelled,
            "survivorship_bias_control": self.survivorship_bias_control,
            "vectorized_simplification": self.vectorized_simplification,
            "decision_grade": self.decision_grade,
            "warnings": warnings,
        }


# ---------------------------------------------------------------------------
# Metrics 数据类
# ---------------------------------------------------------------------------


@dataclass
class BacktestMetrics:
    """回测绩效指标。

    Attributes:
        total_return: 总收益率
        annualized_return: 年化收益率
        max_drawdown: 最大回撤（负值）
        max_drawdown_start: 最大回撤起始日期
        max_drawdown_end: 最大回撤谷底日期
        max_drawdown_recovery_date: 最大回撤恢复日期
        max_drawdown_recovery_days: 从谷底到恢复的自然日天数
        sharpe: 夏普比率（年化，无风险利率默认 0）
        sortino: 索提诺比率
        volatility: 年化波动率
        calmar: 卡尔玛比率（年化收益 / 最大回撤绝对值）
        win_rate: 日频胜率（日收益为正的天数占比，注意区别于交易级别胜率）
        profit_factor: 日频盈亏比（日盈利总和 / 日亏损总和绝对值）
        total_trades: 总交易笔数
        trading_days: 交易天数
    """

    total_return: float
    annualized_return: float
    max_drawdown: float
    max_drawdown_start: date | None
    max_drawdown_end: date | None
    sharpe: float
    sortino: float
    volatility: float
    calmar: float
    win_rate: float
    profit_factor: float
    total_trades: int
    trading_days: int
    max_drawdown_recovery_date: date | None = None
    max_drawdown_recovery_days: int | None = None
    var_95: float = 0.0
    cvar_95: float = 0.0
    # 粗略现金流指标：不是严格交易级胜率，真实交易级指标应基于 lot-level realized PnL。
    cashflow_win_rate_estimate: float = 0.0
    cashflow_profit_factor_estimate: float = 0.0
    trade_win_rate: float = 0.0  # deprecated
    trade_profit_factor: float = 0.0  # deprecated
    trade_metrics_status: str = "cashflow_estimate_unrealized_excluded"
    metric_version: str = METRIC_VERSION
    metrics_status: str = "ok"

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典。"""
        return {
            "total_return": self.total_return,
            "annualized_return": self.annualized_return,
            "max_drawdown": self.max_drawdown,
            "max_drawdown_start": (
                self.max_drawdown_start.isoformat() if self.max_drawdown_start else None
            ),
            "max_drawdown_end": (
                self.max_drawdown_end.isoformat() if self.max_drawdown_end else None
            ),
            "max_drawdown_recovery_date": (
                self.max_drawdown_recovery_date.isoformat() if self.max_drawdown_recovery_date else None
            ),
            "max_drawdown_recovery_days": self.max_drawdown_recovery_days,
            "sharpe": self.sharpe,
            "sortino": self.sortino,
            "volatility": self.volatility,
            "calmar": self.calmar,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "total_trades": self.total_trades,
            "trading_days": self.trading_days,
            "var_95": self.var_95,
            "cvar_95": self.cvar_95,
            "cashflow_win_rate_estimate": self.cashflow_win_rate_estimate,
            "cashflow_profit_factor_estimate": self.cashflow_profit_factor_estimate,
            "trade_win_rate": self.trade_win_rate,
            "trade_profit_factor": self.trade_profit_factor,
            "trade_metrics_deprecated": True,
            "trade_metrics_status": self.trade_metrics_status,
            "trade_metrics_note": (
                "交易级指标为按基金汇总的已实现现金流粗略估计，"
                "未纳入未平仓浮盈浮亏；真实交易胜率应基于 lot-level realized PnL。"
            ),
            "metric_version": self.metric_version,
            "metrics_status": self.metrics_status,
        }


# ---------------------------------------------------------------------------
# 持仓快照
# ---------------------------------------------------------------------------


@dataclass
class HoldingSnapshot:
    """单日持仓快照。

    Attributes:
        trade_date: 交易日期
        positions: 持仓 {fund_code: shares}
        weights: 持仓权重 {fund_code: weight}（基于市值）
    """

    trade_date: date
    positions: dict[str, Decimal]
    weights: dict[str, float]


# ---------------------------------------------------------------------------
# 增强版 BacktestResult
# ---------------------------------------------------------------------------


@dataclass
class BacktestResult:
    """增强版回测结果。

    在引擎原始结果基础上增加 metrics 计算和序列化能力。

    Attributes:
        equity_curve: 每日权益快照列表
        trades: 成交记录列表
        holdings_history: 每日持仓快照列表
        metrics: 绩效指标（调用 compute_metrics 后填充）
        start_date: 回测起始日期
        end_date: 回测结束日期
        initial_capital: 初始资金
    """

    equity_curve: list[EquitySnapshot]
    trades: list[Fill]
    holdings_history: list[HoldingSnapshot]
    metrics: BacktestMetrics | None = None
    quality: BacktestQuality = field(default_factory=BacktestQuality)
    start_date: date = field(default_factory=lambda: date(2020, 1, 1))
    end_date: date = field(default_factory=lambda: date(2020, 12, 31))
    initial_capital: Decimal = Decimal("100000")

    @classmethod
    def from_engine_result(
        cls,
        engine_result: EngineBacktestResult,
        holdings_history: list[HoldingSnapshot] | None = None,
    ) -> "BacktestResult":
        """从引擎原始结果构建增强版结果。

        Args:
            engine_result: EventDrivenEngine 返回的原始结果
            holdings_history: 每日持仓快照（可选）

        Returns:
            增强版 BacktestResult
        """
        result = cls(
            equity_curve=engine_result.equity_curve,
            trades=engine_result.trades,
            holdings_history=holdings_history or [],
            start_date=engine_result.start_date,
            end_date=engine_result.end_date,
            initial_capital=engine_result.initial_capital,
        )
        result.metrics = result.compute_metrics()
        return result

    def compute_metrics(self, risk_free_rate: float = 0.0) -> BacktestMetrics:
        """计算绩效指标。

        基于权益曲线计算所有关键绩效指标。

        Args:
            risk_free_rate: 年化无风险利率，默认 0

        Returns:
            BacktestMetrics 实例
        """
        if not self.equity_curve:
            return _empty_metrics()

        # 提取权益序列
        equities = [float(s.equity) for s in self.equity_curve]
        trading_days_count = len(equities)

        if trading_days_count < 2:
            return _empty_metrics(
                trading_days=trading_days_count,
                total_trades=len(self.trades),
            )

        # 计算日收益率序列
        daily_returns = [
            (equities[i] - equities[i - 1]) / equities[i - 1]
            for i in range(1, len(equities))
            if equities[i - 1] != 0
        ]

        if not daily_returns:
            return _empty_metrics(
                trading_days=trading_days_count,
                total_trades=len(self.trades),
            )

        equity_series = pd.Series(
            equities,
            index=pd.Index([s.trade_date for s in self.equity_curve]),
            dtype=float,
        )

        # 总收益率 / 年化收益率 / 最大回撤：复用统一指标工具，确保与因子、报告口径一致。
        total_return = total_return_from_nav(equity_series)
        if math.isnan(total_return):
            total_return = 0.0
        annualized_return = annualized_return_from_nav(equity_series)
        if math.isnan(annualized_return):
            annualized_return = 0.0
        dd_details = drawdown_details_from_nav(equity_series)
        max_dd = max_drawdown_from_nav(equity_series)
        if math.isnan(max_dd):
            max_dd = 0.0
        dd_start = dd_details.get("peak_date")
        dd_end = dd_details.get("trough_date")
        dd_recovery_date = dd_details.get("recovery_date")
        dd_recovery_days = dd_details.get("recovery_days")
        if dd_start is None or dd_end is None:
            _, dd_start, dd_end = _max_drawdown(self.equity_curve)

        # 年化波动率
        volatility = _annualized_volatility(daily_returns)

        # 夏普比率
        sharpe = _sharpe_ratio(daily_returns, risk_free_rate)

        # 索提诺比率
        sortino = _sortino_ratio(daily_returns, risk_free_rate)

        # 卡尔玛比率
        calmar = _calmar_ratio(annualized_return, max_dd)

        # 胜率（日频）
        win_rate = _win_rate(daily_returns)

        # 盈亏比（日频）
        profit_factor = _profit_factor(daily_returns)

        # 现金流估算胜率和盈亏比：不是严格交易级指标，旧 trade_* 字段仅兼容输出。
        trade_win_rate_val, trade_pf_val = _trade_level_metrics(self.trades)

        metrics = BacktestMetrics(
            total_return=round(total_return, 6),
            annualized_return=round(annualized_return, 6),
            max_drawdown=round(max_dd, 6),
            max_drawdown_start=dd_start,
            max_drawdown_end=dd_end,
            sharpe=round(sharpe, 4),
            sortino=round(sortino, 4),
            volatility=round(volatility, 6),
            calmar=round(calmar, 4),
            win_rate=round(win_rate, 4),
            profit_factor=round(profit_factor, 4),
            total_trades=len(self.trades),
            trading_days=trading_days_count,
            max_drawdown_recovery_date=dd_recovery_date if isinstance(dd_recovery_date, date) else None,
            max_drawdown_recovery_days=dd_recovery_days if isinstance(dd_recovery_days, int) else None,
            var_95=round(calc_var(daily_returns, 0.95), 6),
            cvar_95=round(calc_cvar(daily_returns, 0.95), 6),
            cashflow_win_rate_estimate=round(trade_win_rate_val, 4),
            cashflow_profit_factor_estimate=round(trade_pf_val, 4),
            trade_win_rate=round(trade_win_rate_val, 4),
            trade_profit_factor=round(trade_pf_val, 4),
        )

        self.metrics = metrics
        return metrics

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典，适用于 JSON 存储和 API 响应。

        Returns:
            包含完整回测结果的字典
        """
        return {
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "initial_capital": str(self.initial_capital),
            "metrics": self.metrics.to_dict() if self.metrics else None,
            "quality": self.quality.to_dict(),
            "equity_curve": [
                {
                    "trade_date": s.trade_date.isoformat(),
                    "equity": str(s.equity),
                    "cash": str(s.cash),
                    "position_value": str(s.position_value),
                }
                for s in self.equity_curve
            ],
            "trades": [
                {
                    "order_id": t.order_id,
                    "fund_code": t.fund_code,
                    "direction": t.direction,
                    "shares": str(t.shares),
                    "amount": str(t.amount),
                    "nav": str(t.nav),
                    "fee": str(t.fee),
                    "confirm_date": t.confirm_date.isoformat(),
                    "lot_details": t.lot_details,
                }
                for t in self.trades
            ],
            "holdings_history": [
                {
                    "trade_date": h.trade_date.isoformat(),
                    "positions": {k: str(v) for k, v in h.positions.items()},
                    "weights": h.weights,
                }
                for h in self.holdings_history
            ],
        }


# ---------------------------------------------------------------------------
# 指标计算辅助函数
# ---------------------------------------------------------------------------


def _empty_metrics(
    trading_days: int = 0,
    total_trades: int = 0,
) -> BacktestMetrics:
    """返回空指标（数据不足时使用）。"""
    return BacktestMetrics(
        total_return=0.0,
        annualized_return=0.0,
        max_drawdown=0.0,
        max_drawdown_start=None,
        max_drawdown_end=None,
        sharpe=0.0,
        sortino=0.0,
        volatility=0.0,
        calmar=0.0,
        win_rate=0.0,
        profit_factor=0.0,
        total_trades=total_trades,
        trading_days=trading_days,
        metrics_status="insufficient_data",
    )



def _annualize_return(total_return: float, years: float) -> float:
    """年化收益率。

    公式: (1 + total_return)^(1/years) - 1
    """
    if years <= 0:
        return 0.0
    if total_return <= -1.0:
        return -1.0
    return (1 + total_return) ** (1 / years) - 1


def _max_drawdown(
    equity_curve: list[EquitySnapshot],
) -> tuple[float, date | None, date | None]:
    """计算最大回撤及其起止日期。

    Returns:
        (max_drawdown, start_date, end_date)
        max_drawdown 为负值或零
    """
    if len(equity_curve) < 2:
        return 0.0, None, None

    equities = [float(s.equity) for s in equity_curve]
    peak = equities[0]
    peak_idx = 0
    max_dd = 0.0
    dd_start_idx = 0
    dd_end_idx = 0

    for i in range(1, len(equities)):
        if equities[i] > peak:
            peak = equities[i]
            peak_idx = i

        drawdown = (equities[i] - peak) / peak if peak != 0 else 0.0
        if drawdown < max_dd:
            max_dd = drawdown
            dd_start_idx = peak_idx
            dd_end_idx = i

    start_date = equity_curve[dd_start_idx].trade_date if max_dd < 0 else None
    end_date = equity_curve[dd_end_idx].trade_date if max_dd < 0 else None

    return max_dd, start_date, end_date


def _annualized_volatility(daily_returns: list[float]) -> float:
    """年化波动率。

    公式: std(daily_returns) * sqrt(252)
    """
    value = annualized_volatility_from_returns(daily_returns)
    if math.isnan(value) or abs(value) < 1e-12:
        return 0.0
    return value


def _sharpe_ratio(daily_returns: list[float], risk_free_rate: float = 0.0) -> float:
    """夏普比率（年化）。

    公式: (mean_return - rf_daily) / std * sqrt(252)
    """
    value = sharpe_ratio_from_returns(daily_returns, risk_free_rate=risk_free_rate)
    return 0.0 if math.isnan(value) else value


def _sortino_ratio(daily_returns: list[float], risk_free_rate: float = 0.0) -> float:
    """索提诺比率（年化）。

    使用下行偏差代替标准差。下行偏差采用统一指标工具中的全样本分母口径。
    """
    value = sortino_ratio_from_returns(daily_returns, risk_free_rate=risk_free_rate)
    return 0.0 if math.isnan(value) else value


def _calmar_ratio(annualized_return: float, max_drawdown: float) -> float:
    """卡尔玛比率。

    公式: annualized_return / abs(max_drawdown)
    """
    if max_drawdown == 0:
        return 0.0
    return annualized_return / abs(max_drawdown)


def _win_rate(daily_returns: list[float]) -> float:
    """胜率：日收益为正的天数占比。"""
    if not daily_returns:
        return 0.0
    wins = sum(1 for r in daily_returns if r > 0)
    return wins / len(daily_returns)


def _profit_factor(daily_returns: list[float]) -> float:
    """盈亏比：总盈利 / 总亏损绝对值。"""
    gains = sum(r for r in daily_returns if r > 0)
    losses = sum(abs(r) for r in daily_returns if r < 0)

    if losses == 0:
        return float("inf") if gains > 0 else 0.0
    return gains / losses


def _trade_level_metrics(trades: list[Fill]) -> tuple[float, float]:
    """计算按基金代码聚合的现金流估算胜率和盈亏比。

    这不是严格交易级胜率：未按 lot-level realized PnL 逐笔配对，
    也不纳入未平仓浮盈浮亏。保留该估算仅用于兼容旧报告字段，
    展示层不应把它作为核心专业交易指标。

    口径：申购为现金流出，赎回为现金流入，按基金代码汇总后估算净盈亏。

    Args:
        trades: 成交记录列表

    Returns:
        (trade_win_rate, trade_profit_factor) 元组
    """
    if not trades:
        return 0.0, 0.0

    # 按基金代码分组，计算每只基金的净盈亏
    # 申购为支出（负），赎回为收入（正），差值为盈亏
    fund_flows: dict[str, list[tuple[str, float]]] = {}
    for t in trades:
        code = t.fund_code
        if code not in fund_flows:
            fund_flows[code] = []
        amount = float(t.amount) if t.amount else 0.0
        fee = float(t.fee) if t.fee else 0.0
        direction = t.direction

        if direction == "subscribe":
            # 申购：支出 = amount + fee
            fund_flows[code].append(("out", amount + fee))
        elif direction == "redeem":
            # 赎回：收入 = amount - fee
            fund_flows[code].append(("in", amount - fee))

    # 计算每只基金的净盈亏
    pnl_list: list[float] = []
    for code, flows in fund_flows.items():
        total_out = sum(amt for direction, amt in flows if direction == "out")
        total_in = sum(amt for direction, amt in flows if direction == "in")
        if total_out > 0:  # 只有有过申购的基金才计入
            pnl = total_in - total_out
            pnl_list.append(pnl)

    if not pnl_list:
        return 0.0, 0.0

    # 交易级别胜率
    wins = sum(1 for p in pnl_list if p > 0)
    trade_win_rate = wins / len(pnl_list)

    # 交易级别盈亏比
    total_gains = sum(p for p in pnl_list if p > 0)
    total_losses = sum(abs(p) for p in pnl_list if p < 0)

    if total_losses == 0:
        trade_pf = float("inf") if total_gains > 0 else 0.0
    else:
        trade_pf = total_gains / total_losses

    return trade_win_rate, trade_pf

# ---------------------------------------------------------------------------
# VaR / CVaR 风险度量
# ---------------------------------------------------------------------------


def calc_var(daily_returns: list[float], confidence: float = 0.95) -> float:
    """计算历史模拟法 VaR（Value at Risk）。

    VaR(α) 表示在 α 置信度下，单日最大可能损失。

    Args:
        daily_returns: 日收益率序列
        confidence: 置信度（默认 95%）

    Returns:
        VaR 值（正数表示损失），数据不足返回 0
    """
    value = historical_var(daily_returns, confidence=confidence)
    return 0.0 if math.isnan(value) else value


def calc_cvar(daily_returns: list[float], confidence: float = 0.95) -> float:
    """计算 CVaR（Conditional Value at Risk / Expected Shortfall）。

    CVaR(α) 表示在超过 VaR 的极端情况下的平均损失。

    Args:
        daily_returns: 日收益率序列
        confidence: 置信度（默认 95%）

    Returns:
        CVaR 值（正数表示损失），数据不足返回 0
    """
    value = historical_cvar(daily_returns, confidence=confidence)
    return 0.0 if math.isnan(value) else value


# ---------------------------------------------------------------------------
# 基准相对指标
# ---------------------------------------------------------------------------


@dataclass
class BenchmarkMetrics:
    """基准相对绩效指标。

    Attributes:
        alpha: Jensen's Alpha（年化超额收益中不能被 Beta 解释的部分）
        beta: 组合收益对基准收益的回归系数
        information_ratio: 信息比率 = 超额收益均值 / 跟踪误差
        tracking_error: 跟踪误差（超额收益的年化标准差）
        treynor_ratio: Treynor 比率 = (R_p - R_f) / β
        excess_return: 总超额收益（组合收益 - 基准收益）
        excess_annualized: 年化超额收益
        var_95: 95% VaR（单日）
        cvar_95: 95% CVaR（单日）
    """

    alpha: float
    beta: float
    information_ratio: float
    tracking_error: float
    treynor_ratio: float
    excess_return: float
    excess_annualized: float
    var_95: float
    cvar_95: float

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典。"""
        return {
            "alpha": round(self.alpha, 6),
            "beta": round(self.beta, 4),
            "information_ratio": round(self.information_ratio, 4),
            "tracking_error": round(self.tracking_error, 6),
            "treynor_ratio": round(self.treynor_ratio, 4),
            "excess_return": round(self.excess_return, 6),
            "excess_annualized": round(self.excess_annualized, 6),
            "var_95": round(self.var_95, 6),
            "cvar_95": round(self.cvar_95, 6),
        }


def compute_benchmark_metrics(
    portfolio_returns: list[float],
    benchmark_returns: list[float],
    risk_free_rate: float = 0.0,
) -> BenchmarkMetrics | None:
    """计算基准相对指标。

    Args:
        portfolio_returns: 组合日收益率序列
        benchmark_returns: 基准日收益率序列（需与组合对齐）
        risk_free_rate: 年化无风险利率

    Returns:
        BenchmarkMetrics 实例，数据不足返回 None
    """
    if len(portfolio_returns) < 10 or len(benchmark_returns) < 10:
        return None

    n = min(len(portfolio_returns), len(benchmark_returns))
    p_ret = portfolio_returns[:n]
    b_ret = benchmark_returns[:n]

    # 超额收益序列
    excess = [p - b for p, b in zip(p_ret, b_ret)]

    # Beta: Cov(Rp, Rb) / Var(Rb)
    mean_p = sum(p_ret) / n
    mean_b = sum(b_ret) / n

    cov_pb = sum((p - mean_p) * (b - mean_b) for p, b in zip(p_ret, b_ret)) / (n - 1)
    var_b = sum((b - mean_b) ** 2 for b in b_ret) / (n - 1)

    beta = cov_pb / var_b if var_b > 0 else 0.0

    # Alpha: Jensen's Alpha (年化)
    rf_daily = risk_free_rate / 252.0
    alpha_daily = mean_p - rf_daily - beta * (mean_b - rf_daily)
    alpha = alpha_daily * 252

    # 跟踪误差 (年化)
    mean_excess = sum(excess) / n
    excess_var = sum((e - mean_excess) ** 2 for e in excess) / (n - 1)
    tracking_error = math.sqrt(excess_var) * math.sqrt(252)

    # 信息比率
    information_ratio = (mean_excess * 252) / tracking_error if tracking_error > 0 else 0.0

    # Treynor 比率
    annualized_excess_over_rf = (mean_p - rf_daily) * 252
    treynor_ratio = annualized_excess_over_rf / beta if abs(beta) > 1e-8 else 0.0

    # 总超额收益
    total_p = 1.0
    total_b = 1.0
    for p, b in zip(p_ret, b_ret):
        total_p *= (1 + p)
        total_b *= (1 + b)
    excess_return = (total_p - 1) - (total_b - 1)

    # 年化超额收益
    years = n / 252.0
    excess_annualized = _annualize_return(total_p - 1, years) - _annualize_return(total_b - 1, years)

    # VaR / CVaR
    var_95 = calc_var(p_ret, 0.95)
    cvar_95 = calc_cvar(p_ret, 0.95)

    return BenchmarkMetrics(
        alpha=alpha,
        beta=beta,
        information_ratio=information_ratio,
        tracking_error=tracking_error,
        treynor_ratio=treynor_ratio,
        excess_return=excess_return,
        excess_annualized=excess_annualized,
        var_95=var_95,
        cvar_95=cvar_95,
    )


# ---------------------------------------------------------------------------
# 滚动指标计算
# ---------------------------------------------------------------------------


@dataclass
class RollingMetrics:
    """滚动指标数据。

    Attributes:
        dates: 日期序列
        rolling_return: 20日滚动收益率
        rolling_sharpe: 60日滚动Sharpe
        rolling_drawdown: 当前回撤序列
        rolling_volatility: 20日滚动波动率
        monthly_returns: 月度收益率 {"2024-01": 0.023}
        yearly_returns: 年度收益率 {"2024": 0.156}
    """

    dates: list[date]
    rolling_return: list[float]
    rolling_sharpe: list[float]
    rolling_drawdown: list[float]
    rolling_volatility: list[float]
    monthly_returns: dict[str, float]
    yearly_returns: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典。"""
        return {
            "dates": [d.isoformat() for d in self.dates],
            "rolling_return": [round(v, 6) for v in self.rolling_return],
            "rolling_sharpe": [round(v, 4) for v in self.rolling_sharpe],
            "rolling_drawdown": [round(v, 6) for v in self.rolling_drawdown],
            "rolling_volatility": [round(v, 6) for v in self.rolling_volatility],
            "monthly_returns": {k: round(v, 6) for k, v in self.monthly_returns.items()},
            "yearly_returns": {k: round(v, 6) for k, v in self.yearly_returns.items()},
        }


def compute_rolling_metrics(
    equity_curve: list[EquitySnapshot],
    sharpe_window: int = 60,
    vol_window: int = 20,
    return_window: int = 20,
) -> RollingMetrics | None:
    """计算滚动指标。

    Args:
        equity_curve: 权益曲线
        sharpe_window: Sharpe 滚动窗口（交易日）
        vol_window: 波动率滚动窗口（交易日）
        return_window: 收益率滚动窗口（交易日）

    Returns:
        RollingMetrics 实例，数据不足返回 None
    """
    if len(equity_curve) < max(sharpe_window, vol_window, return_window) + 1:
        return None

    equities = [float(s.equity) for s in equity_curve]
    dates = [s.trade_date for s in equity_curve]

    # 日收益率
    daily_returns = [
        (equities[i] - equities[i - 1]) / equities[i - 1]
        for i in range(1, len(equities))
        if equities[i - 1] != 0
    ]

    n = len(daily_returns)
    rolling_return: list[float] = []
    rolling_sharpe: list[float] = []
    rolling_vol: list[float] = []
    rolling_dd: list[float] = []

    # 滚动收益、Sharpe 和波动率
    for i in range(n):
        equity_idx = i + 1

        # 滚动收益率（20日窗口）
        if equity_idx >= return_window and equities[equity_idx - return_window] > 0:
            start_equity = equities[equity_idx - return_window]
            rolling_return.append(equities[equity_idx] / start_equity - 1.0)
        else:
            rolling_return.append(0.0)

        # 滚动 Sharpe（60日窗口）
        if i >= sharpe_window - 1:
            window = daily_returns[i - sharpe_window + 1: i + 1]
            rolling_sharpe.append(_sharpe_ratio(window))
        else:
            rolling_sharpe.append(0.0)

        # 滚动波动率（20日窗口）
        if i >= vol_window - 1:
            window = daily_returns[i - vol_window + 1: i + 1]
            rolling_vol.append(_annualized_volatility(window))
        else:
            rolling_vol.append(0.0)

    # 当前回撤序列
    peak = equities[0]
    for i in range(len(equities)):
        if equities[i] > peak:
            peak = equities[i]
        dd = (equities[i] - peak) / peak if peak > 0 else 0.0
        rolling_dd.append(dd)

    # 月度收益率
    monthly_returns: dict[str, float] = {}
    month_start_equity = equities[0]
    current_month = f"{dates[0].year}-{dates[0].month:02d}"

    for i in range(1, len(equities)):
        month_key = f"{dates[i].year}-{dates[i].month:02d}"
        if month_key != current_month:
            # 上个月结束
            monthly_returns[current_month] = (
                (equities[i - 1] - month_start_equity) / month_start_equity
                if month_start_equity > 0 else 0.0
            )
            month_start_equity = equities[i - 1]
            current_month = month_key

    # 最后一个月
    if month_start_equity > 0:
        monthly_returns[current_month] = (equities[-1] - month_start_equity) / month_start_equity

    # 年度收益率
    yearly_returns: dict[str, float] = {}
    year_start_equity = equities[0]
    current_year = str(dates[0].year)

    for i in range(1, len(equities)):
        year_key = str(dates[i].year)
        if year_key != current_year:
            yearly_returns[current_year] = (
                (equities[i - 1] - year_start_equity) / year_start_equity
                if year_start_equity > 0 else 0.0
            )
            year_start_equity = equities[i - 1]
            current_year = year_key

    if year_start_equity > 0:
        yearly_returns[current_year] = (equities[-1] - year_start_equity) / year_start_equity

    return RollingMetrics(
        dates=dates[1:],  # 与 daily_returns 对齐
        rolling_return=rolling_return,
        rolling_sharpe=rolling_sharpe,
        rolling_drawdown=rolling_dd[1:],
        rolling_volatility=rolling_vol,
        monthly_returns=monthly_returns,
        yearly_returns=yearly_returns,
    )
