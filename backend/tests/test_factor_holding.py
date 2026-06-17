"""Unit tests for holding-category factors.

Covers:
- concentration_hhi: HHI computation, edge cases (empty, single holding)
- top10_weight: top 10 sum, fewer than 10 holdings
- industry_exposure: industry aggregation, missing columns
- turnover: weight change calculation, new/removed positions

Satisfies requirement 3.5.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.domain.factors.registry import _restore_registry, _snapshot_registry

import app.domain.factors.holding as holding_mod  # noqa: F401
from app.domain.factors.holding import (
    concentration_hhi,
    industry_exposure,
    top10_weight,
    turnover,
)


@pytest.fixture(autouse=True)
def _ensure_registry_clean():
    """Snapshot registry before test, restore after to avoid cross-pollution."""
    snapshot = _snapshot_registry()
    yield
    _restore_registry(snapshot)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_holdings(
    stocks: list[tuple[str, float, str]],
) -> pd.DataFrame:
    """Create a holdings DataFrame from (stock_code, weight, industry) tuples."""
    return pd.DataFrame(stocks, columns=["stock_code", "weight", "industry"])


# ===========================================================================
# concentration_hhi
# ===========================================================================


class TestConcentrationHHI:
    """Tests for the concentration_hhi factor."""

    def test_equal_weights(self):
        """N equal-weight holdings → HHI = 1/N."""
        n = 10
        weight = 1.0 / n
        holdings = _make_holdings(
            [(f"S{i:04d}", weight, "Tech") for i in range(n)]
        )
        result = concentration_hhi(holdings)
        expected = n * (weight**2)  # = 1/N = 0.1
        assert result == pytest.approx(expected, abs=1e-10)

    def test_single_holding(self):
        """Single holding with 100% weight → HHI = 1.0."""
        holdings = _make_holdings([("S0001", 1.0, "Finance")])
        result = concentration_hhi(holdings)
        assert result == pytest.approx(1.0, abs=1e-10)

    def test_concentrated_portfolio(self):
        """One dominant holding + many small ones."""
        holdings = _make_holdings([
            ("S0001", 0.80, "Tech"),
            ("S0002", 0.05, "Finance"),
            ("S0003", 0.05, "Health"),
            ("S0004", 0.05, "Energy"),
            ("S0005", 0.05, "Consumer"),
        ])
        result = concentration_hhi(holdings)
        expected = 0.80**2 + 4 * (0.05**2)  # 0.64 + 0.01 = 0.65
        assert result == pytest.approx(expected, abs=1e-10)

    def test_empty_dataframe_returns_nan(self):
        holdings = pd.DataFrame(columns=["stock_code", "weight", "industry"])
        assert np.isnan(concentration_hhi(holdings))

    def test_none_returns_nan(self):
        assert np.isnan(concentration_hhi(None))

    def test_missing_weight_column_returns_nan(self):
        holdings = pd.DataFrame({"stock_code": ["S0001"], "industry": ["Tech"]})
        assert np.isnan(concentration_hhi(holdings))

    def test_all_nan_weights_returns_nan(self):
        holdings = pd.DataFrame({
            "stock_code": ["S0001", "S0002"],
            "weight": [np.nan, np.nan],
            "industry": ["Tech", "Finance"],
        })
        assert np.isnan(concentration_hhi(holdings))

    def test_partial_nan_weights(self):
        """NaN weights are dropped before computation."""
        holdings = pd.DataFrame({
            "stock_code": ["S0001", "S0002", "S0003"],
            "weight": [0.5, np.nan, 0.5],
            "industry": ["Tech", "Finance", "Health"],
        })
        result = concentration_hhi(holdings)
        expected = 0.5**2 + 0.5**2  # 0.5
        assert result == pytest.approx(expected, abs=1e-10)

    def test_deterministic(self):
        """Same input produces same output."""
        holdings = _make_holdings([
            ("S0001", 0.3, "Tech"),
            ("S0002", 0.4, "Finance"),
            ("S0003", 0.3, "Health"),
        ])
        r1 = concentration_hhi(holdings)
        r2 = concentration_hhi(holdings)
        assert r1 == r2


# ===========================================================================
# top10_weight
# ===========================================================================


class TestTop10Weight:
    """Tests for the top10_weight factor."""

    def test_exactly_10_holdings(self):
        """10 holdings → sum of all weights."""
        weights = [0.15, 0.12, 0.11, 0.10, 0.09, 0.08, 0.08, 0.07, 0.06, 0.05]
        holdings = _make_holdings(
            [(f"S{i:04d}", w, "Tech") for i, w in enumerate(weights)]
        )
        result = top10_weight(holdings)
        expected = sum(weights)  # 0.91
        assert result == pytest.approx(expected, abs=1e-10)

    def test_more_than_10_holdings(self):
        """Only top 10 by weight should be summed."""
        weights = [0.10] * 10 + [0.001] * 5  # 10 large + 5 small
        holdings = _make_holdings(
            [(f"S{i:04d}", w, "Tech") for i, w in enumerate(weights)]
        )
        result = top10_weight(holdings)
        expected = 0.10 * 10  # 1.0
        assert result == pytest.approx(expected, abs=1e-10)

    def test_fewer_than_10_holdings(self):
        """Fewer than 10 holdings → sum of all."""
        holdings = _make_holdings([
            ("S0001", 0.30, "Tech"),
            ("S0002", 0.25, "Finance"),
            ("S0003", 0.20, "Health"),
        ])
        result = top10_weight(holdings)
        expected = 0.75
        assert result == pytest.approx(expected, abs=1e-10)

    def test_single_holding(self):
        holdings = _make_holdings([("S0001", 0.95, "Tech")])
        result = top10_weight(holdings)
        assert result == pytest.approx(0.95, abs=1e-10)

    def test_empty_returns_nan(self):
        holdings = pd.DataFrame(columns=["stock_code", "weight", "industry"])
        assert np.isnan(top10_weight(holdings))

    def test_none_returns_nan(self):
        assert np.isnan(top10_weight(None))

    def test_missing_weight_column_returns_nan(self):
        holdings = pd.DataFrame({"stock_code": ["S0001"]})
        assert np.isnan(top10_weight(holdings))

    def test_all_nan_weights_returns_nan(self):
        holdings = pd.DataFrame({
            "stock_code": ["S0001"],
            "weight": [np.nan],
            "industry": ["Tech"],
        })
        assert np.isnan(top10_weight(holdings))

    def test_deterministic(self):
        holdings = _make_holdings([
            ("S0001", 0.3, "Tech"),
            ("S0002", 0.4, "Finance"),
        ])
        r1 = top10_weight(holdings)
        r2 = top10_weight(holdings)
        assert r1 == r2


# ===========================================================================
# industry_exposure
# ===========================================================================


class TestIndustryExposure:
    """Tests for the industry_exposure factor."""

    def test_basic_aggregation(self):
        """Multiple stocks in same industry → weights summed."""
        holdings = _make_holdings([
            ("S0001", 0.20, "Tech"),
            ("S0002", 0.15, "Tech"),
            ("S0003", 0.10, "Finance"),
            ("S0004", 0.05, "Health"),
        ])
        result = industry_exposure(holdings)
        assert isinstance(result, dict)
        assert result["Tech"] == pytest.approx(0.35, abs=1e-10)
        assert result["Finance"] == pytest.approx(0.10, abs=1e-10)
        assert result["Health"] == pytest.approx(0.05, abs=1e-10)

    def test_single_industry(self):
        holdings = _make_holdings([
            ("S0001", 0.50, "Tech"),
            ("S0002", 0.30, "Tech"),
            ("S0003", 0.20, "Tech"),
        ])
        result = industry_exposure(holdings)
        assert isinstance(result, dict)
        assert result == {"Tech": pytest.approx(1.0, abs=1e-10)}

    def test_empty_returns_nan(self):
        holdings = pd.DataFrame(columns=["stock_code", "weight", "industry"])
        assert np.isnan(industry_exposure(holdings))

    def test_none_returns_nan(self):
        assert np.isnan(industry_exposure(None))

    def test_missing_industry_column_returns_nan(self):
        holdings = pd.DataFrame({
            "stock_code": ["S0001"],
            "weight": [0.5],
        })
        assert np.isnan(industry_exposure(holdings))

    def test_missing_weight_column_returns_nan(self):
        holdings = pd.DataFrame({
            "stock_code": ["S0001"],
            "industry": ["Tech"],
        })
        assert np.isnan(industry_exposure(holdings))

    def test_nan_industry_rows_dropped(self):
        """Rows with NaN industry are excluded."""
        holdings = pd.DataFrame({
            "stock_code": ["S0001", "S0002", "S0003"],
            "weight": [0.30, 0.20, 0.10],
            "industry": ["Tech", np.nan, "Finance"],
        })
        result = industry_exposure(holdings)
        assert isinstance(result, dict)
        assert "Tech" in result
        assert "Finance" in result
        assert len(result) == 2
        assert result["Tech"] == pytest.approx(0.30, abs=1e-10)
        assert result["Finance"] == pytest.approx(0.10, abs=1e-10)

    def test_all_nan_returns_nan(self):
        holdings = pd.DataFrame({
            "stock_code": ["S0001"],
            "weight": [np.nan],
            "industry": [np.nan],
        })
        assert np.isnan(industry_exposure(holdings))

    def test_deterministic(self):
        holdings = _make_holdings([
            ("S0001", 0.3, "Tech"),
            ("S0002", 0.4, "Finance"),
        ])
        r1 = industry_exposure(holdings)
        r2 = industry_exposure(holdings)
        assert r1 == r2


# ===========================================================================
# turnover
# ===========================================================================


class TestTurnover:
    """Tests for the turnover factor."""

    def test_no_change(self):
        """Same holdings in both periods → turnover = 0."""
        current = _make_holdings([
            ("S0001", 0.30, "Tech"),
            ("S0002", 0.40, "Finance"),
            ("S0003", 0.30, "Health"),
        ])
        previous = _make_holdings([
            ("S0001", 0.30, "Tech"),
            ("S0002", 0.40, "Finance"),
            ("S0003", 0.30, "Health"),
        ])
        result = turnover(current, previous)
        assert result == pytest.approx(0.0, abs=1e-10)

    def test_complete_replacement(self):
        """Completely different holdings → turnover = 1.0."""
        current = _make_holdings([
            ("S0001", 0.50, "Tech"),
            ("S0002", 0.50, "Finance"),
        ])
        previous = _make_holdings([
            ("S0003", 0.50, "Health"),
            ("S0004", 0.50, "Energy"),
        ])
        result = turnover(current, previous)
        # |0.5-0| + |0.5-0| + |0-0.5| + |0-0.5| = 2.0 / 2 = 1.0
        assert result == pytest.approx(1.0, abs=1e-10)

    def test_partial_change(self):
        """Some holdings changed, some stayed."""
        current = _make_holdings([
            ("S0001", 0.40, "Tech"),
            ("S0002", 0.30, "Finance"),
            ("S0003", 0.30, "Health"),
        ])
        previous = _make_holdings([
            ("S0001", 0.30, "Tech"),
            ("S0002", 0.40, "Finance"),
            ("S0003", 0.30, "Health"),
        ])
        result = turnover(current, previous)
        # |0.40-0.30| + |0.30-0.40| + |0.30-0.30| = 0.10 + 0.10 + 0 = 0.20 / 2 = 0.10
        assert result == pytest.approx(0.10, abs=1e-10)

    def test_new_position_added(self):
        """New stock added in current period."""
        current = _make_holdings([
            ("S0001", 0.40, "Tech"),
            ("S0002", 0.30, "Finance"),
            ("S0003", 0.30, "Health"),
        ])
        previous = _make_holdings([
            ("S0001", 0.50, "Tech"),
            ("S0002", 0.50, "Finance"),
        ])
        result = turnover(current, previous)
        # S0001: |0.40-0.50| = 0.10
        # S0002: |0.30-0.50| = 0.20
        # S0003: |0.30-0.00| = 0.30
        # Total = 0.60 / 2 = 0.30
        assert result == pytest.approx(0.30, abs=1e-10)

    def test_position_removed(self):
        """Stock removed in current period."""
        current = _make_holdings([
            ("S0001", 0.60, "Tech"),
            ("S0002", 0.40, "Finance"),
        ])
        previous = _make_holdings([
            ("S0001", 0.40, "Tech"),
            ("S0002", 0.30, "Finance"),
            ("S0003", 0.30, "Health"),
        ])
        result = turnover(current, previous)
        # S0001: |0.60-0.40| = 0.20
        # S0002: |0.40-0.30| = 0.10
        # S0003: |0.00-0.30| = 0.30
        # Total = 0.60 / 2 = 0.30
        assert result == pytest.approx(0.30, abs=1e-10)

    def test_none_current_returns_nan(self):
        previous = _make_holdings([("S0001", 0.5, "Tech")])
        assert np.isnan(turnover(None, previous))

    def test_none_previous_returns_nan(self):
        current = _make_holdings([("S0001", 0.5, "Tech")])
        assert np.isnan(turnover(current, None))

    def test_both_none_returns_nan(self):
        assert np.isnan(turnover(None, None))

    def test_empty_current_returns_nan(self):
        current = pd.DataFrame(columns=["stock_code", "weight", "industry"])
        previous = _make_holdings([("S0001", 0.5, "Tech")])
        assert np.isnan(turnover(current, previous))

    def test_empty_previous_returns_nan(self):
        current = _make_holdings([("S0001", 0.5, "Tech")])
        previous = pd.DataFrame(columns=["stock_code", "weight", "industry"])
        assert np.isnan(turnover(current, previous))

    def test_missing_columns_returns_nan(self):
        current = pd.DataFrame({"stock_code": ["S0001"], "industry": ["Tech"]})
        previous = _make_holdings([("S0001", 0.5, "Tech")])
        assert np.isnan(turnover(current, previous))

    def test_deterministic(self):
        current = _make_holdings([
            ("S0001", 0.5, "Tech"),
            ("S0002", 0.5, "Finance"),
        ])
        previous = _make_holdings([
            ("S0001", 0.3, "Tech"),
            ("S0002", 0.7, "Finance"),
        ])
        r1 = turnover(current, previous)
        r2 = turnover(current, previous)
        assert r1 == r2
