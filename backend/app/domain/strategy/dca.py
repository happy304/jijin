"""定投策略模块。

实现三种定投策略变体：
1. 定额定投 (FixedAmountDCA): 固定金额定期投入
2. 价值平均 (ValueAveragingDCA): 调整投入金额以维持目标增长路径
3. 智能定投 (SmartDCA): 当价格低于均线时加倍投入

设计要点：
- 所有策略继承 BaseStrategy，实现 on_bar 方法
- 使用 context.date 判断是否为投资日（基于频率参数）
- 智能定投通过比较当前 NAV 与移动平均线决定投入倍数
- 返回 OrderIntent（subscribe 方向）

需求: 5.1
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import Field

from app.domain.backtest.engine_event import BarContext
from app.domain.backtest.order import OrderIntent
from app.domain.strategy.base import BaseStrategy, StrategyParams


# ---------------------------------------------------------------------------
# 投资频率枚举
# ---------------------------------------------------------------------------


class Frequency(str, Enum):
    """定投频率。"""

    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    MONTHLY = "monthly"


# ---------------------------------------------------------------------------
# 参数类
# ---------------------------------------------------------------------------


class DCAParams(StrategyParams):
    """定额定投参数。

    Attributes:
        amount: 每期投入金额
        frequency: 投资频率（weekly/biweekly/monthly）
        fund_code: 定投基金代码
    """

    amount: Decimal = Field(default=Decimal("1000"), gt=0, description="每期投入金额")
    frequency: Frequency = Field(default=Frequency.MONTHLY, description="投资频率")
    fund_code: str = Field(default="000001", description="定投基金代码")


class ValueAveragingParams(DCAParams):
    """价值平均定投参数。

    在 DCAParams 基础上增加目标月增长额。

    Attributes:
        target_monthly_growth: 每月目标增长金额
    """

    target_monthly_growth: Decimal = Field(
        default=Decimal("1000"), gt=0, description="每月目标增长金额"
    )


class SmartDCAParams(DCAParams):
    """智能定投参数。

    在 DCAParams 基础上增加均线窗口和加倍系数。

    Attributes:
        ma_window: 移动平均窗口天数
        multiplier_below_ma: 价格低于均线时的投入倍数
    """

    ma_window: int = Field(default=20, gt=0, description="移动平均窗口天数")
    multiplier_below_ma: Decimal = Field(
        default=Decimal("2.0"), gt=0, description="低于均线时投入倍数"
    )


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _is_investment_day(
    current_date: date,
    last_investment_date: date | None,
    frequency: Frequency,
) -> bool:
    """判断当前日期是否为投资日。

    规则：
    - weekly: 距上次投资 >= 7 天
    - biweekly: 距上次投资 >= 14 天
    - monthly: 距上次投资 >= 28 天

    首次投资（last_investment_date 为 None）时直接返回 True。

    Args:
        current_date: 当前交易日
        last_investment_date: 上次投资日期
        frequency: 投资频率

    Returns:
        是否应该在当日投资
    """
    if last_investment_date is None:
        return True

    days_since_last = (current_date - last_investment_date).days

    if frequency == Frequency.WEEKLY:
        return days_since_last >= 7
    elif frequency == Frequency.BIWEEKLY:
        return days_since_last >= 14
    else:  # MONTHLY
        return days_since_last >= 28


def _compute_moving_average(
    nav_series: dict[date, Decimal],
    window: int,
) -> Decimal | None:
    """计算最近 window 天的移动平均净值。

    Args:
        nav_series: 历史净值序列 {date: nav}
        window: 窗口天数

    Returns:
        移动平均值，数据不足时返回 None
    """
    if not nav_series:
        return None

    sorted_dates = sorted(nav_series.keys(), reverse=True)
    recent_navs = [nav_series[d] for d in sorted_dates[:window]]

    if len(recent_navs) < window:
        return None

    total = sum(recent_navs, Decimal("0"))
    return total / Decimal(str(len(recent_navs)))


# ---------------------------------------------------------------------------
# 定额定投策略
# ---------------------------------------------------------------------------


class FixedAmountDCA(BaseStrategy):
    """定额定投策略。

    每隔固定周期投入固定金额。最简单的定投方式，
    利用时间分散降低择时风险。

    Example::

        strategy = FixedAmountDCA(
            params=DCAParams(amount=Decimal("1000"), frequency=Frequency.MONTHLY, fund_code="000001"),
            universe=["000001"],
        )
    """

    name = "fixed_amount_dca"

    def __init__(
        self,
        params: DCAParams | None = None,
        universe: list[str] | None = None,
    ) -> None:
        super().__init__(params=params, universe=universe)
        self._last_investment_date: date | None = None
        self._investment_count: int = 0

    @property
    def dca_params(self) -> DCAParams:
        """获取类型化的参数。"""
        assert isinstance(self.params, DCAParams)
        return self.params

    def on_bar(self, context: BarContext) -> list[OrderIntent]:
        """每个交易日判断是否投资。

        如果到达投资日且现金充足，生成申购意图。

        Args:
            context: 当日策略上下文

        Returns:
            OrderIntent 列表（最多一个申购意图）
        """
        if not _is_investment_day(
            context.date, self._last_investment_date, self.dca_params.frequency
        ):
            return []

        amount = self.dca_params.amount

        # 检查现金是否充足
        if context.cash < amount:
            return []

        self._last_investment_date = context.date
        self._investment_count += 1

        return [
            OrderIntent(
                fund_code=self.dca_params.fund_code,
                direction="subscribe",
                amount=amount,
            )
        ]


# ---------------------------------------------------------------------------
# 价值平均定投策略
# ---------------------------------------------------------------------------


class ValueAveragingDCA(BaseStrategy):
    """价值平均定投策略。

    维持一条目标增长路径（每期增长固定金额），
    当实际持仓价值低于目标时多投，高于目标时少投或不投。

    目标路径：target_value = target_monthly_growth × investment_count

    每期投入 = target_value - current_holding_value
    如果差额 <= 0，则不投资。

    Example::

        strategy = ValueAveragingDCA(
            params=ValueAveragingParams(
                amount=Decimal("1000"),
                frequency=Frequency.MONTHLY,
                fund_code="000001",
                target_monthly_growth=Decimal("1000"),
            ),
            universe=["000001"],
        )
    """

    name = "value_averaging_dca"

    def __init__(
        self,
        params: ValueAveragingParams | None = None,
        universe: list[str] | None = None,
    ) -> None:
        super().__init__(params=params, universe=universe)
        self._last_investment_date: date | None = None
        self._investment_count: int = 0

    @property
    def va_params(self) -> ValueAveragingParams:
        """获取类型化的参数。"""
        assert isinstance(self.params, ValueAveragingParams)
        return self.params

    def on_bar(self, context: BarContext) -> list[OrderIntent]:
        """每个交易日判断是否投资。

        计算目标价值与当前持仓价值的差额，差额为正则投入。

        Args:
            context: 当日策略上下文

        Returns:
            OrderIntent 列表
        """
        if not _is_investment_day(
            context.date, self._last_investment_date, self.va_params.frequency
        ):
            return []

        self._investment_count += 1
        self._last_investment_date = context.date

        # 计算目标价值
        target_value = self.va_params.target_monthly_growth * Decimal(
            str(self._investment_count)
        )

        # 计算当前持仓价值
        fund_code = self.va_params.fund_code
        current_shares = context.positions.get(fund_code, Decimal("0"))
        nav = context.nav(fund_code)

        if nav is None or nav <= Decimal("0"):
            # 无净值数据，使用基础金额
            amount = self.va_params.amount
        else:
            current_value = current_shares * nav
            amount = target_value - current_value

        # 如果差额 <= 0，不投资
        if amount <= Decimal("0"):
            return []

        # 限制投入不超过可用现金
        if context.cash < amount:
            amount = context.cash

        if amount <= Decimal("0"):
            return []

        return [
            OrderIntent(
                fund_code=fund_code,
                direction="subscribe",
                amount=amount,
            )
        ]


# ---------------------------------------------------------------------------
# 智能定投策略
# ---------------------------------------------------------------------------


class SmartDCA(BaseStrategy):
    """智能定投策略（均线偏离加倍）。

    基本逻辑与定额定投相同，但当当前净值低于移动平均线时，
    投入金额乘以 multiplier_below_ma 倍数。

    判断逻辑：
    - 当前 NAV < MA(window) → 投入 amount × multiplier_below_ma
    - 当前 NAV >= MA(window) → 投入 amount

    如果历史数据不足以计算均线，则使用基础金额。

    Example::

        strategy = SmartDCA(
            params=SmartDCAParams(
                amount=Decimal("1000"),
                frequency=Frequency.MONTHLY,
                fund_code="000001",
                ma_window=20,
                multiplier_below_ma=Decimal("2.0"),
            ),
            universe=["000001"],
        )
    """

    name = "smart_dca"

    def __init__(
        self,
        params: SmartDCAParams | None = None,
        universe: list[str] | None = None,
    ) -> None:
        super().__init__(params=params, universe=universe)
        self._last_investment_date: date | None = None
        self._investment_count: int = 0

    @property
    def smart_params(self) -> SmartDCAParams:
        """获取类型化的参数。"""
        assert isinstance(self.params, SmartDCAParams)
        return self.params

    def on_bar(self, context: BarContext) -> list[OrderIntent]:
        """每个交易日判断是否投资。

        到达投资日时，比较当前 NAV 与移动平均线，
        低于均线则加倍投入。

        Args:
            context: 当日策略上下文

        Returns:
            OrderIntent 列表
        """
        if not _is_investment_day(
            context.date, self._last_investment_date, self.smart_params.frequency
        ):
            return []

        fund_code = self.smart_params.fund_code
        base_amount = self.smart_params.amount

        # 获取当前 NAV（T-1 日）
        current_nav = context.nav(fund_code)

        # 计算移动平均
        nav_series = context.nav_series(fund_code)
        ma = _compute_moving_average(nav_series, self.smart_params.ma_window)

        # 决定投入金额
        if current_nav is not None and ma is not None and current_nav < ma:
            amount = base_amount * self.smart_params.multiplier_below_ma
        else:
            amount = base_amount

        # 检查现金是否充足
        if context.cash < amount:
            # 现金不足时尝试使用基础金额
            if context.cash >= base_amount:
                amount = base_amount
            else:
                return []

        self._last_investment_date = context.date
        self._investment_count += 1

        return [
            OrderIntent(
                fund_code=fund_code,
                direction="subscribe",
                amount=amount,
            )
        ]
