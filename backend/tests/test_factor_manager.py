"""Unit tests for manager and scale-category factors.

Covers:
- fund_size: AUM passthrough, edge cases (None, negative)
- size_change_rate: growth/decline calculation, zero/negative previous
- manager_tenure: tenure in years, edge cases (None, future start)
- manager_fund_count: count passthrough, edge cases (None, negative)

Satisfies requirement 3.6.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from app.domain.factors.registry import _restore_registry, _snapshot_registry

import app.domain.factors.manager as manager_mod  # noqa: F401
from app.domain.factors.manager import (
    fund_size,
    manager_fund_count,
    manager_tenure,
    size_change_rate,
)


@pytest.fixture(autouse=True)
def _ensure_registry_clean():
    """Snapshot registry before test, restore after to avoid cross-pollution."""
    snapshot = _snapshot_registry()
    yield
    _restore_registry(snapshot)


# ===========================================================================
# fund_size
# ===========================================================================


class TestFundSize:
    """Tests for the fund_size factor."""

    def test_normal_value(self):
        """Normal AUM value is returned as-is."""
        assert fund_size(150.5) == pytest.approx(150.5)

    def test_zero_value(self):
        """Zero AUM is valid (e.g. liquidating fund)."""
        assert fund_size(0.0) == pytest.approx(0.0)

    def test_large_value(self):
        """Large AUM value."""
        assert fund_size(5000.0) == pytest.approx(5000.0)

    def test_small_value(self):
        """Very small AUM value."""
        assert fund_size(0.01) == pytest.approx(0.01)

    def test_none_returns_nan(self):
        assert np.isnan(fund_size(None))

    def test_negative_returns_nan(self):
        assert np.isnan(fund_size(-10.0))

    def test_deterministic(self):
        r1 = fund_size(100.0)
        r2 = fund_size(100.0)
        assert r1 == r2


# ===========================================================================
# size_change_rate
# ===========================================================================


class TestSizeChangeRate:
    """Tests for the size_change_rate factor."""

    def test_growth(self):
        """Fund grew from 100 to 150 → 50% growth."""
        result = size_change_rate(150.0, 100.0)
        assert result == pytest.approx(0.5)

    def test_decline(self):
        """Fund shrank from 200 to 100 → -50% decline."""
        result = size_change_rate(100.0, 200.0)
        assert result == pytest.approx(-0.5)

    def test_no_change(self):
        """Same size → 0% change."""
        result = size_change_rate(100.0, 100.0)
        assert result == pytest.approx(0.0)

    def test_double(self):
        """Fund doubled → 100% growth."""
        result = size_change_rate(200.0, 100.0)
        assert result == pytest.approx(1.0)

    def test_none_current_returns_nan(self):
        assert np.isnan(size_change_rate(None, 100.0))

    def test_none_previous_returns_nan(self):
        assert np.isnan(size_change_rate(100.0, None))

    def test_both_none_returns_nan(self):
        assert np.isnan(size_change_rate(None, None))

    def test_zero_previous_returns_nan(self):
        """Division by zero case."""
        assert np.isnan(size_change_rate(100.0, 0.0))

    def test_negative_previous_returns_nan(self):
        assert np.isnan(size_change_rate(100.0, -50.0))

    def test_negative_current_returns_nan(self):
        assert np.isnan(size_change_rate(-10.0, 100.0))

    def test_current_zero_valid(self):
        """Current size can be zero (fund liquidating)."""
        result = size_change_rate(0.0, 100.0)
        assert result == pytest.approx(-1.0)

    def test_deterministic(self):
        r1 = size_change_rate(150.0, 100.0)
        r2 = size_change_rate(150.0, 100.0)
        assert r1 == r2


# ===========================================================================
# manager_tenure
# ===========================================================================


class TestManagerTenure:
    """Tests for the manager_tenure factor."""

    def test_one_year(self):
        """Approximately one year tenure."""
        start = date(2023, 1, 1)
        ref = date(2024, 1, 1)
        result = manager_tenure(start, ref)
        # 365 days (2023 is not a leap year) / 365.25
        expected = 365 / 365.25
        assert result == pytest.approx(expected, abs=1e-6)

    def test_exact_days(self):
        """Known number of days."""
        start = date(2020, 6, 15)
        ref = date(2023, 6, 15)
        result = manager_tenure(start, ref)
        # 3 years: 2020-06-15 to 2023-06-15 = 1096 days
        delta = (ref - start).days
        expected = delta / 365.25
        assert result == pytest.approx(expected, abs=1e-6)

    def test_same_day(self):
        """Start and reference on same day → 0 tenure."""
        d = date(2023, 5, 1)
        result = manager_tenure(d, d)
        assert result == pytest.approx(0.0)

    def test_long_tenure(self):
        """10+ years tenure."""
        start = date(2010, 3, 1)
        ref = date(2024, 3, 1)
        result = manager_tenure(start, ref)
        delta = (ref - start).days
        expected = delta / 365.25
        assert result == pytest.approx(expected, abs=1e-6)

    def test_none_start_returns_nan(self):
        assert np.isnan(manager_tenure(None, date(2024, 1, 1)))

    def test_none_reference_uses_today(self):
        """When reference_date is None, defaults to today."""
        start = date(2020, 1, 1)
        result = manager_tenure(start, None)
        expected = (date.today() - start).days / 365.25
        assert result == pytest.approx(expected, abs=0.01)

    def test_future_start_returns_nan(self):
        """Start date after reference date → negative tenure → nan."""
        start = date(2025, 1, 1)
        ref = date(2024, 1, 1)
        assert np.isnan(manager_tenure(start, ref))

    def test_deterministic(self):
        start = date(2020, 1, 1)
        ref = date(2024, 6, 15)
        r1 = manager_tenure(start, ref)
        r2 = manager_tenure(start, ref)
        assert r1 == r2


# ===========================================================================
# manager_fund_count
# ===========================================================================


class TestManagerFundCount:
    """Tests for the manager_fund_count factor."""

    def test_normal_count(self):
        """Normal fund count."""
        assert manager_fund_count(5) == pytest.approx(5.0)

    def test_single_fund(self):
        assert manager_fund_count(1) == pytest.approx(1.0)

    def test_many_funds(self):
        assert manager_fund_count(20) == pytest.approx(20.0)

    def test_zero_funds(self):
        """Zero is valid (manager just left all funds)."""
        assert manager_fund_count(0) == pytest.approx(0.0)

    def test_none_returns_nan(self):
        assert np.isnan(manager_fund_count(None))

    def test_negative_returns_nan(self):
        assert np.isnan(manager_fund_count(-1))

    def test_deterministic(self):
        r1 = manager_fund_count(3)
        r2 = manager_fund_count(3)
        assert r1 == r2
