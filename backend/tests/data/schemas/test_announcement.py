"""Tests for Announcement DTO."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from app.data.schemas import Announcement, AnnouncementCategory


def _minimal() -> dict:
    return {"fund_code": "000001"}


def test_minimal_construction() -> None:
    a = Announcement(**_minimal())
    assert a.fund_code == "000001"
    assert a.requires_review is False
    assert a.category is None
    assert a.id is None


def test_full_round_trip_json() -> None:
    a = Announcement(
        id=42,
        fund_code="000001",
        title="关于限制大额申购的公告",
        category=AnnouncementCategory.LIMIT_PURCHASE,
        publish_date=date(2024, 1, 10),
        content_url="https://example.com/ann/42",
        parsed_data={"limit_amount": "1000000", "effective_date": "2024-01-15"},
        requires_review=False,
    )
    restored = Announcement.model_validate_json(a.model_dump_json())
    assert restored.id == 42
    assert restored.category == AnnouncementCategory.LIMIT_PURCHASE
    assert restored.parsed_data == a.parsed_data
    assert restored.publish_date == date(2024, 1, 10)


def test_category_enum_validation() -> None:
    a = Announcement(**_minimal(), category="DIVIDEND")
    assert a.category == AnnouncementCategory.DIVIDEND


def test_invalid_category_rejected() -> None:
    with pytest.raises(ValidationError):
        Announcement(**_minimal(), category="INVALID_CATEGORY")


def test_requires_review_defaults_false() -> None:
    a = Announcement(**_minimal())
    assert a.requires_review is False


def test_requires_review_can_be_set_true() -> None:
    a = Announcement(**_minimal(), requires_review=True)
    assert a.requires_review is True


def test_unknown_field_rejected() -> None:
    with pytest.raises(ValidationError):
        Announcement(**_minimal(), bogus="x")


def test_all_categories_accepted() -> None:
    for cat in AnnouncementCategory:
        a = Announcement(**_minimal(), category=cat)
        assert a.category == cat
