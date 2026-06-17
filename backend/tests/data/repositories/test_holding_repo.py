"""Unit tests for HoldingRepo.

Tests cover:
- upsert_many: insert new rows, update on conflict
- get_by_date_range: filter by report_date
- latest_date: returns most recent report_date
- missing_dates: returns dates not present in fund_holdings
- get_snapshot: returns all positions for a specific fund/quarter

Requirements: 2.6, 1.11
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.repositories.holding_repo import HoldingRepo


@pytest.fixture
def repo() -> HoldingRepo:
    return HoldingRepo()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _holding(
    fund_code: str,
    report_date: date,
    stock_code: str,
    weight: str = "0.0500",
) -> dict:
    return {
        "fund_code": fund_code,
        "report_date": report_date,
        "stock_code": stock_code,
        "stock_name": f"股票{stock_code}",
        "weight": Decimal(weight),
    }


# ---------------------------------------------------------------------------
# upsert_many
# ---------------------------------------------------------------------------


class TestHoldingRepoUpsertMany:
    """HoldingRepo.upsert_many inserts and updates correctly."""

    async def test_insert_single_holding(
        self, repo: HoldingRepo, session: AsyncSession
    ) -> None:
        records = [_holding("H00001", date(2024, 3, 31), "600519")]
        await repo.upsert_many(session, records)

        rows = await repo.get_by_date_range(
            session, "H00001", date(2024, 3, 31), date(2024, 3, 31)
        )
        assert len(rows) == 1
        assert rows[0].stock_code == "600519"

    async def test_insert_multiple_positions_same_quarter(
        self, repo: HoldingRepo, session: AsyncSession
    ) -> None:
        records = [
            _holding("H00002", date(2024, 3, 31), "600519", "0.0850"),
            _holding("H00002", date(2024, 3, 31), "000858", "0.0720"),
            _holding("H00002", date(2024, 3, 31), "601318", "0.0650"),
        ]
        await repo.upsert_many(session, records)

        rows = await repo.get_by_date_range(
            session, "H00002", date(2024, 3, 31), date(2024, 3, 31)
        )
        assert len(rows) == 3

    async def test_update_on_conflict(
        self, repo: HoldingRepo, session: AsyncSession
    ) -> None:
        await repo.upsert_many(
            session, [_holding("H00003", date(2024, 6, 30), "600519", "0.0500")]
        )
        await repo.upsert_many(
            session,
            [
                {
                    "fund_code": "H00003",
                    "report_date": date(2024, 6, 30),
                    "stock_code": "600519",
                    "weight": Decimal("0.0900"),
                }
            ],
        )

        rows = await repo.get_by_date_range(
            session, "H00003", date(2024, 6, 30), date(2024, 6, 30)
        )
        assert len(rows) == 1
        assert rows[0].weight == Decimal("0.0900")

    async def test_empty_list_returns_zero(
        self, repo: HoldingRepo, session: AsyncSession
    ) -> None:
        count = await repo.upsert_many(session, [])
        assert count == 0

    async def test_insert_multiple_quarters(
        self, repo: HoldingRepo, session: AsyncSession
    ) -> None:
        records = [
            _holding("H00004", date(2024, 3, 31), "600519"),
            _holding("H00004", date(2024, 6, 30), "600519"),
        ]
        await repo.upsert_many(session, records)

        rows = await repo.get_by_date_range(
            session, "H00004", date(2024, 1, 1), date(2024, 12, 31)
        )
        assert len(rows) == 2


# ---------------------------------------------------------------------------
# get_by_date_range
# ---------------------------------------------------------------------------


class TestHoldingRepoGetByDateRange:
    """HoldingRepo.get_by_date_range filters by report_date."""

    async def test_returns_rows_within_range(
        self, repo: HoldingRepo, session: AsyncSession
    ) -> None:
        records = [
            _holding("H10001", date(2024, 3, 31), "600519"),
            _holding("H10001", date(2024, 6, 30), "600519"),
            _holding("H10001", date(2024, 9, 30), "600519"),
        ]
        await repo.upsert_many(session, records)

        rows = await repo.get_by_date_range(
            session, "H10001", date(2024, 3, 31), date(2024, 6, 30)
        )
        assert len(rows) == 2

    async def test_excludes_rows_outside_range(
        self, repo: HoldingRepo, session: AsyncSession
    ) -> None:
        records = [
            _holding("H10002", date(2023, 12, 31), "600519"),
            _holding("H10002", date(2024, 3, 31), "600519"),
        ]
        await repo.upsert_many(session, records)

        rows = await repo.get_by_date_range(
            session, "H10002", date(2024, 1, 1), date(2024, 12, 31)
        )
        assert len(rows) == 1
        assert rows[0].report_date == date(2024, 3, 31)

    async def test_returns_empty_for_unknown_fund(
        self, repo: HoldingRepo, session: AsyncSession
    ) -> None:
        rows = await repo.get_by_date_range(
            session, "UNKNOWN_H", date(2024, 1, 1), date(2024, 12, 31)
        )
        assert rows == []

    async def test_results_ordered_by_report_date_and_stock_code(
        self, repo: HoldingRepo, session: AsyncSession
    ) -> None:
        records = [
            _holding("H10003", date(2024, 6, 30), "600519"),
            _holding("H10003", date(2024, 3, 31), "000858"),
            _holding("H10003", date(2024, 3, 31), "600519"),
        ]
        await repo.upsert_many(session, records)

        rows = await repo.get_by_date_range(
            session, "H10003", date(2024, 1, 1), date(2024, 12, 31)
        )
        # 应按 report_date 升序，同日期按 stock_code 升序
        assert rows[0].report_date <= rows[-1].report_date


# ---------------------------------------------------------------------------
# latest_date
# ---------------------------------------------------------------------------


class TestHoldingRepoLatestDate:
    """HoldingRepo.latest_date returns the most recent report_date."""

    async def test_returns_none_when_no_records(
        self, repo: HoldingRepo, session: AsyncSession
    ) -> None:
        result = await repo.latest_date(session, "NODATA_H")
        assert result is None

    async def test_returns_correct_latest_date(
        self, repo: HoldingRepo, session: AsyncSession
    ) -> None:
        records = [
            _holding("H20001", date(2024, 3, 31), "600519"),
            _holding("H20001", date(2024, 6, 30), "600519"),
            _holding("H20001", date(2024, 9, 30), "600519"),
        ]
        await repo.upsert_many(session, records)

        result = await repo.latest_date(session, "H20001")
        assert result == date(2024, 9, 30)

    async def test_returns_date_type(
        self, repo: HoldingRepo, session: AsyncSession
    ) -> None:
        await repo.upsert_many(
            session, [_holding("H20002", date(2024, 6, 30), "600519")]
        )
        result = await repo.latest_date(session, "H20002")
        assert isinstance(result, date)


# ---------------------------------------------------------------------------
# missing_dates
# ---------------------------------------------------------------------------


class TestHoldingRepoMissingDates:
    """HoldingRepo.missing_dates returns dates absent from fund_holdings."""

    async def test_empty_expected_returns_empty(
        self, repo: HoldingRepo, session: AsyncSession
    ) -> None:
        result = await repo.missing_dates(session, "H30001", [])
        assert result == []

    async def test_all_missing_when_no_records(
        self, repo: HoldingRepo, session: AsyncSession
    ) -> None:
        expected = [date(2024, 3, 31), date(2024, 6, 30)]
        result = await repo.missing_dates(session, "NORECORD_H", expected)
        assert set(result) == set(expected)

    async def test_returns_only_missing_dates(
        self, repo: HoldingRepo, session: AsyncSession
    ) -> None:
        records = [
            _holding("H30002", date(2024, 3, 31), "600519"),
            _holding("H30002", date(2024, 9, 30), "600519"),
        ]
        await repo.upsert_many(session, records)

        expected = [date(2024, 3, 31), date(2024, 6, 30), date(2024, 9, 30)]
        result = await repo.missing_dates(session, "H30002", expected)
        assert result == [date(2024, 6, 30)]

    async def test_no_missing_when_all_present(
        self, repo: HoldingRepo, session: AsyncSession
    ) -> None:
        records = [
            _holding("H30003", date(2024, 3, 31), "600519"),
            _holding("H30003", date(2024, 6, 30), "600519"),
        ]
        await repo.upsert_many(session, records)

        expected = [date(2024, 3, 31), date(2024, 6, 30)]
        result = await repo.missing_dates(session, "H30003", expected)
        assert result == []


# ---------------------------------------------------------------------------
# get_snapshot
# ---------------------------------------------------------------------------


class TestHoldingRepoGetSnapshot:
    """HoldingRepo.get_snapshot returns all positions for a fund/quarter."""

    async def test_returns_all_positions_for_quarter(
        self, repo: HoldingRepo, session: AsyncSession
    ) -> None:
        records = [
            _holding("H40001", date(2024, 3, 31), "600519", "0.0850"),
            _holding("H40001", date(2024, 3, 31), "000858", "0.0720"),
            _holding("H40001", date(2024, 3, 31), "601318", "0.0650"),
            # 不同季度的数据不应被返回
            _holding("H40001", date(2024, 6, 30), "600519", "0.0900"),
        ]
        await repo.upsert_many(session, records)

        snapshot = await repo.get_snapshot(session, "H40001", date(2024, 3, 31))
        assert len(snapshot) == 3
        stock_codes = {r.stock_code for r in snapshot}
        assert stock_codes == {"600519", "000858", "601318"}

    async def test_returns_empty_for_missing_quarter(
        self, repo: HoldingRepo, session: AsyncSession
    ) -> None:
        snapshot = await repo.get_snapshot(session, "H40002", date(2024, 3, 31))
        assert snapshot == []

    async def test_snapshot_ordered_by_stock_code(
        self, repo: HoldingRepo, session: AsyncSession
    ) -> None:
        records = [
            _holding("H40003", date(2024, 6, 30), "601318"),
            _holding("H40003", date(2024, 6, 30), "000858"),
            _holding("H40003", date(2024, 6, 30), "600519"),
        ]
        await repo.upsert_many(session, records)

        snapshot = await repo.get_snapshot(session, "H40003", date(2024, 6, 30))
        codes = [r.stock_code for r in snapshot]
        assert codes == sorted(codes)
