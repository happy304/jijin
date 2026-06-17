"""Unit tests for the ORM models defined in task 1.2.

These tests verify:
1. All six models are importable and registered with ``Base.metadata``.
2. Column names, types, and constraints match the design spec (§2.1).
3. The Alembic migration runs cleanly against SQLite (no live DB needed).
4. Indexes are created as specified (fund_type, company_id, nav DESC).

No live PostgreSQL is required — SQLite is used for structural checks.
TimescaleDB-specific behaviour (hypertable) is verified by inspecting
the migration SQL rather than executing it.

Requirements: 2.7, 2.8
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import inspect

from app.data.models import (
    Base,
    Fund,
    FundAnnouncement,
    FundDividend,
    FundFee,
    FundHolding,
    FundNav,
)
from app.data.migrations import build_alembic_config, run_upgrade
from app.core.config import Settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _test_settings(db_path: str) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        DATABASE_SYNC_URL=f"sqlite:///{db_path}",
        DB_AUTO_MIGRATE="true",
    )


# ---------------------------------------------------------------------------
# 1. Model registration
# ---------------------------------------------------------------------------


class TestModelRegistration:
    """All six models must be registered with Base.metadata."""

    def test_all_tables_in_metadata(self) -> None:
        expected = {
            "funds",
            "fund_nav",
            "fund_holdings",
            "fund_dividends",
            "fund_announcements",
            "fund_fees",
        }
        assert expected.issubset(set(Base.metadata.tables.keys()))

    def test_fund_table_name(self) -> None:
        assert Fund.__tablename__ == "funds"

    def test_fund_nav_table_name(self) -> None:
        assert FundNav.__tablename__ == "fund_nav"

    def test_fund_holding_table_name(self) -> None:
        assert FundHolding.__tablename__ == "fund_holdings"

    def test_fund_dividend_table_name(self) -> None:
        assert FundDividend.__tablename__ == "fund_dividends"

    def test_fund_announcement_table_name(self) -> None:
        assert FundAnnouncement.__tablename__ == "fund_announcements"

    def test_fund_fee_table_name(self) -> None:
        assert FundFee.__tablename__ == "fund_fees"


# ---------------------------------------------------------------------------
# 2. Column structure
# ---------------------------------------------------------------------------


class TestFundColumns:
    """funds table column structure matches design §2.1."""

    def _cols(self) -> dict[str, sa.Column]:  # type: ignore[type-arg]
        return {c.name: c for c in Fund.__table__.columns}

    def test_primary_key_is_code(self) -> None:
        cols = self._cols()
        assert "code" in cols
        assert cols["code"].primary_key is True

    def test_name_is_not_nullable(self) -> None:
        assert self._cols()["name"].nullable is False

    def test_fund_type_is_nullable(self) -> None:
        assert self._cols()["fund_type"].nullable is True

    def test_company_id_is_nullable(self) -> None:
        assert self._cols()["company_id"].nullable is True

    def test_currency_has_server_default(self) -> None:
        col = self._cols()["currency"]
        assert col.server_default is not None

    def test_status_has_server_default(self) -> None:
        col = self._cols()["status"]
        assert col.server_default is not None

    def test_is_purchasable_has_server_default(self) -> None:
        col = self._cols()["is_purchasable"]
        assert col.server_default is not None

    def test_management_fee_is_numeric(self) -> None:
        col = self._cols()["management_fee"]
        assert isinstance(col.type, sa.Numeric)

    def test_updated_at_is_timezone_aware(self) -> None:
        col = self._cols()["updated_at"]
        assert isinstance(col.type, sa.DateTime)
        assert col.type.timezone is True


class TestFundNavColumns:
    """fund_nav table column structure matches design §2.1."""

    def _cols(self) -> dict[str, sa.Column]:  # type: ignore[type-arg]
        return {c.name: c for c in FundNav.__table__.columns}

    def test_composite_primary_key(self) -> None:
        pk_cols = {c.name for c in FundNav.__table__.primary_key.columns}
        assert pk_cols == {"fund_code", "trade_date"}

    def test_unit_nav_is_numeric(self) -> None:
        col = self._cols()["unit_nav"]
        assert isinstance(col.type, sa.Numeric)

    def test_accum_nav_is_numeric(self) -> None:
        col = self._cols()["accum_nav"]
        assert isinstance(col.type, sa.Numeric)

    def test_adj_nav_is_numeric(self) -> None:
        """Requirement 2.6: adj_nav field must exist."""
        col = self._cols()["adj_nav"]
        assert isinstance(col.type, sa.Numeric)

    def test_daily_return_is_numeric(self) -> None:
        col = self._cols()["daily_return"]
        assert isinstance(col.type, sa.Numeric)

    def test_status_has_server_default(self) -> None:
        col = self._cols()["status"]
        assert col.server_default is not None

    def test_created_at_is_timezone_aware(self) -> None:
        col = self._cols()["created_at"]
        assert isinstance(col.type, sa.DateTime)
        assert col.type.timezone is True


class TestFundHoldingColumns:
    """fund_holdings table column structure."""

    def test_composite_primary_key(self) -> None:
        pk_cols = {c.name for c in FundHolding.__table__.primary_key.columns}
        assert pk_cols == {"fund_code", "report_date", "stock_code"}

    def test_weight_is_numeric(self) -> None:
        col = FundHolding.__table__.columns["weight"]
        assert isinstance(col.type, sa.Numeric)


class TestFundDividendColumns:
    """fund_dividends table column structure."""

    def test_composite_primary_key(self) -> None:
        pk_cols = {c.name for c in FundDividend.__table__.primary_key.columns}
        assert pk_cols == {"fund_code", "ex_date"}

    def test_dividend_per_share_has_server_default(self) -> None:
        col = FundDividend.__table__.columns["dividend_per_share"]
        assert col.server_default is not None

    def test_split_ratio_has_server_default(self) -> None:
        col = FundDividend.__table__.columns["split_ratio"]
        assert col.server_default is not None


class TestFundAnnouncementColumns:
    """fund_announcements table column structure."""

    def test_primary_key_is_id(self) -> None:
        pk_cols = {c.name for c in FundAnnouncement.__table__.primary_key.columns}
        assert pk_cols == {"id"}

    def test_id_is_biginteger(self) -> None:
        col = FundAnnouncement.__table__.columns["id"]
        assert isinstance(col.type, sa.BigInteger)

    def test_requires_review_has_server_default(self) -> None:
        col = FundAnnouncement.__table__.columns["requires_review"]
        assert col.server_default is not None

    def test_parsed_data_is_json(self) -> None:
        col = FundAnnouncement.__table__.columns["parsed_data"]
        assert isinstance(col.type, sa.JSON)


class TestFundFeeColumns:
    """fund_fees table column structure."""

    def test_composite_primary_key(self) -> None:
        pk_cols = {c.name for c in FundFee.__table__.primary_key.columns}
        assert pk_cols == {"fund_code", "fee_type", "min_amount", "min_holding_days"}

    def test_rate_is_numeric(self) -> None:
        col = FundFee.__table__.columns["rate"]
        assert isinstance(col.type, sa.Numeric)

    def test_max_amount_is_nullable(self) -> None:
        col = FundFee.__table__.columns["max_amount"]
        assert col.nullable is True

    def test_max_holding_days_is_nullable(self) -> None:
        col = FundFee.__table__.columns["max_holding_days"]
        assert col.nullable is True


# ---------------------------------------------------------------------------
# 3. Index declarations (requirement 2.8)
# ---------------------------------------------------------------------------


class TestIndexDeclarations:
    """Verify indexes are declared on the correct tables."""

    def _index_names(self, table: sa.Table) -> set[str]:  # type: ignore[type-arg]
        return {idx.name for idx in table.indexes}

    def test_funds_has_type_index(self) -> None:
        assert "idx_funds_type" in self._index_names(Fund.__table__)

    def test_funds_has_company_index(self) -> None:
        assert "idx_funds_company" in self._index_names(Fund.__table__)

    def test_fund_nav_has_code_date_index(self) -> None:
        assert "idx_nav_code_date" in self._index_names(FundNav.__table__)

    def test_funds_type_index_on_fund_type_column(self) -> None:
        idx = next(i for i in Fund.__table__.indexes if i.name == "idx_funds_type")
        col_names = [c.name for c in idx.columns]
        assert col_names == ["fund_type"]

    def test_funds_company_index_on_company_id_column(self) -> None:
        idx = next(i for i in Fund.__table__.indexes if i.name == "idx_funds_company")
        col_names = [c.name for c in idx.columns]
        assert col_names == ["company_id"]


# ---------------------------------------------------------------------------
# 4. Migration integration (SQLite)
# ---------------------------------------------------------------------------


class TestMigrationIntegration:
    """Migration creates all tables and indexes in SQLite."""

    @pytest.fixture
    def db_path(self, tmp_path: Path) -> str:
        return str(tmp_path / "test.db")

    def test_migration_creates_all_tables(self, db_path: str) -> None:
        settings = _test_settings(db_path)
        run_upgrade(settings, "head")

        conn = sqlite3.connect(db_path)
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()

        expected = {
            "funds",
            "fund_nav",
            "fund_holdings",
            "fund_dividends",
            "fund_announcements",
            "fund_fees",
        }
        assert expected.issubset(tables)

    def test_migration_creates_funds_indexes(self, db_path: str) -> None:
        settings = _test_settings(db_path)
        run_upgrade(settings, "head")

        conn = sqlite3.connect(db_path)
        indexes = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        conn.close()

        assert "idx_funds_type" in indexes
        assert "idx_funds_company" in indexes

    def test_migration_creates_nav_code_date_index(self, db_path: str) -> None:
        settings = _test_settings(db_path)
        run_upgrade(settings, "head")

        conn = sqlite3.connect(db_path)
        indexes = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        conn.close()

        assert "idx_nav_code_date" in indexes

    def test_migration_is_idempotent(self, db_path: str) -> None:
        """Running upgrade head twice must not raise."""
        settings = _test_settings(db_path)
        run_upgrade(settings, "head")
        run_upgrade(settings, "head")  # second run — should be a no-op

    def test_downgrade_removes_all_tables(self, db_path: str) -> None:
        from alembic import command

        settings = _test_settings(db_path)
        run_upgrade(settings, "head")

        cfg = build_alembic_config(settings)
        command.downgrade(cfg, "base")

        conn = sqlite3.connect(db_path)
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()

        fund_tables = {
            "funds",
            "fund_nav",
            "fund_holdings",
            "fund_dividends",
            "fund_announcements",
            "fund_fees",
        }
        assert fund_tables.isdisjoint(tables), f"Tables still present after downgrade: {tables}"


# ---------------------------------------------------------------------------
# 5. Migration file contains TimescaleDB hypertable call (requirement 2.7)
# ---------------------------------------------------------------------------


class TestMigrationContainsHypertable:
    """The migration script must contain the create_hypertable call."""

    def test_migration_file_has_create_hypertable(self) -> None:
        versions_dir = Path(__file__).resolve().parents[2] / "migrations" / "versions"
        migration_files = list(versions_dir.glob("*.py"))
        assert migration_files, "No migration files found"

        combined = "\n".join(f.read_text(encoding="utf-8") for f in migration_files)
        assert "create_hypertable" in combined, (
            "Migration must call create_hypertable for fund_nav (requirement 2.7)"
        )

    def test_migration_file_targets_fund_nav(self) -> None:
        versions_dir = Path(__file__).resolve().parents[2] / "migrations" / "versions"
        migration_files = list(versions_dir.glob("*.py"))
        combined = "\n".join(f.read_text(encoding="utf-8") for f in migration_files)
        assert "'fund_nav'" in combined or '"fund_nav"' in combined


# ---------------------------------------------------------------------------
# 6. ORM model instantiation (smoke tests)
# ---------------------------------------------------------------------------


class TestModelInstantiation:
    """Models can be instantiated with valid data."""

    def test_fund_instantiation(self) -> None:
        fund = Fund(code="000001", name="华夏成长混合")
        assert fund.code == "000001"
        assert fund.name == "华夏成长混合"

    def test_fund_nav_instantiation(self) -> None:
        from datetime import date

        nav = FundNav(
            fund_code="000001",
            trade_date=date(2024, 1, 2),
            unit_nav=Decimal("1.2345"),
            accum_nav=Decimal("2.3456"),
            adj_nav=Decimal("2.3456"),
        )
        assert nav.fund_code == "000001"
        assert nav.unit_nav == Decimal("1.2345")

    def test_fund_holding_instantiation(self) -> None:
        from datetime import date

        holding = FundHolding(
            fund_code="000001",
            report_date=date(2024, 3, 31),
            stock_code="600519",
            stock_name="贵州茅台",
            weight=Decimal("0.0850"),
        )
        assert holding.stock_code == "600519"
        assert holding.weight == Decimal("0.0850")

    def test_fund_dividend_instantiation(self) -> None:
        from datetime import date

        div = FundDividend(
            fund_code="000001",
            ex_date=date(2024, 6, 15),
            dividend_per_share=Decimal("0.05"),
            split_ratio=Decimal("1"),
        )
        assert div.dividend_per_share == Decimal("0.05")

    def test_fund_announcement_instantiation(self) -> None:
        ann = FundAnnouncement(
            fund_code="000001",
            title="关于限制大额申购的公告",
            category="LIMIT_PURCHASE",
        )
        assert ann.category == "LIMIT_PURCHASE"
        assert ann.requires_review is None  # server default, not set in Python

    def test_fund_fee_instantiation(self) -> None:
        fee = FundFee(
            fund_code="000001",
            fee_type="subscribe",
            min_amount=Decimal("0"),
            min_holding_days=0,
            rate=Decimal("0.015"),
        )
        assert fee.rate == Decimal("0.015")
