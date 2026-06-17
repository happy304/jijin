"""Unit tests for FundRepo.

Tests cover:
- upsert_many: insert new rows, update on conflict
- get_by_date_range: filter by updated_at date
- latest_date: returns most recent updated_at date
- missing_dates: returns dates not present in updated_at
- get_by_code / get_all convenience helpers

Requirements: 2.6, 1.11
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models.funds import Fund
from app.data.repositories.fund_repo import FundRepo


@pytest.fixture
def repo() -> FundRepo:
    return FundRepo()


# ---------------------------------------------------------------------------
# upsert_many
# ---------------------------------------------------------------------------


class TestFundRepoUpsertMany:
    """FundRepo.upsert_many inserts and updates correctly."""

    async def test_insert_single_record(
        self, repo: FundRepo, session: AsyncSession
    ) -> None:
        ts = datetime(2024, 1, 2, 0, 0, 0, tzinfo=timezone.utc)
        records = [{"code": "000001", "name": "华夏成长混合", "updated_at": ts}]
        count = await repo.upsert_many(session, records)
        assert count >= 0  # rowcount varies by driver

        fund = await repo.get_by_code(session, "000001")
        assert fund is not None
        assert fund.name == "华夏成长混合"

    async def test_insert_multiple_records(
        self, repo: FundRepo, session: AsyncSession
    ) -> None:
        ts = datetime(2024, 1, 2, 0, 0, 0, tzinfo=timezone.utc)
        records = [
            {"code": "000002", "name": "华夏大盘精选", "updated_at": ts},
            {"code": "000003", "name": "华夏蓝筹核心", "updated_at": ts},
        ]
        await repo.upsert_many(session, records)

        f1 = await repo.get_by_code(session, "000002")
        f2 = await repo.get_by_code(session, "000003")
        assert f1 is not None and f1.name == "华夏大盘精选"
        assert f2 is not None and f2.name == "华夏蓝筹核心"

    async def test_update_on_conflict(
        self, repo: FundRepo, session: AsyncSession
    ) -> None:
        ts = datetime(2024, 1, 2, 0, 0, 0, tzinfo=timezone.utc)
        await repo.upsert_many(session, [{"code": "000004", "name": "旧名称", "updated_at": ts}])
        await repo.upsert_many(session, [{"code": "000004", "name": "新名称", "updated_at": ts}])

        fund = await repo.get_by_code(session, "000004")
        assert fund is not None
        assert fund.name == "新名称"

    async def test_empty_list_returns_zero(
        self, repo: FundRepo, session: AsyncSession
    ) -> None:
        count = await repo.upsert_many(session, [])
        assert count == 0

    async def test_upsert_with_optional_fields(
        self, repo: FundRepo, session: AsyncSession
    ) -> None:
        from decimal import Decimal

        ts = datetime(2024, 1, 2, 0, 0, 0, tzinfo=timezone.utc)
        records = [
            {
                "code": "000005",
                "name": "测试基金",
                "fund_type": "stock",
                "status": "active",
                "management_fee": Decimal("0.015"),
                "updated_at": ts,
            }
        ]
        await repo.upsert_many(session, records)
        fund = await repo.get_by_code(session, "000005")
        assert fund is not None
        assert fund.fund_type == "stock"
        assert fund.management_fee == Decimal("0.015")


# ---------------------------------------------------------------------------
# get_by_date_range
# ---------------------------------------------------------------------------


class TestFundRepoGetByDateRange:
    """FundRepo.get_by_date_range filters by updated_at date."""

    async def test_returns_fund_within_range(
        self, repo: FundRepo, session: AsyncSession
    ) -> None:
        ts = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        await repo.upsert_many(
            session, [{"code": "DR001", "name": "日期范围测试基金", "updated_at": ts}]
        )

        results = await repo.get_by_date_range(
            session, "DR001", date(2024, 6, 15), date(2024, 6, 15)
        )
        assert any(f.code == "DR001" for f in results)

    async def test_excludes_fund_outside_range(
        self, repo: FundRepo, session: AsyncSession
    ) -> None:
        ts = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        await repo.upsert_many(
            session, [{"code": "DR002", "name": "范围外基金", "updated_at": ts}]
        )

        results = await repo.get_by_date_range(
            session, "DR002", date(2024, 6, 1), date(2024, 6, 30)
        )
        assert not any(f.code == "DR002" for f in results)

    async def test_empty_string_fund_code_returns_all_in_range(
        self, repo: FundRepo, session: AsyncSession
    ) -> None:
        ts = datetime(2024, 7, 10, 0, 0, 0, tzinfo=timezone.utc)
        await repo.upsert_many(
            session, [{"code": "DR003", "name": "全量查询基金", "updated_at": ts}]
        )

        results = await repo.get_by_date_range(
            session, "", date(2024, 7, 10), date(2024, 7, 10)
        )
        assert any(f.code == "DR003" for f in results)


# ---------------------------------------------------------------------------
# latest_date
# ---------------------------------------------------------------------------


class TestFundRepoLatestDate:
    """FundRepo.latest_date returns the most recent updated_at date."""

    async def test_returns_none_when_no_records(
        self, repo: FundRepo, session: AsyncSession
    ) -> None:
        result = await repo.latest_date(session, "NONEXISTENT")
        assert result is None

    async def test_returns_correct_date(
        self, repo: FundRepo, session: AsyncSession
    ) -> None:
        ts = datetime(2024, 8, 20, 10, 0, 0, tzinfo=timezone.utc)
        await repo.upsert_many(
            session, [{"code": "LD001", "name": "最新日期测试", "updated_at": ts}]
        )

        result = await repo.latest_date(session, "LD001")
        assert result is not None
        assert result == date(2024, 8, 20)

    async def test_returns_latest_when_multiple_updates(
        self, repo: FundRepo, session: AsyncSession
    ) -> None:
        # 先插入旧时间戳，再更新为新时间戳
        ts_old = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        ts_new = datetime(2024, 12, 31, 0, 0, 0, tzinfo=timezone.utc)
        await repo.upsert_many(
            session, [{"code": "LD002", "name": "多次更新基金", "updated_at": ts_old}]
        )
        await repo.upsert_many(
            session, [{"code": "LD002", "name": "多次更新基金", "updated_at": ts_new}]
        )

        result = await repo.latest_date(session, "LD002")
        assert result == date(2024, 12, 31)


# ---------------------------------------------------------------------------
# missing_dates
# ---------------------------------------------------------------------------


class TestFundRepoMissingDates:
    """FundRepo.missing_dates returns dates absent from updated_at."""

    async def test_empty_expected_returns_empty(
        self, repo: FundRepo, session: AsyncSession
    ) -> None:
        result = await repo.missing_dates(session, "MD001", [])
        assert result == []

    async def test_all_missing_when_no_records(
        self, repo: FundRepo, session: AsyncSession
    ) -> None:
        expected = [date(2024, 1, 1), date(2024, 1, 2)]
        result = await repo.missing_dates(session, "NORECORD", expected)
        assert set(result) == set(expected)

    async def test_returns_only_missing_dates(
        self, repo: FundRepo, session: AsyncSession
    ) -> None:
        ts = datetime(2024, 3, 15, 0, 0, 0, tzinfo=timezone.utc)
        await repo.upsert_many(
            session, [{"code": "MD002", "name": "缺失日期测试", "updated_at": ts}]
        )

        expected = [date(2024, 3, 15), date(2024, 3, 16)]
        result = await repo.missing_dates(session, "MD002", expected)
        # 2024-03-15 已存在，2024-03-16 缺失
        assert date(2024, 3, 16) in result
        assert date(2024, 3, 15) not in result


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------


class TestFundRepoHelpers:
    """FundRepo.get_by_code and get_all helpers."""

    async def test_get_by_code_returns_none_for_missing(
        self, repo: FundRepo, session: AsyncSession
    ) -> None:
        result = await repo.get_by_code(session, "MISSING")
        assert result is None

    async def test_get_by_code_returns_correct_fund(
        self, repo: FundRepo, session: AsyncSession
    ) -> None:
        ts = datetime(2024, 1, 2, 0, 0, 0, tzinfo=timezone.utc)
        await repo.upsert_many(session, [{"code": "GC001", "name": "查询测试基金", "updated_at": ts}])
        fund = await repo.get_by_code(session, "GC001")
        assert fund is not None
        assert fund.code == "GC001"

    async def test_get_all_returns_inserted_funds(
        self, repo: FundRepo, session: AsyncSession
    ) -> None:
        ts = datetime(2024, 1, 2, 0, 0, 0, tzinfo=timezone.utc)
        await repo.upsert_many(
            session,
            [
                {"code": "GA001", "name": "全量基金A", "updated_at": ts},
                {"code": "GA002", "name": "全量基金B", "updated_at": ts},
            ],
        )
        all_funds = await repo.get_all(session)
        codes = {f.code for f in all_funds}
        assert "GA001" in codes
        assert "GA002" in codes
