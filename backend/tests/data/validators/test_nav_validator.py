"""Unit tests for NAV data validator.

Covers:
- Valid NAV records pass validation
- Negative NAV detected
- Daily return exceeding threshold by fund type
- NAV deviation from previous day exceeding threshold
- Series validation with automatic previous-day tracking
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.data.schemas.funds import FundType, NavRecord
from app.data.validators.nav_validator import (
    DAILY_RETURN_THRESHOLDS,
    get_daily_return_threshold,
    validate_nav_record,
    validate_nav_series,
)
from app.data.validators.models import ValidationSeverity, ValidationStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_nav(
    fund_code: str = "000001",
    trade_date: date = date(2024, 1, 15),
    unit_nav: Decimal | None = Decimal("1.5000"),
    accum_nav: Decimal | None = Decimal("2.3000"),
    daily_return: Decimal | None = Decimal("0.0100"),
    source: str = "eastmoney",
) -> NavRecord:
    # Use model_construct to bypass Pydantic validation so we can test
    # the validator's own checks against invalid data (e.g. negative NAV
    # that might come from raw DB reads or corrupted external sources).
    return NavRecord.model_construct(
        fund_code=fund_code,
        trade_date=trade_date,
        unit_nav=unit_nav,
        accum_nav=accum_nav,
        daily_return=daily_return,
        source=source,
        adj_nav=None,
        status="normal",
        created_at=None,
    )


# ---------------------------------------------------------------------------
# Tests: get_daily_return_threshold
# ---------------------------------------------------------------------------


class TestGetDailyReturnThreshold:
    def test_stock_fund_threshold(self):
        assert get_daily_return_threshold(FundType.STOCK) == Decimal("0.15")

    def test_bond_fund_threshold(self):
        assert get_daily_return_threshold(FundType.BOND) == Decimal("0.05")

    def test_money_fund_threshold(self):
        assert get_daily_return_threshold(FundType.MONEY) == Decimal("0.01")

    def test_none_type_uses_default(self):
        assert get_daily_return_threshold(None) == Decimal("0.15")


# ---------------------------------------------------------------------------
# Tests: validate_nav_record - valid cases
# ---------------------------------------------------------------------------


class TestValidateNavRecordValid:
    def test_normal_record_passes(self):
        record = _make_nav()
        result = validate_nav_record(record, fund_type=FundType.STOCK)
        assert result.is_valid is True
        assert result.status == ValidationStatus.NORMAL
        assert len(result.issues) == 0

    def test_zero_nav_is_valid(self):
        record = _make_nav(unit_nav=Decimal("0"))
        result = validate_nav_record(record, fund_type=FundType.STOCK)
        assert result.is_valid is True

    def test_none_nav_is_valid(self):
        """None NAV (e.g. suspended fund) should not trigger error."""
        record = _make_nav(unit_nav=None, daily_return=None)
        result = validate_nav_record(record, fund_type=FundType.STOCK)
        assert result.is_valid is True

    def test_daily_return_at_threshold_boundary(self):
        """Exactly at threshold should pass (not strictly greater)."""
        record = _make_nav(daily_return=Decimal("0.15"))
        result = validate_nav_record(record, fund_type=FundType.STOCK)
        assert result.is_valid is True

    def test_negative_daily_return_within_threshold(self):
        record = _make_nav(daily_return=Decimal("-0.10"))
        result = validate_nav_record(record, fund_type=FundType.STOCK)
        assert result.is_valid is True


# ---------------------------------------------------------------------------
# Tests: validate_nav_record - invalid cases
# ---------------------------------------------------------------------------


class TestValidateNavRecordInvalid:
    def test_negative_unit_nav(self):
        record = _make_nav(unit_nav=Decimal("-0.5"))
        result = validate_nav_record(record, fund_type=FundType.STOCK)
        assert result.is_valid is False
        assert result.status == ValidationStatus.SUSPECT
        assert any(i.field == "unit_nav" for i in result.issues)

    def test_negative_accum_nav(self):
        record = _make_nav(accum_nav=Decimal("-1.0"))
        result = validate_nav_record(record, fund_type=FundType.STOCK)
        assert result.is_valid is False
        assert any(i.field == "accum_nav" for i in result.issues)

    def test_daily_return_exceeds_stock_threshold(self):
        """Stock fund with >15% daily return should fail."""
        record = _make_nav(daily_return=Decimal("0.20"))
        result = validate_nav_record(record, fund_type=FundType.STOCK)
        assert result.is_valid is False
        assert result.issues[0].field == "daily_return"
        assert result.issues[0].threshold == Decimal("0.15")

    def test_daily_return_exceeds_money_threshold(self):
        """Money fund with >1% daily return should fail."""
        record = _make_nav(daily_return=Decimal("0.02"))
        result = validate_nav_record(record, fund_type=FundType.MONEY)
        assert result.is_valid is False
        assert result.issues[0].threshold == Decimal("0.01")

    def test_daily_return_exceeds_bond_threshold(self):
        """Bond fund with >5% daily return should fail."""
        record = _make_nav(daily_return=Decimal("0.06"))
        result = validate_nav_record(record, fund_type=FundType.BOND)
        assert result.is_valid is False
        assert result.issues[0].threshold == Decimal("0.05")

    def test_negative_daily_return_exceeds_threshold(self):
        """Large negative return should also fail."""
        record = _make_nav(daily_return=Decimal("-0.20"))
        result = validate_nav_record(record, fund_type=FundType.STOCK)
        assert result.is_valid is False

    def test_nav_deviation_from_previous(self):
        """NAV jump >15% from previous day should be suspect."""
        record = _make_nav(unit_nav=Decimal("2.0"), daily_return=Decimal("0.01"))
        previous_nav = Decimal("1.5")  # 33% jump
        result = validate_nav_record(
            record, fund_type=FundType.STOCK, previous_nav=previous_nav
        )
        assert result.is_valid is False
        assert any("deviation" in i.message.lower() for i in result.issues)

    def test_nav_deviation_within_threshold(self):
        """Small NAV change from previous day should pass."""
        record = _make_nav(unit_nav=Decimal("1.52"), daily_return=Decimal("0.01"))
        previous_nav = Decimal("1.50")  # ~1.3% change
        result = validate_nav_record(
            record, fund_type=FundType.STOCK, previous_nav=previous_nav
        )
        assert result.is_valid is True

    def test_multiple_issues_accumulated(self):
        """Record with multiple problems should report all issues."""
        record = _make_nav(
            unit_nav=Decimal("-1.0"),
            accum_nav=Decimal("-2.0"),
            daily_return=Decimal("0.50"),
        )
        result = validate_nav_record(record, fund_type=FundType.STOCK)
        assert result.is_valid is False
        assert len(result.issues) >= 3


# ---------------------------------------------------------------------------
# Tests: validate_nav_series
# ---------------------------------------------------------------------------


class TestValidateNavSeries:
    def test_valid_series(self):
        records = [
            _make_nav(trade_date=date(2024, 1, 15), unit_nav=Decimal("1.50")),
            _make_nav(trade_date=date(2024, 1, 16), unit_nav=Decimal("1.52")),
            _make_nav(trade_date=date(2024, 1, 17), unit_nav=Decimal("1.51")),
        ]
        results = validate_nav_series(records, fund_type=FundType.STOCK)
        assert all(r.is_valid for r in results)

    def test_series_detects_jump(self):
        """A sudden NAV jump in the series should be caught."""
        records = [
            _make_nav(trade_date=date(2024, 1, 15), unit_nav=Decimal("1.50")),
            _make_nav(trade_date=date(2024, 1, 16), unit_nav=Decimal("1.52")),
            _make_nav(
                trade_date=date(2024, 1, 17),
                unit_nav=Decimal("2.50"),  # ~64% jump
                daily_return=Decimal("0.01"),
            ),
        ]
        results = validate_nav_series(records, fund_type=FundType.STOCK)
        assert results[0].is_valid is True
        assert results[1].is_valid is True
        assert results[2].is_valid is False

    def test_series_skips_invalid_for_previous(self):
        """Invalid record's NAV should not be used as previous for next."""
        records = [
            _make_nav(trade_date=date(2024, 1, 15), unit_nav=Decimal("1.50")),
            _make_nav(
                trade_date=date(2024, 1, 16),
                unit_nav=Decimal("3.00"),  # 100% jump - invalid
                daily_return=Decimal("0.01"),
            ),
            _make_nav(
                trade_date=date(2024, 1, 17),
                unit_nav=Decimal("1.52"),  # compared to 1.50, not 3.00
                daily_return=Decimal("0.01"),
            ),
        ]
        results = validate_nav_series(records, fund_type=FundType.STOCK)
        assert results[1].is_valid is False
        # Third record compared against first (1.50), ~1.3% change - valid
        assert results[2].is_valid is True

    def test_empty_series(self):
        results = validate_nav_series([], fund_type=FundType.STOCK)
        assert results == []

    def test_single_record_series(self):
        records = [_make_nav()]
        results = validate_nav_series(records, fund_type=FundType.STOCK)
        assert len(results) == 1
        assert results[0].is_valid is True


