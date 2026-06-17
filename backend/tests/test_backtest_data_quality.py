"""Tests for backtest NAV data quality checks."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.domain.backtest.data_quality import check_backtest_data_quality


def test_data_quality_uses_bond_threshold_for_spike_detection() -> None:
    """债基 8% 单日跳变应超过 5% 阈值并产生警告。"""
    trading_days = [date(2024, 1, 2), date(2024, 1, 3)]
    nav_data = {
        "BOND001": {
            date(2024, 1, 2): Decimal("1.0000"),
            date(2024, 1, 3): Decimal("1.0800"),
        }
    }

    report = check_backtest_data_quality(
        nav_data,
        trading_days,
        fund_types={"BOND001": "bond"},
    )

    assert report.overall_status == "warning"
    assert report.can_proceed is True
    assert report.fund_reports[0].spike_count == 1
    assert report.fund_reports[0].spike_threshold == Decimal("0.05")
    assert "净值跳变 1 次" in report.warnings[0]


def test_data_quality_uses_stock_threshold_for_same_move() -> None:
    """股票型基金 8% 单日波动未超过 15% 阈值，不应被标记为跳变。"""
    trading_days = [date(2024, 1, 2), date(2024, 1, 3)]
    nav_data = {
        "STOCK001": {
            date(2024, 1, 2): Decimal("1.0000"),
            date(2024, 1, 3): Decimal("1.0800"),
        }
    }

    report = check_backtest_data_quality(
        nav_data,
        trading_days,
        fund_types={"STOCK001": "stock"},
    )

    assert report.overall_status == "good"
    assert report.fund_reports[0].spike_count == 0
    assert report.fund_reports[0].spike_threshold == Decimal("0.15")


def test_data_quality_serializes_spike_threshold() -> None:
    """质量报告应输出使用的阈值，便于后续审计。"""
    trading_days = [date(2024, 1, 2), date(2024, 1, 3)]
    nav_data = {
        "MONEY001": {
            date(2024, 1, 2): Decimal("1.0000"),
            date(2024, 1, 3): Decimal("1.0200"),
        }
    }

    report = check_backtest_data_quality(
        nav_data,
        trading_days,
        fund_types={"MONEY001": "money"},
    )

    payload = report.to_dict()
    assert payload["funds"][0]["spike_threshold"] == "0.01"
