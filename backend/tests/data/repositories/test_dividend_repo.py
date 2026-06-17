"""Unit tests for DividendRepo.

Tests cover:
- upsert_many: insert new rows, update on conflict
- get_by_date_range: filter by ex_date
- latest_date: returns most recent ex_date
- missing_dates: returns dates not present in fund_dividends

Requirements: 2.6, 1.11
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.repositories.dividend_repo import DividendRepo


@pytest.fixture
def repo() -> DividendRepo:
    return DividendRepo()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dividend(
    fund_code: str,
    ex_date: date,
    dividend_per_share: str = "0.050000",
    split_ratio: str = "1.000000",
) -> dict:
    return {
        "fund_code": fund_code,
        "ex_date": ex_date,
        "dividend_per_share": Decimal(dividend_per_share),
        "split_ratio": Decimal(split_ratio),
    }


# ---------------------------------------------------------------------------
# upsert_many
# ---------------------------------------------------------------------------


class TestDividendRepoUpsertMany:
    """DividendRepo.upsert_many inserts and updates correctly."""

    async def test_insert_single_dividend(
        self, repo: DividendRepo, session: AsyncSession
    ) -> None:
        records = [_dividend("D00001", date(2024, 6, 15))]
        await repo.upsert_many(session, records)

        rows = await repo.get_by_date_range(
            session, "D00001", date(2024, 6, 15), date(2024, 6, 15)
        )
        assert len(rows) == 1
        assert rows[0].fund_code == "D00001"
        assert rows[0].ex_date == date(2024, 6, 15)

    async def test_insert_multiple_dividends(
        self, repo: DividendRepo, session: AsyncSession
    ) -> None:
        records = [
            _dividend("D00002", date(2024, 3, 20), "0.030000"),
            _dividend("D00002", date(2024, 6, 20), "0.050000"),
            _dividend("D00002", date(2024, 9, 20), "0.040000"),
        ]
        await repo.upsert_many(session, records)

        rows = await repo.get_by_date_range(
            session, "D00002", date(2024, 1, 1), date(2024, 12, 31)
        )
        assert len(rows) == 3

    async def test_update_on_conflict(
        self, repo: DividendRepo, session: AsyncSession
    ) -> None:
        await repo.upsert_many(
            session, [_dividend("D00003", date(2024, 4, 10), "0.020000")]
        )
        await repo.upsert_many(
            session,
            [
                {
                    "fund_code": "D00003",
                    "ex_date": date(2024, 4, 10),
                    "dividend_per_share": Decimal("0.080000"),
                    "split_ratio": Decimal("1.000000"),
                }
            ],
        )

        rows = await repo.get_by_date_range(
            session, "D00003", date(2024, 4, 10), date(2024, 4, 10)
        )
        assert len(rows) == 1
        assert rows[0].dividend_per_share == Decimal("0.080000")

    async def test_empty_list_returns_zero(
        self, repo: DividendRepo, session: AsyncSession
    ) -> None:
        count = await repo.upsert_many(session, [])
        assert count == 0

    async def test_insert_split_event(
        self, repo: DividendRepo, session: AsyncSession
    ) -> None:
        """纯拆分事件：dividend_per_share=0, split_ratio!=1."""
        records = [_dividend("D00004", date(2024, 7, 1), "0.000000", "2.000000")]
        await repo.upsert_many(session, records)

        rows = await repo.get_by_date_range(
            session, "D00004", date(2024, 7, 1), date(2024, 7, 1)
        )
        assert rows[0].split_ratio == Decimal("2.000000")
        assert rows[0].dividend_per_share == Decimal("0.000000")

    async def test_insert_with_optional_dates(
        self, repo: DividendRepo, session: AsyncSession
    ) -> None:
        records = [
            {
                "fund_code": "D00005",
                "ex_date": date(2024, 8, 15),
                "record_date": date(2024, 8, 14),
                "pay_date": date(2024, 8, 20),
                "dividend_per_share": Decimal("0.060000"),
                "split_ratio": Decimal("1.000000"),
            }
        ]
        await repo.upsert_many(session, records)

        rows = await repo.get_by_date_range(
            session, "D00005", date(2024, 8, 15), date(2024, 8, 15)
        )
        assert rows[0].record_date == date(2024, 8, 14)
        assert rows[0].pay_date == date(2024, 8, 20)


# ---------------------------------------------------------------------------
# get_by_date_range
# ---------------------------------------------------------------------------


class TestDividendRepoGetByDateRange:
    """DividendRepo.get_by_date_range filters by ex_date."""

    async def test_returns_rows_within_range(
        self, repo: DividendRepo, session: AsyncSession
    ) -> None:
        records = [
            _dividend("D10001", date(2024, 1, 15)),
            _dividend("D10001", date(2024, 6, 15)),
            _dividend("D10001", date(2024, 12, 15)),
        ]
        await repo.upsert_many(session, records)

        rows = await repo.get_by_date_range(
            session, "D10001", date(2024, 1, 1), date(2024, 6, 30)
        )
        assert len(rows) == 2

    async def test_excludes_rows_outside_range(
        self, repo: DividendRepo, session: AsyncSession
    ) -> None:
        records = [
            _dividend("D10002", date(2023, 12, 15)),
            _dividend("D10002", date(2024, 6, 15)),
        ]
        await repo.upsert_many(session, records)

        rows = await repo.get_by_date_range(
            session, "D10002", date(2024, 1, 1), date(2024, 12, 31)
        )
        assert len(rows) == 1
        assert rows[0].ex_date == date(2024, 6, 15)

    async def test_returns_empty_for_unknown_fund(
        self, repo: DividendRepo, session: AsyncSession
    ) -> None:
        rows = await repo.get_by_date_range(
            session, "UNKNOWN_D", date(2024, 1, 1), date(2024, 12, 31)
        )
        assert rows == []

    async def test_results_ordered_by_ex_date(
        self, repo: DividendRepo, session: AsyncSession
    ) -> None:
        records = [
            _dividend("D10003", date(2024, 9, 15)),
            _dividend("D10003", date(2024, 3, 15)),
            _dividend("D10003", date(2024, 6, 15)),
        ]
        await repo.upsert_many(session, records)

        rows = await repo.get_by_date_range(
            session, "D10003", date(2024, 1, 1), date(2024, 12, 31)
        )
        dates = [r.ex_date for r in rows]
        assert dates == sorted(dates)

    async def test_inclusive_boundary_dates(
        self, repo: DividendRepo, session: AsyncSession
    ) -> None:
        records = [
            _dividend("D10004", date(2024, 1, 1)),
            _dividend("D10004", date(2024, 12, 31)),
        ]
        await repo.upsert_many(session, records)

        rows = await repo.get_by_date_range(
            session, "D10004", date(2024, 1, 1), date(2024, 12, 31)
        )
        assert len(rows) == 2


# ---------------------------------------------------------------------------
# latest_date
# ---------------------------------------------------------------------------


class TestDividendRepoLatestDate:
    """DividendRepo.latest_date returns the most recent ex_date."""

    async def test_returns_none_when_no_records(
        self, repo: DividendRepo, session: AsyncSession
    ) -> None:
        result = await repo.latest_date(session, "NODATA_D")
        assert result is None

    async def test_returns_correct_latest_date(
        self, repo: DividendRepo, session: AsyncSession
    ) -> None:
        records = [
            _dividend("D20001", date(2024, 3, 15)),
            _dividend("D20001", date(2024, 6, 15)),
            _dividend("D20001", date(2024, 9, 15)),
        ]
        await repo.upsert_many(session, records)

        result = await repo.latest_date(session, "D20001")
        assert result == date(2024, 9, 15)

    async def test_returns_date_type(
        self, repo: DividendRepo, session: AsyncSession
    ) -> None:
        await repo.upsert_many(
            session, [_dividend("D20002", date(2024, 6, 15))]
        )
        result = await repo.latest_date(session, "D20002")
        assert isinstance(result, date)

    async def test_single_record_returns_that_date(
        self, repo: DividendRepo, session: AsyncSession
    ) -> None:
        await repo.upsert_many(
            session, [_dividend("D20003", date(2024, 11, 1))]
        )
        result = await repo.latest_date(session, "D20003")
        assert result == date(2024, 11, 1)


# ---------------------------------------------------------------------------
# missing_dates
# ---------------------------------------------------------------------------


class TestDividendRepoMissingDates:
    """DividendRepo.missing_dates returns dates absent from fund_dividends."""

    async def test_empty_expected_returns_empty(
        self, repo: DividendRepo, session: AsyncSession
    ) -> None:
        result = await repo.missing_dates(session, "D30001", [])
        assert result == []

    async def test_all_missing_when_no_records(
        self, repo: DividendRepo, session: AsyncSession
    ) -> None:
        expected = [date(2024, 3, 15), date(2024, 6, 15)]
        result = await repo.missing_dates(session, "NORECORD_D", expected)
        assert set(result) == set(expected)

    async def test_returns_only_missing_dates(
        self, repo: DividendRepo, session: AsyncSession
    ) -> None:
        records = [
            _dividend("D30002", date(2024, 3, 15)),
            _dividend("D30002", date(2024, 9, 15)),
        ]
        await repo.upsert_many(session, records)

        expected = [date(2024, 3, 15), date(2024, 6, 15), date(2024, 9, 15)]
        result = await repo.missing_dates(session, "D30002", expected)
        assert result == [date(2024, 6, 15)]

    async def test_no_missing_when_all_present(
        self, repo: DividendRepo, session: AsyncSession
    ) -> None:
        records = [
            _dividend("D30003", date(2024, 3, 15)),
            _dividend("D30003", date(2024, 6, 15)),
        ]
        await repo.upsert_many(session, records)

        expected = [date(2024, 3, 15), date(2024, 6, 15)]
        result = await repo.missing_dates(session, "D30003", expected)
        assert result == []

    async def test_preserves_order_of_expected_dates(
        self, repo: DividendRepo, session: AsyncSession
    ) -> None:
        expected = [date(2024, 9, 15), date(2024, 6, 15), date(2024, 3, 15)]
        result = await repo.missing_dates(session, "NORECORD_D2", expected)
        assert result == expected
