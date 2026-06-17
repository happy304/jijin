"""Tests for HoldingPosition and HoldingSnapshot DTOs."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.data.schemas import HoldingPosition, HoldingSnapshot


def _position(**kwargs) -> dict:
    return {
        "stock_code": "600519",
        "stock_name": "贵州茅台",
        "weight": Decimal("0.05"),
        "shares": Decimal("10000"),
        "market_value": Decimal("1500000"),
        "industry": "食品饮料",
        **kwargs,
    }


def test_minimal_snapshot() -> None:
    s = HoldingSnapshot(fund_code="000001", report_date=date(2024, 3, 31))
    assert s.positions == []


def test_snapshot_with_positions() -> None:
    s = HoldingSnapshot(
        fund_code="000001",
        report_date=date(2024, 3, 31),
        positions=[HoldingPosition(**_position())],
    )
    assert len(s.positions) == 1
    assert s.positions[0].stock_code == "600519"


def test_round_trip_json() -> None:
    s = HoldingSnapshot(
        fund_code="000001",
        report_date=date(2024, 3, 31),
        positions=[HoldingPosition(**_position()), HoldingPosition(**_position(stock_code="000858"))],
    )
    restored = HoldingSnapshot.model_validate_json(s.model_dump_json())
    assert len(restored.positions) == 2
    assert restored.positions[0].weight == Decimal("0.05")
    assert restored.report_date == date(2024, 3, 31)


def test_position_weight_bounds() -> None:
    # Valid: 0 to 2 (200% for leveraged)
    HoldingPosition(**_position(weight=Decimal("0")))
    HoldingPosition(**_position(weight=Decimal("2")))
    with pytest.raises(ValidationError):
        HoldingPosition(**_position(weight=Decimal("-0.001")))
    with pytest.raises(ValidationError):
        HoldingPosition(**_position(weight=Decimal("2.001")))


def test_position_optional_fields() -> None:
    p = HoldingPosition()
    assert p.stock_code is None
    assert p.weight is None


def test_unknown_field_rejected() -> None:
    with pytest.raises(ValidationError):
        HoldingSnapshot(fund_code="000001", report_date=date(2024, 3, 31), extra="x")