# ---------------------------------------------------------------------------
# Tests: additional edge cases and abnormal scenarios
# ---------------------------------------------------------------------------


class TestNavValidatorEdgeCases:
    """Additional edge case tests for comprehensive abnormal scenario coverage."""

    def test_missing_trade_date_none(self):
        """Record with trade_date=None should be flagged as error."""
        record = _make_nav(trade_date=None)
        result = validate_nav_record(record, fund_type=FundType.STOCK)
        assert result.is_valid is False
        assert any(i.field == "trade_date" for i in result.issues)

    def test_fof_fund_threshold(self):
        """FOF fund has 10% threshold - return of 11% should fail."""
        record = _make_nav(daily_return=Decimal("0.11"))
        result = validate_nav_record(record, fund_type=FundType.FOF)
        assert result.is_valid is False
        assert result.issues[0].threshold == Decimal("0.10")

    def test_fof_fund_within_threshold(self):
        """FOF fund with 9% return should pass."""
        record = _make_nav(daily_return=Decimal("0.09"))
        result = validate_nav_record(record, fund_type=FundType.FOF)
        assert result.is_valid is True

    def test_qdii_fund_threshold(self):
        """QDII fund has 15% threshold - return of 16% should fail."""
        record = _make_nav(daily_return=Decimal("0.16"))
        result = validate_nav_record(record, fund_type=FundType.QDII)
        assert result.is_valid is False
        assert result.issues[0].threshold == Decimal("0.15")

    def test_index_fund_threshold(self):
        """Index fund has 15% threshold - return of 16% should fail."""
        record = _make_nav(daily_return=Decimal("0.16"))
        result = validate_nav_record(record, fund_type=FundType.INDEX)
        assert result.is_valid is False

    def test_previous_nav_zero_no_division_error(self):
        """Previous NAV of zero should not cause division by zero."""
        record = _make_nav(unit_nav=Decimal("1.50"))
        result = validate_nav_record(
            record, fund_type=FundType.STOCK, previous_nav=Decimal("0")
        )
        # Should not crash; zero previous_nav means deviation check is skipped
        assert result.is_valid is True

    def test_very_large_nav_value(self):
        """Very large NAV (e.g. 999) should still pass if no deviation issue."""
        record = _make_nav(unit_nav=Decimal("999.9999"), daily_return=Decimal("0.01"))
        result = validate_nav_record(record, fund_type=FundType.STOCK)
        assert result.is_valid is True

    def test_very_small_positive_nav(self):
        """Very small positive NAV (e.g. 0.0001) should pass."""
        record = _make_nav(unit_nav=Decimal("0.0001"), daily_return=Decimal("0.001"))
        result = validate_nav_record(record, fund_type=FundType.STOCK)
        assert result.is_valid is True

    def test_daily_return_exactly_negative_threshold(self):
        """Daily return exactly at negative threshold boundary should pass."""
        record = _make_nav(daily_return=Decimal("-0.15"))
        result = validate_nav_record(record, fund_type=FundType.STOCK)
        assert result.is_valid is True

    def test_money_fund_small_deviation_fails(self):
        """Money fund with 1.5% deviation should fail (threshold is 1%)."""
        record = _make_nav(unit_nav=Decimal("1.015"), daily_return=Decimal("0.005"))
        previous_nav = Decimal("1.000")
        result = validate_nav_record(
            record, fund_type=FundType.MONEY, previous_nav=previous_nav
        )
        assert result.is_valid is False

    def test_bond_fund_moderate_deviation_fails(self):
        """Bond fund with 6% deviation should fail (threshold is 5%)."""
        record = _make_nav(unit_nav=Decimal("1.06"), daily_return=Decimal("0.01"))
        previous_nav = Decimal("1.000")
        result = validate_nav_record(
            record, fund_type=FundType.BOND, previous_nav=previous_nav
        )
        assert result.is_valid is False

    def test_suspect_status_on_failure(self):
        """Failed validation should set status to SUSPECT."""
        record = _make_nav(unit_nav=Decimal("-1.0"))
        result = validate_nav_record(record, fund_type=FundType.STOCK)
        assert result.status == ValidationStatus.SUSPECT

    def test_series_all_invalid(self):
        """Series where all records have issues."""
        records = [
            _make_nav(
                trade_date=date(2024, 1, 15),
                unit_nav=Decimal("-1.0"),
            ),
            _make_nav(
                trade_date=date(2024, 1, 16),
                unit_nav=Decimal("-2.0"),
            ),
        ]
        results = validate_nav_series(records, fund_type=FundType.STOCK)
        assert all(not r.is_valid for r in results)

    def test_series_with_none_nav_records(self):
        """Series with None NAV records (suspended fund days)."""
        records = [
            _make_nav(trade_date=date(2024, 1, 15), unit_nav=Decimal("1.50")),
            _make_nav(trade_date=date(2024, 1, 16), unit_nav=None, daily_return=None),
            _make_nav(
                trade_date=date(2024, 1, 17),
                unit_nav=Decimal("1.52"),
                daily_return=Decimal("0.01"),
            ),
        ]
        results = validate_nav_series(records, fund_type=FundType.STOCK)
        # All should be valid (None NAV is acceptable for suspended days)
        assert results[0].is_valid is True
        assert results[1].is_valid is True
        assert results[2].is_valid is True
