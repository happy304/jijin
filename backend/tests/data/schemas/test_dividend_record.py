"""Tests for DividendRecord DTO."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.data.schemas import DividendRecord


def _minimal() -> dict:
    return {"fund_code": "000001", "ex_date": date(2024, 3, 15)}


def test_minimal_construction() -> None:
    d = DividendRecord(**_minimal())
    assert d.split_ratio == Decimal("1")
    assert d.dividend_per_share == Decimal("0")


def test_full_round_trip_json() -> None:
    d = DividendRecord(
        fund_code="000001",
        ex_date=date(2024, 3, 15),
        record_date=date(2024, 3, 14),
        pay_date=date(2024, 3, 20),
        dividend_per_share=Decimal("0.05"),
        split_ratio=Decimal("1"),
    )
    restored = DividendRecord.model_validate_json(d.model_dump_json())
    assert restored.dividend_per_share == d.dividend_per_share
    assert restored.ex_date == d.ex_date
    assert restored.pay_date == d.pay_date


def test_split_ratio_default_is_one() -> None:
    d = DividendRecord(**_minimal())
    assert d.split_ratio == Decimal("1")


def test_split_ratio_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        DividendRecord(**_minimal(), split_ratio=Decimal("0"))
    with pytest.raises(ValidationError):
        DividendRecord(**_minimal(), split_ratio=Decimal("-1"))


def test_dividend_per_share_non_negative() -> None:
    with pytest.raises(ValidationError):
        DividendRecord(**_minimal(), dividend_per_share=Decimal("-0.001"))


def test_unknown_field_rejected() -> None:
    with pytest.raises(ValidationError):
        DividendRecord(**_minimal(), bogus="x")


def test_pure_split_no_dividend() -> None:
    d = DividendRecord(
        fund_code="000001",
        ex_date=date(2024, 3, 15),
        dividend_per_share=Decimal("0"),
        split_ratio=Decimal("2"),
    )
    assert d.split_ratio == Decimal("2")
    assert d.dividend_per_share == Decimal("0")
