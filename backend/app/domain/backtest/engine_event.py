"""事件驱动回测引擎核心模块。

实现 EventDrivenEngine，按交易日迭代执行以下事件循环：
1. MarketOpen - 市场开盘
2. Dividend - 处理当日分红/拆分事件
3. Confirm pending - 确认 T+1 待确认订单
4. Strategy.on_bar - 策略决策（只能看到 T-1 及之前数据）
5. Risk check - 风控检查
6. Queue orders - 订单入队
7. MarketClose - 市场收盘，更新组合市值，记录权益快照

核心设计：
- BarContext 强制只提供 T-1 及之前净值，防止未来函数
- 订单以 T 日收盘净值确认，但策略下单时无法看到 T 日净值
- 同步执行，适用于回测场景
- 详细日志 + progress 回调

需求: 4.9, 4.10, 4.11
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Callable, Protocol, Sequence

from app.domain.backtest.calendar import trading_days
from app.domain.backtest.corporate_actions import process_dividend, process_split
from app.domain.backtest.purchase_check import check_purchase_eligibility
from app.domain.backtest.events import (
    DividendEvent,
    EventBus,
    MarketCloseEvent,
    MarketOpenEvent,
)
from app.domain.backtest.fees import (
    FeeTier,
    calc_redeem_fee,
    calc_subscribe_fee,
)
from app.domain.backtest.order import Fill, Order, OrderIntent, OrderStatus
from app.domain.backtest.portfolio import Portfolio
from app.domain.backtest.settlement import get_cash_arrival_date, get_confirm_date
from app.domain.backtest.slippage import SlippageConfig, compute_slippage

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Strategy Protocol
# ---------------------------------------------------------------------------


class Strategy(Protocol):
    """策略接口协议。

    策略必须实现 on_bar 方法，接收 BarContext 返回 OrderIntent 列表。
    """

    def on_bar(self, context: "BarContext") -> list[OrderIntent]: ...


# ---------------------------------------------------------------------------
# Risk Engine Protocol
# ---------------------------------------------------------------------------


class RiskEngine(Protocol):
    """风控引擎接口协议。"""

    def validate(
        self, orders: list[OrderIntent], portfolio: Portfolio
    ) -> list[OrderIntent]: ...


# ---------------------------------------------------------------------------
# BarContext - 防未来函数的策略上下文
# ---------------------------------------------------------------------------


@dataclass
class BarContext:
    """策略决策上下文。

    强制只提供 T-1 及之前的净值数据，防止未来函数。
    策略在 T 日决策时，只能看到 T-1 日及之前的净值。

    Attributes:
        current_date: 当前交易日（T 日）
        portfolio: 当前组合快照（只读视图）
        nav_history: 全量净值数据 {fund_code: {date: nav}}
        _cutoff_date: 数据截止日期（T-1 日），策略只能看到此日期及之前的数据
    """

    current_date: date
    portfolio: Portfolio
    nav_history: dict[str, dict[date, Decimal]]
    _cutoff_date: date

    def nav(self, fund_code: str, query_date: date | None = None) -> Decimal | None:
        """获取指定基金在指定日期的净值。

        如果未指定日期，返回截止日期（T-1）的净值。
        只能查询 _cutoff_date 及之前的数据。

        Args:
            fund_code: 基金代码
            query_date: 查询日期，默认为 T-1

        Returns:
            净值，如果数据不存在返回 None

        Raises:
            LookaheadError: 如果试图查询未来数据（T 日及之后）
        """
        if query_date is None:
            query_date = self._cutoff_date

        if query_date > self._cutoff_date:
            raise LookaheadError(
                f"Cannot access NAV for {fund_code} on {query_date}: "
                f"current date is {self.current_date}, "
                f"data cutoff is {self._cutoff_date}"
            )

        fund_navs = self.nav_history.get(fund_code)
        if fund_navs is None:
            return None
        return fund_navs.get(query_date)

    def nav_series(self, fund_code: str) -> dict[date, Decimal]:
        """获取指定基金截止到 T-1 的全部净值序列。

        Args:
            fund_code: 基金代码

        Returns:
            {date: nav} 字典，只包含 _cutoff_date 及之前的数据
        """
        fund_navs = self.nav_history.get(fund_code, {})
        return {d: v for d, v in fund_navs.items() if d <= self._cutoff_date}

    @property
    def cash(self) -> Decimal:
        """当前可用现金。"""
        return self.portfolio.cash

    @property
    def positions(self) -> dict[str, Decimal]:
        """当前持仓。"""
        return dict(self.portfolio.positions)

    @property
    def date(self) -> date:
        """当前交易日。"""
        return self.current_date


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class LookaheadError(Exception):
    """未来函数错误：策略试图访问未来数据。"""


# ---------------------------------------------------------------------------
# Backtest Result
# ---------------------------------------------------------------------------


@dataclass
class EquitySnapshot:
    """每日权益快照。"""

    trade_date: date
    equity: Decimal
    cash: Decimal
    position_value: Decimal


@dataclass
class BacktestResult:
    """回测结果。

    Attributes:
        equity_curve: 每日权益快照列表
        trades: 成交记录列表
        final_portfolio: 最终组合状态
        start_date: 回测起始日期
        end_date: 回测结束日期
        initial_capital: 初始资金
    """

    equity_curve: list[EquitySnapshot]
    trades: list[Fill]
    final_portfolio: Portfolio
    start_date: date
    end_date: date
    initial_capital: Decimal


# ---------------------------------------------------------------------------
# Dividend Info (for engine input)
# ---------------------------------------------------------------------------


@dataclass
class DividendInfo:
    """分红/拆分信息（引擎输入数据）。

    Attributes:
        fund_code: 基金代码
        ex_date: 除权日
        dividend_per_share: 每份分红金额（分红时使用）
        split_ratio: 拆分比例（拆分时使用，默认 1 表示无拆分）
        reinvest: 是否红利再投
    """

    fund_code: str
    ex_date: date
    dividend_per_share: Decimal = Decimal("0")
    split_ratio: Decimal = Decimal("1")
    reinvest: bool = False


# ---------------------------------------------------------------------------
# Fund Metadata (for settlement rules)
# ---------------------------------------------------------------------------


@dataclass
class FundMeta:
    """基金元数据（引擎使用的最小信息集）。

    Attributes:
        code: 基金代码
        fund_type: 基金类型（stock/bond/mixed/money/qdii/index/fof）
        subscribe_fee_tiers: 申购费率阶梯
        redeem_fee_tiers: 赎回费率阶梯
        is_purchasable: 基金是否可申购（暂停申购时为 False）
        purchase_limit: 限购额度，None 表示无限制
        total_shares: 基金总份额（用于大额赎回判断），None 表示不限制
        delisting_date: 基金清盘日。如果当前回测日 >= 此日期，
            引擎会拒绝新订单，并在清盘日按当日净值强制赎回该基金的所有持仓
            （转为现金）。None 表示基金仍存续。这个字段是消除生存偏差
            的最小可行修复——确保历史回测能正确处理在样本期内被清盘的基金。
    """

    code: str
    fund_type: str = "stock"
    subscribe_fee_tiers: list[FeeTier] = field(default_factory=list)
    redeem_fee_tiers: list[FeeTier] = field(default_factory=list)
    is_purchasable: bool = True
    purchase_limit: Decimal | None = None
    total_shares: Decimal | None = None
    delisting_date: date | None = None
    # Slippage / market impact configuration. None = no slippage modelled
    # (the historical default for open-end funds at quoted NAV).
    slippage_config: SlippageConfig | None = None


# ---------------------------------------------------------------------------
# Default No-op Risk Engine
# ---------------------------------------------------------------------------


class NoOpRiskEngine:
    """默认无操作风控引擎，直接放行所有订单。"""

    def validate(
        self, orders: list[OrderIntent], portfolio: Portfolio
    ) -> list[OrderIntent]:
        return orders


# ---------------------------------------------------------------------------
# Progress callback type
# ---------------------------------------------------------------------------

ProgressCallback = Callable[[int, int, date], None]
"""进度回调函数签名: (current_day_index, total_days, current_date) -> None"""


# ---------------------------------------------------------------------------
# EventDrivenEngine
# ---------------------------------------------------------------------------


class EventDrivenEngine:
    """事件驱动回测引擎。

    按交易日迭代执行事件循环，正确模拟基金 T+1 结算、费率、分红等特性。

    用法示例::

        from datetime import date
        from decimal import Decimal

        engine = EventDrivenEngine()
        result = engine.run(
            start=date(2024, 1, 2),
            end=date(2024, 6, 28),
            strategy=my_strategy,
            nav_data={"000001": {date(2024,1,2): Decimal("1.5"), ...}},
            initial_capital=Decimal("100000"),
            fund_meta={"000001": FundMeta(code="000001", fund_type="stock", ...)},
        )
    """

    def __init__(self, redeem_cash_delay: bool = True) -> None:
        self._event_bus = EventBus()
        self._portfolio: Portfolio = Portfolio()
        self._equity_curve: list[EquitySnapshot] = []
        self._trades: list[Fill] = []
        self._order_counter: int = 0
        self._redeem_cash_delay = redeem_cash_delay

    def run(
        self,
        start: date,
        end: date,
        strategy: Strategy,
        nav_data: dict[str, dict[date, Decimal]],
        initial_capital: Decimal,
        fund_meta: dict[str, FundMeta] | None = None,
        dividends: Sequence[DividendInfo] | None = None,
        risk_engine: RiskEngine | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> BacktestResult:
        """运行事件驱动回测。

        Args:
            start: 回测起始日期
            end: 回测结束日期
            strategy: 策略实例
            nav_data: 净值数据 {fund_code: {date: nav}}
            initial_capital: 初始资金
            fund_meta: 基金元数据 {fund_code: FundMeta}，用于费率和结算规则
            dividends: 分红/拆分事件列表
            risk_engine: 风控引擎，默认为 NoOpRiskEngine
            progress_callback: 进度回调函数

        Returns:
            BacktestResult 包含权益曲线、成交记录、最终组合状态
        """
        # 初始化
        self._init(initial_capital)
        risk = risk_engine or NoOpRiskEngine()
        fund_meta = fund_meta or {}
        dividend_list = list(dividends) if dividends else []

        # 构建分红索引 {date: [DividendInfo]}
        dividend_index: dict[date, list[DividendInfo]] = {}
        for div in dividend_list:
            dividend_index.setdefault(div.ex_date, []).append(div)

        # 获取交易日列表
        trade_dates = trading_days(start, end)
        total_days = len(trade_dates)

        logger.info(
            "回测开始: start=%s, end=%s, initial_capital=%s, trading_days=%d",
            start, end, initial_capital, total_days,
        )

        for day_idx, trade_date in enumerate(trade_dates):
            self._run_single_day(
                trade_date=trade_date,
                strategy=strategy,
                nav_data=nav_data,
                fund_meta=fund_meta,
                dividend_index=dividend_index,
                risk_engine=risk,
            )

            # 进度回调
            if progress_callback is not None:
                progress_callback(day_idx + 1, total_days, trade_date)

            if (day_idx + 1) % 50 == 0:
                logger.debug(
                    "回测进度: %d/%d (%.1f%%), date=%s, equity=%s",
                    day_idx + 1, total_days,
                    (day_idx + 1) / total_days * 100,
                    trade_date,
                    self._equity_curve[-1].equity if self._equity_curve else "N/A",
                )

        logger.info(
            "回测完成: final_equity=%s, total_trades=%d",
            self._equity_curve[-1].equity if self._equity_curve else "N/A",
            len(self._trades),
        )

        return self._build_result(start, end, initial_capital)

    # -----------------------------------------------------------------------
    # 内部方法
    # -----------------------------------------------------------------------

    def _init(self, initial_capital: Decimal) -> None:
        """初始化引擎状态。"""
        self._portfolio = Portfolio(cash=initial_capital)
        self._equity_curve = []
        self._trades = []
        self._order_counter = 0
        self._event_bus.clear()

    def _run_single_day(
        self,
        trade_date: date,
        strategy: Strategy,
        nav_data: dict[str, dict[date, Decimal]],
        fund_meta: dict[str, FundMeta],
        dividend_index: dict[date, list[DividendInfo]],
        risk_engine: RiskEngine,
    ) -> None:
        """执行单个交易日的事件循环。"""

        # 1. MarketOpen
        self._event_bus.emit(
            MarketOpenEvent(timestamp=datetime(trade_date.year, trade_date.month, trade_date.day, 9, 30))
        )
        logger.debug("Day %s: MarketOpen", trade_date)

        # 2. 处理分红/拆分事件
        self._process_dividends(trade_date, nav_data, dividend_index)

        # 2.5. 赎回款到账：只有到账后的现金才可用于当日策略再申购
        self._portfolio.settle_pending_cash(trade_date)

        # 3. 确认 pending 订单（T+1 确认）
        self._confirm_pending_orders(trade_date, nav_data, fund_meta)

        # 3.5. 清盘日强制赎回（生存偏差最小修复）
        # 如果今天是某只持仓基金的清盘日，按当日净值按市值转现，避免在
        # 历史回测中假装持有一只已经停止运营的基金。
        self._handle_delisting(trade_date, nav_data, fund_meta)

        # 4. 策略 on_bar（只能看到 T-1 及之前数据）
        context = self._build_bar_context(trade_date, nav_data)
        order_intents = strategy.on_bar(context)
        logger.debug(
            "Day %s: Strategy generated %d order intents",
            trade_date, len(order_intents),
        )

        # 5. 风控检查
        validated_intents = risk_engine.validate(order_intents, self._portfolio)
        if len(validated_intents) < len(order_intents):
            logger.debug(
                "Day %s: Risk engine filtered %d -> %d orders",
                trade_date, len(order_intents), len(validated_intents),
            )

        # 5.5. 拒绝清盘后基金的新订单
        validated_intents = self._filter_delisted_orders(
            validated_intents, trade_date, fund_meta
        )

        # 6. 订单入队
        self._queue_orders(validated_intents, trade_date, fund_meta)

        # 7. MarketClose + 更新组合市值 + 记录权益快照
        self._event_bus.emit(
            MarketCloseEvent(timestamp=datetime(trade_date.year, trade_date.month, trade_date.day, 15, 0))
        )
        self._snapshot_equity(trade_date, nav_data)

        # 推进持有天数
        self._portfolio.advance_day(current_date=trade_date)

    def _process_dividends(
        self,
        trade_date: date,
        nav_data: dict[str, dict[date, Decimal]],
        dividend_index: dict[date, list[DividendInfo]],
    ) -> None:
        """处理当日分红/拆分事件。"""
        divs = dividend_index.get(trade_date, [])
        for div in divs:
            if div.fund_code not in self._portfolio.positions:
                continue

            if div.split_ratio != Decimal("1"):
                # 拆分事件
                process_split(self._portfolio, div.fund_code, div.split_ratio)
                logger.debug(
                    "Day %s: Split %s ratio=%s",
                    trade_date, div.fund_code, div.split_ratio,
                )

            if div.dividend_per_share > Decimal("0"):
                # 分红事件 - 获取除权日净值用于红利再投
                nav = self._get_nav(div.fund_code, trade_date, nav_data)
                if nav is not None:
                    process_dividend(
                        self._portfolio,
                        div.fund_code,
                        div.dividend_per_share,
                        nav,
                        reinvest=div.reinvest,
                    )
                    logger.debug(
                        "Day %s: Dividend %s per_share=%s reinvest=%s",
                        trade_date, div.fund_code, div.dividend_per_share, div.reinvest,
                    )

                    # 发出分红事件
                    self._event_bus.emit(
                        DividendEvent(
                            timestamp=datetime(trade_date.year, trade_date.month, trade_date.day, 9, 35),
                            fund_code=div.fund_code,
                            dividend_per_share=div.dividend_per_share,
                            reinvest=div.reinvest,
                        )
                    )

    def _handle_delisting(
        self,
        trade_date: date,
        nav_data: dict[str, dict[date, Decimal]],
        fund_meta: dict[str, FundMeta],
    ) -> None:
        """清盘日强制赎回（生存偏差防护）。

        如果当前交易日 == 任何持仓基金的清盘日（FundMeta.delisting_date），
        按当日净值（无可用净值时回落到最近可用净值）将该基金所有持仓
        转为现金，并从持仓中移除。

        没有 FundMeta 或没有 delisting_date 的基金不会被影响。
        无相关持仓时直接返回。

        生成的"清盘平仓"会以 Fill 形式记入 ``self._trades``，方便事后审计。
        """
        # 复制 keys，因为我们要在迭代中修改 positions
        for code in list(self._portfolio.positions.keys()):
            meta = fund_meta.get(code)
            if meta is None or meta.delisting_date is None:
                continue
            if trade_date < meta.delisting_date:
                continue

            shares = self._portfolio.positions.get(code, Decimal("0"))
            if shares <= Decimal("0"):
                continue

            nav = self._get_nav(code, trade_date, nav_data)
            if nav is None:
                # 当日无 NAV 数据，回落到最近可用 NAV
                nav = self._get_latest_nav(code, trade_date, nav_data)
            if nav <= Decimal("0"):
                logger.warning(
                    "Day %s: Cannot liquidate %s on delisting — no NAV available",
                    trade_date,
                    code,
                )
                continue

            gross_amount = (shares * nav).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            # 清盘平仓不收赎回费（清盘是基金被动行为）
            self._portfolio.cash += gross_amount
            del self._portfolio.positions[code]
            if code in self._portfolio.holding_days:
                del self._portfolio.holding_days[code]
            if code in self._portfolio.confirm_dates:
                del self._portfolio.confirm_dates[code]

            # 记录一笔"清盘"成交，trade direction 复用 redeem 语义
            self._order_counter += 1
            order_id = f"DELIST-{trade_date.isoformat()}-{self._order_counter:04d}"
            self._trades.append(
                Fill(
                    order_id=order_id,
                    fund_code=code,
                    direction="redeem",
                    shares=shares,
                    amount=gross_amount,
                    nav=nav,
                    fee=Decimal("0"),
                    confirm_date=trade_date,
                )
            )
            logger.info(
                "Day %s: Forced liquidation of %s on delisting "
                "(shares=%s, nav=%s, proceeds=%s)",
                trade_date, code, shares, nav, gross_amount,
            )

    def _filter_delisted_orders(
        self,
        intents: list[OrderIntent],
        trade_date: date,
        fund_meta: dict[str, FundMeta],
    ) -> list[OrderIntent]:
        """过滤掉对清盘后基金的订单。

        策略仍可能给一只清盘基金下单（基金选池逻辑里没读到 delisting_date）；
        我们在订单入队前直接拒绝。
        """
        kept: list[OrderIntent] = []
        for intent in intents:
            meta = fund_meta.get(intent.fund_code)
            if (
                meta is not None
                and meta.delisting_date is not None
                and trade_date >= meta.delisting_date
            ):
                logger.debug(
                    "Day %s: Rejected order for delisted fund %s "
                    "(delisting_date=%s)",
                    trade_date, intent.fund_code, meta.delisting_date,
                )
                continue
            kept.append(intent)
        return kept

    def _confirm_pending_orders(
        self,
        trade_date: date,
        nav_data: dict[str, dict[date, Decimal]],
        fund_meta: dict[str, FundMeta],
    ) -> None:
        """确认到期的 pending 订单。

        订单在 T 日下单，T+N 日确认（N 取决于基金类型）。
        确认时使用下单日（T 日）的收盘净值计算份额。
        """
        confirmed_orders: list[str] = []

        for order in list(self._portfolio.pending_orders):
            # 计算该订单的确认日期
            meta = fund_meta.get(order.fund_code)
            fund_type = meta.fund_type if meta else "stock"
            confirm_date = get_confirm_date(order.order_date, fund_type)

            if trade_date < confirm_date:
                continue

            # 使用下单日净值确认
            order_day_nav = self._get_nav(order.fund_code, order.order_date, nav_data)
            if order_day_nav is None:
                logger.warning(
                    "Day %s: Cannot confirm order %s - no NAV for %s on %s",
                    trade_date, order.order_id, order.fund_code, order.order_date,
                )
                continue

            fill = self._execute_order(order, order_day_nav, trade_date, fund_meta)
            if fill is not None:
                self._trades.append(fill)
                confirmed_orders.append(order.order_id)
                logger.debug(
                    "Day %s: Confirmed order %s (%s %s shares=%s amount=%s fee=%s)",
                    trade_date, order.order_id, order.direction,
                    order.fund_code, fill.shares, fill.amount, fill.fee,
                )

        # 移除已确认的订单
        for order_id in confirmed_orders:
            self._portfolio.remove_pending_order(order_id)

    def _execute_order(
        self,
        order: Order,
        nav: Decimal,
        confirm_date: date,
        fund_meta: dict[str, FundMeta],
    ) -> Fill | None:
        """执行订单确认，计算份额/金额/费用。"""
        meta = fund_meta.get(order.fund_code)

        if order.direction == "subscribe":
            return self._execute_subscribe(order, nav, confirm_date, meta)
        else:
            return self._execute_redeem(order, nav, confirm_date, meta)

    def _execute_subscribe(
        self,
        order: Order,
        nav: Decimal,
        confirm_date: date,
        meta: FundMeta | None,
    ) -> Fill | None:
        """执行申购确认。"""
        amount = order.amount
        if amount is None or amount <= Decimal("0"):
            logger.warning("Order %s has invalid subscribe amount: %s", order.order_id, amount)
            return None

        # 计算申购费
        fee = Decimal("0")
        net_amount = amount
        if meta and meta.subscribe_fee_tiers:
            fee_result = calc_subscribe_fee(amount, meta.subscribe_fee_tiers)
            fee = fee_result.fee
            net_amount = fee_result.net_amount
        else:
            # 无费率表时，不收费
            net_amount = amount

        # 计算市场冲击/滑点（在 net_amount 上扣除，等价于推高了实际申购价）
        slippage_cost = Decimal("0")
        if meta is not None and meta.slippage_config is not None:
            slip = compute_slippage(net_amount, meta.slippage_config)
            slippage_cost = slip.total_cost
            net_amount = max(net_amount - slippage_cost, Decimal("0"))

        # 计算份额
        if nav <= Decimal("0"):
            logger.warning("Order %s: NAV is zero or negative, cannot confirm", order.order_id)
            return None

        shares = (net_amount / nav).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # 确认订单 - 更新组合
        # 注意：现金已在下单时冻结（freeze_cash_for_subscribe）
        # 这里需要将冻结的现金转为持仓
        order.confirm(confirm_date)
        order.fill()

        # 增加持仓批次。现金已在下单日冻结，这里释放冻结金额并确认份额和 lot 成本。
        self._portfolio.release_frozen_cash_for_subscribe(amount)
        self._portfolio.add_lot(order.fund_code, shares, confirm_date, net_amount)
        if order.fund_code not in self._portfolio.holding_days:
            self._portfolio.holding_days[order.fund_code] = 0

        # fee 字段汇总申购费 + 滑点（账户角度都是钱出去了）
        return Fill(
            order_id=order.order_id,
            fund_code=order.fund_code,
            direction="subscribe",
            shares=shares,
            amount=amount,
            nav=nav,
            fee=fee + slippage_cost,
            confirm_date=confirm_date,
            order_date=order.order_date,
        )

    def _execute_redeem(
        self,
        order: Order,
        nav: Decimal,
        confirm_date: date,
        meta: FundMeta | None,
    ) -> Fill | None:
        """执行赎回确认。"""
        shares = order.shares
        if shares is None or shares <= Decimal("0"):
            logger.warning("Order %s has invalid redeem shares: %s", order.order_id, shares)
            return None

        # 检查持仓是否足够
        current_shares = self._portfolio.positions.get(order.fund_code, Decimal("0"))
        if current_shares < shares:
            logger.warning(
                "Order %s: insufficient shares for %s (need %s, have %s)",
                order.order_id, order.fund_code, shares, current_shares,
            )
            return None

        # 大额赎回限制检查
        from app.domain.backtest.large_redemption import check_large_redemption

        fund_total_shares = getattr(meta, "total_shares", None) if meta else None
        redemption_check = check_large_redemption(shares, fund_total_shares)

        if redemption_check.is_large_redemption:
            # 只确认当日可确认的部分
            shares = redemption_check.immediate_shares
            if shares <= Decimal("0"):
                logger.info(
                    "Order %s: 大额赎回全部延期，当日不确认",
                    order.order_id,
                )
                return None

            # 延期部分生成新的 pending order，确认日期推后
            if redemption_check.delayed_shares > Decimal("0"):
                logger.info(
                    "Order %s: %s", order.order_id, redemption_check.message,
                )
                # 生成延期订单，重新入队等待后续确认
                self._order_counter += 1
                delayed_order_id = f"ORD-DELAY-{confirm_date.isoformat()}-{self._order_counter:04d}"
                delayed_order = Order(
                    order_id=delayed_order_id,
                    fund_code=order.fund_code,
                    direction="redeem",
                    shares=redemption_check.delayed_shares,
                    order_date=confirm_date,  # 延期部分以当前确认日为新下单日
                )
                self._portfolio.add_pending_order(delayed_order)
                logger.debug(
                    "Day %s: Delayed redemption order %s queued (shares=%s, delay=%d days)",
                    confirm_date, delayed_order_id,
                    redemption_check.delayed_shares,
                    redemption_check.delay_days,
                )

        # 计算赎回费：按持仓批次 FIFO 分别计算持有天数，避免加仓后用最早确认日低估费用。
        fee = Decimal("0")
        gross_amount = (shares * nav).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        net_amount = gross_amount
        lot_details: list[dict[str, Any]] = []

        if meta and meta.redeem_fee_tiers:
            lots = self._portfolio.position_lots.get(order.fund_code)
            if not lots:
                fallback_holding_days = self._portfolio.get_holding_days(order.fund_code, as_of=confirm_date)
                fee_result = calc_redeem_fee(shares, nav, fallback_holding_days, meta.redeem_fee_tiers)
                fee = fee_result.fee
                net_amount = fee_result.net_amount
                lot_details.append({
                    "fund_code": order.fund_code,
                    "shares": str(shares),
                    "confirm_date": None,
                    "holding_days": fallback_holding_days,
                    "gross_amount": str(gross_amount),
                    "fee": str(fee_result.fee),
                })
            else:
                remaining = shares
                for lot in lots:
                    if remaining <= Decimal("0"):
                        break
                    lot_shares = min(lot.shares, remaining)
                    holding_days = max((confirm_date - lot.confirm_date).days, 0)
                    fee_result = calc_redeem_fee(lot_shares, nav, holding_days, meta.redeem_fee_tiers)
                    fee += fee_result.fee
                    lot_details.append({
                        "fund_code": lot.fund_code,
                        "shares": str(lot_shares),
                        "confirm_date": lot.confirm_date.isoformat(),
                        "holding_days": holding_days,
                        "gross_amount": str(fee_result.gross_amount),
                        "fee": str(fee_result.fee),
                    })
                    remaining -= lot_shares
                net_amount = gross_amount - fee

        # 滑点 / 市场冲击：在 net_amount 上再扣一次，相当于实际成交价低于
        # 报告 NAV（卖方滑点）。对开放式基金 slippage_config=None 时为 0，
        # 维持原有行为。
        slippage_cost = Decimal("0")
        if meta is not None and meta.slippage_config is not None:
            slip = compute_slippage(gross_amount, meta.slippage_config)
            slippage_cost = slip.total_cost
            net_amount = max(net_amount - slippage_cost, Decimal("0"))

        # 确认订单
        order.confirm(confirm_date)
        order.fill()

        # 更新组合：确认日扣减份额；赎回款进入 pending_cash，到账日才转为可用现金。
        fund_type = meta.fund_type if meta else "stock"
        cash_arrival_date = (
            get_cash_arrival_date(confirm_date, fund_type)
            if self._redeem_cash_delay
            else confirm_date
        )
        self._portfolio.consume_lots_fifo(order.fund_code, shares)
        if cash_arrival_date <= confirm_date:
            self._portfolio.cash += net_amount
        else:
            self._portfolio.add_pending_cash(
                order.fund_code,
                net_amount,
                cash_arrival_date,
                order.order_id,
                confirm_date,
            )

        return Fill(
            order_id=order.order_id,
            fund_code=order.fund_code,
            direction="redeem",
            shares=shares,
            amount=gross_amount,
            nav=nav,
            fee=fee + slippage_cost,
            confirm_date=confirm_date,
            order_date=order.order_date,
            lot_details=lot_details,
        )

    def _queue_orders(
        self,
        intents: list[OrderIntent],
        trade_date: date,
        fund_meta: dict[str, FundMeta],
    ) -> None:
        """将订单意图转为正式订单并入队。

        在入队前执行限购与状态检查，违规订单拒绝并记录原因。
        """
        for intent in intents:
            self._order_counter += 1
            order_id = f"ORD-{trade_date.isoformat()}-{self._order_counter:04d}"

            # 限购与状态检查
            meta = fund_meta.get(intent.fund_code)
            is_purchasable = meta.is_purchasable if meta else True
            purchase_limit = meta.purchase_limit if meta else None

            eligible, reason = check_purchase_eligibility(
                fund_code=intent.fund_code,
                direction=intent.direction,
                amount=intent.amount,
                is_purchasable=is_purchasable,
                purchase_limit=purchase_limit,
            )

            if not eligible:
                logger.debug(
                    "Day %s: Order %s rejected - %s (fund=%s, direction=%s, amount=%s)",
                    trade_date, order_id, reason, intent.fund_code,
                    intent.direction, intent.amount,
                )
                continue

            order = Order.from_intent(
                intent=intent,
                order_id=order_id,
                order_date=trade_date,
            )

            # 申购时冻结现金
            if intent.direction == "subscribe" and intent.amount is not None:
                if self._portfolio.cash < intent.amount:
                    logger.debug(
                        "Day %s: Insufficient cash for order %s (need %s, have %s)",
                        trade_date, order_id, intent.amount, self._portfolio.cash,
                    )
                    continue
                self._portfolio.freeze_cash_for_subscribe(intent.amount)

            self._portfolio.add_pending_order(order)
            logger.debug(
                "Day %s: Queued order %s (%s %s amount=%s shares=%s)",
                trade_date, order_id, intent.direction, intent.fund_code,
                intent.amount, intent.shares,
            )

    def _build_bar_context(
        self,
        trade_date: date,
        nav_data: dict[str, dict[date, Decimal]],
    ) -> BarContext:
        """构建策略上下文，截止日期为 T-1。"""
        # 找到 T-1 日期：trade_date 之前最近的一个有数据的日期
        # 简单实现：使用 trade_date 前一天作为 cutoff
        from app.domain.backtest.calendar import prev_trading_day

        cutoff_date = prev_trading_day(trade_date)

        return BarContext(
            current_date=trade_date,
            portfolio=self._portfolio,
            nav_history=nav_data,
            _cutoff_date=cutoff_date,
        )

    def _snapshot_equity(
        self,
        trade_date: date,
        nav_data: dict[str, dict[date, Decimal]],
    ) -> None:
        """记录当日权益快照。"""
        # 使用 T 日净值计算持仓市值
        nav_dict: dict[str, Decimal] = {}
        for fund_code in self._portfolio.positions:
            nav = self._get_nav(fund_code, trade_date, nav_data)
            if nav is not None:
                nav_dict[fund_code] = nav
            else:
                # 如果 T 日无净值，尝试使用最近的净值
                nav_dict[fund_code] = self._get_latest_nav(fund_code, trade_date, nav_data)

        position_value = Decimal("0")
        for fund_code, shares in self._portfolio.positions.items():
            if fund_code in nav_dict:
                position_value += shares * nav_dict[fund_code]

        # 权益包含可用现金、冻结现金（申购下单后待确认）、待到账赎回款及持仓市值；
        # 策略只能使用 available_cash（即 cash），但权益快照必须反映全部资产。
        equity = (
            self._portfolio.cash
            + self._portfolio.frozen_cash
            + self._portfolio.pending_cash_amount
            + position_value
        )

        snapshot = EquitySnapshot(
            trade_date=trade_date,
            equity=equity,
            cash=self._portfolio.cash,
            position_value=position_value,
        )
        self._equity_curve.append(snapshot)

    def _get_nav(
        self,
        fund_code: str,
        target_date: date,
        nav_data: dict[str, dict[date, Decimal]],
    ) -> Decimal | None:
        """获取指定基金在指定日期的净值。"""
        fund_navs = nav_data.get(fund_code)
        if fund_navs is None:
            return None
        return fund_navs.get(target_date)

    def _get_latest_nav(
        self,
        fund_code: str,
        before_date: date,
        nav_data: dict[str, dict[date, Decimal]],
    ) -> Decimal:
        """获取指定基金在指定日期之前最近的净值。

        如果该基金在 before_date 之前完全没有净值数据（例如基金尚未成立），
        记录警告并返回 Decimal("0")，使该持仓不计入市值。
        """
        fund_navs = nav_data.get(fund_code, {})
        available_dates = sorted(
            [d for d in fund_navs.keys() if d <= before_date],
            reverse=True,
        )
        if available_dates:
            return fund_navs[available_dates[0]]
        # 基金在此日期前无任何净值数据，返回 0 避免虚假市值
        logger.warning(
            "基金 %s 在 %s 之前无净值数据，可能尚未成立，持仓市值按 0 计算",
            fund_code,
            before_date,
        )
        return Decimal("0")

    def _build_result(
        self,
        start: date,
        end: date,
        initial_capital: Decimal,
    ) -> BacktestResult:
        """构建回测结果。"""
        return BacktestResult(
            equity_curve=self._equity_curve,
            trades=self._trades,
            final_portfolio=self._portfolio,
            start_date=start,
            end_date=end,
            initial_capital=initial_capital,
        )
