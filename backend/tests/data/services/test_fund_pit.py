"""Tests for the point-in-time fund metadata lookup service.

Reuses the SAVEPOINT-rollback ``session`` fixture from the local
``conftest.py``.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.data.models.fund_meta_history import FundMetaHistory
from app.data.models.funds import Fund
from app.data.services.fund_pit import (
    PITFundMeta,
    get_fund_meta_at,
    get_fund_meta_at_batch,
)


@pytest.fixture
async def populated_session(session):
    """Insert sample fund + 3 PIT history snapshots."""
    # Explicit updated_at — under the SQLite test backend the "NOW()"
    # server_default on funds.updated_at is stored as a literal string
    # rather than evaluated, so we always pass datetimes ourselves.
    now = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    # Fund row (live)
    session.add(
        Fund(
            code="000001",
            name="Test Fund",
            fund_type="stock",
            company_id="COMP_A",
            inception_date=date(2020, 1, 1),
            status="active",
            is_purchasable=True,
            management_fee=Decimal("0.0150"),
            updated_at=now,
        )
    )
    # 3 history snapshots: manager change in 2021, size jump in 2022,
    # status change to suspended in 2023
    session.add_all(
        [
            FundMetaHistory(
                fund_code="000001",
                effective_date=date(2020, 1, 1),
                manager_id="ZHANG_SAN",
                company_id="COMP_A",
                fund_size=Decimal("100000000"),
                status="active",
                is_purchasable=True,
                source="seed",
            ),
            FundMetaHistory(
                fund_code="000001",
                effective_date=date(2021, 6, 1),
                manager_id="LI_SI",
                company_id="COMP_A",
                fund_size=Decimal("500000000"),
                status="active",
                is_purchasable=True,
                source="seed",
            ),
            FundMetaHistory(
                fund_code="000001",
                effective_date=date(2023, 3, 15),
                manager_id="LI_SI",
                company_id="COMP_A",
                fund_size=Decimal("300000000"),
                status="suspended",
                is_purchasable=False,
                source="seed",
            ),
        ]
    )
    await session.flush()
    return session


# ---------------------------------------------------------------------------
# Single-fund lookup
# ---------------------------------------------------------------------------


class TestGetFundMetaAt:
    @pytest.mark.asyncio
    async def test_returns_earliest_snapshot_for_inception_date(self, populated_session):
        result = await get_fund_meta_at(populated_session, "000001", date(2020, 1, 1))
        assert result.source == "history"
        assert result.manager_id == "ZHANG_SAN"
        assert result.fund_size == Decimal("100000000")

    @pytest.mark.asyncio
    async def test_returns_correct_snapshot_after_manager_change(self, populated_session):
        # Date 2022-01-01 is after manager change but before size drop
        result = await get_fund_meta_at(populated_session, "000001", date(2022, 1, 1))
        assert result.manager_id == "LI_SI"
        assert result.fund_size == Decimal("500000000")
        assert result.status == "active"

    @pytest.mark.asyncio
    async def test_returns_latest_snapshot_after_all_changes(self, populated_session):
        result = await get_fund_meta_at(populated_session, "000001", date(2024, 6, 15))
        assert result.status == "suspended"
        assert result.is_purchasable is False
        assert result.fund_size == Decimal("300000000")

    @pytest.mark.asyncio
    async def test_strict_mode_returns_missing_when_no_history(self, populated_session):
        from datetime import datetime as _dt, timezone as _tz

        populated_session.add(
            Fund(
                code="999998",
                name="Strict No History Fund",
                fund_type="bond",
                company_id="COMP_STRICT",
                status="active",
                updated_at=_dt(2024, 1, 1, tzinfo=_tz.utc),
            )
        )
        await populated_session.flush()

        result = await get_fund_meta_at(
            populated_session,
            "999998",
            date(2024, 1, 1),
            allow_live_fallback=False,
        )
        assert result.source == "missing"
        assert result.company_id is None

    @pytest.mark.asyncio
    async def test_returns_live_fallback_when_no_history(self, populated_session):
        # Add a fund with no history rows
        from datetime import datetime as _dt, timezone as _tz

        populated_session.add(
            Fund(
                code="999999",
                name="No History Fund",
                fund_type="bond",
                company_id="COMP_X",
                status="active",
                updated_at=_dt(2024, 1, 1, tzinfo=_tz.utc),
            )
        )
        await populated_session.flush()

        result = await get_fund_meta_at(populated_session, "999999", date(2024, 1, 1))
        assert result.source == "live_fallback"
        assert result.company_id == "COMP_X"
        assert result.status == "active"

    @pytest.mark.asyncio
    async def test_returns_missing_for_unknown_fund(self, populated_session):
        result = await get_fund_meta_at(populated_session, "BOGUS01", date(2024, 1, 1))
        assert result.source == "missing"
        assert result.manager_id is None

    @pytest.mark.asyncio
    async def test_query_before_first_history_falls_back_to_live(
        self, populated_session
    ):
        # Date earlier than any history row → should fall back
        result = await get_fund_meta_at(populated_session, "000001", date(2019, 6, 1))
        assert result.source == "live_fallback"


# ---------------------------------------------------------------------------
# Batch lookup
# ---------------------------------------------------------------------------


class TestGetFundMetaAtBatch:
    @pytest.mark.asyncio
    async def test_batch_returns_one_snapshot_per_code(self, populated_session):
        # Add a second fund with history
        from datetime import datetime as _dt, timezone as _tz

        populated_session.add(
            Fund(
                code="000002",
                name="Second Fund",
                fund_type="bond",
                company_id="COMP_B",
                status="active",
                updated_at=_dt(2024, 1, 1, tzinfo=_tz.utc),
            )
        )
        populated_session.add(
            FundMetaHistory(
                fund_code="000002",
                effective_date=date(2022, 1, 1),
                manager_id="WANG_WU",
                fund_size=Decimal("200000000"),
                status="active",
                source="seed",
            )
        )
        await populated_session.flush()

        result = await get_fund_meta_at_batch(
            populated_session,
            ["000001", "000002", "BOGUS"],
            date(2023, 1, 1),
        )
        assert len(result) == 3
        assert result["000001"].source == "history"
        assert result["000001"].manager_id == "LI_SI"
        assert result["000002"].source == "history"
        assert result["000002"].manager_id == "WANG_WU"
        assert result["BOGUS"].source == "missing"

    @pytest.mark.asyncio
    async def test_batch_strict_mode_does_not_live_fallback(self, populated_session):
        from datetime import datetime as _dt, timezone as _tz

        populated_session.add(
            Fund(
                code="999997",
                name="Batch Strict No History Fund",
                fund_type="bond",
                company_id="COMP_BATCH_STRICT",
                status="active",
                updated_at=_dt(2024, 1, 1, tzinfo=_tz.utc),
            )
        )
        await populated_session.flush()

        result = await get_fund_meta_at_batch(
            populated_session,
            ["999997"],
            date(2024, 1, 1),
            allow_live_fallback=False,
        )
        assert result["999997"].source == "missing"
        assert result["999997"].company_id is None

    @pytest.mark.asyncio
    async def test_empty_batch_returns_empty_dict(self, populated_session):
        result = await get_fund_meta_at_batch(populated_session, [], date(2023, 1, 1))
        assert result == {}

    @pytest.mark.asyncio
    async def test_batch_picks_most_recent_per_fund(self, populated_session):
        # 000001 has 3 history rows; batch query should still pick the most
        # recent one (per fund) for the as_of date
        result = await get_fund_meta_at_batch(
            populated_session, ["000001"], date(2023, 6, 1)
        )
        assert result["000001"].fund_size == Decimal("300000000")
        assert result["000001"].status == "suspended"
