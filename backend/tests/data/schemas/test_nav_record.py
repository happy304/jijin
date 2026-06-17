"""Tests for NavRecord DTO."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from app.data.schemas import NavRecord, NavStatus


def _minimal() -> dict:
    return {"fund_code": "000001", "trade_date": date(2024, 1, 2)}


def test_minimal_construction() -> None:
    r = NavRecord(**_minimal())
    assert r.fund_code == "000001"
    assert r.trade_date == date(2024, 1, 2)
    assert r.status == NavStatus.NORMAL


def test_full_round_trip_json() -> None:
    r = NavRecord(
        fund_code="000001",
        trade_date=date(2024, 1, 2),
        unit_nav=Decimal("1.2345"),
        accum_nav=Decimal("3.4567"),
        adj_nav=Decimal("2.1000"),
        daily_return=Decimal("0.0123"),
        status=NavStatus.NORMAL,
        source="eastmoney",
    )
    restored = NavRecord.model_validate_json(r.model_dump_json())
    assert restored.unit_nav == r.unit_nav
    assert restored.adj_nav == r.adj_nav
    assert restored.trade_date == r.trade_date


def test_decimal_precision_preserved() -> None:
    r = NavRecord(**_minimal(), unit_nav=Decimal("1.234567"))
    data = json.loads(r.model_dump_json())
    assert data["unit_nav"] == "1.234567"


def test_unit_nav_non_negative() -> None:
    with pytest.raises(ValidationError):
        NavRecord(**_minimal(), unit_nav=Decimal("-0.001"))


def test_daily_return_bounds() -> None:
    # Valid extremes
    NavRecord(**_minimal(), daily_return=Decimal("-1"))
    NavRecord(**_minimal(), daily_return=Decimal("100"))
    # Out of bounds
    with pytest.raises(ValidationError):
        NavRecord(**_minimal(), daily_return=Decimal("-1.001"))
    with pytest.raises(ValidationError):
        NavRecord(**_minimal(), daily_return=Decimal("100.001"))


def test_unknown_field_rejected() -> None:
    with pytest.raises(ValidationError):
        NavRecord(**_minimal(), bogus="x")


def test_status_enum() -> None:
    r = NavRecord(**_minimal(), status="suspended")
    assert r.status == NavStatus.SUSPENDED


@given(
    unit_nav=st.decimals(min_value=Decimal("0"), max_value=Decimal("9999"), places=6),
    daily_return=st.decimals(min_value=Decimal("-1"), max_value=Decimal("100"), places=6),
)
@settings(max_examples=50)
def test_round_trip_hypothesis(unit_nav: Decimal, daily_return: Decimal) -> None:
    r = NavRecord(
        fund_code="000001",
        trade_date=date(2024, 1, 2),
        unit_nav=unit_nav,
        daily_return=daily_return,
    )
    restored = NavRecord.model_validate_json(r.model_dump_json())
    assert restored.unit_nav == r.unit_nav
    assert restored.daily_return == r.daily_return
