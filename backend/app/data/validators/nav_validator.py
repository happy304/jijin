"""NAV data validator.

Validates net asset value records against business rules:
- Date format correctness (handled by Pydantic, but we check for None)
- NAV non-negative
- Daily return within type-specific thresholds
- Deviation from previous trading day within threshold → mark suspect

Requirement 2.1: date format, NAV non-negative, daily return ±15% (money/bond separate)
Requirement 2.2: deviation exceeding threshold → mark suspect, preserve history
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.data.schemas.funds import FundType, NavRecord
from app.data.validators.models import (
    ValidationIssue,
    ValidationResult,
    ValidationSeverity,
    ValidationStatus,
)

# ---------------------------------------------------------------------------
# Daily return thresholds by fund type (as decimal fractions)
# e.g. 0.15 means ±15%
# ---------------------------------------------------------------------------

DAILY_RETURN_THRESHOLDS: dict[FundType | None, Decimal] = {
    FundType.STOCK: Decimal("0.15"),
    FundType.INDEX: Decimal("0.15"),
    FundType.MIXED: Decimal("0.15"),
    FundType.QDII: Decimal("0.20"),  # QDII 投资海外市场，无涨跌停限制
    FundType.FOF: Decimal("0.10"),
    FundType.BOND: Decimal("0.05"),
    FundType.MONEY: Decimal("0.01"),
    None: Decimal("0.15"),  # default for unknown type
}


def get_daily_return_threshold(fund_type: FundType | None) -> Decimal:
    """Get the daily return threshold for a given fund type."""
    return DAILY_RETURN_THRESHOLDS.get(fund_type, Decimal("0.15"))


def validate_nav_record(
    record: NavRecord,
    fund_type: FundType | None = None,
    previous_nav: Decimal | None = None,
) -> ValidationResult:
    """Validate a single NAV record.

    Args:
        record: The NAV record to validate.
        fund_type: Fund type for threshold selection.
        previous_nav: Previous trading day's unit_nav for deviation check.

    Returns:
        ValidationResult with issues found.
    """
    result = ValidationResult()

    # 1. Date must not be None
    if record.trade_date is None:
        result.add_issue(
            ValidationIssue(
                field="trade_date",
                message="Trade date is missing",
                severity=ValidationSeverity.ERROR,
                fund_code=record.fund_code,
            )
        )

    # 2. NAV non-negative
    if record.unit_nav is not None and record.unit_nav < Decimal("0"):
        result.add_issue(
            ValidationIssue(
                field="unit_nav",
                message=f"Unit NAV is negative: {record.unit_nav}",
                severity=ValidationSeverity.ERROR,
                fund_code=record.fund_code,
                trade_date=record.trade_date,
                value=record.unit_nav,
            )
        )

    if record.accum_nav is not None and record.accum_nav < Decimal("0"):
        result.add_issue(
            ValidationIssue(
                field="accum_nav",
                message=f"Accumulated NAV is negative: {record.accum_nav}",
                severity=ValidationSeverity.ERROR,
                fund_code=record.fund_code,
                trade_date=record.trade_date,
                value=record.accum_nav,
            )
        )

    # 3. Daily return within threshold
    threshold = get_daily_return_threshold(fund_type)

    if record.daily_return is not None:
        if abs(record.daily_return) > threshold:
            result.add_issue(
                ValidationIssue(
                    field="daily_return",
                    message=(
                        f"Daily return {record.daily_return} exceeds "
                        f"threshold ±{threshold} for fund type {fund_type}"
                    ),
                    severity=ValidationSeverity.ERROR,
                    fund_code=record.fund_code,
                    trade_date=record.trade_date,
                    value=record.daily_return,
                    threshold=threshold,
                )
            )

    # 4. Deviation from previous NAV (Requirement 2.2)
    if previous_nav is not None and record.unit_nav is not None and previous_nav > Decimal("0"):
        deviation = (record.unit_nav - previous_nav) / previous_nav
        if abs(deviation) > threshold:
            result.add_issue(
                ValidationIssue(
                    field="unit_nav",
                    message=(
                        f"NAV deviation {deviation:.6f} from previous day "
                        f"exceeds threshold ±{threshold} for fund type {fund_type}. "
                        f"Marking as suspect, preserving historical value."
                    ),
                    severity=ValidationSeverity.ERROR,
                    fund_code=record.fund_code,
                    trade_date=record.trade_date,
                    value=deviation,
                    threshold=threshold,
                )
            )

    return result


def validate_nav_series(
    records: list[NavRecord],
    fund_type: FundType | None = None,
) -> list[ValidationResult]:
    """Validate a series of NAV records in chronological order.

    Automatically computes deviation from previous record's unit_nav.

    Args:
        records: NAV records sorted by trade_date ascending.
        fund_type: Fund type for threshold selection.

    Returns:
        List of ValidationResult, one per record.
    """
    results: list[ValidationResult] = []
    previous_nav: Decimal | None = None

    for record in records:
        result = validate_nav_record(record, fund_type=fund_type, previous_nav=previous_nav)
        results.append(result)

        # Update previous_nav only if current record is valid
        if result.is_valid and record.unit_nav is not None:
            previous_nav = record.unit_nav

    return results
