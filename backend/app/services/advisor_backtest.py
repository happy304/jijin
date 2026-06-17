"""交易建议引擎历史回测验证模块。

目的：验证交易建议引擎的信号聚合逻辑是否在历史上有效。
与策略回测的区别：
- 策略回测：验证"某个交易规则"（如双均线择时）的历史表现
- 本模块：验证"交易建议引擎"（综合评分系统）的历史建议准确性

验证方法：
1. 在历史每个交易日，用当时可用的数据运行建议引擎
2. 记录引擎给出的买/卖/持有建议
3. 统计建议后 N 天的实际收益
4. 计算命中率、平均收益、风险指标

输出指标：
- 增配候选命中率（增配候选后 N 天正收益的比例）
- 减配候选命中率（减配候选后 N 天负收益的比例）
- 候选信号后平均收益（增配/减配分别统计）
- 模拟组合收益曲线（按建议操作的累计收益）
- 与基准（持有不动）的对比

局限性说明：
- 这是样本内验证，不能证明未来有效
- 存在前视偏差风险（虽然代码设计上避免了，但需要审慎对待）
- 交易费用使用估算值，实际费用可能不同
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Literal

import numpy as np

from app.domain.performance.metrics import sharpe_ratio_from_returns
from app.services.trading_advisor import (
    AdvisorConfig,
    TradingAdvisor,
)

logger = logging.getLogger(__name__)


ExecutionMode = Literal["pre_nav", "post_nav_next_day"]

EXECUTION_MODE_DESCRIPTIONS: dict[str, str] = {
    "pre_nav": "盘前/盘中决策：仅使用执行日前一交易日及以前的净值，按执行日净值成交。",
    "post_nav_next_day": "净值公布后决策：使用执行日已公布净值，最早按下一交易日净值成交。",
}

_EXECUTION_MODE_ALIASES = {"post_nav": "post_nav_next_day"}


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class AdviceRecord:
    """单条历史建议记录。"""

    advice_date: str
    fund_code: str
    action: str  # buy/sell/hold
    composite_score: float
    confidence: float
    # 建议后的实际收益
    return_5d: float | None = None
    return_10d: float | None = None
    return_20d: float | None = None
    return_60d: float | None = None
    # 是否命中
    hit_5d: bool | None = None
    hit_10d: bool | None = None
    hit_20d: bool | None = None


@dataclass
class AdvisorBacktestMetrics:
    """建议引擎回测验证指标。"""

    # 基本统计
    total_advice_days: int = 0
    total_buy_signals: int = 0
    total_sell_signals: int = 0
    total_hold_signals: int = 0

    # 命中率（买入建议后正收益 / 卖出建议后负收益）
    buy_hit_rate_5d: float | None = None
    buy_hit_rate_10d: float | None = None
    buy_hit_rate_20d: float | None = None
    sell_hit_rate_5d: float | None = None
    sell_hit_rate_10d: float | None = None
    sell_hit_rate_20d: float | None = None

    # 平均收益
    buy_avg_return_5d: float | None = None
    buy_avg_return_10d: float | None = None
    buy_avg_return_20d: float | None = None
    sell_avg_return_5d: float | None = None
    sell_avg_return_10d: float | None = None
    sell_avg_return_20d: float | None = None

    # 模拟组合表现
    simulated_total_return: float | None = None
    simulated_annualized_return: float | None = None
    simulated_max_drawdown: float | None = None
    simulated_sharpe: float | None = None
    benchmark_total_return: float | None = None  # 持有不动的收益

    # 信号质量
    avg_confidence_when_correct: float | None = None
    avg_confidence_when_wrong: float | None = None
    information_coefficient: float | None = None  # IC: score 与未来收益的相关性

    # 费用影响
    total_fees_paid: float = 0.0
    fee_drag_pct: float = 0.0  # 费用对收益的拖累

    def to_dict(self) -> dict[str, Any]:
        """序列化为 API 响应。"""
        return {
            "total_advice_days": self.total_advice_days,
            "signals": {
                "buy": self.total_buy_signals,
                "sell": self.total_sell_signals,
                "hold": self.total_hold_signals,
            },
            "hit_rates": {
                "buy_5d": self.buy_hit_rate_5d,
                "buy_10d": self.buy_hit_rate_10d,
                "buy_20d": self.buy_hit_rate_20d,
                "sell_5d": self.sell_hit_rate_5d,
                "sell_10d": self.sell_hit_rate_10d,
                "sell_20d": self.sell_hit_rate_20d,
            },
            "avg_returns": {
                "buy_5d": self.buy_avg_return_5d,
                "buy_10d": self.buy_avg_return_10d,
                "buy_20d": self.buy_avg_return_20d,
                "sell_5d": self.sell_avg_return_5d,
                "sell_10d": self.sell_avg_return_10d,
                "sell_20d": self.sell_avg_return_20d,
            },
            "simulated_portfolio": {
                "total_return": self.simulated_total_return,
                "annualized_return": self.simulated_annualized_return,
                "max_drawdown": self.simulated_max_drawdown,
                "sharpe": self.simulated_sharpe,
                "benchmark_return": self.benchmark_total_return,
            },
            "signal_quality": {
                "avg_confidence_correct": self.avg_confidence_when_correct,
                "avg_confidence_wrong": self.avg_confidence_when_wrong,
                "information_coefficient": self.information_coefficient,
            },
            "fees": {
                "total_paid": round(self.total_fees_paid, 2),
                "drag_pct": round(self.fee_drag_pct, 4),
            },
        }


@dataclass
class AdvisorBacktestResult:
    """完整的回测验证结果。"""

    fund_code: str
    fund_name: str | None = None
    start_date: str = ""
    end_date: str = ""
    config: dict[str, Any] = field(default_factory=dict)
    metrics: AdvisorBacktestMetrics = field(default_factory=AdvisorBacktestMetrics)
    equity_curve: list[dict[str, Any]] = field(default_factory=list)
    advice_records: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fund_code": self.fund_code,
            "fund_name": self.fund_name,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "config": self.config,
            "metrics": self.metrics.to_dict(),
            "equity_curve": self.equity_curve,
            "advice_sample": self.advice_records[:50],  # 只返回前50条
            "warnings": self.warnings,
            "disclaimer": (
                "本回测为样本内验证，仅说明引擎在历史数据上的表现，"
                "不能证明未来有效。存在过拟合风险。"
            ),
        }


# ---------------------------------------------------------------------------
# 核心回测逻辑
# ---------------------------------------------------------------------------


def _generate_live_advice_for_history(
    fund_code: str,
    nav_records: list[tuple[str, float]],
    as_of_date: date,
    total_capital: float,
    config: AdvisorConfig,
    fund_name: str | None = None,
    fund_type: str | None = None,
    sub_type: str | None = None,
    current_positions: dict[str, float] | None = None,
    positions_detail: dict[str, dict[str, Any]] | None = None,
    last_advices: dict[str, dict[str, str]] | None = None,
    subscribe_fee_rate: float = 0.0015,
    redeem_fee_rate: float = 0.005,
):
    """在历史时点直接调用 live advisor 生成建议。"""
    advisor = TradingAdvisor(
        config=config,
        total_capital=max(total_capital, 0.0),
        current_positions=current_positions,
        positions_detail=positions_detail,
        last_advices=last_advices,
        learned_weights=None,
        as_of_date=as_of_date,
    )
    advices = advisor.generate_advice(
        fund_codes=[fund_code],
        nav_data={fund_code: nav_records},
        strategy_signals={},
        fund_names={fund_code: fund_name or fund_code},
        fund_types={fund_code: (fund_type, sub_type)},
        fee_data={
            fund_code: {
                "subscribe_rate": subscribe_fee_rate,
                "redeem_rate": redeem_fee_rate,
            }
        },
        fund_rules={},
    )
    if not advices:
        raise ValueError(f"无法为基金 {fund_code} 生成历史建议")
    return advices[0]


def run_advisor_backtest(
    fund_code: str,
    nav_records: list[tuple[str, float]],
    fund_name: str | None = None,
    fund_type: str | None = None,
    sub_type: str | None = None,
    config: AdvisorConfig | None = None,
    lookback_window: int = 250,
    rebalance_freq: int = 5,
    initial_capital: float = 100000.0,
    subscribe_fee_rate: float = 0.0015,
    redeem_fee_rate: float = 0.005,
    execution_mode: ExecutionMode | str = "post_nav_next_day",
) -> AdvisorBacktestResult:
    """对单只基金运行交易建议引擎的历史回测验证。

    方法：
    1. 从第 lookback_window 天开始，每隔 rebalance_freq 天运行一次引擎
    2. 按 execution_mode 明确区分“可见数据截止日”和“成交净值日”
    3. 记录建议方向和评分
    4. 从真实可执行成交日之后统计 5/10/20/60 天的实际收益
    5. 模拟按建议操作的组合收益

    Args:
        fund_code: 基金代码
        nav_records: 完整净值序列 [(date_str, nav), ...]
        fund_name: 基金名称
        fund_type: 基金类型
        sub_type: 子类型
        config: 引擎配置
        lookback_window: 引擎需要的最少历史数据天数
        rebalance_freq: 每隔多少天运行一次引擎（模拟调仓频率）
        initial_capital: 初始资金
        subscribe_fee_rate: 申购费率
        redeem_fee_rate: 赎回费率
        execution_mode: 执行口径。pre_nav=盘前/盘中决策，仅能看到前一交易日净值，
            按当日净值成交；post_nav_next_day=净值公布后决策，可看到当日净值，最早按下一交易日净值成交。

    Returns:
        AdvisorBacktestResult
    """
    execution_mode = _EXECUTION_MODE_ALIASES.get(str(execution_mode), str(execution_mode))
    if execution_mode not in EXECUTION_MODE_DESCRIPTIONS:
        raise ValueError(
            f"Unsupported execution_mode={execution_mode!r}; "
            f"expected one of {sorted(EXECUTION_MODE_DESCRIPTIONS)}"
        )

    if not config:
        config = AdvisorConfig()

    result = AdvisorBacktestResult(
        fund_code=fund_code,
        fund_name=fund_name,
        config={
            "lookback_window": lookback_window,
            "rebalance_freq": rebalance_freq,
            "execution_assumption": execution_mode,
            "execution_mode": execution_mode,
            "execution_mode_description": EXECUTION_MODE_DESCRIPTIONS[execution_mode],
            "buy_threshold": config.buy_threshold,
            "sell_threshold": config.sell_threshold,
        },
    )

    n = len(nav_records)
    if n < lookback_window + 60:
        result.warnings.append(
            f"数据不足：需要至少 {lookback_window + 60} 天，实际 {n} 天"
        )
        return result

    first_execution_idx = lookback_window if execution_mode == "pre_nav" else lookback_window + 1
    if first_execution_idx >= n:
        result.warnings.append(
            f"数据不足：执行口径 {execution_mode} 需要至少 {first_execution_idx + 1} 天，实际 {n} 天"
        )
        return result

    result.start_date = nav_records[first_execution_idx][0]
    result.end_date = nav_records[-1][0]

    # 提取净值数组
    dates = [r[0] for r in nav_records]
    navs = np.array([r[1] for r in nav_records], dtype=np.float64)

    # 收集所有建议记录
    advice_records: list[AdviceRecord] = []

    # 模拟组合状态
    cash = initial_capital
    position_shares = 0.0  # 持有份额
    total_fees = 0.0
    equity_curve: list[tuple[str, float]] = []

    # 从 lookback_window 开始生成建议，每隔 rebalance_freq 天运行引擎。
    # i 表示“可见数据截止日”，execution_idx 表示真实可执行成交日。
    last_advices: dict[str, dict[str, str]] = {}
    last_buy_date_str: str | None = None
    last_buy_cost = 0.0
    for i in range(lookback_window, n, rebalance_freq):
        if execution_mode == "pre_nav":
            # 盘前/盘中决策：T 日决策只能看到 T-1 及以前净值，按 T 日净值成交。
            visible_end_idx = i - 1
            execution_idx = i
        else:
            # 净值公布后决策：T 日净值公布后才生成建议，最早按 T+1 净值成交。
            visible_end_idx = i
            execution_idx = i + 1

        if visible_end_idx < 0 or execution_idx >= n:
            continue
        if visible_end_idx + 1 < lookback_window:
            continue

        current_nav = navs[execution_idx]
        current_date = dates[execution_idx]
        as_of_date = date.fromisoformat(dates[visible_end_idx])
        historical_records = nav_records[:visible_end_idx + 1]

        # 计算当前持仓市值
        position_value = position_shares * current_nav
        total_equity = cash + position_value

        positions_detail = {}
        if position_shares > 0:
            positions_detail[fund_code] = {
                "buy_date": last_buy_date_str,
                "cost": last_buy_cost,
            }

        advice = _generate_live_advice_for_history(
            fund_code=fund_code,
            nav_records=historical_records,
            as_of_date=as_of_date,
            total_capital=total_equity,
            config=config,
            fund_name=fund_name,
            fund_type=fund_type,
            sub_type=sub_type,
            current_positions={fund_code: position_value} if position_value > 0 else {},
            positions_detail=positions_detail,
            last_advices=last_advices,
            subscribe_fee_rate=subscribe_fee_rate,
            redeem_fee_rate=redeem_fee_rate,
        )
        action = advice.action
        composite = advice.composite_score
        confidence = advice.confidence

        # 计算建议后的实际收益（前瞻数据，仅用于验证统计）
        record = AdviceRecord(
            advice_date=current_date,
            fund_code=fund_code,
            action=action,
            composite_score=composite,
            confidence=confidence,
        )

        if execution_idx + 5 < n:
            record.return_5d = float((navs[execution_idx + 5] / current_nav) - 1)
            if action == "buy":
                record.hit_5d = record.return_5d > 0
            elif action == "sell":
                record.hit_5d = record.return_5d < 0
        if execution_idx + 10 < n:
            record.return_10d = float((navs[execution_idx + 10] / current_nav) - 1)
            if action == "buy":
                record.hit_10d = record.return_10d > 0
            elif action == "sell":
                record.hit_10d = record.return_10d < 0
        if execution_idx + 20 < n:
            record.return_20d = float((navs[execution_idx + 20] / current_nav) - 1)
            if action == "buy":
                record.hit_20d = record.return_20d > 0
            elif action == "sell":
                record.hit_20d = record.return_20d < 0
        if execution_idx + 60 < n:
            record.return_60d = float((navs[execution_idx + 60] / current_nav) - 1)

        advice_records.append(record)

        # 模拟交易执行：直接按 live advisor 的建议金额执行
        if action == "buy" and cash > config.min_trade_amount:
            buy_amount = min(cash, max(0.0, advice.suggested_amount))
            if buy_amount >= config.min_trade_amount:
                fee = 0.0
                if advice.fee_estimate is not None:
                    fee = min(float(advice.fee_estimate.estimated_fee), buy_amount)
                net_amount = max(0.0, buy_amount - fee)
                if net_amount > 0:
                    shares_bought = net_amount / current_nav
                    position_shares += shares_bought
                    cash -= buy_amount
                    total_fees += fee
                    last_buy_date_str = current_date
                    last_buy_cost = position_shares * current_nav

        elif action == "sell" and position_shares > 0:
            sell_amount = min(position_value, max(0.0, advice.suggested_amount))
            if sell_amount > 0:
                shares_to_sell = min(position_shares, sell_amount / current_nav)
                gross_value = shares_to_sell * current_nav
                fee = 0.0
                if advice.fee_estimate is not None:
                    estimated_fee = float(advice.fee_estimate.estimated_fee)
                    if position_value > 0:
                        fee = min(gross_value, estimated_fee * (gross_value / position_value))
                    else:
                        fee = min(gross_value, estimated_fee)
                cash += gross_value - fee
                position_shares -= shares_to_sell
                total_fees += fee
                if position_shares <= 1e-12:
                    position_shares = 0.0
                    last_buy_date_str = None
                    last_buy_cost = 0.0
                else:
                    last_buy_cost = position_shares * current_nav

        last_advices[fund_code] = {"action": action, "date": current_date}

        # 记录权益曲线
        equity = cash + position_shares * current_nav
        equity_curve.append((current_date, equity))

    # 计算最终权益（用最后一天的净值）
    final_equity = cash + position_shares * navs[-1]
    equity_curve.append((dates[-1], final_equity))

    # ---------------------------------------------------------------------------
    # 统计指标计算
    # ---------------------------------------------------------------------------
    metrics = AdvisorBacktestMetrics()
    metrics.total_advice_days = len(advice_records)

    buy_records = [r for r in advice_records if r.action == "buy"]
    sell_records = [r for r in advice_records if r.action == "sell"]
    hold_records = [r for r in advice_records if r.action == "hold"]

    metrics.total_buy_signals = len(buy_records)
    metrics.total_sell_signals = len(sell_records)
    metrics.total_hold_signals = len(hold_records)

    # 命中率
    def _hit_rate(records: list[AdviceRecord], attr: str) -> float | None:
        hits = [getattr(r, attr) for r in records if getattr(r, attr) is not None]
        if not hits:
            return None
        return round(sum(1 for h in hits if h) / len(hits), 4)

    def _avg_return(records: list[AdviceRecord], attr: str) -> float | None:
        returns = [getattr(r, attr) for r in records if getattr(r, attr) is not None]
        if not returns:
            return None
        return round(float(np.mean(returns)), 6)

    metrics.buy_hit_rate_5d = _hit_rate(buy_records, "hit_5d")
    metrics.buy_hit_rate_10d = _hit_rate(buy_records, "hit_10d")
    metrics.buy_hit_rate_20d = _hit_rate(buy_records, "hit_20d")
    metrics.sell_hit_rate_5d = _hit_rate(sell_records, "hit_5d")
    metrics.sell_hit_rate_10d = _hit_rate(sell_records, "hit_10d")
    metrics.sell_hit_rate_20d = _hit_rate(sell_records, "hit_20d")

    metrics.buy_avg_return_5d = _avg_return(buy_records, "return_5d")
    metrics.buy_avg_return_10d = _avg_return(buy_records, "return_10d")
    metrics.buy_avg_return_20d = _avg_return(buy_records, "return_20d")
    metrics.sell_avg_return_5d = _avg_return(sell_records, "return_5d")
    metrics.sell_avg_return_10d = _avg_return(sell_records, "return_10d")
    metrics.sell_avg_return_20d = _avg_return(sell_records, "return_20d")

    # 模拟组合表现
    if equity_curve:
        equities = np.array([e[1] for e in equity_curve])
        metrics.simulated_total_return = round(float(equities[-1] / equities[0] - 1), 6)

        # 年化收益
        n_days = len(nav_records) - lookback_window
        if n_days > 0:
            years = n_days / 252.0
            if years > 0:
                metrics.simulated_annualized_return = round(
                    float((equities[-1] / equities[0]) ** (1 / years) - 1), 6
                )

        # 最大回撤
        running_max = np.maximum.accumulate(equities)
        drawdowns = (equities - running_max) / running_max
        metrics.simulated_max_drawdown = round(float(np.min(drawdowns)), 6)

        # 夏普比率：复用统一绩效指标工具，避免 Advisor 与回测报告口径漂移。
        if len(equities) > 1:
            returns = np.diff(equities) / equities[:-1]
            sharpe = sharpe_ratio_from_returns(returns, freq=max(1, int(round(252 / rebalance_freq))))
            if not np.isnan(sharpe):
                metrics.simulated_sharpe = round(float(sharpe), 4)

    # 基准收益（持有不动）
    start_nav = navs[lookback_window]
    end_nav = navs[-1]
    metrics.benchmark_total_return = round(float(end_nav / start_nav - 1), 6)

    # 信号质量：IC（综合评分与未来20日收益的 Spearman 秩相关性）
    # 使用 Spearman 而非 Pearson：对极端收益更稳健，且 Rank IC 是量化行业标准
    scores_with_returns = [
        (r.composite_score, r.return_20d)
        for r in advice_records
        if r.return_20d is not None
    ]
    if len(scores_with_returns) >= 20:
        scores_arr = np.array([s[0] for s in scores_with_returns])
        returns_arr = np.array([s[1] for s in scores_with_returns])
        if np.std(scores_arr) > 0 and np.std(returns_arr) > 0:
            from scipy.stats import spearmanr
            ic_val, _ = spearmanr(scores_arr, returns_arr)
            metrics.information_coefficient = round(float(ic_val), 4) if not np.isnan(ic_val) else None

    # 置信度与正确性的关系
    correct_confidences = []
    wrong_confidences = []
    for r in advice_records:
        if r.hit_20d is True:
            correct_confidences.append(r.confidence)
        elif r.hit_20d is False:
            wrong_confidences.append(r.confidence)
    if correct_confidences:
        metrics.avg_confidence_when_correct = round(float(np.mean(correct_confidences)), 4)
    if wrong_confidences:
        metrics.avg_confidence_when_wrong = round(float(np.mean(wrong_confidences)), 4)

    # 费用统计
    metrics.total_fees_paid = total_fees
    if initial_capital > 0:
        metrics.fee_drag_pct = total_fees / initial_capital

    # 组装结果
    result.metrics = metrics
    result.equity_curve = [
        {"date": d, "equity": round(e, 2)} for d, e in equity_curve
    ]
    result.advice_records = [
        {
            "date": r.advice_date,
            "action": r.action,
            "score": round(r.composite_score, 4),
            "confidence": round(r.confidence, 4),
            "return_5d": r.return_5d,
            "return_20d": r.return_20d,
            "hit_20d": r.hit_20d,
            "execution_assumption": execution_mode,
        }
        for r in advice_records
    ]

    # 警告
    if metrics.information_coefficient is not None:
        if metrics.information_coefficient < 0.02:
            result.warnings.append(
                f"IC 值极低（{metrics.information_coefficient:.4f}），"
                f"引擎评分与未来收益几乎无相关性，信号可能无效"
            )
        elif metrics.information_coefficient < 0.05:
            result.warnings.append(
                f"IC 值较低（{metrics.information_coefficient:.4f}），"
                f"信号有微弱预测力但不稳定"
            )

    if metrics.total_buy_signals < 10:
        result.warnings.append("买入信号样本量不足（<10），命中率统计不可靠")
    if metrics.total_sell_signals < 10:
        result.warnings.append("卖出信号样本量不足（<10），命中率统计不可靠")

    result.warnings.append(EXECUTION_MODE_DESCRIPTIONS[execution_mode])
    result.warnings.append(
        "本回测为样本内验证（in-sample），不能作为未来表现的保证"
    )

    return result


# ---------------------------------------------------------------------------
# 数据库加载辅助
# ---------------------------------------------------------------------------


async def load_and_run_advisor_backtest(
    fund_code: str,
    session: Any,
    lookback_days: int | None = None,
    rebalance_freq: int = 5,
    config: AdvisorConfig | None = None,
) -> AdvisorBacktestResult:
    """从数据库加载数据并运行建议引擎回测。

    Args:
        fund_code: 基金代码
        session: AsyncSession
        lookback_days: 加载多少天的历史数据。None 表示使用全部可用数据。
        rebalance_freq: 调仓频率（天）
        config: 引擎配置

    Returns:
        AdvisorBacktestResult
    """
    from sqlalchemy import text

    from app.services.trading_advisor import load_fund_names, load_fund_types

    # 加载净值数据
    end_date = date.today()

    if lookback_days:
        start_date = end_date - timedelta(days=lookback_days)
        query = text(
            "SELECT trade_date, COALESCE(adj_nav, unit_nav) as nav FROM fund_nav "
            "WHERE fund_code = :code "
            "AND trade_date BETWEEN :start AND :end "
            "AND (adj_nav IS NOT NULL OR unit_nav IS NOT NULL) "
            "ORDER BY trade_date"
        )
        result = await session.execute(
            query, {"code": fund_code, "start": start_date, "end": end_date}
        )
    else:
        # 使用全部可用数据
        query = text(
            "SELECT trade_date, COALESCE(adj_nav, unit_nav) as nav FROM fund_nav "
            "WHERE fund_code = :code "
            "AND (adj_nav IS NOT NULL OR unit_nav IS NOT NULL) "
            "ORDER BY trade_date"
        )
        result = await session.execute(query, {"code": fund_code})
    nav_records = [(str(row[0]), float(row[1])) for row in result]

    # 加载基金信息
    names = await load_fund_names([fund_code], session)
    types = await load_fund_types([fund_code], session)
    fund_name = names.get(fund_code)
    fund_type, sub_type = types.get(fund_code, (None, None))

    # 加载费率
    fee_query = text(
        "SELECT fee_type, MIN(rate) FROM fund_fees "
        "WHERE fund_code = :code GROUP BY fee_type"
    )
    fee_result = await session.execute(fee_query, {"code": fund_code})
    subscribe_fee = 0.0015
    redeem_fee = 0.005
    for row in fee_result:
        if row[0] == "subscribe":
            subscribe_fee = float(row[1])
        elif row[0] == "redeem":
            redeem_fee = float(row[1])

    # 运行回测
    return run_advisor_backtest(
        fund_code=fund_code,
        nav_records=nav_records,
        fund_name=fund_name,
        fund_type=fund_type,
        sub_type=sub_type,
        config=config,
        lookback_window=min(250, len(nav_records) // 3),
        rebalance_freq=rebalance_freq,
        subscribe_fee_rate=subscribe_fee,
        redeem_fee_rate=redeem_fee,
    )


# ---------------------------------------------------------------------------
# Walk-Forward 样本外验证
# ---------------------------------------------------------------------------


@dataclass
class WalkForwardFold:
    """单个 walk-forward 折叠的结果。"""

    fold_index: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    # 样本内指标
    in_sample_ic: float | None = None
    in_sample_buy_hit_rate: float | None = None
    in_sample_sell_hit_rate: float | None = None
    # 样本外指标
    oos_ic: float | None = None
    oos_buy_hit_rate: float | None = None
    oos_sell_hit_rate: float | None = None
    oos_buy_count: int = 0
    oos_sell_count: int = 0
    oos_avg_return_buy: float | None = None
    oos_avg_return_sell: float | None = None


@dataclass
class AdvisorCPCVDiagnostics:
    """Advisor CPCV/PBO 过拟合诊断摘要。"""

    pbo: float | None = None
    avg_oos_sharpe: float | None = None
    std_oos_sharpe: float | None = None
    avg_is_sharpe: float | None = None
    n_paths: int = 0
    n_splits: int = 0
    n_test_splits: int = 0
    is_overfit: bool = False
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pbo": self.pbo,
            "avg_oos_sharpe": self.avg_oos_sharpe,
            "std_oos_sharpe": self.std_oos_sharpe,
            "avg_is_sharpe": self.avg_is_sharpe,
            "n_paths": self.n_paths,
            "n_splits": self.n_splits,
            "n_test_splits": self.n_test_splits,
            "is_overfit": self.is_overfit,
            "warnings": self.warnings,
        }


@dataclass
class WalkForwardResult:
    """Walk-Forward 验证完整结果。"""

    fund_code: str
    fund_name: str | None = None
    n_folds: int = 0
    train_window_days: int = 0
    test_window_days: int = 0
    # 数据信息
    requested_days: int | None = None  # 用户请求的天数（None=全部）
    actual_trading_days: int = 0  # 实际获取的交易日数
    data_start_date: str | None = None  # 数据起始日期
    data_end_date: str | None = None  # 数据结束日期
    # 汇总指标（所有 OOS 折叠的平均）
    avg_oos_ic: float | None = None
    avg_oos_buy_hit_rate: float | None = None
    avg_oos_sell_hit_rate: float | None = None
    # 与样本内对比
    avg_is_ic: float | None = None
    ic_degradation: float | None = None  # OOS IC / IS IC，<1 表示过拟合
    # 各折叠详情
    folds: list[WalkForwardFold] = field(default_factory=list)
    # 汇总
    total_oos_signals: int = 0
    total_oos_buy: int = 0
    total_oos_sell: int = 0
    cpcv: AdvisorCPCVDiagnostics | None = None
    multi_objective_score: float | None = None
    multi_objective_components: dict[str, float] = field(default_factory=dict)
    multi_objective_eliminated: bool = False
    multi_objective_reasons: list[str] = field(default_factory=list)
    baseline_adjusted_score: float | None = None
    baseline_comparison: dict[str, dict[str, Any]] = field(default_factory=dict)
    baseline_passed: bool | None = None
    baseline_reasons: list[str] = field(default_factory=list)
    baseline_best: dict[str, Any] | None = None
    baseline_metrics: dict[str, dict[str, Any]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fund_code": self.fund_code,
            "fund_name": self.fund_name,
            "n_folds": self.n_folds,
            "train_window_days": self.train_window_days,
            "test_window_days": self.test_window_days,
            "data_info": {
                "requested_days": self.requested_days,
                "actual_trading_days": self.actual_trading_days,
                "data_start_date": self.data_start_date,
                "data_end_date": self.data_end_date,
            },
            "summary": {
                "avg_oos_ic": self.avg_oos_ic,
                "avg_oos_buy_hit_rate": self.avg_oos_buy_hit_rate,
                "avg_oos_sell_hit_rate": self.avg_oos_sell_hit_rate,
                "avg_is_ic": self.avg_is_ic,
                "ic_degradation": self.ic_degradation,
                "total_oos_signals": self.total_oos_signals,
                "total_oos_buy": self.total_oos_buy,
                "total_oos_sell": self.total_oos_sell,
                "multi_objective_score": self.multi_objective_score,
                "multi_objective_components": self.multi_objective_components,
                "multi_objective_eliminated": self.multi_objective_eliminated,
                "multi_objective_reasons": self.multi_objective_reasons,
                "baseline_adjusted_score": self.baseline_adjusted_score,
                "baseline_passed": self.baseline_passed,
                "baseline_reasons": self.baseline_reasons,
            },
            "multi_objective": {
                "score": self.multi_objective_score,
                "components": self.multi_objective_components,
                "eliminated": self.multi_objective_eliminated,
                "reasons": self.multi_objective_reasons,
            },
            "baseline": {
                "adjusted_score": self.baseline_adjusted_score,
                "passed": self.baseline_passed,
                "reasons": self.baseline_reasons,
                "best": self.baseline_best,
                "comparison": self.baseline_comparison,
                "metrics": self.baseline_metrics,
            },
            "baseline_adjusted_score": self.baseline_adjusted_score,
            "baseline_passed": self.baseline_passed,
            "baseline_reasons": self.baseline_reasons,
            "baseline_comparison": self.baseline_comparison,
            "baseline_metrics": self.baseline_metrics,
            "cpcv": self.cpcv.to_dict() if self.cpcv else None,
            "folds": [
                {
                    "fold": f.fold_index,
                    "train_period": f"{f.train_start} ~ {f.train_end}",
                    "test_period": f"{f.test_start} ~ {f.test_end}",
                    "in_sample_ic": f.in_sample_ic,
                    "oos_ic": f.oos_ic,
                    "oos_buy_hit_rate": f.oos_buy_hit_rate,
                    "oos_sell_hit_rate": f.oos_sell_hit_rate,
                    "oos_buy_count": f.oos_buy_count,
                    "oos_sell_count": f.oos_sell_count,
                }
                for f in self.folds
            ],
            "warnings": self.warnings,
            "disclaimer": (
                "Walk-Forward 验证将数据分为训练期和测试期，"
                "测试期的指标为真正的样本外表现。"
                "IC 衰减率 < 0.5 表示严重过拟合，建议不信任引擎信号。"
            ),
        }


def _path_metrics_from_returns(returns: np.ndarray) -> dict[str, float]:
    if returns.size == 0:
        return {
            "total_return": 0.0,
            "sharpe": 0.0,
            "max_drawdown": 0.0,
        }

    equity = np.cumprod(1.0 + returns)
    total_return = float(equity[-1] - 1.0)
    running_max = np.maximum.accumulate(equity)
    drawdowns = (equity - running_max) / np.where(running_max == 0, 1.0, running_max)
    max_drawdown = float(np.min(drawdowns)) if drawdowns.size else 0.0
    sharpe = 0.0
    std = float(np.std(returns, ddof=1)) if returns.size > 1 else 0.0
    if std > 0:
        sharpe = float(np.mean(returns) / std * np.sqrt(252))
    return {
        "total_return": round(total_return, 6),
        "sharpe": round(sharpe, 6),
        "max_drawdown": round(max_drawdown, 6),
    }



def _build_baseline_metrics(
    nav_records: list[tuple[str, float]],
    *,
    rebalance_freq: int = 5,
) -> dict[str, dict[str, float]]:
    if len(nav_records) < 30:
        return {}

    navs = np.array([float(item[1]) for item in nav_records], dtype=np.float64)
    if navs.size < 30 or np.any(navs <= 0):
        return {}

    daily_returns = (navs[1:] / navs[:-1]) - 1.0
    baselines: dict[str, dict[str, float]] = {}
    baselines["dca"] = _path_metrics_from_returns(daily_returns)

    if daily_returns.size >= 20:
        target_daily_vol = 0.12 / np.sqrt(252)
        rp_returns: list[float] = []
        for idx in range(20, daily_returns.size):
            trailing = daily_returns[idx - 20:idx]
            trailing_vol = float(np.std(trailing, ddof=1)) if trailing.size > 1 else 0.0
            weight = 1.0 if trailing_vol <= 0 else min(1.0, max(0.0, target_daily_vol / trailing_vol))
            rp_returns.append(float(daily_returns[idx] * weight))
        if rp_returns:
            baselines["risk_parity"] = _path_metrics_from_returns(np.array(rp_returns, dtype=np.float64))

    momentum_returns: list[float] = []
    cooldown = max(int(rebalance_freq), 1)
    hold_days = 0
    for idx in range(20, daily_returns.size):
        if hold_days > 0:
            hold_days -= 1
            momentum_returns.append(0.0)
            continue
        lookback_return = float(navs[idx] / navs[idx - 20] - 1.0)
        invested = lookback_return > 0
        momentum_returns.append(float(daily_returns[idx] if invested else 0.0))
        if not invested:
            hold_days = max(cooldown - 1, 0)
    if momentum_returns:
        baselines["simple_momentum"] = _path_metrics_from_returns(np.array(momentum_returns, dtype=np.float64))

    return baselines



def _advisor_cpcv_diagnostics(
    nav_records: list[tuple[str, float]],
    *,
    n_splits: int = 6,
    n_test_splits: int = 2,
    max_paths: int = 15,
) -> AdvisorCPCVDiagnostics:
    """基于净值收益率的轻量 CPCV/PBO 诊断。

    这里不重新优化 Advisor 参数，而是用历史净值在组合切分下评估 IS/OOS 风险收益
    稳定性，作为 Walk-Forward IC 衰减之外的直接 PBO 证据。
    """
    diagnostics = AdvisorCPCVDiagnostics(
        n_splits=n_splits,
        n_test_splits=n_test_splits,
    )
    if len(nav_records) < n_splits * 30:
        diagnostics.warnings.append("净值样本不足，未运行 CPCV/PBO 诊断")
        return diagnostics

    try:
        from app.domain.backtest.cpcv import CPCVConfig, run_cpcv
    except Exception as exc:
        diagnostics.warnings.append(f"CPCV/PBO 模块不可用：{exc}")
        return diagnostics

    dates = [date.fromisoformat(str(item[0])) for item in nav_records]
    nav_by_date = {date.fromisoformat(str(d)): float(nav) for d, nav in nav_records}

    def _range_returns(ranges: list[tuple[date, date]]) -> list[float]:
        values: list[float] = []
        for start, end in ranges:
            range_dates = [d for d in dates if start <= d <= end]
            for prev, curr in zip(range_dates, range_dates[1:]):
                prev_nav = nav_by_date.get(prev)
                curr_nav = nav_by_date.get(curr)
                if prev_nav and curr_nav and prev_nav > 0:
                    values.append(float(curr_nav / prev_nav - 1))
        return values

    def _sharpe(returns: list[float]) -> float:
        if len(returns) < 2:
            return 0.0
        arr = np.array(returns, dtype=np.float64)
        std = float(np.std(arr, ddof=1))
        if std <= 0:
            return 0.0
        return float(np.mean(arr) / std * np.sqrt(252))

    def _total_return(returns: list[float]) -> float:
        if not returns:
            return 0.0
        return float(np.prod(1 + np.array(returns, dtype=np.float64)) - 1)

    def _backtest_fn(train_ranges: list[tuple[date, date]], test_ranges: list[tuple[date, date]]):
        train_returns = _range_returns(train_ranges)
        test_returns = _range_returns(test_ranges)
        return (
            _sharpe(train_returns),
            _sharpe(test_returns),
            _total_return(train_returns),
            _total_return(test_returns),
        )

    try:
        result = run_cpcv(
            all_dates=dates,
            backtest_fn=_backtest_fn,
            config=CPCVConfig(n_splits=n_splits, n_test_splits=n_test_splits, embargo_days=5),
            max_paths=max_paths,
        )
    except ValueError as exc:
        diagnostics.warnings.append(str(exc))
        return diagnostics
    except Exception as exc:
        diagnostics.warnings.append(f"CPCV/PBO 诊断失败：{exc}")
        return diagnostics

    diagnostics.pbo = round(float(result.pbo), 4)
    diagnostics.avg_oos_sharpe = round(float(result.avg_oos_sharpe), 4)
    diagnostics.std_oos_sharpe = round(float(result.std_oos_sharpe), 4)
    diagnostics.avg_is_sharpe = round(float(result.avg_is_sharpe), 4)
    diagnostics.n_paths = int(result.n_paths)
    diagnostics.n_splits = int(result.config.n_splits)
    diagnostics.n_test_splits = int(result.config.n_test_splits)
    diagnostics.is_overfit = bool(result.is_overfit)
    if diagnostics.pbo is not None and diagnostics.pbo >= 0.5:
        diagnostics.warnings.append(f"CPCV/PBO={diagnostics.pbo:.0%}，提示过拟合概率偏高")
    return diagnostics


def run_walk_forward_validation(
    fund_code: str,
    nav_records: list[tuple[str, float]],
    fund_name: str | None = None,
    fund_type: str | None = None,
    sub_type: str | None = None,
    config: AdvisorConfig | None = None,
    n_folds: int = 5,
    train_ratio: float = 0.7,
    rebalance_freq: int = 5,
) -> WalkForwardResult:
    """Walk-Forward 样本外验证。

    方法（Anchored Walk-Forward）：
    - 将数据按时间分为 n_folds 个折叠
    - 每个折叠：前 train_ratio 为训练期，后 (1-train_ratio) 为测试期
    - 训练期起点固定（anchored），测试期逐步向前滚动
    - 训练期用于计算样本内 IC，测试期为真正的样本外验证
    - 引擎在测试期只能看到测试日及之前的数据（无前视偏差）

    与普通回测的区别：
    - 普通回测：整段数据既是训练集又是测试集（样本内）
    - Walk-Forward：严格分离训练期和测试期，测试期指标为 OOS

    Args:
        fund_code: 基金代码
        nav_records: 净值序列 [(date_str, nav), ...]
        fund_name: 基金名称
        fund_type: 基金类型
        sub_type: 子类型
        config: 引擎配置
        n_folds: 折叠数（默认5）
        train_ratio: 训练期占比（默认0.7）
        rebalance_freq: 调仓频率

    Returns:
        WalkForwardResult
    """
    if not config:
        config = AdvisorConfig()

    result = WalkForwardResult(
        fund_code=fund_code,
        fund_name=fund_name,
    )

    n = len(nav_records)
    min_required = 400  # 至少需要 400 天数据

    if n < min_required:
        result.warnings.append(
            f"数据不足：Walk-Forward 需要至少 {min_required} 天，实际 {n} 天"
        )
        return result

    dates = [r[0] for r in nav_records]
    navs = np.array([r[1] for r in nav_records], dtype=np.float64)

    # 计算折叠边界
    # 使用 expanding window：训练期从头开始，测试期逐步向后
    usable_start = 250  # 引擎需要至少 250 天历史数据
    usable_length = n - usable_start
    test_window = usable_length // (n_folds + 1)  # 每个测试窗口的大小

    if test_window < 40:
        result.warnings.append("数据不足以支持有效的 walk-forward 分割")
        return result

    result.train_window_days = usable_start + test_window * int(n_folds * train_ratio)
    result.test_window_days = test_window
    result.n_folds = n_folds

    all_oos_ics: list[float] = []
    all_is_ics: list[float] = []
    all_oos_buy_hits: list[bool] = []
    all_oos_sell_hits: list[bool] = []
    total_oos_buy = 0
    total_oos_sell = 0

    for fold_idx in range(n_folds):
        # 测试期边界
        test_end_idx = n - 1 - (n_folds - 1 - fold_idx) * test_window
        test_start_idx = test_end_idx - test_window + 1

        if test_start_idx < usable_start + 60:
            continue

        # 训练期：从 usable_start 到 test_start_idx - 1（不含测试期）
        # 加入 gap（20天）避免信息泄露
        gap = 20
        train_end_idx = test_start_idx - gap - 1

        if train_end_idx < usable_start + 60:
            continue

        fold = WalkForwardFold(
            fold_index=fold_idx + 1,
            train_start=dates[usable_start],
            train_end=dates[train_end_idx],
            test_start=dates[test_start_idx],
            test_end=dates[min(test_end_idx, n - 1)],
        )

        # --- 训练期：计算样本内 IC ---
        is_scores: list[float] = []
        is_returns: list[float] = []

        last_advices_is: dict[str, dict[str, str]] = {}
        for i in range(usable_start, train_end_idx + 1, rebalance_freq):
            current_date = dates[i]
            advice = _generate_live_advice_for_history(
                fund_code=fund_code,
                nav_records=nav_records[:i + 1],
                as_of_date=date.fromisoformat(current_date),
                total_capital=100000.0,
                config=config,
                fund_name=fund_name,
                fund_type=fund_type,
                sub_type=sub_type,
                last_advices=last_advices_is,
            )
            composite = advice.composite_score
            last_advices_is[fund_code] = {"action": advice.action, "date": current_date}
            # 前瞻 20 日收益（训练期内允许）
            if i + 20 <= train_end_idx:
                future_ret = float((navs[i + 20] / navs[i]) - 1)
                is_scores.append(composite)
                is_returns.append(future_ret)

        if len(is_scores) >= 15:
            from scipy.stats import spearmanr
            s_arr = np.array(is_scores)
            r_arr = np.array(is_returns)
            if np.std(s_arr) > 0 and np.std(r_arr) > 0:
                ic_val, _ = spearmanr(s_arr, r_arr)
                fold.in_sample_ic = round(float(ic_val), 4) if not np.isnan(ic_val) else None

        # 训练期命中率
        is_buy_hits = [
            r > 0 for s, r in zip(is_scores, is_returns)
            if s > config.buy_threshold
        ]
        is_sell_hits = [
            r < 0 for s, r in zip(is_scores, is_returns)
            if s < config.sell_threshold
        ]
        if is_buy_hits:
            fold.in_sample_buy_hit_rate = round(sum(is_buy_hits) / len(is_buy_hits), 4)
        if is_sell_hits:
            fold.in_sample_sell_hit_rate = round(sum(is_sell_hits) / len(is_sell_hits), 4)

        # --- 测试期：计算样本外 IC ---
        oos_scores: list[float] = []
        oos_returns: list[float] = []
        oos_buy_hits: list[bool] = []
        oos_sell_hits: list[bool] = []
        oos_buy_returns: list[float] = []
        oos_sell_returns: list[float] = []
        last_advices_oos: dict[str, dict[str, str]] = {}

        for i in range(test_start_idx, min(test_end_idx + 1, n - 20), rebalance_freq):
            # 引擎只能看到 i 及之前的数据（严格无前视）
            current_date = dates[i]
            advice = _generate_live_advice_for_history(
                fund_code=fund_code,
                nav_records=nav_records[:i + 1],
                as_of_date=date.fromisoformat(current_date),
                total_capital=100000.0,
                config=config,
                fund_name=fund_name,
                fund_type=fund_type,
                sub_type=sub_type,
                last_advices=last_advices_oos,
            )
            composite = advice.composite_score
            last_advices_oos[fund_code] = {"action": advice.action, "date": current_date}

            # 前瞻 20 日收益（用于验证，引擎看不到）
            if i + 20 < n:
                future_ret = float((navs[i + 20] / navs[i]) - 1)
                oos_scores.append(composite)
                oos_returns.append(future_ret)

                if advice.action == "buy":
                    oos_buy_hits.append(future_ret > 0)
                    oos_buy_returns.append(future_ret)
                elif advice.action == "sell":
                    oos_sell_hits.append(future_ret < 0)
                    oos_sell_returns.append(future_ret)

        # OOS IC
        if len(oos_scores) >= 10:
            from scipy.stats import spearmanr
            s_arr = np.array(oos_scores)
            r_arr = np.array(oos_returns)
            if np.std(s_arr) > 0 and np.std(r_arr) > 0:
                ic_val, _ = spearmanr(s_arr, r_arr)
                fold.oos_ic = round(float(ic_val), 4) if not np.isnan(ic_val) else None

        # OOS 命中率
        fold.oos_buy_count = len(oos_buy_hits)
        fold.oos_sell_count = len(oos_sell_hits)
        if oos_buy_hits:
            fold.oos_buy_hit_rate = round(sum(oos_buy_hits) / len(oos_buy_hits), 4)
        if oos_sell_hits:
            fold.oos_sell_hit_rate = round(sum(oos_sell_hits) / len(oos_sell_hits), 4)
        if oos_buy_returns:
            fold.oos_avg_return_buy = round(float(np.mean(oos_buy_returns)), 6)
        if oos_sell_returns:
            fold.oos_avg_return_sell = round(float(np.mean(oos_sell_returns)), 6)

        result.folds.append(fold)

        # 汇总
        if fold.oos_ic is not None:
            all_oos_ics.append(fold.oos_ic)
        if fold.in_sample_ic is not None:
            all_is_ics.append(fold.in_sample_ic)
        all_oos_buy_hits.extend(oos_buy_hits)
        all_oos_sell_hits.extend(oos_sell_hits)
        total_oos_buy += fold.oos_buy_count
        total_oos_sell += fold.oos_sell_count

    # 汇总统计
    result.total_oos_signals = total_oos_buy + total_oos_sell
    result.total_oos_buy = total_oos_buy
    result.total_oos_sell = total_oos_sell

    if all_oos_ics:
        result.avg_oos_ic = round(float(np.mean(all_oos_ics)), 4)
    if all_is_ics:
        result.avg_is_ic = round(float(np.mean(all_is_ics)), 4)
    if result.avg_oos_ic is not None and result.avg_is_ic is not None and result.avg_is_ic > 0:
        result.ic_degradation = round(result.avg_oos_ic / result.avg_is_ic, 4)
    if all_oos_buy_hits:
        result.avg_oos_buy_hit_rate = round(
            sum(all_oos_buy_hits) / len(all_oos_buy_hits), 4
        )
    if all_oos_sell_hits:
        result.avg_oos_sell_hit_rate = round(
            sum(all_oos_sell_hits) / len(all_oos_sell_hits), 4
        )

    result.cpcv = _advisor_cpcv_diagnostics(nav_records)
    result.warnings.extend(result.cpcv.warnings)

    try:
        from app.services.optimization import compare_against_baselines, compute_multi_objective_score
        hit_candidates = [
            value for value in [result.avg_oos_buy_hit_rate, result.avg_oos_sell_hit_rate]
            if value is not None
        ]
        candidate_metrics = {
            "avg_oos_ic": result.avg_oos_ic,
            "oos_hit_rate": (float(np.mean(hit_candidates)) if hit_candidates else None),
            "avg_oos_sharpe": result.cpcv.avg_oos_sharpe if result.cpcv else None,
            "pbo": result.cpcv.pbo if result.cpcv else None,
            "ic_degradation": result.ic_degradation,
            "total_oos_signals": result.total_oos_signals,
            "sample_count": result.total_oos_signals or len(nav_records),
        }
        multi_score = compute_multi_objective_score(candidate_metrics)
        result.multi_objective_score = round(float(multi_score.score), 6)
        result.multi_objective_components = multi_score.components
        result.multi_objective_eliminated = multi_score.eliminated
        result.multi_objective_reasons = multi_score.reasons
        if multi_score.eliminated:
            result.warnings.append("多目标评分淘汰：" + "；".join(multi_score.reasons[:3]))

        baseline_metrics = _build_baseline_metrics(nav_records, rebalance_freq=rebalance_freq)
        result.baseline_metrics = baseline_metrics
        if baseline_metrics:
            baseline_result = compare_against_baselines(
                {**candidate_metrics, **multi_score.to_metrics()},
                baseline_metrics=baseline_metrics,
            )
            result.baseline_adjusted_score = round(float(baseline_result.adjusted_score), 6)
            result.baseline_comparison = baseline_result.comparisons
            result.baseline_passed = baseline_result.passed
            result.baseline_reasons = baseline_result.reasons
            result.baseline_best = baseline_result.best_baseline.to_dict() if baseline_result.best_baseline else None
            if not baseline_result.passed:
                result.warnings.append("baseline 对照未通过：" + "；".join(baseline_result.reasons[:3]))
    except Exception:
        pass

    # 警告
    if result.ic_degradation is not None:
        if result.ic_degradation < 0.3:
            result.warnings.append(
                f"严重过拟合：OOS IC 仅为样本内的 {result.ic_degradation*100:.0f}%，"
                f"引擎信号在未见数据上几乎无效"
            )
        elif result.ic_degradation < 0.5:
            result.warnings.append(
                f"明显过拟合：OOS IC 为样本内的 {result.ic_degradation*100:.0f}%，"
                f"信号可靠性大幅下降"
            )
        elif result.ic_degradation < 0.7:
            result.warnings.append(
                f"轻度过拟合：OOS IC 为样本内的 {result.ic_degradation*100:.0f}%，"
                f"信号有一定泛化能力但需谨慎"
            )
        else:
            result.warnings.append(
                f"泛化良好：OOS IC 为样本内的 {result.ic_degradation*100:.0f}%"
            )

    if result.avg_oos_ic is not None and result.avg_oos_ic < 0.02:
        result.warnings.append(
            "样本外 IC < 0.02，引擎在未见数据上无显著预测力"
        )

    if total_oos_buy + total_oos_sell < 20:
        result.warnings.append("样本外信号总数不足（<20），统计结论不可靠")

    return result


async def load_and_run_walk_forward(
    fund_code: str,
    session: Any,
    lookback_days: int | None = None,
    n_folds: int = 5,
    rebalance_freq: int = 5,
    config: AdvisorConfig | None = None,
    risk_level: str = "moderate",
) -> WalkForwardResult:
    """从数据库加载数据并运行 Walk-Forward 验证。

    Args:
        fund_code: 基金代码
        session: AsyncSession
        lookback_days: 加载多少天的历史数据。None 表示使用全部可用数据（推荐）。
        n_folds: 折叠数
        rebalance_freq: 调仓频率
        config: 引擎配置
    """
    from sqlalchemy import text

    from app.services.trading_advisor import load_fund_names, load_fund_types

    latest_query = text(
        "SELECT MAX(trade_date) FROM fund_nav WHERE fund_code = :code "
        "AND (adj_nav IS NOT NULL OR unit_nav IS NOT NULL)"
    )
    latest_result = await session.execute(latest_query, {"code": fund_code})
    latest_trade_date = latest_result.scalar_one_or_none()
    end_date = latest_trade_date or date.today()

    if lookback_days:
        start_date = end_date - timedelta(days=lookback_days)
        query = text(
            "SELECT trade_date, COALESCE(adj_nav, unit_nav) as nav FROM fund_nav "
            "WHERE fund_code = :code "
            "AND trade_date BETWEEN :start AND :end "
            "AND (adj_nav IS NOT NULL OR unit_nav IS NOT NULL) "
            "ORDER BY trade_date"
        )
        result = await session.execute(
            query, {"code": fund_code, "start": start_date, "end": end_date}
        )
    else:
        # 使用全部可用数据（不限制起始日期）
        query = text(
            "SELECT trade_date, COALESCE(adj_nav, unit_nav) as nav FROM fund_nav "
            "WHERE fund_code = :code "
            "AND (adj_nav IS NOT NULL OR unit_nav IS NOT NULL) "
            "ORDER BY trade_date"
        )
        result = await session.execute(query, {"code": fund_code})

    nav_records = [(str(row[0]), float(row[1])) for row in result]

    names = await load_fund_names([fund_code], session)
    types = await load_fund_types([fund_code], session)
    fund_name = names.get(fund_code)
    fund_type, sub_type = types.get(fund_code, (None, None))

    wf_result = run_walk_forward_validation(
        fund_code=fund_code,
        nav_records=nav_records,
        fund_name=fund_name,
        fund_type=fund_type,
        sub_type=sub_type,
        config=config,
        n_folds=n_folds,
        rebalance_freq=rebalance_freq,
    )

    # 填充数据信息
    wf_result.requested_days = lookback_days
    wf_result.actual_trading_days = len(nav_records)
    if nav_records:
        wf_result.data_start_date = nav_records[0][0]
        wf_result.data_end_date = nav_records[-1][0]

    # 如果请求天数超过实际数据，添加提示
    if lookback_days and len(nav_records) < lookback_days * 0.6:
        # 交易日约为日历日的 60-70%，如果实际数据远少于预期，说明超过了上市时间
        wf_result.warnings.insert(0,
            f"请求 {lookback_days} 天数据，实际仅获取 {len(nav_records)} 个交易日"
            f"（{wf_result.data_start_date} ~ {wf_result.data_end_date}），"
            f"已使用全部可用数据"
        )

    try:
        from app.services.advisor_oos import OOSValidationStore
        OOSValidationStore.save_from_walk_forward_result(
            fund_code=fund_code,
            risk_level=risk_level,
            result_dict=wf_result.to_dict(),
        )
    except Exception:
        pass

    return wf_result


__all__ = [
    "run_advisor_backtest",
    "load_and_run_advisor_backtest",
    "run_walk_forward_validation",
    "load_and_run_walk_forward",
    "AdvisorBacktestResult",
    "AdvisorBacktestMetrics",
    "WalkForwardResult",
]
