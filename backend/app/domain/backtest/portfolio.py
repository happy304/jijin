"""投资组合模型模块。

定义回测引擎中的 Portfolio 类，跟踪：
- 现金余额
- 各基金持仓份额
- 未确认（pending）订单
- 每个持仓的持有天数（自然日）

设计要点：
- subscribe() 确认申购：扣减现金，增加份额，记录确认日期
- redeem() 确认赎回：扣减份额，增加现金
- advance_day() 每个交易日结束时调用（保留接口兼容性）
- get_holding_days() 根据确认日期与当前日期计算自然日天数
- total_value() 根据当前净值字典计算组合总市值

注意：持有天数按**自然日**计算（赎回日 - 申购确认日），
这与中国公募基金赎回费率阶梯的实际计算口径一致。
参考：《公开募集证券投资基金运作管理办法》及各基金合同中
"持有期"均以自然日计算。

用法示例::

    from decimal import Decimal
    from datetime import date
    from app.domain.backtest.portfolio import Portfolio

    portfolio = Portfolio(cash=Decimal("100000"))
    portfolio.subscribe(
        fund_code="000001",
        shares=Decimal("6543.21"),
        amount=Decimal("10000"),
        fee=Decimal("15.00"),
        confirm_date=date(2024, 1, 3),
    )
    nav_dict = {"000001": Decimal("1.5280")}
    total = portfolio.total_value(nav_dict)
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from pydantic import BaseModel, ConfigDict, Field

from app.domain.backtest.order import Order


class PositionLot(BaseModel):
    """单笔申购确认形成的持仓批次。"""

    model_config = ConfigDict(validate_assignment=True)

    fund_code: str
    shares: Decimal
    confirm_date: date
    cost_amount: Decimal = Decimal("0")
    cost_nav: Decimal = Decimal("0")


class PendingCash(BaseModel):
    """已确认但尚未到账的赎回现金。"""

    model_config = ConfigDict(validate_assignment=True)

    fund_code: str
    amount: Decimal
    confirm_date: date
    arrival_date: date
    order_id: str | None = None


class Portfolio(BaseModel):
    """投资组合。

    跟踪回测过程中的资金状态，包括现金、持仓份额、未确认订单和持有天数。

    Attributes:
        cash: 可用现金余额
        positions: 持仓字典 {fund_code: shares}
        pending_orders: 未确认的订单列表
        holding_days: 持有天数字典 {fund_code: days}（兼容旧接口，由 _current_date 动态计算）
        confirm_dates: 申购确认日期字典 {fund_code: date}，用于按自然日计算持有天数
        _current_date: 当前交易日（由引擎在每个交易日设置）
    """

    model_config = ConfigDict(validate_assignment=True)

    cash: Decimal = Field(default=Decimal("0"))
    frozen_cash: Decimal = Field(default=Decimal("0"))
    positions: dict[str, Decimal] = Field(default_factory=dict)
    pending_orders: list[Order] = Field(default_factory=list)
    holding_days: dict[str, int] = Field(default_factory=dict)
    confirm_dates: dict[str, date] = Field(default_factory=dict)
    position_lots: dict[str, list[PositionLot]] = Field(default_factory=dict)
    pending_cash: list[PendingCash] = Field(default_factory=list)
    _current_date: date | None = None

    def subscribe(
        self,
        fund_code: str,
        shares: Decimal,
        amount: Decimal,
        fee: Decimal,
        confirm_date: date,
    ) -> None:
        """确认申购，更新持仓和现金。

        申购确认时：
        1. 从现金中扣除申购金额（含费用）
        2. 增加对应基金的持仓份额
        3. 如果是新持仓，记录确认日期（用于自然日持有天数计算）

        Args:
            fund_code: 基金代码
            shares: 确认的份额
            amount: 申购金额（不含费用的净投入金额）
            fee: 申购费用
            confirm_date: 确认日期

        Raises:
            ValueError: 如果现金不足以支付申购金额+费用
        """
        total_cost = amount + fee
        if self.cash < total_cost:
            raise ValueError(
                f"Insufficient cash for subscription: "
                f"need {total_cost}, available {self.cash}"
            )

        self.cash -= total_cost
        self.add_lot(fund_code, shares, confirm_date, amount)

        # 兼容旧接口：同步更新 holding_days
        if fund_code not in self.holding_days:
            self.holding_days[fund_code] = 0

    def redeem(
        self,
        fund_code: str,
        shares: Decimal,
        amount: Decimal,
        fee: Decimal,
        confirm_date: date,
        cash_arrival_date: date | None = None,
        order_id: str | None = None,
    ) -> None:
        """确认赎回，更新持仓和现金。

        赎回确认时：
        1. 扣减对应基金的持仓份额
        2. 将赎回金额（扣除费用后）加入现金
        3. 如果份额归零，清除持仓、持有天数和确认日期记录

        Args:
            fund_code: 基金代码
            shares: 赎回份额
            amount: 赎回总金额（费前）
            fee: 赎回费用
            confirm_date: 确认日期

        Raises:
            ValueError: 如果持仓份额不足
        """
        current_shares = self.positions.get(fund_code, Decimal("0"))
        if current_shares < shares:
            raise ValueError(
                f"Insufficient shares for redemption of {fund_code}: "
                f"need {shares}, available {current_shares}"
            )

        self.consume_lots_fifo(fund_code, shares)
        net_amount = amount - fee
        if cash_arrival_date is None or cash_arrival_date <= confirm_date:
            self.cash += net_amount
        else:
            self.add_pending_cash(fund_code, net_amount, cash_arrival_date, order_id, confirm_date)

        # consume_lots_fifo 会同步清理 positions / lots / confirm_dates。

    def add_lot(
        self,
        fund_code: str,
        shares: Decimal,
        confirm_date: date,
        cost_amount: Decimal = Decimal("0"),
    ) -> None:
        """新增一个持仓批次，并同步聚合持仓。"""
        if shares <= Decimal("0"):
            return
        cost_nav = (cost_amount / shares) if shares > Decimal("0") else Decimal("0")
        self.position_lots.setdefault(fund_code, []).append(
            PositionLot(
                fund_code=fund_code,
                shares=shares,
                confirm_date=confirm_date,
                cost_amount=cost_amount,
                cost_nav=cost_nav,
            )
        )
        self.positions[fund_code] = self.positions.get(fund_code, Decimal("0")) + shares
        if fund_code not in self.confirm_dates or confirm_date < self.confirm_dates[fund_code]:
            self.confirm_dates[fund_code] = confirm_date

    def consume_lots_fifo(self, fund_code: str, shares: Decimal) -> list[PositionLot]:
        """按 FIFO 消耗持仓批次，返回实际被赎回的批次片段。"""
        if shares <= Decimal("0"):
            return []
        current_shares = self.positions.get(fund_code, Decimal("0"))
        if current_shares < shares:
            raise ValueError(
                f"Insufficient shares for redemption of {fund_code}: "
                f"need {shares}, available {current_shares}"
            )

        if fund_code not in self.position_lots:
            fallback_date = self.confirm_dates.get(fund_code) or self._current_date or date.min
            self.position_lots[fund_code] = [
                PositionLot(
                    fund_code=fund_code,
                    shares=current_shares,
                    confirm_date=fallback_date,
                )
            ]

        remaining = shares
        consumed: list[PositionLot] = []
        new_lots: list[PositionLot] = []
        for lot in self.position_lots.get(fund_code, []):
            if remaining <= Decimal("0"):
                new_lots.append(lot)
                continue
            take = min(lot.shares, remaining)
            consumed.append(
                PositionLot(
                    fund_code=fund_code,
                    shares=take,
                    confirm_date=lot.confirm_date,
                    cost_amount=(
                        (lot.cost_amount * take / lot.shares).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                        if lot.shares > Decimal("0") and lot.cost_amount > Decimal("0")
                        else Decimal("0")
                    ),
                    cost_nav=lot.cost_nav,
                )
            )
            remaining -= take
            leftover = lot.shares - take
            if leftover > Decimal("0"):
                new_lots.append(
                    PositionLot(
                        fund_code=fund_code,
                        shares=leftover,
                        confirm_date=lot.confirm_date,
                        cost_amount=(
                            lot.cost_amount - consumed[-1].cost_amount
                            if lot.cost_amount > Decimal("0")
                            else Decimal("0")
                        ),
                        cost_nav=lot.cost_nav,
                    )
                )

        self.positions[fund_code] = current_shares - shares
        if self.positions[fund_code] <= Decimal("0"):
            self.positions.pop(fund_code, None)
            self.position_lots.pop(fund_code, None)
            self.holding_days.pop(fund_code, None)
            self.confirm_dates.pop(fund_code, None)
        else:
            self.position_lots[fund_code] = new_lots
            if new_lots:
                self.confirm_dates[fund_code] = min(lot.confirm_date for lot in new_lots)

        return consumed

    def add_pending_cash(
        self,
        fund_code: str,
        amount: Decimal,
        arrival_date: date,
        order_id: str | None = None,
        confirm_date: date | None = None,
    ) -> None:
        """登记赎回待到账现金，不增加可用现金。"""
        if amount > Decimal("0"):
            self.pending_cash.append(
                PendingCash(
                    fund_code=fund_code,
                    amount=amount,
                    confirm_date=confirm_date or arrival_date,
                    arrival_date=arrival_date,
                    order_id=order_id,
                )
            )

    def settle_pending_cash(self, current_date: date) -> Decimal:
        """将到账日不晚于 current_date 的 pending cash 转入可用现金。"""
        arrived = Decimal("0")
        remaining: list[PendingCash] = []
        for item in self.pending_cash:
            if item.arrival_date <= current_date:
                arrived += item.amount
            else:
                remaining.append(item)
        if arrived > Decimal("0"):
            self.cash += arrived
        self.pending_cash = remaining
        return arrived

    @property
    def pending_cash_amount(self) -> Decimal:
        """待到账赎回款总额。"""
        return sum((item.amount for item in self.pending_cash), Decimal("0"))

    @property
    def available_cash(self) -> Decimal:
        """可用于新申购的现金。pending_cash 未到账，frozen_cash 已被订单占用，均不可用。"""
        return self.cash

    @property
    def lots(self) -> dict[str, list[PositionLot]]:
        """兼容旧调用的持仓批次别名；新代码应使用 position_lots。"""
        return self.position_lots

    def total_value(self, nav_dict: dict[str, Decimal]) -> Decimal:
        """计算组合总市值（现金 + 持仓市值）。

        Args:
            nav_dict: 当前净值字典 {fund_code: nav}

        Returns:
            组合总市值

        Raises:
            KeyError: 如果持仓基金在 nav_dict 中找不到对应净值
        """
        position_value = Decimal("0")
        for fund_code, shares in self.positions.items():
            if fund_code not in nav_dict:
                raise KeyError(
                    f"NAV not found for fund {fund_code} in nav_dict"
                )
            position_value += shares * nav_dict[fund_code]
        return self.cash + position_value

    def advance_day(self, current_date: date | None = None) -> None:
        """推进一个交易日。

        更新内部当前日期（用于自然日持有天数计算），
        同时保持 holding_days 字典的兼容性更新。

        Args:
            current_date: 当前交易日。如果提供，将用于自然日计算。
        """
        if current_date is not None:
            self._current_date = current_date

        # 兼容旧接口：递增交易日计数（仅用于不依赖 confirm_dates 的场景）
        for fund_code in list(self.holding_days.keys()):
            if fund_code in self.positions:
                self.holding_days[fund_code] += 1

    def get_holding_days(self, fund_code: str, as_of: date | None = None) -> int:
        """获取指定基金的持有天数（自然日）。

        优先使用 confirm_dates 按自然日计算：
            holding_days = (as_of - confirm_date).days

        如果 confirm_dates 中无记录（兼容旧数据），回退到 holding_days 字典。

        注意：中国公募基金赎回费率阶梯按自然日计算持有期，
        而非交易日。例如"持有不满 7 天"指自然日不满 7 天。

        Args:
            fund_code: 基金代码
            as_of: 计算持有天数的截止日期。默认使用 _current_date。

        Returns:
            持有自然日天数，如果无持仓返回 0
        """
        # 优先使用确认日期按自然日计算
        if fund_code in self.confirm_dates:
            ref_date = as_of or self._current_date
            if ref_date is not None:
                delta = (ref_date - self.confirm_dates[fund_code]).days
                return max(delta, 0)

        # 回退到旧的交易日计数
        return self.holding_days.get(fund_code, 0)

    def add_pending_order(self, order: Order) -> None:
        """添加未确认订单到待处理列表。

        Args:
            order: 待确认的订单
        """
        self.pending_orders.append(order)

    def remove_pending_order(self, order_id: str) -> Order | None:
        """从待处理列表中移除指定订单。

        Args:
            order_id: 要移除的订单 ID

        Returns:
            被移除的订单，如果未找到返回 None
        """
        for i, order in enumerate(self.pending_orders):
            if order.order_id == order_id:
                return self.pending_orders.pop(i)
        return None

    def freeze_cash_for_subscribe(self, amount: Decimal) -> None:
        """为申购订单冻结现金（下单时预扣）。

        在订单提交时预扣现金，防止超额下单。
        如果订单被拒绝，需要调用 unfreeze_cash 归还。

        Args:
            amount: 冻结金额

        Raises:
            ValueError: 如果可用现金不足
        """
        if self.cash < amount:
            raise ValueError(
                f"Insufficient cash to freeze: need {amount}, available {self.cash}"
            )
        self.cash -= amount
        self.frozen_cash += amount

    def release_frozen_cash_for_subscribe(self, amount: Decimal) -> None:
        """申购确认时释放已冻结现金，表示该金额已转为基金份额或费用。"""
        release_amount = min(amount, self.frozen_cash)
        self.frozen_cash -= release_amount

    def unfreeze_cash(self, amount: Decimal) -> None:
        """解冻现金（订单被拒绝时归还）。

        Args:
            amount: 解冻金额
        """
        release_amount = min(amount, self.frozen_cash)
        self.frozen_cash -= release_amount
        self.cash += amount

    @property
    def position_count(self) -> int:
        """当前持仓基金数量。"""
        return len(self.positions)

    @property
    def pending_order_count(self) -> int:
        """未确认订单数量。"""
        return len(self.pending_orders)
