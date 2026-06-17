"""Validation result models used across all data validators.

These lightweight dataclasses carry validation outcomes without coupling
to any specific storage or alerting mechanism.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum


class ValidationSeverity(str, Enum):
    """Severity level for a validation issue."""

    WARNING = "warning"
    ERROR = "error"


class ValidationStatus(str, Enum):
    """Status assigned to a data record after validation."""

    NORMAL = "normal"
    SUSPECT = "suspect"


@dataclass(frozen=True)
class ValidationIssue:
    """A single validation issue found during data checking.

    Attributes:
        field: The field name that failed validation.
        message: Human-readable description of the issue.
        severity: Whether this is a warning or error.
        fund_code: Fund code related to this issue.
        trade_date: Date related to this issue, if applicable.
        value: The problematic value.
        threshold: The threshold that was exceeded, if applicable.
    """

    field: str
    message: str
    severity: ValidationSeverity = ValidationSeverity.ERROR
    fund_code: str = ""
    trade_date: date | None = None
    value: Decimal | float | str | None = None
    threshold: Decimal | float | None = None


@dataclass
class ValidationResult:
    """Aggregated result of a validation pass.

    Attributes:
        is_valid: True if no errors were found (warnings are acceptable).
        status: Recommended status for the data record.
        issues: List of all issues found.
    """

    is_valid: bool = True
    status: ValidationStatus = ValidationStatus.NORMAL
    issues: list[ValidationIssue] = field(default_factory=list)

    def add_issue(self, issue: ValidationIssue) -> None:
        """Add an issue and update validity/status accordingly."""
        self.issues.append(issue)
        if issue.severity == ValidationSeverity.ERROR:
            self.is_valid = False
            self.status = ValidationStatus.SUSPECT


@dataclass(frozen=True)
class DateGap:
    """Represents a gap in a date sequence.

    Attributes:
        fund_code: Fund code with the gap.
        gap_start: First missing date (inclusive).
        gap_end: Last missing date (inclusive).
        missing_count: Number of missing trading days.
    """

    fund_code: str
    gap_start: date
    gap_end: date
    missing_count: int


@dataclass(frozen=True)
class CrossSourceAlert:
    """Alert raised when cross-source data differs beyond threshold.

    Attributes:
        fund_code: Fund code with discrepancy.
        trade_date: Date of the discrepancy.
        field: Field name that differs.
        source_a: First source name.
        value_a: Value from first source.
        source_b: Second source name.
        value_b: Value from second source.
        difference: Absolute difference between values.
        threshold: The threshold that was exceeded.
    """

    fund_code: str
    trade_date: date
    field: str
    source_a: str
    value_a: Decimal
    source_b: str
    value_b: Decimal
    difference: Decimal
    threshold: Decimal
