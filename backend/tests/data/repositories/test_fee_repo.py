"""Unit tests for FeeRepo.

Tests cover:
- upsert_many: insert new rows, update on conflict
- get_by_date_range: no-op (fees have no date column)
- latest_date: always returns None
- missing_dates: always returns empty list
- get_tiers: returns fee tiers filtered by fund/type
- get_applicable_tier: returns the matching tier for given params

Requirements: 2.6, 1.11
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.repositories.fee_repo import FeeRepo


@pytest.fixture
def repo() -> FeeRepo:
    return FeeRepo()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _subscribe_tier(
    fund_code: str,
    min_amount: str,
    max_amount: str | None,
    rate: str,
) -> dict:
    return {
        "fund_code": fund_code,
        "fee_type": "subscribe",
        "min_amount": Decimal(min_amount),
        "max_amount": Decimal(max_amount) if max_amount else None,
        "min_holding_days": 0,
        "max_holding_days": None,
        "rate": Decimal(rate),
    }


def _redeem_tier(
    fund_code: str,
    min_holding_days: int,
    max_holding_days: int | None,
    rate: str,
) -> dict:
    return {
        "fund_code": fund_code,
        "fee_type": "redeem",
        "min_amount": Decimal("0"),
        "max_amount": None,
        "min_holding_days": min_holding_days,
        "max_holding_days": max_holding_days,
        "rate": Decimal(rate),
    }


# ---------------------------------------------------------------------------
# upsert_many
# ---------------------------------------------------------------------------


class TestFeeRepoUpsertMany:
    """FeeRepo.upsert_many inserts and updates correctly."""

    async def test_insert_single_subscribe_tier(
        self, repo: FeeRepo, session: AsyncSession
    ) -> None:
        records = [_subscribe_tier("F00001", "0", "10000", "0.015000")]
        await repo.upsert_many(session, records)

        tiers = await repo.get_tiers(session, "F00001", "subscribe")
        assert len(tiers) == 1
        assert tiers[0].rate == Decimal("0.015000")

    async def test_insert_tiered_subscribe_schedule(
        self, repo: FeeRepo, session: AsyncSession
    ) -> None:
        records = [
            _subscribe_tier("F00002", "0", "10000", "0.015000"),
            _subscribe_tier("F00002", "10000", "500000", "0.012000"),
            _subscribe_tier("F00002", "500000", None, "0.001000"),
        ]
        await repo.upsert_many(session, records)

        tiers = await repo.get_tiers(session, "F00002", "subscribe")
        assert len(tiers) == 3

    async def test_insert_redeem_tiers(
        self, repo: FeeRepo, session: AsyncSession
    ) -> None:
        records = [
            _redeem_tier("F00003", 0, 7, "0.015000"),
            _redeem_tier("F00003", 7, 365, "0.005000"),
            _redeem_tier("F00003", 365, None, "0.000000"),
        ]
        await repo.upsert_many(session, records)

        tiers = await repo.get_tiers(session, "F00003", "redeem")
        assert len(tiers) == 3

    async def test_update_on_conflict(
        self, repo: FeeRepo, session: AsyncSession
    ) -> None:
        await repo.upsert_many(
            session, [_subscribe_tier("F00004", "0", "10000", "0.015000")]
        )
        await repo.upsert_many(
            session,
            [
                {
                    "fund_code": "F00004",
                    "fee_type": "subscribe",
                    "min_amount": Decimal("0"),
                    "max_amount": Decimal("10000"),
                    "min_holding_days": 0,
                    "max_holding_days": None,
                    "rate": Decimal("0.010000"),
                }
            ],
        )

        tiers = await repo.get_tiers(session, "F00004", "subscribe")
        assert len(tiers) == 1
        assert tiers[0].rate == Decimal("0.010000")

    async def test_empty_list_returns_zero(
        self, repo: FeeRepo, session: AsyncSession
    ) -> None:
        count = await repo.upsert_many(session, [])
        assert count == 0

    async def test_insert_both_fee_types(
        self, repo: FeeRepo, session: AsyncSession
    ) -> None:
        records = [
            _subscribe_tier("F00005", "0", None, "0.015000"),
            _redeem_tier("F00005", 0, 365, "0.005000"),
        ]
        await repo.upsert_many(session, records)

        all_tiers = await repo.get_tiers(session, "F00005")
        assert len(all_tiers) == 2


# ---------------------------------------------------------------------------
# get_by_date_range (no-op)
# ---------------------------------------------------------------------------


class TestFeeRepoGetByDateRange:
    """FeeRepo.get_by_date_range returns all tiers (date range ignored)."""

    async def test_returns_all_tiers_regardless_of_date(
        self, repo: FeeRepo, session: AsyncSession
    ) -> None:
        records = [
            _subscribe_tier("F10001", "0", "10000", "0.015000"),
            _subscribe_tier("F10001", "10000", None, "0.001000"),
        ]
        await repo.upsert_many(session, records)

        # 日期范围参数被忽略，返回所有费率
        rows = await repo.get_by_date_range(
            session, "F10001", date(2020, 1, 1), date(2020, 12, 31)
        )
        assert len(rows) == 2

    async def test_returns_empty_for_unknown_fund(
        self, repo: FeeRepo, session: AsyncSession
    ) -> None:
        rows = await repo.get_by_date_range(
            session, "UNKNOWN_F", date(2024, 1, 1), date(2024, 12, 31)
        )
        assert rows == []


# ---------------------------------------------------------------------------
# latest_date (no-op)
# ---------------------------------------------------------------------------


class TestFeeRepoLatestDate:
    """FeeRepo.latest_date always returns None."""

    async def test_returns_none_always(
        self, repo: FeeRepo, session: AsyncSession
    ) -> None:
        await repo.upsert_many(
            session, [_subscribe_tier("F20001", "0", None, "0.015000")]
        )
        result = await repo.latest_date(session, "F20001")
        assert result is None

    async def test_returns_none_for_unknown_fund(
        self, repo: FeeRepo, session: AsyncSession
    ) -> None:
        result = await repo.latest_date(session, "UNKNOWN_F2")
        assert result is None


# ---------------------------------------------------------------------------
# missing_dates (no-op)
# ---------------------------------------------------------------------------


class TestFeeRepoMissingDates:
    """FeeRepo.missing_dates always returns empty list."""

    async def test_returns_empty_always(
        self, repo: FeeRepo, session: AsyncSession
    ) -> None:
        await repo.upsert_many(
            session, [_subscribe_tier("F30001", "0", None, "0.015000")]
        )
        expected = [date(2024, 1, 1), date(2024, 6, 1)]
        result = await repo.missing_dates(session, "F30001", expected)
        assert result == []

    async def test_returns_empty_for_empty_expected(
        self, repo: FeeRepo, session: AsyncSession
    ) -> None:
        result = await repo.missing_dates(session, "F30002", [])
        assert result == []


# ---------------------------------------------------------------------------
# get_tiers
# ---------------------------------------------------------------------------


class TestFeeRepoGetTiers:
    """FeeRepo.get_tiers returns fee tiers filtered by fund/type."""

    async def test_returns_all_tiers_for_fund(
        self, repo: FeeRepo, session: AsyncSession
    ) -> None:
        records = [
            _subscribe_tier("F40001", "0", "10000", "0.015000"),
            _redeem_tier("F40001", 0, 365, "0.005000"),
        ]
        await repo.upsert_many(session, records)

        tiers = await repo.get_tiers(session, "F40001")
        assert len(tiers) == 2

    async def test_filters_by_fee_type(
        self, repo: FeeRepo, session: AsyncSession
    ) -> None:
        records = [
            _subscribe_tier("F40002", "0", "10000", "0.015000"),
            _subscribe_tier("F40002", "10000", None, "0.001000"),
            _redeem_tier("F40002", 0, 365, "0.005000"),
        ]
        await repo.upsert_many(session, records)

        subscribe_tiers = await repo.get_tiers(session, "F40002", "subscribe")
        assert len(subscribe_tiers) == 2
        assert all(t.fee_type == "subscribe" for t in subscribe_tiers)

        redeem_tiers = await repo.get_tiers(session, "F40002", "redeem")
        assert len(redeem_tiers) == 1
        assert redeem_tiers[0].fee_type == "redeem"

    async def test_returns_empty_for_unknown_fund(
        self, repo: FeeRepo, session: AsyncSession
    ) -> None:
        tiers = await repo.get_tiers(session, "UNKNOWN_F3")
        assert tiers == []


# ---------------------------------------------------------------------------
# get_applicable_tier
# ---------------------------------------------------------------------------


class TestFeeRepoGetApplicableTier:
    """FeeRepo.get_applicable_tier returns the matching tier."""

    async def test_subscribe_tier_by_amount_first_bracket(
        self, repo: FeeRepo, session: AsyncSession
    ) -> None:
        records = [
            _subscribe_tier("F50001", "0", "10000", "0.015000"),
            _subscribe_tier("F50001", "10000", "500000", "0.012000"),
            _subscribe_tier("F50001", "500000", None, "0.001000"),
        ]
        await repo.upsert_many(session, records)

        tier = await repo.get_applicable_tier(
            session, "F50001", "subscribe", amount=Decimal("5000")
        )
        assert tier is not None
        assert tier.rate == Decimal("0.015000")

    async def test_subscribe_tier_by_amount_middle_bracket(
        self, repo: FeeRepo, session: AsyncSession
    ) -> None:
        records = [
            _subscribe_tier("F50002", "0", "10000", "0.015000"),
            _subscribe_tier("F50002", "10000", "500000", "0.012000"),
            _subscribe_tier("F50002", "500000", None, "0.001000"),
        ]
        await repo.upsert_many(session, records)

        tier = await repo.get_applicable_tier(
            session, "F50002", "subscribe", amount=Decimal("50000")
        )
        assert tier is not None
        assert tier.rate == Decimal("0.012000")

    async def test_subscribe_tier_by_amount_last_bracket_no_cap(
        self, repo: FeeRepo, session: AsyncSession
    ) -> None:
        records = [
            _subscribe_tier("F50003", "0", "10000", "0.015000"),
            _subscribe_tier("F50003", "10000", "500000", "0.012000"),
            _subscribe_tier("F50003", "500000", None, "0.001000"),
        ]
        await repo.upsert_many(session, records)

        tier = await repo.get_applicable_tier(
            session, "F50003", "subscribe", amount=Decimal("1000000")
        )
        assert tier is not None
        assert tier.rate == Decimal("0.001000")

    async def test_redeem_tier_by_holding_days_short_term(
        self, repo: FeeRepo, session: AsyncSession
    ) -> None:
        records = [
            _redeem_tier("F50004", 0, 7, "0.015000"),
            _redeem_tier("F50004", 7, 365, "0.005000"),
            _redeem_tier("F50004", 365, None, "0.000000"),
        ]
        await repo.upsert_many(session, records)

        tier = await repo.get_applicable_tier(
            session, "F50004", "redeem", holding_days=3
        )
        assert tier is not None
        assert tier.rate == Decimal("0.015000")

    async def test_redeem_tier_by_holding_days_long_term(
        self, repo: FeeRepo, session: AsyncSession
    ) -> None:
        records = [
            _redeem_tier("F50005", 0, 7, "0.015000"),
            _redeem_tier("F50005", 7, 365, "0.005000"),
            _redeem_tier("F50005", 365, None, "0.000000"),
        ]
        await repo.upsert_many(session, records)

        tier = await repo.get_applicable_tier(
            session, "F50005", "redeem", holding_days=500
        )
        assert tier is not None
        assert tier.rate == Decimal("0.000000")

    async def test_returns_none_for_unknown_fund(
        self, repo: FeeRepo, session: AsyncSession
    ) -> None:
        tier = await repo.get_applicable_tier(
            session, "UNKNOWN_F4", "subscribe", amount=Decimal("10000")
        )
        assert tier is None

    async def test_boundary_amount_excluded_from_upper_bracket(
        self, repo: FeeRepo, session: AsyncSession
    ) -> None:
        """max_amount 是排他上界：amount=10000 应匹配第二档而非第一档。"""
        records = [
            _subscribe_tier("F50006", "0", "10000", "0.015000"),
            _subscribe_tier("F50006", "10000", None, "0.012000"),
        ]
        await repo.upsert_many(session, records)

        tier = await repo.get_applicable_tier(
            session, "F50006", "subscribe", amount=Decimal("10000")
        )
        assert tier is not None
        assert tier.rate == Decimal("0.012000")

    async def test_boundary_holding_days_excluded_from_upper_bracket(
        self, repo: FeeRepo, session: AsyncSession
    ) -> None:
        """max_holding_days 是排他上界：holding_days=7 应匹配第二档。"""
        records = [
            _redeem_tier("F50007", 0, 7, "0.015000"),
            _redeem_tier("F50007", 7, None, "0.005000"),
        ]
        await repo.upsert_many(session, records)

        tier = await repo.get_applicable_tier(
            session, "F50007", "redeem", holding_days=7
        )
        assert tier is not None
        assert tier.rate == Decimal("0.005000")
