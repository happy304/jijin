"""Adjusted NAV (复权净值) calculation service.

Computes forward-adjusted NAV for a fund based on dividend and split events.

口径说明：这里采用“前复权到最新净值”口径：保持最新区间的单位净值
不变，将除权/拆分事件之前的历史单位净值按事件影响向下调整，使
``adj_nav.pct_change()`` 能反映包含现金分红和拆分影响的总回报。
服务同时将 ``daily_return`` 重算为相邻 ``adj_nav`` 的日收益率，避免
数据源原始单位净值涨跌幅在分红除权日污染因子、回测或校验链路。

Algorithm:
    adj_nav_t = unit_nav_t × adj_factor_t
    daily_return_t = adj_nav_t / adj_nav_{t-1} - 1
    adj_factor_t = ∏(from t to latest) [(1 - dividend_d / nav_before_d) / split_ratio_d]

The adjustment factor accumulates from the most recent date backwards.
Each dividend/split event on date D uses the NAV on the trading day
*before* D (the ex-date) as the denominator for the dividend ratio.

After each dividend/split upsert, the entire historical adj_nav for
that fund should be recalculated.

Requirements: 2.6
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models.fund_dividends import FundDividend
from app.data.models.fund_nav import FundNav


async def recalculate_adj_nav(
    session: AsyncSession,
    fund_code: str,
) -> int:
    """Recalculate adj_nav for all NAV records of a given fund.

    Parameters
    ----------
    session:
        Active async session. The caller is responsible for committing.
    fund_code:
        The fund code to recalculate.

    Returns
    -------
    int
        Number of NAV records updated.

    Algorithm
    ---------
    1. Fetch all NAV records ordered by trade_date ASC.
    2. Fetch all dividend/split events ordered by ex_date ASC.
    3. For each NAV record, compute the cumulative adjustment factor
       from all future events (events with ex_date > trade_date).
    4. adj_nav = unit_nav × adj_factor
    5. daily_return = adj_nav.pct_change()（首个有效点为 None）

    The forward-adjustment-to-latest formula ensures that the most recent NAV
    equals unit_nav (adj_factor = 1 for dates after the last event) while
    historical NAVs are adjusted downward across dividend/split events so
    total-return pct_change is continuous around ex-dates.
    """
    # 1. Fetch all NAV records for this fund, ordered by date
    nav_query = (
        select(FundNav)
        .where(FundNav.fund_code == fund_code)
        .order_by(FundNav.trade_date.asc())
    )
    nav_result = await session.execute(nav_query)
    nav_records: list[FundNav] = list(nav_result.scalars().all())

    if not nav_records:
        return 0

    # 2. Fetch all dividend/split events for this fund, ordered by date
    div_query = (
        select(FundDividend)
        .where(FundDividend.fund_code == fund_code)
        .order_by(FundDividend.ex_date.asc())
    )
    div_result = await session.execute(div_query)
    div_events: list[FundDividend] = list(div_result.scalars().all())

    # 3. Build a date->NAV lookup for finding nav_before (the NAV on the
    #    trading day before the ex-date)
    nav_by_date: dict[date, Decimal] = {}
    sorted_dates: list[date] = []
    for nav in nav_records:
        if nav.unit_nav is not None:
            nav_by_date[nav.trade_date] = nav.unit_nav
            sorted_dates.append(nav.trade_date)

    # 4. For each dividend event, determine the NAV before the ex-date.
    #    "nav_before" is the unit_nav on the last trading day before ex_date.
    event_adjustments: list[tuple[date, Decimal]] = []
    for event in div_events:
        dividend = event.dividend_per_share or Decimal("0")
        split_ratio = event.split_ratio or Decimal("1")

        # Find the NAV on the trading day just before ex_date
        nav_before = _find_nav_before(sorted_dates, nav_by_date, event.ex_date)

        if nav_before is None or nav_before == Decimal("0"):
            # Cannot compute dividend adjustment without a valid prior NAV;
            # still apply split adjustment if present.
            if split_ratio != Decimal("1"):
                factor = Decimal("1") / split_ratio
                event_adjustments.append((event.ex_date, factor))
            continue

        # Adjustment factor for this single event, forward-adjusted to latest NAV:
        # factor = (1 - dividend / nav_before) / split_ratio
        dividend_ratio = dividend / nav_before
        denominator = Decimal("1") - dividend_ratio

        if denominator <= Decimal("0"):
            # Edge case: dividend >= nav (shouldn't happen in practice)
            # Skip dividend to avoid negative/non-sensical factor, but still
            # apply split adjustment if present.
            if split_ratio != Decimal("1"):
                event_adjustments.append((event.ex_date, Decimal("1") / split_ratio))
            continue

        factor = denominator / split_ratio
        event_adjustments.append((event.ex_date, factor))

    # 5. Compute adj_nav for each NAV record.
    #    adj_factor_t = product of all event factors where event.ex_date > trade_date
    #    (forward adjustment: events after this date affect this date's adj_nav)
    updated_count = 0
    previous_adj_nav: Decimal | None = None
    for nav in nav_records:
        if nav.unit_nav is None:
            if nav.daily_return is not None:
                nav.daily_return = None
                updated_count += 1
            previous_adj_nav = None
            continue

        adj_factor = Decimal("1")
        for event_date, factor in event_adjustments:
            if event_date > nav.trade_date:
                adj_factor *= factor

        new_adj_nav = (nav.unit_nav * adj_factor).quantize(
            Decimal("0.000001"), rounding=ROUND_HALF_UP
        )

        new_daily_return: Decimal | None = None
        if previous_adj_nav is not None and previous_adj_nav > Decimal("0"):
            new_daily_return = ((new_adj_nav / previous_adj_nav) - Decimal("1")).quantize(
                Decimal("0.000001"), rounding=ROUND_HALF_UP
            )

        changed = False
        if nav.adj_nav != new_adj_nav:
            nav.adj_nav = new_adj_nav
            changed = True
        if nav.daily_return != new_daily_return:
            nav.daily_return = new_daily_return
            changed = True
        if changed:
            updated_count += 1

        previous_adj_nav = new_adj_nav

    return updated_count


def _find_nav_before(
    sorted_dates: list[date],
    nav_by_date: dict[date, Decimal],
    ex_date: date,
) -> Decimal | None:
    """Find the unit_nav on the last trading day strictly before ex_date.

    Uses binary search on the sorted date list for efficiency.
    """
    import bisect

    idx = bisect.bisect_left(sorted_dates, ex_date)
    if idx == 0:
        # No trading day before ex_date
        return None
    # The date at idx-1 is the last date < ex_date
    prev_date = sorted_dates[idx - 1]
    return nav_by_date.get(prev_date)
