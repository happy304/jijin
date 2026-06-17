"""Date sequence validator.

Detects gaps in date sequences for fund NAV data. Records missing
intervals so they can be prioritized for backfill.

Requirement 2.4: detect date sequence gaps, record missing intervals.

Note: This validator uses a simple heuristic — gaps of more than
`max_gap_days` calendar days between consecutive records are flagged.
A more sophisticated approach would use a trading calendar, but for
the initial implementation we use a configurable threshold.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.data.validators.models import DateGap

# Default maximum allowed gap between consecutive trading days.
# Weekends = 2 days gap (Fri→Mon), so 3 calendar days is normal.
# Long holidays (Spring Festival, National Day) can be up to 9 days.
# We use 5 as default to catch most gaps while allowing normal weekends.
DEFAULT_MAX_GAP_DAYS = 5


def detect_date_gaps(
    fund_code: str,
    dates: list[date],
    max_gap_days: int = DEFAULT_MAX_GAP_DAYS,
) -> list[DateGap]:
    """Detect gaps in a sorted date sequence.

    Args:
        fund_code: Fund code for the date series.
        dates: List of dates sorted ascending. Must not contain duplicates.
        max_gap_days: Maximum allowed calendar days between consecutive
            dates before flagging as a gap.

    Returns:
        List of DateGap objects representing detected gaps.
    """
    if len(dates) < 2:
        return []

    gaps: list[DateGap] = []

    for i in range(1, len(dates)):
        prev_date = dates[i - 1]
        curr_date = dates[i]
        delta_days = (curr_date - prev_date).days

        if delta_days > max_gap_days:
            # The gap starts the day after prev_date and ends the day before curr_date
            gap_start = prev_date + timedelta(days=1)
            gap_end = curr_date - timedelta(days=1)
            # Estimate missing trading days (rough: exclude weekends)
            missing_count = _estimate_trading_days(gap_start, gap_end)
            gaps.append(
                DateGap(
                    fund_code=fund_code,
                    gap_start=gap_start,
                    gap_end=gap_end,
                    missing_count=missing_count,
                )
            )

    return gaps


def validate_date_monotonicity(
    fund_code: str,
    dates: list[date],
) -> list[date]:
    """Check that dates are strictly monotonically increasing.

    Args:
        fund_code: Fund code for context.
        dates: List of dates to check.

    Returns:
        List of dates that violate monotonicity (duplicates or out-of-order).
    """
    violations: list[date] = []

    for i in range(1, len(dates)):
        if dates[i] <= dates[i - 1]:
            violations.append(dates[i])

    return violations


def _estimate_trading_days(start: date, end: date) -> int:
    """Estimate the number of trading days in a date range.

    Uses a simple heuristic: exclude weekends (Saturday=5, Sunday=6).
    Does not account for public holidays.
    """
    if start > end:
        return 0

    count = 0
    current = start
    while current <= end:
        # weekday(): Monday=0 ... Sunday=6
        if current.weekday() < 5:
            count += 1
        current += timedelta(days=1)

    return count
