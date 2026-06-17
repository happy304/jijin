"""Tests for NAV source quality warning helpers."""

from __future__ import annotations

from datetime import date

from app.tasks.nav_quality import (
    build_nav_quality_warning,
    new_nav_source_stats,
    record_nav_source_usage,
)


def test_build_nav_quality_warning_returns_none_without_fallback() -> None:
    stats = new_nav_source_stats()
    record_nav_source_usage(stats, date(2024, 1, 2), used_adj_nav=True)
    record_nav_source_usage(stats, date(2024, 1, 3), used_adj_nav=True)

    assert build_nav_quality_warning({"000001": stats}) is None


def test_build_nav_quality_warning_summarizes_unit_nav_fallback() -> None:
    stats = new_nav_source_stats()
    record_nav_source_usage(stats, date(2024, 1, 2), used_adj_nav=True)
    record_nav_source_usage(stats, date(2024, 1, 3), used_adj_nav=False)
    record_nav_source_usage(stats, date(2024, 1, 4), used_adj_nav=False)

    warning = build_nav_quality_warning({"000001": stats})

    assert warning is not None
    assert warning["has_unit_nav_fallback"] is True
    fund_warning = warning["funds"]["000001"]
    assert fund_warning["total_points"] == 3
    assert fund_warning["adj_nav_points"] == 1
    assert fund_warning["unit_nav_fallback_points"] == 2
    assert fund_warning["unit_nav_fallback_ratio"] == 0.666667
    assert fund_warning["first_fallback_date"] == "2024-01-03"
    assert fund_warning["last_fallback_date"] == "2024-01-04"
