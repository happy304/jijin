"""分红与拆分处理模块。

在回测引擎中，当除权日到来时，根据分红/拆分事件调整投资组合：
- 现金分红（reinvest=False）：持有份额 × 每份分红 → 加入现金
- 红利再投（reinvest=True）：持有份额 × 每份分红 / 除权日净值 → 增加份额
- 基金拆分：持有份额 × 拆分比例 → 新份额

设计要点：
- 所有函数直接修改 Portfolio 对象（in-place）
- 使用 Decimal 保证精度
- 对无持仓、零分红、拆分比例为 1 等边界情况做安全处理

用法示例::

    from decimal import Decimal
    from app.domain.backtest.portfolio import Portfolio
    from app.domain.backtest.corporate_actions import process_dividend, process_split

    portfolio = Portfolio(cash=Decimal("50000"))
    portfolio.positions["000001"] = Decimal("10000")

    # 现金分红
    process_dividend(portfolio, "000001", Decimal("0.5"), Decimal("1.2"), reinvest=False)

    # 红利再投
    process_dividend(portfolio, "000001", Decimal("0.5"), Decimal("1.2"), reinvest=True)

    # 基金拆分
    process_split(portfolio, "000001", Decimal("2.0"))
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.backtest.portfolio import Portfolio


def process_dividend(
    portfolio: Portfolio,
    fund_code: str,
    dividend_per_share: Decimal,
    nav: Decimal,
    reinvest: bool = False,
) -> None:
    """处理分红事件。

    在除权日根据持仓份额和分红方式调整组合：
    - 现金分红：cash += shares × dividend_per_share
    - 红利再投：shares += (shares × dividend_per_share) / nav

    Args:
        portfolio: 投资组合对象，将被 in-place 修改
        fund_code: 基金代码
        dividend_per_share: 每份分红金额
        nav: 除权日净值（红利再投时用于计算新增份额）
        reinvest: 是否红利再投，False 为现金分红

    Notes:
        - 如果组合中无该基金持仓，不做任何操作
        - 如果 dividend_per_share 为 0，不做任何操作
        - 红利再投时 nav 不能为 0，否则抛出 ValueError
    """
    shares = portfolio.positions.get(fund_code, Decimal("0"))

    # 无持仓或零分红，直接返回
    if shares == Decimal("0") or dividend_per_share == Decimal("0"):
        return

    total_dividend = shares * dividend_per_share

    if reinvest:
        if nav == Decimal("0"):
            raise ValueError(
                f"NAV cannot be zero for dividend reinvestment of {fund_code}"
            )
        # 红利再投：将分红金额按当日净值折算为新增份额
        additional_shares = total_dividend / nav
        portfolio.positions[fund_code] = shares + additional_shares
    else:
        # 现金分红：分红金额直接加入现金
        portfolio.cash += total_dividend


def process_split(
    portfolio: Portfolio,
    fund_code: str,
    split_ratio: Decimal,
) -> None:
    """处理基金拆分事件。

    在拆分日按比例调整持仓份额：shares = shares × split_ratio

    Args:
        portfolio: 投资组合对象，将被 in-place 修改
        fund_code: 基金代码
        split_ratio: 拆分比例（例如 2.0 表示 1 拆 2）

    Notes:
        - 如果组合中无该基金持仓，不做任何操作
        - 如果 split_ratio 为 1.0，不做任何操作（无实际拆分）
        - split_ratio 不能为 0，否则抛出 ValueError
    """
    shares = portfolio.positions.get(fund_code, Decimal("0"))

    # 无持仓，直接返回
    if shares == Decimal("0"):
        return

    if split_ratio == Decimal("0"):
        raise ValueError(
            f"Split ratio cannot be zero for fund {fund_code}"
        )

    # 拆分比例为 1 时无需操作
    if split_ratio == Decimal("1"):
        return

    # 按拆分比例调整份额
    portfolio.positions[fund_code] = shares * split_ratio
