"""Tests for FeeTier DTO."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.data.schemas import FeeTier, FeeType


def _subscribe(**kwargs) -> dict:
    return {
        "fund_code": "000001",
        "fee_type": FeeType.SUBSCRIBE,
        "min_amount": Decimal("0"),
        "max_amount": Decimal("1000000"),
        "rate": Decimal("0.015"),
        **kwargs,
    }


def _redeem(**kwargs) -> dict:
    return {
        "fund_code": "000001",
        "fee_type": FeeType.REDEEM,
        "min_holding_days": 0,
        "max_holding_days": 7,
        "rate": Decimal("0.015"),
        **kwargs,
    }


def test_subscribe_tier_construction() -> None:
    t = FeeTier(**_subscribe())
    assert t.fee_type == FeeType.SUBSCRIBE
    assert t.rate == Decimal("0.015")
    assert t.min_amount == Decimal("0")


def test_redeem_tier_construction() -> None:
    t = FeeTier(**_redeem())
    assert t.fee_type == FeeType.REDEEM
    assert t.min_holding_days == 0
    assert t.max_holding_days == 7


def test_round_trip_json_subscribe() -> None:
    t = FeeTier(**_subscribe())
    restored = FeeTier.model_validate_json(t.model_dump_json())
    assert restored.rate == t.rate
    assert restored.min_amount == t.min_amount
    assert restored.max_amount == t.max_amount


def test_round_trip_json_redeem() -> None:
    t = FeeTier(**_redeem())
    restored = FeeTier.model_validate_json(t.model_dump_json())
    assert restored.min_holding_days == t.min_holding_days
    assert restored.max_holding_days == t.max_holding_days


def test_rate_bounds() -> None:
    FeeTier(**_subscribe(rate=Decimal("0")))
    FeeTier(**_subscribe(rate=Decimal("1")))
    with pytest.raises(ValidationError):
        FeeTier(**_subscribe(rate=Decimal("-0.001")))
    with pytest.raises(ValidationError):
        FeeTier(**_subscribe(rate=Decimal("1.001")))


def test_fee_type_enum_validation() -> None:
    t = FeeTier(**_subscribe(fee_type="subscribe"))
    assert t.fee_type == FeeType.SUBSCRIBE
    t2 = FeeTier(**_redeem(fee_type="redeem"))
    assert t2.fee_type == FeeType.REDEEM


def test_invalid_fee_type_rejected() -> None:
    with pytest.raises(ValidationError):
        FeeTier(**_subscribe(fee_type="transfer"))


def test_no_cap_tiers_use_none() -> None:
    # Unlimited subscribe tier
    t = FeeTier(**_subscribe(max_amount=None))
    assert t.max_amount is None
    # Unlimited redeem tier (hold forever)
    t2 = FeeTier(**_redeem(max_holding_days=None))
    assert t2.max_holding_days is None


def test_unknown_field_rejected() -> None:
    with pytest.raises(ValidationError):
        FeeTier(**_subscribe(bogus="x"))


def test_min_amount_non_negative() -> None:
    with pytest.raises(ValidationError):
        FeeTier(**_subscribe(min_amount=Decimal("-1")))


def test_min_holding_days_non_negative() -> None:
    with pytest.raises(ValidationError):
        FeeTier(**_redeem(min_holding_days=-1))
