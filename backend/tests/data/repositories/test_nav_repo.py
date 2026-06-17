"""Unit tests for NavRepo.

Tests cover:
- upsert_many: insert new rows, update on conflict
- get_by_date_range: filter by trade_date
- latest_date: returns most recent trade_date
- missing_dates: returns dates not present in fund_nav

Requirements: 2.6, 1.11
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.repositories.nav_repo import NavRepo


@pytest.fixture
def repo() -> NavRepo:
    return NavRepo()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _nav_record(
    fund_code: str,
    trade_date: date,
    unit_nav: str = "1.0000",
) -> dict:
    from datetime import datetime, timezone
    return {
        "fund_code": fund_code,
        "trade_date": trade_date,
        "unit_nav": Decimal(unit_nav),
        "accum_nav": Decimal(unit_nav),
        "created_at": datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
    }


# ---------------------------------------------------------------------------
# upsert_many
# ---------------------------------------------------------------------------


class TestNavRepoUpsertMany:
    """NavRepo.upsert_many inserts and updates correctly."""

    async def test_insert_single_nav(
        self, repo: NavRepo, session: AsyncSession
    ) -> None:
        records = [_nav_record("100001", date(2024, 1, 2))]
        await repo.upsert_many(session, records)

        rows = await repo.get_by_date_range(
            session, "100001", date(2024, 1, 2), date(2024, 1, 2)
        )
        assert len(rows) == 1
        assert rows[0].fund_code == "100001"
        assert rows[0].trade_date == date(2024, 1, 2)

    async def test_insert_multiple_navs(
        self, repo: NavRepo, session: AsyncSession
    ) -> None:
        records = [
            _nav_record("100002", date(2024, 1, 2), "1.1000"),
            _nav_record("100002", date(2024, 1, 3), "1.1050"),
            _nav_record("100002", date(2024, 1, 4), "1.1100"),
        ]
        await repo.upsert_many(session, records)

        rows = await repo.get_by_date_range(
            session, "100002", date(2024, 1, 2), date(2024, 1, 4)
        )
        assert len(rows) == 3

    async def test_update_on_conflict(
        self, repo: NavRepo, session: AsyncSession
    ) -> None:
        from datetime import datetime, timezone
        ts = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        await repo.upsert_many(
            session, [_nav_record("100003", date(2024, 2, 1), "1.0000")]
        )
        await repo.upsert_many(
            session,
            [
                {
                    "fund_code": "100003",
                    "trade_date": date(2024, 2, 1),
                    "unit_nav": Decimal("1.0500"),
                    "accum_nav": Decimal("1.0500"),
                    "created_at": ts,
                }
            ],
        )

        rows = await repo.get_by_date_range(
            session, "100003", date(2024, 2, 1), date(2024, 2, 1)
        )
        assert len(rows) == 1
        assert rows[0].unit_nav == Decimal("1.0500")

    async def test_empty_list_returns_zero(
        self, repo: NavRepo, session: AsyncSession
    ) -> None:
        count = await repo.upsert_many(session, [])
        assert count == 0

    async def test_upsert_with_adj_nav(
        self, repo: NavRepo, session: AsyncSession
    ) -> None:
        """Requirement 2.6: adj_nav field must be stored."""
        from datetime import datetime, timezone
        ts = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        records = [
            {
                "fund_code": "100004",
                "trade_date": date(2024, 3, 1),
                "unit_nav": Decimal("1.2000"),
                "accum_nav": Decimal("2.5000"),
                "adj_nav": Decimal("2.4800"),
                "created_at": ts,
            }
        ]
        await repo.upsert_many(session, records)

        rows = await repo.get_by_date_range(
            session, "100004", date(2024, 3, 1), date(2024, 3, 1)
        )
        assert rows[0].adj_nav == Decimal("2.4800")


# ---------------------------------------------------------------------------
# get_by_date_range
# ---------------------------------------------------------------------------


class TestNavRepoGetByDateRange:
    """NavRepo.get_by_date_range filters by trade_date."""

    async def test_returns_rows_within_range(
        self, repo: NavRepo, session: AsyncSession
    ) -> None:
        records = [
            _nav_record("200001", date(2024, 5, 1)),
            _nav_record("200001", date(2024, 5, 2)),
            _nav_record("200001", date(2024, 5, 3)),
        ]
        await repo.upsert_many(session, records)

        rows = await repo.get_by_date_range(
            session, "200001", date(2024, 5, 1), date(2024, 5, 2)
        )
        assert len(rows) == 2
        assert all(r.fund_code == "200001" for r in rows)

    async def test_excludes_rows_outside_range(
        self, repo: NavRepo, session: AsyncSession
    ) -> None:
        records = [
            _nav_record("200002", date(2024, 4, 1)),
            _nav_record("200002", date(2024, 5, 1)),
        ]
        await repo.upsert_many(session, records)

        rows = await repo.get_by_date_range(
            session, "200002", date(2024, 5, 1), date(2024, 5, 31)
        )
        assert len(rows) == 1
        assert rows[0].trade_date == date(2024, 5, 1)

    async def test_returns_empty_for_unknown_fund(
        self, repo: NavRepo, session: AsyncSession
    ) -> None:
        rows = await repo.get_by_date_range(
            session, "UNKNOWN", date(2024, 1, 1), date(2024, 12, 31)
        )
        assert rows == []

    async def test_results_ordered_by_trade_date(
        self, repo: NavRepo, session: AsyncSession
    ) -> None:
        records = [
            _nav_record("200003", date(2024, 6, 3)),
            _nav_record("200003", date(2024, 6, 1)),
            _nav_record("200003", date(2024, 6, 2)),
        ]
        await repo.upsert_many(session, records)

        rows = await repo.get_by_date_range(
            session, "200003", date(2024, 6, 1), date(2024, 6, 3)
        )
        dates = [r.trade_date for r in rows]
        assert dates == sorted(dates)

    async def test_inclusive_boundary_dates(
        self, repo: NavRepo, session: AsyncSession
    ) -> None:
        records = [
            _nav_record("200004", date(2024, 7, 1)),
            _nav_record("200004", date(2024, 7, 31)),
        ]
        await repo.upsert_many(session, records)

        rows = await repo.get_by_date_range(
            session, "200004", date(2024, 7, 1), date(2024, 7, 31)
        )
        assert len(rows) == 2


# ---------------------------------------------------------------------------
# latest_date
# ---------------------------------------------------------------------------


class TestNavRepoLatestDate:
    """NavRepo.latest_date returns the most recent trade_date."""

    async def test_returns_none_when_no_records(
        self, repo: NavRepo, session: AsyncSession
    ) -> None:
        result = await repo.latest_date(session, "NODATA")
        assert result is None

    async def test_returns_correct_latest_date(
        self, repo: NavRepo, session: AsyncSession
    ) -> None:
        records = [
            _nav_record("300001", date(2024, 1, 2)),
            _nav_record("300001", date(2024, 1, 3)),
            _nav_record("300001", date(2024, 1, 4)),
        ]
        await repo.upsert_many(session, records)

        result = await repo.latest_date(session, "300001")
        assert result == date(2024, 1, 4)

    async def test_returns_date_type(
        self, repo: NavRepo, session: AsyncSession
    ) -> None:
        await repo.upsert_many(
            session, [_nav_record("300002", date(2024, 6, 15))]
        )
        result = await repo.latest_date(session, "300002")
        assert isinstance(result, date)

    async def test_single_record_returns_that_date(
        self, repo: NavRepo, session: AsyncSession
    ) -> None:
        await repo.upsert_many(
            session, [_nav_record("300003", date(2024, 9, 1))]
        )
        result = await repo.latest_date(session, "300003")
        assert result == date(2024, 9, 1)


# ---------------------------------------------------------------------------
# missing_dates
# ---------------------------------------------------------------------------


class TestNavRepoMissingDates:
    """NavRepo.missing_dates returns dates absent from fund_nav."""

    async def test_empty_expected_returns_empty(
        self, repo: NavRepo, session: AsyncSession
    ) -> None:
        result = await repo.missing_dates(session, "400001", [])
        assert result == []

    async def test_all_missing_when_no_records(
        self, repo: NavRepo, session: AsyncSession
    ) -> None:
        expected = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
        result = await repo.missing_dates(session, "NORECORD2", expected)
        assert set(result) == set(expected)

    async def test_returns_only_missing_dates(
        self, repo: NavRepo, session: AsyncSession
    ) -> None:
        records = [
            _nav_record("400002", date(2024, 2, 1)),
            _nav_record("400002", date(2024, 2, 3)),
        ]
        await repo.upsert_many(session, records)

        expected = [date(2024, 2, 1), date(2024, 2, 2), date(2024, 2, 3)]
        result = await repo.missing_dates(session, "400002", expected)
        assert result == [date(2024, 2, 2)]

    async def test_no_missing_when_all_present(
        self, repo: NavRepo, session: AsyncSession
    ) -> None:
        records = [
            _nav_record("400003", date(2024, 3, 1)),
            _nav_record("400003", date(2024, 3, 2)),
        ]
        await repo.upsert_many(session, records)

        expected = [date(2024, 3, 1), date(2024, 3, 2)]
        result = await repo.missing_dates(session, "400003", expected)
        assert result == []

    async def test_preserves_order_of_expected_dates(
        self, repo: NavRepo, session: AsyncSession
    ) -> None:
        expected = [date(2024, 4, 5), date(2024, 4, 3), date(2024, 4, 1)]
        result = await repo.missing_dates(session, "NORECORD3", expected)
        assert result == expected
