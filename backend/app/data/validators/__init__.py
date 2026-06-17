"""Data validators package.

Provides validation logic for fund data quality assurance:
- NAV validation (date, non-negative, daily return thresholds)
- Holding weight validation (total weight in [0, 110%])
- Date sequence gap detection
- Cross-source comparison and alerting

Requirement 2.1: NAV data validation
Requirement 2.2: Suspect marking on threshold breach
Requirement 2.3: Holding weight validation
Requirement 2.4: Date gap detection
Requirement 2.5: Cross-source comparison
"""

from app.data.validators.cross_source_validator import (
    compare_nav_records,
    compare_nav_series,
)
from app.data.validators.date_validator import (
    detect_date_gaps,
    validate_date_monotonicity,
)
from app.data.validators.holding_validator import validate_holding_snapshot
from app.data.validators.models import (
    CrossSourceAlert,
    DateGap,
    ValidationIssue,
    ValidationResult,
    ValidationSeverity,
    ValidationStatus,
)
from app.data.validators.nav_validator import (
    get_daily_return_threshold,
    validate_nav_record,
    validate_nav_series,
)

__all__ = [
    # NAV validation
    "validate_nav_record",
    "validate_nav_series",
    "get_daily_return_threshold",
    # Holding validation
    "validate_holding_snapshot",
    # Date validation
    "detect_date_gaps",
    "validate_date_monotonicity",
    # Cross-source validation
    "compare_nav_records",
    "compare_nav_series",
    # Models
    "CrossSourceAlert",
    "DateGap",
    "ValidationIssue",
    "ValidationResult",
    "ValidationSeverity",
    "ValidationStatus",
]
