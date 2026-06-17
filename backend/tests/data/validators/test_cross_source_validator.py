"""Unit tests for cross-source data validator.

Covers:
- Matching records with no difference
- NAV difference exceeding threshold
- Daily return difference exceeding threshold
- Series comparison with partial overlap
- Edge cases: different fund codes, different dates, None values
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.data.schemas.funds import NavRecord
from app.data.validators.cross_source_validator import (
    DEFAULT_NAV_THRESHOLD,
    DEFAULT_RETURN_THRESHOLD,
    build_cross_source_nav_diagnostics,
    compare_nav_records,
    compare_nav_series,
)


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
    return NavRecord(
        fund_code=fund_code,
        trade_date=trade_date,
        unit_nav=unit_nav,
        accum_nav=accum_nav,
        daily_return=daily_return,
        source=source,
    )


# ---------------------------------------------------------------------------
# Tests: compare_nav_records
# ---------------------------------------------------------------------------


class TestCompareNavRecords:
    def test_identical_records_no_alerts(self):
        record_a = _make_nav(source="eastmoney")
        record_b = _make_nav(source="akshare")
        alerts = compare_nav_records(record_a, "eastmoney", record_b, "akshare")
        assert alerts == []

    def test_small_nav_difference_no_alert(self):
        """Difference within threshold should not trigger alert."""
        record_a = _make_nav(unit_nav=Decimal("1.5000"))
        record_b = _make_nav(unit_nav=Decimal("1.5001"))  # 0.0067% diff
        alerts = compare_nav_records(record_a, "eastmoney", record_b, "akshare")
        assert alerts == []

    def test_large_nav_difference_triggers_alert(self):
        """Difference exceeding threshold should trigger alert."""
        record_a = _make_nav(unit_nav=Decimal("1.5000"))
        record_b = _make_nav(unit_nav=Decimal("1.5100"))  # 0.67% diff > 0.1%
        alerts = compare_nav_records(record_a, "eastmoney", record_b, "akshare")
        assert len(alerts) == 1
        alert = alerts[0]
        assert alert.fund_code == "000001"
        assert alert.trade_date == date(2024, 1, 15)
        assert alert.field == "unit_nav"
        assert alert.source_a == "eastmoney"
        assert alert.source_b == "akshare"
        assert alert.difference > DEFAULT_NAV_THRESHOLD

    def test_accum_nav_difference_triggers_alert(self):
        record_a = _make_nav(accum_nav=Decimal("2.3000"))
        record_b = _make_nav(accum_nav=Decimal("2.3100"))  # ~0.43% diff
        alerts = compare_nav_records(record_a, "eastmoney", record_b, "akshare")
        assert any(a.field == "accum_nav" for a in alerts)

    def test_daily_return_difference_triggers_alert(self):
        """Daily return difference > 0.5pp should trigger alert."""
        record_a = _make_nav(daily_return=Decimal("0.0100"))
        record_b = _make_nav(daily_return=Decimal("0.0200"))  # 1pp diff > 0.5pp
        alerts = compare_nav_records(record_a, "eastmoney", record_b, "akshare")
        assert any(a.field == "daily_return" for a in alerts)

    def test_daily_return_small_difference_no_alert(self):
        """Daily return difference < 0.5pp should not trigger alert."""
        record_a = _make_nav(daily_return=Decimal("0.0100"))
        record_b = _make_nav(daily_return=Decimal("0.0120"))  # 0.2pp diff
        alerts = compare_nav_records(record_a, "eastmoney", record_b, "akshare")
        assert not any(a.field == "daily_return" for a in alerts)

    def test_different_fund_codes_no_comparison(self):
        """Records for different funds should not be compared."""
        record_a = _make_nav(fund_code="000001")
        record_b = _make_nav(fund_code="000002")
        alerts = compare_nav_records(record_a, "eastmoney", record_b, "akshare")
        assert alerts == []

    def test_different_dates_no_comparison(self):
        """Records for different dates should not be compared."""
        record_a = _make_nav(trade_date=date(2024, 1, 15))
        record_b = _make_nav(trade_date=date(2024, 1, 16))
        alerts = compare_nav_records(record_a, "eastmoney", record_b, "akshare")
        assert alerts == []

    def test_none_values_skipped(self):
        """None values should not trigger comparison."""
        record_a = _make_nav(unit_nav=None)
        record_b = _make_nav(unit_nav=Decimal("1.5000"))
        alerts = compare_nav_records(record_a, "eastmoney", record_b, "akshare")
        # unit_nav comparison skipped, but accum_nav and daily_return still compared
        assert not any(a.field == "unit_nav" for a in alerts)

    def test_both_zero_no_alert(self):
        """Both values being zero should not trigger alert."""
        record_a = _make_nav(unit_nav=Decimal("0"), accum_nav=Decimal("0"))
        record_b = _make_nav(unit_nav=Decimal("0"), accum_nav=Decimal("0"))
        alerts = compare_nav_records(record_a, "eastmoney", record_b, "akshare")
        assert not any(a.field == "unit_nav" for a in alerts)

    def test_custom_threshold(self):
        """Custom threshold should be respected."""
        record_a = _make_nav(unit_nav=Decimal("1.5000"))
        record_b = _make_nav(unit_nav=Decimal("1.5010"))  # 0.067% diff

        # With tight threshold (0.0001 = 0.01%), should alert
        alerts = compare_nav_records(
            record_a, "eastmoney", record_b, "akshare",
            nav_threshold=Decimal("0.0001"),
        )
        assert any(a.field == "unit_nav" for a in alerts)

        # With loose threshold (0.01 = 1%), should not alert
        alerts = compare_nav_records(
            record_a, "eastmoney", record_b, "akshare",
            nav_threshold=Decimal("0.01"),
        )
        assert not any(a.field == "unit_nav" for a in alerts)


# ---------------------------------------------------------------------------
# Tests: compare_nav_series
# ---------------------------------------------------------------------------


class TestCompareNavSeries:
    def test_matching_series_no_alerts(self):
        series_a = [
            _make_nav(trade_date=date(2024, 1, 15)),
            _make_nav(trade_date=date(2024, 1, 16)),
        ]
        series_b = [
            _make_nav(trade_date=date(2024, 1, 15)),
            _make_nav(trade_date=date(2024, 1, 16)),
        ]
        alerts = compare_nav_series(series_a, "eastmoney", series_b, "akshare")
        assert alerts == []

    def test_partial_overlap(self):
        """Only overlapping dates should be compared."""
        series_a = [
            _make_nav(trade_date=date(2024, 1, 15)),
            _make_nav(trade_date=date(2024, 1, 16)),
            _make_nav(trade_date=date(2024, 1, 17)),
        ]
        series_b = [
            _make_nav(trade_date=date(2024, 1, 16)),
            _make_nav(trade_date=date(2024, 1, 17)),
            _make_nav(trade_date=date(2024, 1, 18)),
        ]
        # All matching records are identical, so no alerts
        alerts = compare_nav_series(series_a, "eastmoney", series_b, "akshare")
        assert alerts == []

    def test_series_with_discrepancy(self):
        """Discrepancy in one record should generate alert."""
        series_a = [
            _make_nav(trade_date=date(2024, 1, 15), unit_nav=Decimal("1.5000")),
            _make_nav(trade_date=date(2024, 1, 16), unit_nav=Decimal("1.5200")),
        ]
        series_b = [
            _make_nav(trade_date=date(2024, 1, 15), unit_nav=Decimal("1.5000")),
            _make_nav(trade_date=date(2024, 1, 16), unit_nav=Decimal("1.5500")),  # big diff
        ]
        alerts = compare_nav_series(series_a, "eastmoney", series_b, "akshare")
        assert len(alerts) >= 1
        assert any(
            a.trade_date == date(2024, 1, 16) and a.field == "unit_nav"
            for a in alerts
        )

    def test_no_overlap_no_alerts(self):
        """Non-overlapping series should produce no alerts."""
        series_a = [_make_nav(trade_date=date(2024, 1, 15))]
        series_b = [_make_nav(trade_date=date(2024, 1, 16))]
        alerts = compare_nav_series(series_a, "eastmoney", series_b, "akshare")
        assert alerts == []

    def test_empty_series(self):
        alerts = compare_nav_series([], "eastmoney", [], "akshare")
        assert alerts == []


# ---------------------------------------------------------------------------
# Tests: additional edge cases and abnormal scenarios
# ---------------------------------------------------------------------------


class TestCrossSourceValidatorEdgeCases:
    """Additional edge case tests for comprehensive abnormal scenario coverage."""

    def test_negative_nav_values_comparison(self):
        """Negative NAV values (corrupted data) should still be compared correctly."""
        # Use model_construct to bypass Pydantic validation for negative NAV
        # (simulates corrupted data from raw DB reads)
        record_a = NavRecord.model_construct(
            fund_code="000001",
            trade_date=date(2024, 1, 15),
            unit_nav=Decimal("-1.5000"),
            accum_nav=Decimal("2.3000"),
            daily_return=Decimal("0.01"),
            source="eastmoney",
            adj_nav=None,
            status="normal",
            created_at=None,
        )
        record_b = NavRecord.model_construct(
            fund_code="000001",
            trade_date=date(2024, 1, 15),
            unit_nav=Decimal("-1.6000"),
            accum_nav=Decimal("2.3000"),
            daily_return=Decimal("0.01"),
            source="akshare",
            adj_nav=None,
            status="normal",
            created_at=None,
        )
        alerts = compare_nav_records(record_a, "eastmoney", record_b, "akshare")
        # Should detect the difference in unit_nav
        assert any(a.field == "unit_nav" for a in alerts)

    def test_very_small_values_near_zero(self):
        """Very small values near zero should handle relative comparison correctly."""
        record_a = _make_nav(unit_nav=Decimal("0.0001"))
        record_b = _make_nav(unit_nav=Decimal("0.0002"))
        # Relative diff = |0.0001 - 0.0002| / max(0.0001, 0.0002) = 0.5 = 50%
        alerts = compare_nav_records(record_a, "eastmoney", record_b, "akshare")
        assert any(a.field == "unit_nav" for a in alerts)

    def test_one_value_zero_other_nonzero(self):
        """One source has 0, other has non-zero - should alert."""
        record_a = _make_nav(unit_nav=Decimal("0"))
        record_b = _make_nav(unit_nav=Decimal("1.5000"))
        alerts = compare_nav_records(record_a, "eastmoney", record_b, "akshare")
        assert any(a.field == "unit_nav" for a in alerts)

    def test_large_nav_small_absolute_diff(self):
        """Large NAV with small absolute diff but within relative threshold."""
        record_a = _make_nav(unit_nav=Decimal("100.0000"))
        record_b = _make_nav(unit_nav=Decimal("100.0500"))
        # Relative diff = 0.05/100.05 ≈ 0.0005 = 0.05% < 0.1% threshold
        alerts = compare_nav_records(record_a, "eastmoney", record_b, "akshare")
        assert not any(a.field == "unit_nav" for a in alerts)

    def test_large_nav_large_absolute_diff(self):
        """Large NAV with large absolute diff exceeding relative threshold."""
        record_a = _make_nav(unit_nav=Decimal("100.0000"))
        record_b = _make_nav(unit_nav=Decimal("100.2000"))
        # Relative diff = 0.2/100.2 ≈ 0.002 = 0.2% > 0.1% threshold
        alerts = compare_nav_records(record_a, "eastmoney", record_b, "akshare")
        assert any(a.field == "unit_nav" for a in alerts)

    def test_daily_return_both_negative(self):
        """Both sources have negative daily return with large difference."""
        record_a = _make_nav(daily_return=Decimal("-0.0100"))
        record_b = _make_nav(daily_return=Decimal("-0.0200"))
        # Absolute diff = 0.01 > 0.005 threshold
        alerts = compare_nav_records(record_a, "eastmoney", record_b, "akshare")
        assert any(a.field == "daily_return" for a in alerts)

    def test_daily_return_opposite_signs(self):
        """One source positive, other negative - should alert if diff > threshold."""
        record_a = _make_nav(daily_return=Decimal("0.0050"))
        record_b = _make_nav(daily_return=Decimal("-0.0050"))
        # Absolute diff = 0.01 > 0.005 threshold
        alerts = compare_nav_records(record_a, "eastmoney", record_b, "akshare")
        assert any(a.field == "daily_return" for a in alerts)

    def test_alert_contains_correct_metadata(self):
        """Verify alert object contains all expected metadata."""
        record_a = _make_nav(
            fund_code="110011",
            trade_date=date(2024, 3, 15),
            unit_nav=Decimal("2.0000"),
        )
        record_b = _make_nav(
            fund_code="110011",
            trade_date=date(2024, 3, 15),
            unit_nav=Decimal("2.1000"),
        )
        alerts = compare_nav_records(record_a, "eastmoney", record_b, "akshare")
        assert len(alerts) >= 1
        alert = [a for a in alerts if a.field == "unit_nav"][0]
        assert alert.fund_code == "110011"
        assert alert.trade_date == date(2024, 3, 15)
        assert alert.source_a == "eastmoney"
        assert alert.source_b == "akshare"
        assert alert.value_a == Decimal("2.0000")
        assert alert.value_b == Decimal("2.1000")
        assert alert.difference > Decimal("0")
        assert alert.threshold == DEFAULT_NAV_THRESHOLD

    def test_series_multiple_discrepancies(self):
        """Series with multiple discrepancies should report all."""
        series_a = [
            _make_nav(trade_date=date(2024, 1, 15), unit_nav=Decimal("1.5000")),
            _make_nav(trade_date=date(2024, 1, 16), unit_nav=Decimal("1.6000")),
            _make_nav(trade_date=date(2024, 1, 17), unit_nav=Decimal("1.7000")),
        ]
        series_b = [
            _make_nav(trade_date=date(2024, 1, 15), unit_nav=Decimal("1.5500")),  # diff
            _make_nav(trade_date=date(2024, 1, 16), unit_nav=Decimal("1.6000")),  # same
            _make_nav(trade_date=date(2024, 1, 17), unit_nav=Decimal("1.7500")),  # diff
        ]
        alerts = compare_nav_series(series_a, "eastmoney", series_b, "akshare")
        nav_alerts = [a for a in alerts if a.field == "unit_nav"]
        assert len(nav_alerts) == 2

    def test_series_one_empty(self):
        """One empty series should produce no alerts."""
        series_a = [_make_nav(trade_date=date(2024, 1, 15))]
        alerts = compare_nav_series(series_a, "eastmoney", [], "akshare")
        assert alerts == []

    def test_accum_nav_and_unit_nav_both_differ(self):
        """Both unit_nav and accum_nav differing should generate two alerts."""
        record_a = _make_nav(unit_nav=Decimal("1.5000"), accum_nav=Decimal("2.5000"))
        record_b = _make_nav(unit_nav=Decimal("1.6000"), accum_nav=Decimal("2.6000"))
        alerts = compare_nav_records(record_a, "eastmoney", record_b, "akshare")
        fields = [a.field for a in alerts]
        assert "unit_nav" in fields
        assert "accum_nav" in fields


# ---------------------------------------------------------------------------
# Tests: aggregated cross-source diagnostics / hard gate
# ---------------------------------------------------------------------------


class TestCrossSourceNavDiagnostics:
    def test_single_source_is_insufficient_not_hard_gate(self):
        diagnostics = build_cross_source_nav_diagnostics({
            "eastmoney": [_make_nav(trade_date=date(2024, 1, 15))],
        })

        assert diagnostics.status == "insufficient_sources"
        assert diagnostics.hard_gate is False
        assert diagnostics.provider_count == 1

    def test_matching_multi_source_series_passes(self):
        diagnostics = build_cross_source_nav_diagnostics({
            "eastmoney": [
                _make_nav(trade_date=date(2024, 1, 15), source="eastmoney"),
                _make_nav(trade_date=date(2024, 1, 16), source="eastmoney"),
            ],
            "akshare": [
                _make_nav(trade_date=date(2024, 1, 15), source="akshare"),
                _make_nav(trade_date=date(2024, 1, 16), source="akshare"),
            ],
        })

        assert diagnostics.status == "pass"
        assert diagnostics.hard_gate is False
        assert diagnostics.compared_pairs == 1
        assert diagnostics.alert_count == 0

    def test_small_number_of_conflicts_warns(self):
        diagnostics = build_cross_source_nav_diagnostics(
            {
                "eastmoney": [
                    _make_nav(trade_date=date(2024, 1, 15), unit_nav=Decimal("1.5000")),
                    _make_nav(trade_date=date(2024, 1, 16), unit_nav=Decimal("1.5100")),
                    _make_nav(trade_date=date(2024, 1, 17), unit_nav=Decimal("1.5200")),
                ],
                "akshare": [
                    _make_nav(trade_date=date(2024, 1, 15), unit_nav=Decimal("1.5500")),
                    _make_nav(trade_date=date(2024, 1, 16), unit_nav=Decimal("1.5100")),
                    _make_nav(trade_date=date(2024, 1, 17), unit_nav=Decimal("1.5200")),
                ],
            },
            hard_gate_min_alerts=3,
            hard_gate_alert_ratio=Decimal("0.50"),
        )

        assert diagnostics.status == "warning"
        assert diagnostics.hard_gate is False
        assert diagnostics.alert_count == 1
        assert diagnostics.affected_dates == ["2024-01-15"]

    def test_repeated_conflicts_trigger_hard_gate(self):
        eastmoney = []
        akshare = []
        for idx in range(5):
            day = date(2024, 1, 15 + idx)
            eastmoney.append(_make_nav(trade_date=day, unit_nav=Decimal("1.5000")))
            akshare.append(_make_nav(trade_date=day, unit_nav=Decimal("1.5500")))

        diagnostics = build_cross_source_nav_diagnostics({
            "eastmoney": eastmoney,
            "akshare": akshare,
        })

        assert diagnostics.status == "fail"
        assert diagnostics.hard_gate is True
        assert diagnostics.alert_count >= 5
        assert diagnostics.max_difference_field == "unit_nav"
        assert diagnostics.to_dict()["hard_gate"] is True
