"""Holdings data validator.

Validates holding snapshots against business rules:
- Total weight sum must be in [0, 1.10] (i.e. 0% to 110%, allowing leverage)
- Individual position weights must be non-negative

Requirement 2.3: holding weight sum validated in [0, 110%] interval.
"""

from __future__ import annotations

from decimal import Decimal

from app.data.schemas.funds import HoldingSnapshot
from app.data.validators.models import (
    ValidationIssue,
    ValidationResult,
    ValidationSeverity,
)

# Maximum allowed total weight (110% to allow for leveraged funds)
MAX_TOTAL_WEIGHT = Decimal("1.10")
MIN_TOTAL_WEIGHT = Decimal("0")


def validate_holding_snapshot(
    snapshot: HoldingSnapshot,
    max_weight: Decimal = MAX_TOTAL_WEIGHT,
    min_weight: Decimal = MIN_TOTAL_WEIGHT,
) -> ValidationResult:
    """Validate a holding snapshot.

    Checks that the sum of all position weights falls within [0, 110%].

    Args:
        snapshot: The holding snapshot to validate.
        max_weight: Maximum allowed total weight (default 1.10 = 110%).
        min_weight: Minimum allowed total weight (default 0).

    Returns:
        ValidationResult with issues found.
    """
    result = ValidationResult()

    if not snapshot.positions:
        # Empty positions are valid (fund may not have disclosed yet)
        return result

    # Calculate total weight
    total_weight = Decimal("0")
    for i, position in enumerate(snapshot.positions):
        if position.weight is not None:
            if position.weight < Decimal("0"):
                result.add_issue(
                    ValidationIssue(
                        field=f"positions[{i}].weight",
                        message=(
                            f"Position weight is negative: {position.weight} "
                            f"(stock: {position.stock_name or position.stock_code})"
                        ),
                        severity=ValidationSeverity.ERROR,
                        fund_code=snapshot.fund_code,
                        trade_date=snapshot.report_date,
                        value=position.weight,
                    )
                )
            total_weight += position.weight

    # Validate total weight range
    if total_weight > max_weight:
        result.add_issue(
            ValidationIssue(
                field="total_weight",
                message=(
                    f"Total holding weight {total_weight} exceeds "
                    f"maximum {max_weight} (={max_weight * 100}%)"
                ),
                severity=ValidationSeverity.ERROR,
                fund_code=snapshot.fund_code,
                trade_date=snapshot.report_date,
                value=total_weight,
                threshold=max_weight,
            )
        )

    if total_weight < min_weight:
        result.add_issue(
            ValidationIssue(
                field="total_weight",
                message=(
                    f"Total holding weight {total_weight} is below "
                    f"minimum {min_weight}"
                ),
                severity=ValidationSeverity.ERROR,
                fund_code=snapshot.fund_code,
                trade_date=snapshot.report_date,
                value=total_weight,
                threshold=min_weight,
            )
        )

    return result
