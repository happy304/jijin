"""双引擎一致性校验测试。

用相同策略与数据分别运行事件驱动引擎（EventDrivenEngine）和向量化引擎（VectorBacktest），
断言最终净值差异 < 0.5%。

覆盖 3 种典型策略：
1. 买入持有（Buy and Hold）
2. 简单轮动（Simple Rotation）
3. 定投（DCA - Dollar Cost Averaging）

设计要点：
- 为公平比较，事件驱动引擎使用零费率，向量化引擎使用 cost_bps=0
- 两个引擎的差异主要来自 T+1 结算延迟（事件驱动引擎在 T+1 确认，向量化引擎用 shift(1) 模拟）
- 使用合成 NAV 数据，确保测试可复现
- 策略设计尽量减少调仓频率，使 T+1 延迟影响最小化

需求: 4.13
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from app.domain.backtest.calendar import trading_days
from app.domain.backtest.engine_event import (
    BarContext,
    EventDrivenEngine,
    FundMeta,
)
from app.domain.backtest.engine_vector import VectorBacktest
from app.domain.backtest.order import OrderIntent


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

INITIAL_CAPITAL = Decimal("1000000")
INITIAL_CAPITAL_FLOAT = 1_000_000.0
TOLERANCE = 0.005  # 0.5% 最终净值差异容忍度

# 回测区间
START_DATE = date(2024, 1, 2)
END_DATE = date(2024, 6, 28)

# 基金代码
FUND_A = "TEST_A"
FUND_B = "TEST_B"
FUND_C = "TEST_C"
FUNDS = [FUND_A, FUND_B, FUND_C]


# ---------------------------------------------------------------------------
# 合成数据生成
# ---------------------------------------------------------------------------


def _generate_synthetic_nav(
    fund_codes: list[str],
    start: date,
    end: date,
    seed: int = 42,
) -> dict[str, dict[date, Decimal]]:
    """生成合成 NAV 数据。

    使用几何布朗运动模拟基金净值走势，确保数据合理且可复现。
    使用较低波动率以减少 T+1 延迟带来的差异。

    Returns:
        {fund_code: {date: nav}} 格式的净值数据
    """
    rng = np.random.default_rng(seed)
    trade_dates = trading_days(start, end)
    n_days = len(trade_dates)

    nav_data: dict[str, dict[date, Decimal]] = {}

    # 使用较低波动率，减少 T+1 延迟对结果的影响
    params = {
        FUND_A: {"drift": 0.0003, "vol": 0.008, "start_nav": 1.5},
        FUND_B: {"drift": 0.0005, "vol": 0.010, "start_nav": 2.0},
        FUND_C: {"drift": 0.0002, "vol": 0.006, "start_nav": 1.0},
    }

    for code in fund_codes:
        p = params[code]
        navs: dict[date, Decimal] = {}
        current_nav = p["start_nav"]

        for i, d in enumerate(trade_dates):
            if i == 0:
                navs[d] = Decimal(str(round(current_nav, 4)))
            else:
                ret = p["drift"] + p["vol"] * rng.standard_normal()
                current_nav *= (1 + ret)
                current_nav = max(current_nav, 0.01)
                navs[d] = Decimal(str(round(current_nav, 4)))

        nav_data[code] = navs

    return nav_data


def _nav_to_returns(nav_data: dict[str, dict[date, Decimal]]) -> pd.DataFrame:
    """将 NAV 数据转换为收益率 DataFrame（向量化引擎使用）。

    Returns:
        pd.DataFrame，index=日期，columns=基金代码，值为日收益率
    """
    all_dates = sorted(
        set().union(*(navs.keys() for navs in nav_data.values()))
    )

    nav_df = pd.DataFrame(index=all_dates, columns=list(nav_data.keys()), dtype=float)
    for code, navs in nav_data.items():
        for d, nav in navs.items():
            nav_df.loc[d, code] = float(nav)

    nav_df = nav_df.astype(float)

    # 计算日收益率
    returns_df = nav_df.pct_change()
    returns_df.iloc[0] = 0.0

    return returns_df


# ---------------------------------------------------------------------------
# 策略实现（事件驱动引擎版本）
# ---------------------------------------------------------------------------


class BuyAndHoldEventStrategy:
    """买入持有策略（事件驱动版）：第一天全仓买入 FUND_A，之后不操作。"""

    def __init__(self) -> None:
        self._bought = False

    def on_bar(self, context: BarContext) -> list[OrderIntent]:
        if not self._bought:
            self._bought = True
            return [
                OrderIntent(
                    fund_code=FUND_A,
                    direction="subscribe",
                    amount=context.cash,
                )
            ]
        return []


class SimpleRotationEventStrategy:
    """简单轮动策略（事件驱动版）。

    在回测中间点（第 60 个交易日）从 FUND_A 切换到 FUND_B。
    只调仓一次，最大限度减少 T+1 延迟的影响。
    """

    def __init__(self) -> None:
        self._day_count = 0
        self._phase = "init"  # init -> holding_a -> switching -> holding_b

    def on_bar(self, context: BarContext) -> list[OrderIntent]:
        self._day_count += 1
        orders: list[OrderIntent] = []

        if self._phase == "init":
            # 第一天全仓买入 FUND_A
            self._phase = "holding_a"
            return [
                OrderIntent(
                    fund_code=FUND_A,
                    direction="subscribe",
                    amount=context.cash,
                )
            ]

        if self._phase == "holding_a" and self._day_count == 60:
            # 第 60 天赎回 FUND_A
            shares_a = context.positions.get(FUND_A, Decimal("0"))
            if shares_a > 0:
                orders.append(
                    OrderIntent(
                        fund_code=FUND_A,
                        direction="redeem",
                        shares=shares_a,
                    )
                )
            self._phase = "switching"
            return orders

        if self._phase == "switching" and context.cash > Decimal("100"):
            # 赎回到账后买入 FUND_B
            orders.append(
                OrderIntent(
                    fund_code=FUND_B,
                    direction="subscribe",
                    amount=context.cash,
                )
            )
            self._phase = "holding_b"
            return orders

        return []


class DCAEventStrategy:
    """定投策略（事件驱动版）。

    第一天投入全部资金的一半，之后每 20 个交易日再投入剩余资金的一部分。
    总共投入 5 次，每次投入初始资金的 20%。
    """

    def __init__(self) -> None:
        self._day_count = 0
        self._invest_count = 0
        self._invest_amount = Decimal("200000")  # 每次投入 20 万
        self._max_invests = 5

    def on_bar(self, context: BarContext) -> list[OrderIntent]:
        self._day_count += 1

        # 第 1, 21, 41, 61, 81 天定投
        if self._day_count % 20 == 1 and self._invest_count < self._max_invests:
            if context.cash >= self._invest_amount:
                self._invest_count += 1
                return [
                    OrderIntent(
                        fund_code=FUND_A,
                        direction="subscribe",
                        amount=self._invest_amount,
                    )
                ]

        return []


# ---------------------------------------------------------------------------
# 向量化引擎信号生成
# ---------------------------------------------------------------------------


def _buy_and_hold_signals(trade_dates: list[date]) -> pd.DataFrame:
    """买入持有策略的信号矩阵：全仓 FUND_A。"""
    signals = pd.DataFrame(0.0, index=trade_dates, columns=FUNDS)
    signals[FUND_A] = 1.0
    return signals


def _simple_rotation_signals(trade_dates: list[date]) -> pd.DataFrame:
    """简单轮动策略的信号矩阵。

    模拟事件驱动引擎中的轮动时序：
    - 事件引擎 index 59 (day 60): 赎回 FUND_A 订单
    - 事件引擎 index 60 (day 61): 赎回确认，现金到账，同日发出申购 FUND_B
    - 事件引擎 index 61 (day 62): 申购 FUND_B 确认（T+1 跨清明假期）

    向量化引擎中 shift(1) 意味着 signal[i] 在 index i+1 生效：
    - signal[0:59] = FUND_A → 生效于 index 1-59（持有 FUND_A）
    - signal[59] = 0（现金）→ 生效于 index 60（现金状态）
    - signal[60:] = FUND_B → 生效于 index 61+（持有 FUND_B）
    """
    signals = pd.DataFrame(0.0, index=trade_dates, columns=FUNDS)

    # 事件引擎权益快照：
    # index 0: 全部现金（subscribe 尚未确认）
    # index 1-59: 持有 FUND_A（subscribe 在 index 1 确认）
    # index 60: 现金（redeem 在 index 60 确认，同日发出 subscribe FUND_B）
    # index 61+: 持有 FUND_B（subscribe 在 index 61 确认）
    #
    # 向量化引擎 shift(1): signal[i] 在 i+1 生效
    # signal[0:59] = FUND_A → 生效 index 1-59 ✓
    # signal[59] = 0 → 生效 index 60 ✓（现金）
    # signal[60:] = FUND_B → 生效 index 61+ ✓

    for i in range(len(trade_dates)):
        if i < 59:
            signals.iloc[i, signals.columns.get_loc(FUND_A)] = 1.0
        elif i == 59:
            pass  # 现金（权重为 0）
        else:
            signals.iloc[i, signals.columns.get_loc(FUND_B)] = 1.0

    return signals


def _dca_signals(trade_dates: list[date]) -> pd.DataFrame:
    """定投策略的信号矩阵。

    模拟每 20 天投入 20% 资金的效果。
    向量化引擎中，权重代表已投入资金占总资金的比例。
    每次定投后，权重增加 20%，最终达到 100%。
    """
    n_days = len(trade_dates)
    signals = pd.DataFrame(0.0, index=trade_dates, columns=FUNDS)

    # 定投时间点：第 1, 21, 41, 61, 81 天
    invest_days = [i for i in range(n_days) if i % 20 == 0]
    max_invests = 5

    cumulative_weight = 0.0
    invest_count = 0

    for i in range(n_days):
        if i in invest_days and invest_count < max_invests:
            invest_count += 1
            cumulative_weight = invest_count / max_invests
        signals.iloc[i, signals.columns.get_loc(FUND_A)] = cumulative_weight

    return signals


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _run_event_engine(
    strategy,
    nav_data: dict[str, dict[date, Decimal]],
) -> Decimal:
    """运行事件驱动引擎，返回最终权益。"""
    # 该一致性测试用于对齐不建模赎回款 T+N 到账的向量化引擎，
    # 因此关闭事件引擎的真实赎回到账延迟。
    engine = EventDrivenEngine(redeem_cash_delay=False)

    # 使用零费率的 FundMeta
    fund_meta = {
        code: FundMeta(
            code=code,
            fund_type="stock",
            subscribe_fee_tiers=[],
            redeem_fee_tiers=[],
        )
        for code in FUNDS
    }

    result = engine.run(
        start=START_DATE,
        end=END_DATE,
        strategy=strategy,
        nav_data=nav_data,
        initial_capital=INITIAL_CAPITAL,
        fund_meta=fund_meta,
    )

    if result.equity_curve:
        return result.equity_curve[-1].equity
    return INITIAL_CAPITAL


def _run_vector_engine(
    signals: pd.DataFrame,
    returns: pd.DataFrame,
) -> float:
    """运行向量化引擎，返回最终权益。"""
    engine = VectorBacktest(
        initial_capital=INITIAL_CAPITAL_FLOAT,
        cost_bps=0.0,  # 零成本，与事件驱动引擎对齐
    )
    result = engine.run(signals, returns)
    return float(result.equity.iloc[-1])


def _relative_diff(a: float, b: float) -> float:
    """计算两个值的相对差异。"""
    if b == 0:
        return float("inf") if a != 0 else 0.0
    return abs(a - b) / abs(b)


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------


@pytest.fixture
def nav_data() -> dict[str, dict[date, Decimal]]:
    """生成合成 NAV 数据。"""
    return _generate_synthetic_nav(FUNDS, START_DATE, END_DATE)


@pytest.fixture
def returns_df(nav_data: dict[str, dict[date, Decimal]]) -> pd.DataFrame:
    """从 NAV 数据生成收益率 DataFrame。"""
    return _nav_to_returns(nav_data)


@pytest.fixture
def trade_dates_list() -> list[date]:
    """获取回测期间的交易日列表。"""
    return trading_days(START_DATE, END_DATE)


class TestBuyAndHoldConsistency:
    """买入持有策略一致性测试。

    最简单的策略：第一天全仓买入，之后不操作。
    两个引擎的差异应该非常小（仅来自 T+1 确认延迟的第一天）。
    """

    def test_final_equity_within_tolerance(
        self,
        nav_data: dict[str, dict[date, Decimal]],
        returns_df: pd.DataFrame,
        trade_dates_list: list[date],
    ) -> None:
        """买入持有策略：双引擎最终净值差异 < 0.5%。"""
        # 事件驱动引擎
        strategy = BuyAndHoldEventStrategy()
        event_equity = float(_run_event_engine(strategy, nav_data))

        # 向量化引擎
        signals = _buy_and_hold_signals(trade_dates_list)
        vector_equity = _run_vector_engine(signals, returns_df)

        # 断言差异 < 0.5%
        diff = _relative_diff(event_equity, vector_equity)
        assert diff < TOLERANCE, (
            f"买入持有策略双引擎净值差异超过阈值: "
            f"event={event_equity:.2f}, vector={vector_equity:.2f}, "
            f"diff={diff:.4%} > {TOLERANCE:.4%}"
        )


class TestSimpleRotationConsistency:
    """简单轮动策略一致性测试。

    在回测中间点从 FUND_A 切换到 FUND_B，只调仓一次。
    由于只有一次调仓，T+1 延迟的影响被最小化。
    """

    def test_final_equity_within_tolerance(
        self,
        nav_data: dict[str, dict[date, Decimal]],
        returns_df: pd.DataFrame,
        trade_dates_list: list[date],
    ) -> None:
        """简单轮动策略：双引擎最终净值差异 < 0.5%。"""
        # 事件驱动引擎
        strategy = SimpleRotationEventStrategy()
        event_equity = float(_run_event_engine(strategy, nav_data))

        # 向量化引擎
        signals = _simple_rotation_signals(trade_dates_list)
        vector_equity = _run_vector_engine(signals, returns_df)

        # 断言差异 < 0.5%
        diff = _relative_diff(event_equity, vector_equity)
        assert diff < TOLERANCE, (
            f"简单轮动策略双引擎净值差异超过阈值: "
            f"event={event_equity:.2f}, vector={vector_equity:.2f}, "
            f"diff={diff:.4%} > {TOLERANCE:.4%}"
        )


class TestDCAConsistency:
    """定投策略一致性测试。

    每 20 个交易日定额投入，逐步建仓。
    向量化引擎通过递增权重模拟定投效果。
    """

    def test_final_equity_within_tolerance(
        self,
        nav_data: dict[str, dict[date, Decimal]],
        returns_df: pd.DataFrame,
        trade_dates_list: list[date],
    ) -> None:
        """定投策略：双引擎最终净值差异 < 0.5%。"""
        # 事件驱动引擎
        strategy = DCAEventStrategy()
        event_equity = float(_run_event_engine(strategy, nav_data))

        # 向量化引擎
        signals = _dca_signals(trade_dates_list)
        vector_equity = _run_vector_engine(signals, returns_df)

        # 断言差异 < 0.5%
        diff = _relative_diff(event_equity, vector_equity)
        assert diff < TOLERANCE, (
            f"定投策略双引擎净值差异超过阈值: "
            f"event={event_equity:.2f}, vector={vector_equity:.2f}, "
            f"diff={diff:.4%} > {TOLERANCE:.4%}"
        )
