"""Tests for FundMeta DTO."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.data.schemas import FundMeta, FundStatus, FundType


def _minimal() -> dict:
    return {"code": "000001", "name": "华夏成长混合"}


def test_minimal_construction() -> None:
    m = FundMeta(**_minimal())
    assert m.code == "000001"
    assert m.name == "华夏成长混合"
    assert m.status == FundStatus.ACTIVE
    assert m.is_purchasable is True
    assert m.currency == "CNY"


def test_full_round_trip_json() -> None:
    m = FundMeta(
        code="000001",
        name="华夏成长混合",
        fund_type=FundType.MIXED,
        sub_type="偏股混合型",
        company_id="HUAXIA",
        inception_date=date(2001, 12, 18),
        benchmark="沪深300",
        management_fee=Decimal("0.015"),
        custodian_fee=Decimal("0.0025"),
        currency="CNY",
        status=FundStatus.ACTIVE,
        is_purchasable=True,
        purchase_limit=Decimal("100000"),
        source="eastmoney",
        updated_at=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
    )
    json_str = m.model_dump_json()
    restored = FundMeta.model_validate_json(json_str)
    assert restored.code == m.code
    assert restored.management_fee == m.management_fee
    assert restored.inception_date == m.inception_date


def test_decimal_preserved_as_string_in_json() -> None:
    m = FundMeta(code="000001", name="Test", management_fee=Decimal("0.015"))
    data = json.loads(m.model_dump_json())
    # Decimal must be serialised as string to preserve precision
    assert data["management_fee"] == "0.015"


def test_unknown_field_rejected() -> None:
    with pytest.raises(ValidationError, match="extra_inputs_not_permitted|Extra inputs"):
        FundMeta(**_minimal(), unknown_field="x")


def test_fund_type_enum_validation() -> None:
    m = FundMeta(**_minimal(), fund_type="stock")
    assert m.fund_type == FundType.STOCK


def test_invalid_fund_type_rejected() -> None:
    with pytest.raises(ValidationError):
        FundMeta(**_minimal(), fund_type="invalid_type")


def test_management_fee_bounds() -> None:
    # Valid boundary
    FundMeta(**_minimal(), management_fee=Decimal("0"))
    FundMeta(**_minimal(), management_fee=Decimal("1"))
    # Out of bounds
    with pytest.raises(ValidationError):
        FundMeta(**_minimal(), management_fee=Decimal("-0.001"))
    with pytest.raises(ValidationError):
        FundMeta(**_minimal(), management_fee=Decimal("1.001"))


def test_status_defaults_to_active() -> None:
    m = FundMeta(**_minimal())
    assert m.status == FundStatus.ACTIVE


def test_status_enum_validation() -> None:
    m = FundMeta(**_minimal(), status="suspended")
    assert m.status == FundStatus.SUSPENDED


def test_whitespace_stripped_from_strings() -> None:
    m = FundMeta(code="  000001  ", name="  华夏成长  ")
    assert m.code == "000001"
    assert m.name == "华夏成长"
