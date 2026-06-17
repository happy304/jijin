"""Cross-source data validator.

Compares NAV data from multiple sources for the same fund and date.
When differences exceed a threshold, an alert is generated.

Requirement 2.5: same-day multi-source comparison, alert on threshold breach.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from itertools import combinations
from typing import Any

from app.data.schemas.funds import NavRecord
from app.data.validators.models import CrossSourceAlert

# Default threshold for NAV difference (as a fraction of the value).
# 0.001 = 0.1% difference triggers an alert.
DEFAULT_NAV_THRESHOLD = Decimal("0.001")

# Default threshold for daily return difference (absolute).
# 0.005 = 0.5 percentage points difference triggers an alert.
DEFAULT_RETURN_THRESHOLD = Decimal("0.005")

# Aggregated Advisor/ingest guardrail thresholds.  A single bad print from
# one provider should not stop ingestion, but repeated same-day cross-source
# conflicts mean the point-in-time NAV cannot be trusted for trading advice.
DEFAULT_WARNING_ALERT_RATIO = Decimal("0.01")
DEFAULT_HARD_GATE_ALERT_RATIO = Decimal("0.03")
DEFAULT_HARD_GATE_MIN_ALERTS = 3


@dataclass
class CrossSourceNavDiagnostics:
    """Aggregated same-fund NAV consistency diagnostics across providers."""

    status: str = "insufficient_sources"  # pass/warning/fail/insufficient_sources
    hard_gate: bool = False
    provider_count: int = 0
    providers: list[str] = field(default_factory=list)
    compared_pairs: int = 0
    overlap_days: int = 0
    alert_count: int = 0
    alert_ratio: float = 0.0
    affected_dates: list[str] = field(default_factory=list)
    affected_fields: list[str] = field(default_factory=list)
    max_difference: float | None = None
    max_difference_field: str | None = None
    max_difference_date: str | None = None
    max_difference_sources: list[str] = field(default_factory=list)
    sample_alerts: list[dict[str, Any]] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "hard_gate": self.hard_gate,
            "provider_count": self.provider_count,
            "providers": self.providers,
            "compared_pairs": self.compared_pairs,
            "overlap_days": self.overlap_days,
            "alert_count": self.alert_count,
            "alert_ratio": round(float(self.alert_ratio), 4),
            "affected_dates": self.affected_dates[:20],
            "affected_fields": self.affected_fields,
            "max_difference": round(float(self.max_difference), 6) if self.max_difference is not None else None,
            "max_difference_field": self.max_difference_field,
            "max_difference_date": self.max_difference_date,
            "max_difference_sources": self.max_difference_sources,
            "sample_alerts": self.sample_alerts[:10],
            "errors": self.errors,
            "reason": self.reason,
        }


def _decimal_to_float(value: Decimal | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _alert_to_dict(alert: CrossSourceAlert) -> dict[str, Any]:
    return {
        "fund_code": alert.fund_code,
        "trade_date": alert.trade_date.isoformat(),
        "field": alert.field,
        "source_a": alert.source_a,
        "value_a": _decimal_to_float(alert.value_a),
        "source_b": alert.source_b,
        "value_b": _decimal_to_float(alert.value_b),
        "difference": _decimal_to_float(alert.difference),
        "threshold": _decimal_to_float(alert.threshold),
    }


def compare_nav_records(
    record_a: NavRecord,
    source_a: str,
    record_b: NavRecord,
    source_b: str,
    nav_threshold: Decimal = DEFAULT_NAV_THRESHOLD,
    return_threshold: Decimal = DEFAULT_RETURN_THRESHOLD,
) -> list[CrossSourceAlert]:
    """Compare two NAV records from different sources for the same fund/date.

    Args:
        record_a: NAV record from source A.
        source_a: Name of source A.
        record_b: NAV record from source B.
        source_b: Name of source B.
        nav_threshold: Relative threshold for NAV difference.
        return_threshold: Absolute threshold for daily return difference.

    Returns:
        List of CrossSourceAlert for fields that exceed thresholds.
    """
    alerts: list[CrossSourceAlert] = []

    # Validate that records are for the same fund and date
    if record_a.fund_code != record_b.fund_code:
        return alerts
    if record_a.trade_date != record_b.trade_date:
        return alerts

    fund_code = record_a.fund_code
    trade_date = record_a.trade_date

    # Compare unit_nav
    if record_a.unit_nav is not None and record_b.unit_nav is not None:
        alert = _compare_decimal_field(
            fund_code=fund_code,
            trade_date=trade_date,
            field="unit_nav",
            value_a=record_a.unit_nav,
            source_a=source_a,
            value_b=record_b.unit_nav,
            source_b=source_b,
            threshold=nav_threshold,
            relative=True,
        )
        if alert:
            alerts.append(alert)

    # Compare accum_nav
    if record_a.accum_nav is not None and record_b.accum_nav is not None:
        alert = _compare_decimal_field(
            fund_code=fund_code,
            trade_date=trade_date,
            field="accum_nav",
            value_a=record_a.accum_nav,
            source_a=source_a,
            value_b=record_b.accum_nav,
            source_b=source_b,
            threshold=nav_threshold,
            relative=True,
        )
        if alert:
            alerts.append(alert)

    # Compare daily_return (absolute difference)
    if record_a.daily_return is not None and record_b.daily_return is not None:
        alert = _compare_decimal_field(
            fund_code=fund_code,
            trade_date=trade_date,
            field="daily_return",
            value_a=record_a.daily_return,
            source_a=source_a,
            value_b=record_b.daily_return,
            source_b=source_b,
            threshold=return_threshold,
            relative=False,
        )
        if alert:
            alerts.append(alert)

    return alerts


def compare_nav_series(
    series_a: list[NavRecord],
    source_a: str,
    series_b: list[NavRecord],
    source_b: str,
    nav_threshold: Decimal = DEFAULT_NAV_THRESHOLD,
    return_threshold: Decimal = DEFAULT_RETURN_THRESHOLD,
) -> list[CrossSourceAlert]:
    """Compare two NAV series from different sources.

    Matches records by (fund_code, trade_date) and compares overlapping dates.

    Args:
        series_a: NAV records from source A.
        source_a: Name of source A.
        series_b: NAV records from source B.
        source_b: Name of source B.
        nav_threshold: Relative threshold for NAV difference.
        return_threshold: Absolute threshold for daily return difference.

    Returns:
        List of all CrossSourceAlert across the series.
    """
    # Index series_b by (fund_code, trade_date)
    index_b: dict[tuple[str, date], NavRecord] = {
        (r.fund_code, r.trade_date): r for r in series_b
    }

    alerts: list[CrossSourceAlert] = []

    for record_a in series_a:
        key = (record_a.fund_code, record_a.trade_date)
        record_b = index_b.get(key)
        if record_b is not None:
            alerts.extend(
                compare_nav_records(
                    record_a=record_a,
                    source_a=source_a,
                    record_b=record_b,
                    source_b=source_b,
                    nav_threshold=nav_threshold,
                    return_threshold=return_threshold,
                )
            )

    return alerts


def build_cross_source_nav_diagnostics(
    series_by_source: dict[str, list[NavRecord]],
    *,
    errors: dict[str, str] | None = None,
    nav_threshold: Decimal = DEFAULT_NAV_THRESHOLD,
    return_threshold: Decimal = DEFAULT_RETURN_THRESHOLD,
    warning_alert_ratio: Decimal = DEFAULT_WARNING_ALERT_RATIO,
    hard_gate_alert_ratio: Decimal = DEFAULT_HARD_GATE_ALERT_RATIO,
    hard_gate_min_alerts: int = DEFAULT_HARD_GATE_MIN_ALERTS,
) -> CrossSourceNavDiagnostics:
    """Build an aggregated hard-gate diagnostic across raw provider NAV series.

    The function intentionally treats one-off discrepancies as warnings and
    repeated same-day cross-source conflicts as a hard gate.  This keeps the
    ingestion path robust to isolated upstream glitches while preventing a
    conflicted NAV window from becoming the default Advisor data base.
    """
    clean_series = {
        str(source): list(records or [])
        for source, records in (series_by_source or {}).items()
        if source and records
    }
    providers = sorted(clean_series.keys())
    diagnostics = CrossSourceNavDiagnostics(
        provider_count=len(providers),
        providers=providers,
        errors={str(k): str(v) for k, v in (errors or {}).items()},
    )

    if len(providers) < 2:
        diagnostics.status = "insufficient_sources"
        diagnostics.reason = "可用 NAV 来源少于 2 个，无法执行同日跨源硬校验"
        return diagnostics

    all_alerts: list[CrossSourceAlert] = []
    overlap_keys: set[tuple[str, date, str, str]] = set()
    compared_pairs = 0
    for source_a, source_b in combinations(providers, 2):
        series_a = clean_series[source_a]
        series_b = clean_series[source_b]
        index_b = {(r.fund_code, r.trade_date): r for r in series_b}
        pair_overlap = 0
        for record_a in series_a:
            record_b = index_b.get((record_a.fund_code, record_a.trade_date))
            if record_b is None:
                continue
            pair_overlap += 1
            overlap_keys.add((record_a.fund_code, record_a.trade_date, source_a, source_b))
        if pair_overlap <= 0:
            continue
        compared_pairs += 1
        all_alerts.extend(
            compare_nav_series(
                series_a,
                source_a,
                series_b,
                source_b,
                nav_threshold=nav_threshold,
                return_threshold=return_threshold,
            )
        )

    diagnostics.compared_pairs = compared_pairs
    diagnostics.overlap_days = len(overlap_keys)
    diagnostics.alert_count = len(all_alerts)
    diagnostics.alert_ratio = float(
        Decimal(len(all_alerts)) / Decimal(max(1, diagnostics.overlap_days))
    )

    if all_alerts:
        affected_dates = sorted({alert.trade_date.isoformat() for alert in all_alerts})
        affected_fields = sorted({alert.field for alert in all_alerts})
        diagnostics.affected_dates = affected_dates[:20]
        diagnostics.affected_fields = affected_fields
        diagnostics.sample_alerts = [_alert_to_dict(alert) for alert in all_alerts[:10]]
        worst = max(all_alerts, key=lambda alert: alert.difference)
        diagnostics.max_difference = _decimal_to_float(worst.difference)
        diagnostics.max_difference_field = worst.field
        diagnostics.max_difference_date = worst.trade_date.isoformat()
        diagnostics.max_difference_sources = [worst.source_a, worst.source_b]

    if diagnostics.overlap_days <= 0:
        diagnostics.status = "insufficient_sources"
        diagnostics.reason = "多个 NAV 来源没有重叠交易日，无法执行同日跨源硬校验"
        return diagnostics

    alert_ratio_decimal = Decimal(str(round(diagnostics.alert_ratio, 6)))
    if (
        diagnostics.alert_count >= hard_gate_min_alerts
        and alert_ratio_decimal >= hard_gate_alert_ratio
    ):
        diagnostics.status = "fail"
        diagnostics.hard_gate = True
        diagnostics.reason = (
            f"跨源 NAV 冲突达到硬门禁：{diagnostics.alert_count} 个字段差异，"
            f"占重叠样本 {diagnostics.alert_ratio:.1%}"
        )
    elif diagnostics.alert_count > 0:
        diagnostics.status = "warning"
        diagnostics.reason = (
            f"检测到少量跨源 NAV 差异：{diagnostics.alert_count} 个字段差异，"
            f"占重叠样本 {diagnostics.alert_ratio:.1%}"
        )
        if alert_ratio_decimal >= warning_alert_ratio:
            diagnostics.reason += "，需持续观察来源一致性"
    else:
        diagnostics.status = "pass"
        diagnostics.reason = "跨源 NAV 同日对照未发现超阈值差异"

    return diagnostics


def _compare_decimal_field(
    *,
    fund_code: str,
    trade_date: date,
    field: str,
    value_a: Decimal,
    source_a: str,
    value_b: Decimal,
    source_b: str,
    threshold: Decimal,
    relative: bool,
) -> CrossSourceAlert | None:
    """Compare a single decimal field between two sources.

    Args:
        relative: If True, compute relative difference (|a-b|/max(|a|,|b|)).
                  If False, compute absolute difference |a-b|.

    Returns:
        CrossSourceAlert if threshold exceeded, None otherwise.
    """
    diff = abs(value_a - value_b)

    if relative:
        # Use max of absolute values as denominator to avoid division by zero
        denominator = max(abs(value_a), abs(value_b))
        if denominator == Decimal("0"):
            return None
        relative_diff = diff / denominator
        if relative_diff > threshold:
            return CrossSourceAlert(
                fund_code=fund_code,
                trade_date=trade_date,
                field=field,
                source_a=source_a,
                value_a=value_a,
                source_b=source_b,
                value_b=value_b,
                difference=relative_diff,
                threshold=threshold,
            )
    else:
        if diff > threshold:
            return CrossSourceAlert(
                fund_code=fund_code,
                trade_date=trade_date,
                field=field,
                source_a=source_a,
                value_a=value_a,
                source_b=source_b,
                value_b=value_b,
                difference=diff,
                threshold=threshold,
            )

    return None
