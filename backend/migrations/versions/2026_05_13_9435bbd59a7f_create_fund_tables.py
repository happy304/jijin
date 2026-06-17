"""create_fund_tables

Creates the six core fund data tables:
  - funds              (standard PostgreSQL relation, requirement 2.8)
  - fund_nav           (TimescaleDB hypertable, requirements 2.6, 2.7)
  - fund_holdings      (quarterly snapshot table, requirement 2.3)
  - fund_dividends     (corporate-action table, requirement 2.6)
  - fund_announcements (announcement table, requirement 11.10)
  - fund_fees          (tiered fee schedule, requirements 4.4, 4.5)

TimescaleDB notes
-----------------
``fund_nav`` is converted to a hypertable partitioned by ``trade_date``
(requirement 2.7). The ``create_hypertable`` call is wrapped in a
``DO $$ ... $$`` block so the migration is idempotent: if TimescaleDB
is not installed (e.g. plain PostgreSQL in CI) the block raises a
``feature_not_supported`` exception which is caught and ignored.

Indexes
-------
* ``idx_funds_type``    — funds.fund_type
* ``idx_funds_company`` — funds.company_id
* ``idx_nav_code_date`` — fund_nav(fund_code, trade_date DESC)
* ``idx_nav_unit_nav``  — fund_nav.unit_nav DESC  (requirement 2.8 "nav 降序")

Revision ID: 9435bbd59a7f
Revises:
Create Date: 2026-05-13 03:21:05.621839+00:00
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


def _is_postgresql() -> bool:
    """Return True when the target database is PostgreSQL (or TimescaleDB)."""
    return op.get_bind().dialect.name == "postgresql"

# revision identifiers, used by Alembic.
revision: str = "9435bbd59a7f"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # fund_announcements
    # ------------------------------------------------------------------
    op.create_table(
        "fund_announcements",
        sa.Column(
            "id",
            sa.BigInteger(),
            autoincrement=True,
            nullable=False,
            comment="Surrogate primary key (BIGSERIAL)",
        ),
        sa.Column(
            "fund_code",
            sa.String(length=10),
            nullable=True,
            comment="Fund code — FK to funds.code (enforced at app layer)",
        ),
        sa.Column("title", sa.Text(), nullable=True, comment="Announcement title"),
        sa.Column(
            "category",
            sa.String(length=40),
            nullable=True,
            comment=(
                "LLM-classified category: LIMIT_PURCHASE/SUSPEND/DIVIDEND/"
                "MANAGER_CHANGE/CONTRACT_CHANGE/OTHER"
            ),
        ),
        sa.Column("publish_date", sa.Date(), nullable=True, comment="Publication date"),
        sa.Column(
            "content_url",
            sa.Text(),
            nullable=True,
            comment="URL to full announcement text",
        ),
        sa.Column(
            "parsed_data",
            sa.JSON().with_variant(
                sa.JSON(),  # SQLite / generic fallback
                "sqlite",
            ),
            nullable=True,
            comment="Structured fields extracted by LLM pipeline (JSONB on PostgreSQL)",
        ),
        sa.Column(
            "requires_review",
            sa.Boolean(),
            server_default="false",
            nullable=False,
            comment="True when LLM confidence is low or rule-engine cross-check failed",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fund_announcements")),
    )

    # ------------------------------------------------------------------
    # fund_dividends
    # ------------------------------------------------------------------
    op.create_table(
        "fund_dividends",
        sa.Column(
            "fund_code",
            sa.String(length=10),
            nullable=False,
            comment="Fund code — FK to funds.code (enforced at app layer)",
        ),
        sa.Column(
            "ex_date",
            sa.Date(),
            nullable=False,
            comment="Ex-dividend / ex-split date (除权日)",
        ),
        sa.Column(
            "record_date",
            sa.Date(),
            nullable=True,
            comment="Record date (权益登记日)",
        ),
        sa.Column(
            "pay_date",
            sa.Date(),
            nullable=True,
            comment="Payment date (派息日)",
        ),
        sa.Column(
            "dividend_per_share",
            sa.Numeric(precision=10, scale=6),
            server_default="0",
            nullable=False,
            comment="Cash dividend per share (CNY); 0 for pure splits",
        ),
        sa.Column(
            "split_ratio",
            sa.Numeric(precision=10, scale=6),
            server_default="1",
            nullable=False,
            comment="Split ratio (new shares / old shares); 1 = no split",
        ),
        sa.PrimaryKeyConstraint("fund_code", "ex_date", name=op.f("pk_fund_dividends")),
    )

    # ------------------------------------------------------------------
    # fund_fees
    # ------------------------------------------------------------------
    op.create_table(
        "fund_fees",
        sa.Column(
            "fund_code",
            sa.String(length=10),
            nullable=False,
            comment="Fund code — FK to funds.code (enforced at app layer)",
        ),
        sa.Column(
            "fee_type",
            sa.String(length=20),
            nullable=False,
            comment="Fee category: subscribe or redeem",
        ),
        sa.Column(
            "min_amount",
            sa.Numeric(precision=20, scale=2),
            server_default="0",
            nullable=False,
            comment="Lower bound of subscription amount bracket (CNY, inclusive)",
        ),
        sa.Column(
            "min_holding_days",
            sa.Integer(),
            server_default="0",
            nullable=False,
            comment="Lower bound of holding-period bracket (days, inclusive)",
        ),
        sa.Column(
            "max_amount",
            sa.Numeric(precision=20, scale=2),
            nullable=True,
            comment="Upper bound of subscription amount bracket (CNY, exclusive); NULL = no cap",
        ),
        sa.Column(
            "max_holding_days",
            sa.Integer(),
            nullable=True,
            comment="Upper bound of holding-period bracket (days, exclusive); NULL = no cap",
        ),
        sa.Column(
            "rate",
            sa.Numeric(precision=8, scale=6),
            nullable=False,
            comment="Fee rate as a decimal fraction (e.g. 0.015 = 1.5%)",
        ),
        sa.PrimaryKeyConstraint(
            "fund_code",
            "fee_type",
            "min_amount",
            "min_holding_days",
            name=op.f("pk_fund_fees"),
        ),
    )

    # ------------------------------------------------------------------
    # fund_holdings
    # ------------------------------------------------------------------
    op.create_table(
        "fund_holdings",
        sa.Column(
            "fund_code",
            sa.String(length=10),
            nullable=False,
            comment="Fund code — FK to funds.code (enforced at app layer)",
        ),
        sa.Column(
            "report_date",
            sa.Date(),
            nullable=False,
            comment="Quarter-end report date",
        ),
        sa.Column(
            "stock_code",
            sa.String(length=20),
            nullable=False,
            comment="Underlying security code",
        ),
        sa.Column(
            "stock_name",
            sa.String(length=100),
            nullable=True,
            comment="Underlying security name",
        ),
        sa.Column(
            "weight",
            sa.Numeric(precision=8, scale=4),
            nullable=True,
            comment="Position weight as fraction of NAV (e.g. 0.05 = 5%)",
        ),
        sa.Column(
            "shares",
            sa.Numeric(precision=20, scale=2),
            nullable=True,
            comment="Number of shares held",
        ),
        sa.Column(
            "market_value",
            sa.Numeric(precision=20, scale=2),
            nullable=True,
            comment="Market value in CNY",
        ),
        sa.Column(
            "industry",
            sa.String(length=50),
            nullable=True,
            comment="Industry classification",
        ),
        sa.PrimaryKeyConstraint(
            "fund_code",
            "report_date",
            "stock_code",
            name=op.f("pk_fund_holdings"),
        ),
    )

    # ------------------------------------------------------------------
    # fund_nav  (will be converted to a TimescaleDB hypertable below)
    # ------------------------------------------------------------------
    op.create_table(
        "fund_nav",
        sa.Column(
            "fund_code",
            sa.String(length=10),
            nullable=False,
            comment="Fund code — FK to funds.code (enforced at app layer)",
        ),
        sa.Column(
            "trade_date",
            sa.Date(),
            nullable=False,
            comment="Trading date (T); TimescaleDB partition dimension",
        ),
        sa.Column(
            "unit_nav",
            sa.Numeric(precision=12, scale=6),
            nullable=True,
            comment="Unit net asset value (CNY per share)",
        ),
        sa.Column(
            "accum_nav",
            sa.Numeric(precision=12, scale=6),
            nullable=True,
            comment="Cumulative net asset value since inception",
        ),
        sa.Column(
            "adj_nav",
            sa.Numeric(precision=12, scale=6),
            nullable=True,
            comment="Dividend-adjusted (forward-adjusted) NAV; computed by adj_nav service",
        ),
        sa.Column(
            "daily_return",
            sa.Numeric(precision=10, scale=6),
            nullable=True,
            comment="Daily return as a decimal fraction (e.g. 0.0123 = +1.23%)",
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="normal",
            nullable=True,
            comment="Per-day status: normal/suspended/limited",
        ),
        sa.Column(
            "source",
            sa.String(length=20),
            nullable=True,
            comment="Data source identifier",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default="NOW()",
            nullable=True,
            comment="Row creation timestamp (UTC)",
        ),
        sa.PrimaryKeyConstraint("fund_code", "trade_date", name=op.f("pk_fund_nav")),
    )

    # Composite index for "latest NAV for a fund" queries (design §2.1).
    op.create_index("idx_nav_code_date", "fund_nav", ["fund_code", "trade_date"], unique=False)

    # Descending index on unit_nav for "top NAV" queries (requirement 2.8).
    # Only PostgreSQL supports expression indexes; skip on SQLite.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.create_index(
            "idx_nav_unit_nav_desc",
            "fund_nav",
            [sa.text("unit_nav DESC")],
            unique=False,
        )

    # ------------------------------------------------------------------
    # funds
    # ------------------------------------------------------------------
    op.create_table(
        "funds",
        sa.Column(
            "code",
            sa.String(length=10),
            nullable=False,
            comment="Fund code (e.g. '000001')",
        ),
        sa.Column(
            "name",
            sa.String(length=100),
            nullable=False,
            comment="Fund full name",
        ),
        sa.Column(
            "fund_type",
            sa.String(length=20),
            nullable=True,
            comment="Broad category: stock/bond/mixed/money/qdii/fof/index",
        ),
        sa.Column(
            "sub_type",
            sa.String(length=40),
            nullable=True,
            comment="Detailed sub-category",
        ),
        sa.Column(
            "company_id",
            sa.String(length=20),
            nullable=True,
            comment="Fund management company identifier",
        ),
        sa.Column(
            "inception_date",
            sa.Date(),
            nullable=True,
            comment="Fund inception date",
        ),
        sa.Column(
            "benchmark",
            sa.Text(),
            nullable=True,
            comment="Benchmark description",
        ),
        sa.Column(
            "management_fee",
            sa.Numeric(precision=6, scale=4),
            nullable=True,
            comment="Annual management fee rate",
        ),
        sa.Column(
            "custodian_fee",
            sa.Numeric(precision=6, scale=4),
            nullable=True,
            comment="Annual custodian fee rate",
        ),
        sa.Column(
            "currency",
            sa.String(length=10),
            server_default="CNY",
            nullable=False,
            comment="Settlement currency",
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="active",
            nullable=False,
            comment="Lifecycle status: active/suspended/delisted",
        ),
        sa.Column(
            "is_purchasable",
            sa.Boolean(),
            server_default="true",
            nullable=False,
            comment="Whether new subscriptions are currently open",
        ),
        sa.Column(
            "purchase_limit",
            sa.Numeric(precision=18, scale=2),
            nullable=True,
            comment="Single-transaction purchase cap (CNY); NULL = no limit",
        ),
        sa.Column(
            "source",
            sa.String(length=20),
            nullable=True,
            comment="Data source identifier (e.g. 'eastmoney', 'akshare')",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default="NOW()",
            nullable=True,
            comment="Last update timestamp (UTC)",
        ),
        sa.PrimaryKeyConstraint("code", name=op.f("pk_funds")),
    )
    op.create_index("idx_funds_company", "funds", ["company_id"], unique=False)
    op.create_index("idx_funds_type", "funds", ["fund_type"], unique=False)

    # ------------------------------------------------------------------
    # TimescaleDB hypertable conversion for fund_nav (requirement 2.7)
    #
    # Only executed on PostgreSQL. Wrapped in a DO block so the migration
    # is idempotent and degrades gracefully when TimescaleDB is not
    # installed. The ``if_not_exists => true`` argument prevents errors
    # on repeated runs.
    # ------------------------------------------------------------------
    if bind.dialect.name == "postgresql":
        op.execute(
            """
            DO $$
            BEGIN
                PERFORM create_hypertable(
                    'fund_nav',
                    'trade_date',
                    if_not_exists => true,
                    migrate_data   => true
                );
            EXCEPTION
                WHEN feature_not_supported THEN
                    -- TimescaleDB extension not installed; skip silently.
                    NULL;
                WHEN undefined_function THEN
                    -- create_hypertable not available; skip silently.
                    NULL;
            END;
            $$;
            """
        )


def downgrade() -> None:
    # Drop indexes first, then tables (reverse creation order).
    op.drop_index("idx_funds_type", table_name="funds")
    op.drop_index("idx_funds_company", table_name="funds")
    op.drop_table("funds")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_index("idx_nav_unit_nav_desc", table_name="fund_nav")
    op.drop_index("idx_nav_code_date", table_name="fund_nav")
    op.drop_table("fund_nav")
    op.drop_table("fund_holdings")
    op.drop_table("fund_fees")
    op.drop_table("fund_dividends")
    op.drop_table("fund_announcements")
