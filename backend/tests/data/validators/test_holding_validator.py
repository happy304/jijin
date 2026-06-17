"""Unit tests for holding data validator.

Covers:
- Valid holding snapshots pass
- Total weight exceeding 110% detected
- Negative individual position weight detected
- Empty positions are valid
- Edge cases: exactly at boundary, leveraged funds
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.data.schemas.funds import HoldingPosition, HoldingSnapshot
from app.data.validators.holding_validator import validate_holding_snapshot
from app.data.validators.models import ValidationStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_position(
    stock_code: str = "600519",
    stock_name: str = "贵州茅台",
    weight: Decimal | None = Decimal("0.08"),
) -> HoldingPosition:
    # Use model_construct to bypass Pydantic validation so we can test
    # the validator's own checks against invalid data (e.g. negative weight
    # that might come from raw DB reads or corrupted external sources).
    return HoldingPosition.model_construct(
        stock_code=stock_code,
        stock_name=stock_name,
        weight=weight,
        shares=None,
        market_value=None,
        industry=None,
    )


def _make_snapshot(
    fund_code: str = "000001",
    report_date: date = date(2024, 3, 31),
    positions: list[HoldingPosition] | None = None,
) -> HoldingSnapshot:
    if positions is None:
        positions = [
            _make_position("600519", "贵州茅台", Decimal("0.08")),
            _make_position("000858", "五粮液", Decimal("0.06")),
            _make_position("601318", "中国平安", Decimal("0.05")),
        ]
    return HoldingSnapshot(
        fund_code=fund_code,
        report_date=report_date,
        positions=positions,
    )


# ---------------------------------------------------------------------------
# Tests: valid cases
# ---------------------------------------------------------------------------


class TestValidHoldings:
    def test_normal_snapshot_passes(self):
        snapshot = _make_snapshot()
        result = validate_holding_snapshot(snapshot)
        assert result.is_valid is True
        assert result.status == ValidationStatus.NORMAL

    def test_empty_positions_valid(self):
        snapshot = _make_snapshot(positions=[])
        result = validate_holding_snapshot(snapshot)
        assert result.is_valid is True

    def test_total_weight_at_110_percent(self):
        """Exactly 110% should pass (boundary inclusive)."""
        positions = [_make_position(weight=Decimal("1.10"))]
        snapshot = _make_snapshot(positions=positions)
        result = validate_holding_snapshot(snapshot)
        assert result.is_valid is True

    def test_leveraged_fund_within_limit(self):
        """Leveraged fund with total weight 105% should pass."""
        positions = [
            _make_position("600519", "贵州茅台", Decimal("0.30")),
            _make_position("000858", "五粮液", Decimal("0.25")),
            _make_position("601318", "中国平安", Decimal("0.25")),
            _make_position("000001", "平安银行", Decimal("0.25")),
        ]
        snapshot = _make_snapshot(positions=positions)
        result = validate_holding_snapshot(snapshot)
        assert result.is_valid is True

    def test_zero_weight_positions(self):
        """Positions with zero weight are valid."""
        positions = [
            _make_position(weight=Decimal("0")),
            _make_position(weight=Decimal("0.05")),
        ]
        snapshot = _make_snapshot(positions=positions)
        result = validate_holding_snapshot(snapshot)
        assert result.is_valid is True

    def test_none_weight_positions_ignored(self):
        """Positions with None weight are skipped in sum."""
        positions = [
            _make_position(weight=None),
            _make_position(weight=Decimal("0.50")),
        ]
        snapshot = _make_snapshot(positions=positions)
        result = validate_holding_snapshot(snapshot)
        assert result.is_valid is True


# ---------------------------------------------------------------------------
# Tests: invalid cases
# ---------------------------------------------------------------------------


class TestInvalidHoldings:
    def test_total_weight_exceeds_110_percent(self):
        """Total weight > 110% should fail."""
        positions = [
            _make_position("600519", "贵州茅台", Decimal("0.50")),
            _make_position("000858", "五粮液", Decimal("0.40")),
            _make_position("601318", "中国平安", Decimal("0.25")),
        ]
        snapshot = _make_snapshot(positions=positions)
        result = validate_holding_snapshot(snapshot)
        assert result.is_valid is False
        assert result.status == ValidationStatus.SUSPECT
        assert any("total_weight" in i.field for i in result.issues)

    def test_negative_position_weight(self):
        """Negative weight should fail."""
        positions = [
            _make_position(weight=Decimal("-0.05")),
            _make_position(weight=Decimal("0.50")),
        ]
        snapshot = _make_snapshot(positions=positions)
        result = validate_holding_snapshot(snapshot)
        assert result.is_valid is False
        assert any("negative" in i.message.lower() for i in result.issues)

    def test_custom_max_weight(self):
        """Custom max_weight parameter should be respected."""
        positions = [_make_position(weight=Decimal("0.60"))]
        snapshot = _make_snapshot(positions=positions)
        result = validate_holding_snapshot(snapshot, max_weight=Decimal("0.50"))
        assert result.is_valid is False

    def test_multiple_issues(self):
        """Multiple problems should all be reported."""
        positions = [
            _make_position("A", "Stock A", Decimal("-0.05")),
            _make_position("B", "Stock B", Decimal("0.80")),
            _make_position("C", "Stock C", Decimal("0.40")),
        ]
        snapshot = _make_snapshot(positions=positions)
        result = validate_holding_snapshot(snapshot)
        assert result.is_valid is False
        # Should have both negative weight and total weight issues
        assert len(result.issues) >= 2


# ---------------------------------------------------------------------------
# Tests: additional edge cases and abnormal scenarios
# ---------------------------------------------------------------------------


class TestHoldingValidatorEdgeCases:
    """Additional edge case tests for comprehensive abnormal scenario coverage."""

    def test_total_weight_exactly_zero(self):
        """Total weight of exactly 0 should pass (lower boundary inclusive)."""
        positions = [_make_position(weight=Decimal("0"))]
        snapshot = _make_snapshot(positions=positions)
        result = validate_holding_snapshot(snapshot)
        assert result.is_valid is True

    def test_single_position_at_max(self):
        """Single position at exactly 110% should pass."""
        positions = [_make_position("600519", "贵州茅台", Decimal("1.10"))]
        snapshot = _make_snapshot(positions=positions)
        result = validate_holding_snapshot(snapshot)
        assert result.is_valid is True

    def test_single_position_just_over_max(self):
        """Single position at 110.01% should fail."""
        positions = [_make_position("600519", "贵州茅台", Decimal("1.1001"))]
        snapshot = _make_snapshot(positions=positions)
        result = validate_holding_snapshot(snapshot)
        assert result.is_valid is False

    def test_many_small_positions_within_limit(self):
        """Many small positions summing to <110% should pass."""
        positions = [
            _make_position(f"00000{i}", f"Stock {i}", Decimal("0.05"))
            for i in range(20)
        ]
        # 20 * 0.05 = 1.00 = 100%
        snapshot = _make_snapshot(positions=positions)
        result = validate_holding_snapshot(snapshot)
        assert result.is_valid is True

    def test_many_small_positions_exceeding_limit(self):
        """Many small positions summing to >110% should fail."""
        positions = [
            _make_position(f"00000{i}", f"Stock {i}", Decimal("0.06"))
            for i in range(20)
        ]
        # 20 * 0.06 = 1.20 = 120% > 110%
        snapshot = _make_snapshot(positions=positions)
        result = validate_holding_snapshot(snapshot)
        assert result.is_valid is False

    def test_all_none_weights(self):
        """All positions with None weight should pass (sum is 0)."""
        positions = [
            _make_position("600519", "贵州茅台", None),
            _make_position("000858", "五粮液", None),
        ]
        snapshot = _make_snapshot(positions=positions)
        result = validate_holding_snapshot(snapshot)
        assert result.is_valid is True

    def test_multiple_negative_weights(self):
        """Multiple negative weights should each generate an issue."""
        positions = [
            _make_position("A", "Stock A", Decimal("-0.03")),
            _make_position("B", "Stock B", Decimal("-0.02")),
            _make_position("C", "Stock C", Decimal("0.50")),
        ]
        snapshot = _make_snapshot(positions=positions)
        result = validate_holding_snapshot(snapshot)
        assert result.is_valid is False
        negative_issues = [i for i in result.issues if "negative" in i.message.lower()]
        assert len(negative_issues) == 2

    def test_suspect_status_on_invalid(self):
        """Invalid holding should set status to SUSPECT."""
        positions = [_make_position(weight=Decimal("1.20"))]
        snapshot = _make_snapshot(positions=positions)
        result = validate_holding_snapshot(snapshot)
        assert result.status == ValidationStatus.SUSPECT

    def test_custom_min_weight(self):
        """Custom min_weight parameter should be respected."""
        positions = [_make_position(weight=Decimal("0.05"))]
        snapshot = _make_snapshot(positions=positions)
        result = validate_holding_snapshot(snapshot, min_weight=Decimal("0.10"))
        assert result.is_valid is False
        assert any("below" in i.message.lower() for i in result.issues)

    def test_very_small_weight_precision(self):
        """Very small weight values should be handled correctly."""
        positions = [
            _make_position("A", "Stock A", Decimal("0.0001")),
            _make_position("B", "Stock B", Decimal("0.0002")),
        ]
        snapshot = _make_snapshot(positions=positions)
        result = validate_holding_snapshot(snapshot)
        assert result.is_valid is True
