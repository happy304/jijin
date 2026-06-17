"""Unit tests for date sequence validator.

Covers:
- Normal date sequences with no gaps
- Gap detection with various thresholds
- Weekend handling (Fri→Mon is not a gap)
- Long holiday handling
- Date monotonicity violations
- Edge cases: empty list, single date
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.data.validators.date_validator import (
    DEFAULT_MAX_GAP_DAYS,
    detect_date_gaps,
    validate_date_monotonicity,
)


# ---------------------------------------------------------------------------
# Tests: detect_date_gaps
# ---------------------------------------------------------------------------


class TestDetectDateGaps:
    def test_no_gaps_weekday_sequence(self):
        """Consecutive weekdays should have no gaps."""
        dates = [
            date(2024, 1, 15),  # Monday
            date(2024, 1, 16),  # Tuesday
            date(2024, 1, 17),  # Wednesday
            date(2024, 1, 18),  # Thursday
            date(2024, 1, 19),  # Friday
        ]
        gaps = detect_date_gaps("000001", dates)
        assert gaps == []

    def test_weekend_not_flagged(self):
        """Friday to Monday (3 days) should not be flagged with default threshold."""
        dates = [
            date(2024, 1, 19),  # Friday
            date(2024, 1, 22),  # Monday
        ]
        gaps = detect_date_gaps("000001", dates)
        assert gaps == []

    def test_long_gap_detected(self):
        """Gap of 10 days should be detected."""
        dates = [
            date(2024, 1, 15),
            date(2024, 1, 26),  # 11 days gap
        ]
        gaps = detect_date_gaps("000001", dates)
        assert len(gaps) == 1
        assert gaps[0].fund_code == "000001"
        assert gaps[0].gap_start == date(2024, 1, 16)
        assert gaps[0].gap_end == date(2024, 1, 25)
        assert gaps[0].missing_count > 0

    def test_spring_festival_gap(self):
        """Spring Festival (typically 7-9 days) should be detected with default threshold."""
        dates = [
            date(2024, 2, 8),   # Before Spring Festival
            date(2024, 2, 19),  # After Spring Festival (11 days gap)
        ]
        gaps = detect_date_gaps("000001", dates)
        assert len(gaps) == 1

    def test_custom_threshold(self):
        """Custom max_gap_days should be respected."""
        dates = [
            date(2024, 1, 15),
            date(2024, 1, 19),  # 4 days gap
        ]
        # With threshold 3, this should be flagged
        gaps = detect_date_gaps("000001", dates, max_gap_days=3)
        assert len(gaps) == 1

        # With threshold 5, this should not be flagged
        gaps = detect_date_gaps("000001", dates, max_gap_days=5)
        assert len(gaps) == 0

    def test_multiple_gaps(self):
        """Multiple gaps in a sequence should all be detected."""
        dates = [
            date(2024, 1, 5),
            date(2024, 1, 15),  # 10 days gap
            date(2024, 1, 16),
            date(2024, 1, 30),  # 14 days gap
        ]
        gaps = detect_date_gaps("000001", dates)
        assert len(gaps) == 2

    def test_empty_list(self):
        gaps = detect_date_gaps("000001", [])
        assert gaps == []

    def test_single_date(self):
        gaps = detect_date_gaps("000001", [date(2024, 1, 15)])
        assert gaps == []

    def test_gap_missing_count_excludes_weekends(self):
        """Missing count should estimate trading days (exclude weekends)."""
        dates = [
            date(2024, 1, 12),  # Friday
            date(2024, 1, 22),  # Monday (10 days gap, but only 6 trading days missing)
        ]
        gaps = detect_date_gaps("000001", dates)
        assert len(gaps) == 1
        # Gap is Jan 13 (Sat) to Jan 21 (Sun), trading days: Mon-Fri = 5
        # Jan 15, 16, 17, 18, 19 = 5 trading days
        assert gaps[0].missing_count == 5


# ---------------------------------------------------------------------------
# Tests: validate_date_monotonicity
# ---------------------------------------------------------------------------


class TestValidateDateMonotonicity:
    def test_monotonic_sequence(self):
        dates = [date(2024, 1, 15), date(2024, 1, 16), date(2024, 1, 17)]
        violations = validate_date_monotonicity("000001", dates)
        assert violations == []

    def test_duplicate_date(self):
        dates = [date(2024, 1, 15), date(2024, 1, 15), date(2024, 1, 16)]
        violations = validate_date_monotonicity("000001", dates)
        assert date(2024, 1, 15) in violations

    def test_out_of_order(self):
        dates = [date(2024, 1, 15), date(2024, 1, 17), date(2024, 1, 16)]
        violations = validate_date_monotonicity("000001", dates)
        assert date(2024, 1, 16) in violations

    def test_empty_list(self):
        violations = validate_date_monotonicity("000001", [])
        assert violations == []

    def test_single_date(self):
        violations = validate_date_monotonicity("000001", [date(2024, 1, 15)])
        assert violations == []

    def test_multiple_violations(self):
        dates = [
            date(2024, 1, 17),
            date(2024, 1, 15),  # violation
            date(2024, 1, 14),  # violation
            date(2024, 1, 20),
        ]
        violations = validate_date_monotonicity("000001", dates)
        assert len(violations) == 2


# ---------------------------------------------------------------------------
# Tests: additional edge cases and abnormal scenarios
# ---------------------------------------------------------------------------


class TestDateValidatorEdgeCases:
    """Additional edge case tests for comprehensive abnormal scenario coverage."""

    def test_two_consecutive_same_dates(self):
        """Two identical consecutive dates should be a monotonicity violation."""
        dates = [date(2024, 1, 15), date(2024, 1, 15)]
        violations = validate_date_monotonicity("000001", dates)
        assert len(violations) == 1

    def test_completely_reversed_sequence(self):
        """Fully reversed date sequence should have N-1 violations."""
        dates = [
            date(2024, 1, 20),
            date(2024, 1, 19),
            date(2024, 1, 18),
            date(2024, 1, 17),
        ]
        violations = validate_date_monotonicity("000001", dates)
        assert len(violations) == 3

    def test_very_large_gap(self):
        """Gap of several months should be detected."""
        dates = [date(2024, 1, 15), date(2024, 6, 15)]  # ~150 days
        gaps = detect_date_gaps("000001", dates)
        assert len(gaps) == 1
        assert gaps[0].missing_count > 50

    def test_gap_exactly_at_threshold(self):
        """Gap exactly at max_gap_days should NOT be flagged (not strictly greater)."""
        dates = [date(2024, 1, 15), date(2024, 1, 20)]  # 5 days gap
        gaps = detect_date_gaps("000001", dates, max_gap_days=5)
        assert len(gaps) == 0

    def test_gap_one_over_threshold(self):
        """Gap one day over max_gap_days should be flagged."""
        dates = [date(2024, 1, 15), date(2024, 1, 21)]  # 6 days gap
        gaps = detect_date_gaps("000001", dates, max_gap_days=5)
        assert len(gaps) == 1

    def test_national_day_holiday(self):
        """National Day holiday (Oct 1-7) gap should be detected with default threshold."""
        dates = [
            date(2024, 9, 30),  # Before National Day
            date(2024, 10, 8),  # After National Day (8 days gap)
        ]
        gaps = detect_date_gaps("000001", dates)
        assert len(gaps) == 1

    def test_max_gap_days_one(self):
        """With max_gap_days=1, any non-consecutive dates should be flagged."""
        dates = [date(2024, 1, 15), date(2024, 1, 17)]  # 2 days gap
        gaps = detect_date_gaps("000001", dates, max_gap_days=1)
        assert len(gaps) == 1

    def test_three_dates_two_gaps(self):
        """Three dates with gaps between each pair."""
        dates = [
            date(2024, 1, 1),
            date(2024, 1, 15),  # 14 days gap
            date(2024, 2, 1),   # 17 days gap
        ]
        gaps = detect_date_gaps("000001", dates)
        assert len(gaps) == 2

    def test_gap_start_and_end_correct(self):
        """Verify gap_start and gap_end are correctly computed."""
        dates = [date(2024, 1, 10), date(2024, 1, 20)]
        gaps = detect_date_gaps("000001", dates)
        assert len(gaps) == 1
        assert gaps[0].gap_start == date(2024, 1, 11)
        assert gaps[0].gap_end == date(2024, 1, 19)

    def test_monotonicity_with_same_date_multiple_times(self):
        """Multiple occurrences of same date should all be violations."""
        dates = [
            date(2024, 1, 15),
            date(2024, 1, 15),
            date(2024, 1, 15),
        ]
        violations = validate_date_monotonicity("000001", dates)
        assert len(violations) == 2
