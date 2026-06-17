"""Unit tests for the adjusted NAV (复权净值) calculation service.

Tests cover:
- Basic dividend adjustment (single dividend event)
- Multiple dividend events
- Split ratio adjustment (no dividend, just split)
- Combined dividend + split
- No events (adj_nav should equal unit_nav)
- Empty NAV records (returns 0)
- Edge case: dividend on first trading day (no nav_before available)
- Edge case: dividend_per_share >= unit_nav (should be skipped)

Requirements: 2.6
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models.fund_dividends import FundDividend
from app.data.models.fund_nav import FundNav
from app.data.services.adj_nav import recalculate_adj_nav


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2023, 1, 1, tzinfo=timezone.utc)


def _nav(fund_code: str, trade_date: date, unit_nav: Decimal) -> FundNav:
    """Create a FundNav instance for testing."""
    return FundNav(
        fund_code=fund_code,
        trade_date=trade_date,
        unit_nav=unit_nav,
        accum_nav=unit_nav,
        adj_nav=unit_nav,  # initial adj_nav = unit_nav
        created_at=_NOW,
    )


def _div(
    fund_code: str,
    ex_date: date,
    dividend_per_share: Decimal = Decimal("0"),
    split_ratio: Decimal = Decimal("1"),
) -> FundDividend:
    """Create a FundDividend instance for testing."""
    return FundDividend(
        fund_code=fund_code,
        ex_date=ex_date,
        dividend_per_share=dividend_per_share,
        split_ratio=split_ratio,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_events_adj_nav_equals_unit_nav(session: AsyncSession) -> None:
    """When there are no dividend/split events, adj_nav should equal unit_nav."""
    code = "TEST01"
    navs = [
        _nav(code, date(2023, 1, 2), Decimal("1.0000")),
        _nav(code, date(2023, 1, 3), Decimal("1.0100")),
        _nav(code, date(2023, 1, 4), Decimal("1.0200")),
    ]
    session.add_all(navs)
    await session.flush()

    updated = await recalculate_adj_nav(session, code)

    # No events keep adj_nav equal to unit_nav, but daily_return is derived
    # from adj_nav.pct_change(), so later rows are updated.
    assert updated == 2

    # Verify adj_nav == unit_nav for all records
    result = await session.execute(
        select(FundNav).where(FundNav.fund_code == code).order_by(FundNav.trade_date)
    )
    records = list(result.scalars().all())
    for r in records:
        assert r.adj_nav == r.unit_nav


@pytest.mark.asyncio
async def test_empty_nav_records_returns_zero(session: AsyncSession) -> None:
    """When there are no NAV records for the fund, should return 0."""
    updated = await recalculate_adj_nav(session, "EMPTY1")
    assert updated == 0


@pytest.mark.asyncio
async def test_single_dividend_event(session: AsyncSession) -> None:
    """Single dividend event should adjust all NAV records before the ex-date.

    Example:
    - NAV on 2023-01-02: 1.0000
    - NAV on 2023-01-03: 1.0100
    - Dividend on 2023-01-04: 0.05 per share (nav_before = 1.0100)
    - NAV on 2023-01-04: 0.9600
    - NAV on 2023-01-05: 0.9700

    factor = 1 / (1 - 0.05/1.0100) = 1 / (1 - 0.004950...) ≈ 1.004975...
    adj_nav for dates before 2023-01-04:
      2023-01-02: 1.0000 * 1.004975 ≈ 1.004975
      2023-01-03: 1.0100 * 1.004975 ≈ 1.015025
    adj_nav for dates on/after 2023-01-04:
      2023-01-04: 0.9600 * 1 = 0.9600
      2023-01-05: 0.9700 * 1 = 0.9700
    """
    code = "TEST02"
    navs = [
        _nav(code, date(2023, 1, 2), Decimal("1.0000")),
        _nav(code, date(2023, 1, 3), Decimal("1.0100")),
        _nav(code, date(2023, 1, 4), Decimal("0.9600")),
        _nav(code, date(2023, 1, 5), Decimal("0.9700")),
    ]
    div = _div(code, date(2023, 1, 4), dividend_per_share=Decimal("0.05"))
    session.add_all(navs + [div])
    await session.flush()

    updated = await recalculate_adj_nav(session, code)
    assert updated > 0

    result = await session.execute(
        select(FundNav).where(FundNav.fund_code == code).order_by(FundNav.trade_date)
    )
    records = list(result.scalars().all())

    # nav_before for ex_date 2023-01-04 is the NAV on 2023-01-03 = 1.0100
    # factor = 1 / (1 - 0.05/1.0100) = 1.0100 / (1.0100 - 0.05) = 1.0100 / 0.9600
    factor = Decimal("0.9600") / Decimal("1.0100")

    # Records before ex_date should be adjusted
    assert records[0].adj_nav == (Decimal("1.0000") * factor).quantize(
        Decimal("0.000001")
    )
    assert records[1].adj_nav == (Decimal("1.0100") * factor).quantize(
        Decimal("0.000001")
    )
    # Records on/after ex_date: factor = 1
    assert records[2].adj_nav == Decimal("0.960000")
    assert records[3].adj_nav == Decimal("0.970000")


@pytest.mark.asyncio
async def test_multiple_dividend_events(session: AsyncSession) -> None:
    """Multiple dividend events should compound the adjustment factor."""
    code = "TEST03"
    navs = [
        _nav(code, date(2023, 1, 2), Decimal("2.0000")),
        _nav(code, date(2023, 1, 3), Decimal("2.0500")),
        _nav(code, date(2023, 1, 4), Decimal("1.9500")),  # after first dividend
        _nav(code, date(2023, 1, 5), Decimal("2.0000")),
        _nav(code, date(2023, 1, 6), Decimal("1.9000")),  # after second dividend
        _nav(code, date(2023, 1, 9), Decimal("1.9200")),
    ]
    divs = [
        _div(code, date(2023, 1, 4), dividend_per_share=Decimal("0.10")),
        _div(code, date(2023, 1, 6), dividend_per_share=Decimal("0.10")),
    ]
    session.add_all(navs + divs)
    await session.flush()

    updated = await recalculate_adj_nav(session, code)
    assert updated > 0

    result = await session.execute(
        select(FundNav).where(FundNav.fund_code == code).order_by(FundNav.trade_date)
    )
    records = list(result.scalars().all())

    # Event 1: ex_date=2023-01-04, nav_before=2.0500 (on 2023-01-03)
    # factor1 = 1 / (1 - 0.10/2.0500)
    factor1 = Decimal("1") - Decimal("0.10") / Decimal("2.0500")

    # Event 2: ex_date=2023-01-06, nav_before=2.0000 (on 2023-01-05)
    # factor2 = 1 - 0.10/2.0000
    factor2 = Decimal("1") - Decimal("0.10") / Decimal("2.0000")

    # Records before both events: adj_nav = unit_nav * factor1 * factor2
    combined = factor1 * factor2
    assert records[0].adj_nav == (Decimal("2.0000") * combined).quantize(
        Decimal("0.000001")
    )
    assert records[1].adj_nav == (Decimal("2.0500") * combined).quantize(
        Decimal("0.000001")
    )

    # Records between event 1 and event 2: only factor2 applies
    assert records[2].adj_nav == (Decimal("1.9500") * factor2).quantize(
        Decimal("0.000001")
    )
    assert records[3].adj_nav == (Decimal("2.0000") * factor2).quantize(
        Decimal("0.000001")
    )

    # Records on/after event 2: no adjustment
    assert records[4].adj_nav == Decimal("1.900000")
    assert records[5].adj_nav == Decimal("1.920000")


@pytest.mark.asyncio
async def test_split_ratio_only(session: AsyncSession) -> None:
    """Split ratio without dividend should adjust pre-event adj_nav downward."""
    code = "TEST04"
    navs = [
        _nav(code, date(2023, 3, 1), Decimal("3.0000")),
        _nav(code, date(2023, 3, 2), Decimal("3.1000")),
        _nav(code, date(2023, 3, 3), Decimal("1.5500")),  # after 2:1 split
        _nav(code, date(2023, 3, 6), Decimal("1.5800")),
    ]
    # 2:1 split means split_ratio = 2
    div = _div(code, date(2023, 3, 3), split_ratio=Decimal("2"))
    session.add_all(navs + [div])
    await session.flush()

    updated = await recalculate_adj_nav(session, code)
    assert updated > 0

    result = await session.execute(
        select(FundNav).where(FundNav.fund_code == code).order_by(FundNav.trade_date)
    )
    records = list(result.scalars().all())

    # nav_before for ex_date 2023-03-03 is NAV on 2023-03-02 = 3.1000
    # factor = (1 - 0/3.1000) / 2 = 0.5
    factor = Decimal("0.5")

    assert records[0].adj_nav == (Decimal("3.0000") * factor).quantize(
        Decimal("0.000001")
    )
    assert records[1].adj_nav == (Decimal("3.1000") * factor).quantize(
        Decimal("0.000001")
    )
    # After split: no adjustment
    assert records[2].adj_nav == Decimal("1.550000")
    assert records[3].adj_nav == Decimal("1.580000")


@pytest.mark.asyncio
async def test_combined_dividend_and_split(session: AsyncSession) -> None:
    """Combined dividend + split event should apply both adjustments."""
    code = "TEST05"
    navs = [
        _nav(code, date(2023, 6, 1), Decimal("4.0000")),
        _nav(code, date(2023, 6, 2), Decimal("4.1000")),
        _nav(code, date(2023, 6, 5), Decimal("1.9500")),  # after div + split
        _nav(code, date(2023, 6, 6), Decimal("2.0000")),
    ]
    # Dividend 0.20 per share + 2:1 split
    div = _div(
        code,
        date(2023, 6, 5),
        dividend_per_share=Decimal("0.20"),
        split_ratio=Decimal("2"),
    )
    session.add_all(navs + [div])
    await session.flush()

    updated = await recalculate_adj_nav(session, code)
    assert updated > 0

    result = await session.execute(
        select(FundNav).where(FundNav.fund_code == code).order_by(FundNav.trade_date)
    )
    records = list(result.scalars().all())

    # nav_before = 4.1000 (on 2023-06-02)
    # factor = (1 - 0.20/4.1000) / 2
    nav_before = Decimal("4.1000")
    denom = Decimal("1") - Decimal("0.20") / nav_before
    factor = denom / Decimal("2")

    assert records[0].adj_nav == (Decimal("4.0000") * factor).quantize(
        Decimal("0.000001")
    )
    assert records[1].adj_nav == (Decimal("4.1000") * factor).quantize(
        Decimal("0.000001")
    )
    # After event: no adjustment
    assert records[2].adj_nav == Decimal("1.950000")
    assert records[3].adj_nav == Decimal("2.000000")


@pytest.mark.asyncio
async def test_dividend_on_first_trading_day(session: AsyncSession) -> None:
    """Dividend on first trading day: no nav_before available.

    When there's no prior NAV, the dividend adjustment should be skipped
    (only split_ratio applies if present).
    """
    code = "TEST06"
    navs = [
        _nav(code, date(2023, 1, 2), Decimal("1.0000")),
        _nav(code, date(2023, 1, 3), Decimal("1.0100")),
    ]
    # Dividend on the first trading day — no nav_before exists
    div = _div(code, date(2023, 1, 2), dividend_per_share=Decimal("0.05"))
    session.add_all(navs + [div])
    await session.flush()

    updated = await recalculate_adj_nav(session, code)

    # Since nav_before is None, the dividend event is skipped.
    # adj_nav remains equal to unit_nav, but daily_return is recalculated.
    assert updated == 1

    result = await session.execute(
        select(FundNav).where(FundNav.fund_code == code).order_by(FundNav.trade_date)
    )
    records = list(result.scalars().all())
    for r in records:
        assert r.adj_nav == r.unit_nav


@pytest.mark.asyncio
async def test_dividend_exceeds_nav(session: AsyncSession) -> None:
    """When dividend_per_share >= unit_nav, the event should be skipped.

    This prevents division by zero or negative adjustment factors.
    """
    code = "TEST07"
    navs = [
        _nav(code, date(2023, 2, 1), Decimal("0.5000")),
        _nav(code, date(2023, 2, 2), Decimal("0.5100")),
        _nav(code, date(2023, 2, 3), Decimal("0.4800")),
    ]
    # Dividend >= nav_before (0.5100): should be skipped
    div = _div(code, date(2023, 2, 3), dividend_per_share=Decimal("0.6000"))
    session.add_all(navs + [div])
    await session.flush()

    updated = await recalculate_adj_nav(session, code)

    # Event skipped for adj_nav, but daily_return is recalculated from adj_nav.
    assert updated == 2

    result = await session.execute(
        select(FundNav).where(FundNav.fund_code == code).order_by(FundNav.trade_date)
    )
    records = list(result.scalars().all())
    for r in records:
        assert r.adj_nav == r.unit_nav


@pytest.mark.asyncio
async def test_real_fund_dividend_scenario(session: AsyncSession) -> None:
    """Simulate a realistic fund dividend scenario with known expected values.

    Simulates a fund with:
    - Initial NAV of 1.2000 growing over time
    - A dividend of 0.03 per share on 2023-06-15
    - Verifies the forward-adjusted NAV maintains continuity

    The key property of forward-adjusted NAV is that the ratio between
    consecutive adj_nav values should reflect the true return (including
    dividends), not just the price change.
    """
    code = "REAL01"
    # Simulate ~10 trading days around a dividend event
    navs = [
        _nav(code, date(2023, 6, 12), Decimal("1.2000")),
        _nav(code, date(2023, 6, 13), Decimal("1.2050")),
        _nav(code, date(2023, 6, 14), Decimal("1.2100")),
        # Ex-date: 2023-06-15, dividend = 0.03
        _nav(code, date(2023, 6, 15), Decimal("1.1800")),
        _nav(code, date(2023, 6, 16), Decimal("1.1850")),
        _nav(code, date(2023, 6, 19), Decimal("1.1900")),
        _nav(code, date(2023, 6, 20), Decimal("1.1950")),
    ]
    div = _div(code, date(2023, 6, 15), dividend_per_share=Decimal("0.03"))
    session.add_all(navs + [div])
    await session.flush()

    updated = await recalculate_adj_nav(session, code)
    assert updated > 0

    result = await session.execute(
        select(FundNav).where(FundNav.fund_code == code).order_by(FundNav.trade_date)
    )
    records = list(result.scalars().all())

    # nav_before = 1.2100 (on 2023-06-14)
    # factor = 1 - 0.03/1.2100 = (1.2100 - 0.03) / 1.2100 = 1.1800 / 1.2100
    nav_before = Decimal("1.2100")
    factor = (nav_before - Decimal("0.03")) / nav_before

    # Verify pre-event records are adjusted
    for i in range(3):  # first 3 records are before ex_date
        expected = (navs[i].unit_nav * factor).quantize(Decimal("0.000001"))
        assert records[i].adj_nav == expected, (
            f"Record {i}: expected {expected}, got {records[i].adj_nav}"
        )

    # Verify post-event records are NOT adjusted (factor = 1)
    for i in range(3, 7):
        assert records[i].adj_nav == navs[i].unit_nav.quantize(Decimal("0.000001"))

    # Key property of forward-adjusted NAV: for dates within the same
    # adjustment regime (no event boundary between them), the adj_nav
    # ratio equals the unit_nav ratio (i.e., returns are preserved).
    # Check post-event period: records[4]/records[3] should equal
    # unit_nav[4]/unit_nav[3]
    adj_ratio_post = records[4].adj_nav / records[3].adj_nav
    nav_ratio_post = Decimal("1.1850") / Decimal("1.1800")
    assert abs(adj_ratio_post - nav_ratio_post) < Decimal("0.0001"), (
        f"adj_ratio_post={adj_ratio_post}, nav_ratio_post={nav_ratio_post}"
    )

    # Check pre-event period: records[1]/records[0] should equal
    # unit_nav[1]/unit_nav[0]
    adj_ratio_pre = records[1].adj_nav / records[0].adj_nav
    nav_ratio_pre = Decimal("1.2050") / Decimal("1.2000")
    assert abs(adj_ratio_pre - nav_ratio_pre) < Decimal("0.0001"), (
        f"adj_ratio_pre={adj_ratio_pre}, nav_ratio_pre={nav_ratio_pre}"
    )


@pytest.mark.asyncio
async def test_recalculate_daily_return_from_adj_nav(session: AsyncSession) -> None:
    """daily_return should be derived from adj_nav pct_change, not source raw return."""
    code = "RET01"
    navs = [
        _nav(code, date(2023, 1, 2), Decimal("1.0000")),
        _nav(code, date(2023, 1, 3), Decimal("1.0100")),
        _nav(code, date(2023, 1, 4), Decimal("0.9600")),
        _nav(code, date(2023, 1, 5), Decimal("0.9700")),
    ]
    # Simulate source-provided unit-NAV returns that would be wrong around ex-date.
    for nav in navs:
        nav.daily_return = Decimal("0.999999")
    div = _div(code, date(2023, 1, 4), dividend_per_share=Decimal("0.05"))
    session.add_all(navs + [div])
    await session.flush()

    updated = await recalculate_adj_nav(session, code)
    assert updated > 0

    result = await session.execute(
        select(FundNav).where(FundNav.fund_code == code).order_by(FundNav.trade_date)
    )
    records = list(result.scalars().all())

    assert records[0].daily_return is None
    for prev, curr in zip(records, records[1:]):
        expected = ((curr.adj_nav / prev.adj_nav) - Decimal("1")).quantize(
            Decimal("0.000001"), rounding=ROUND_HALF_UP
        )
        assert curr.daily_return == expected

    # On ex-date, total-return daily_return should be near zero in this synthetic
    # scenario, instead of the raw unit NAV drop from 1.0100 to 0.9600.
    assert records[2].daily_return == Decimal("0.000000")
