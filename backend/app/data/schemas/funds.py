"""Pydantic v2 DTOs for fund domain objects.

These mirror the SQL DDL in design.md §2.1 exactly. Field names, types
and units are kept in sync with the ORM models (task 1.2) and the
repository layer (task 1.3).

Unit conventions
----------------
* Monetary amounts (NAV, market value, purchase limit): ``DecimalStr``
* Fee rates / weight percentages: ``DecimalStr`` in decimal form
  (e.g. ``Decimal("0.0015")`` for 0.15%)
* Dates: ``datetime.date``
* Timestamps: ``datetime.datetime`` (UTC-aware)
* Ratios (split_ratio, weight): ``DecimalStr``

``DecimalStr`` is an ``Annotated[Decimal, ...]`` type that serialises to
a JSON string (preserving precision) and deserialises back to ``Decimal``.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.functional_serializers import PlainSerializer


# ---------------------------------------------------------------------------
# Custom annotated type: Decimal serialised as string in JSON
# ---------------------------------------------------------------------------

DecimalStr = Annotated[
    Decimal,
    PlainSerializer(lambda v: str(v), return_type=str, when_used="json"),
]
"""Decimal that round-trips through JSON as a string (preserves precision)."""


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class FundType(str, Enum):
    """Canonical fund type codes used across the platform."""

    STOCK = "stock"
    BOND = "bond"
    MIXED = "mixed"
    MONEY = "money"
    QDII = "qdii"
    FOF = "fof"
    INDEX = "index"


class FundStatus(str, Enum):
    """Lifecycle status of a fund."""

    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELISTED = "delisted"


class NavStatus(str, Enum):
    """Per-day NAV status."""

    NORMAL = "normal"
    SUSPENDED = "suspended"
    LIMITED = "limited"


class FeeType(str, Enum):
    """Fee category."""

    SUBSCRIBE = "subscribe"
    REDEEM = "redeem"


class AnnouncementCategory(str, Enum):
    """LLM-classified announcement categories (design §9.3 / req 11.10)."""

    LIMIT_PURCHASE = "LIMIT_PURCHASE"
    SUSPEND = "SUSPEND"
    DIVIDEND = "DIVIDEND"
    MANAGER_CHANGE = "MANAGER_CHANGE"
    CONTRACT_CHANGE = "CONTRACT_CHANGE"
    OTHER = "OTHER"


# ---------------------------------------------------------------------------
# Shared model config
# ---------------------------------------------------------------------------

_BASE_CONFIG = ConfigDict(
    extra="forbid",
    str_strip_whitespace=True,
    populate_by_name=True,
)


# ---------------------------------------------------------------------------
# FundMeta
# ---------------------------------------------------------------------------


class FundMeta(BaseModel):
    """Basic fund metadata — mirrors the ``funds`` table.

    Requirement 1.1 (basic info), 2.6 (adj_nav field lives in NavRecord).
    """

    model_config = _BASE_CONFIG

    code: str = Field(..., min_length=1, max_length=10, description="Fund code (primary key)")
    name: str = Field(..., min_length=1, max_length=100, description="Fund full name")
    fund_type: FundType | None = Field(None, description="Broad fund category")
    sub_type: str | None = Field(None, max_length=40, description="Detailed sub-category")
    company_id: str | None = Field(None, max_length=20, description="Fund company identifier")
    inception_date: date | None = Field(None, description="Fund inception date")
    benchmark: str | None = Field(None, description="Benchmark description")
    management_fee: DecimalStr | None = Field(
        None,
        ge=Decimal("0"),
        le=Decimal("1"),
        description="Annual management fee rate (decimal, e.g. 0.015 = 1.5%)",
    )
    custodian_fee: DecimalStr | None = Field(
        None,
        ge=Decimal("0"),
        le=Decimal("1"),
        description="Annual custodian fee rate",
    )
    currency: str = Field(default="CNY", max_length=10, description="Settlement currency")
    status: FundStatus = Field(default=FundStatus.ACTIVE, description="Fund lifecycle status")
    is_purchasable: bool = Field(default=True, description="Whether new subscriptions are open")
    purchase_limit: DecimalStr | None = Field(
        None,
        ge=Decimal("0"),
        description="Single-transaction purchase cap (CNY); None = no limit",
    )
    source: str | None = Field(None, max_length=20, description="Data source identifier")
    updated_at: datetime | None = Field(None, description="Last update timestamp (UTC)")


# ---------------------------------------------------------------------------
# NavRecord
# ---------------------------------------------------------------------------


class NavRecord(BaseModel):
    """Single-day NAV record — mirrors the ``fund_nav`` hypertable.

    Requirement 2.6: stores unit_nav, accum_nav, adj_nav.
    """

    model_config = _BASE_CONFIG

    fund_code: str = Field(..., min_length=1, max_length=10)
    trade_date: date = Field(..., description="Trading date (T)")
    unit_nav: DecimalStr | None = Field(
        None,
        ge=Decimal("0"),
        description="Unit net asset value (CNY per share)",
    )
    accum_nav: DecimalStr | None = Field(
        None,
        ge=Decimal("0"),
        description="Cumulative net asset value since inception",
    )
    adj_nav: DecimalStr | None = Field(
        None,
        ge=Decimal("0"),
        description="Dividend-adjusted (forward-adjusted) NAV; computed by adj_nav service",
    )
    daily_return: DecimalStr | None = Field(
        None,
        ge=Decimal("-1"),
        le=Decimal("100"),
        description="Daily return as a decimal (e.g. 0.0123 = +1.23%)",
    )
    status: NavStatus = Field(default=NavStatus.NORMAL)
    source: str | None = Field(None, max_length=20)
    created_at: datetime | None = Field(None, description="Row creation timestamp (UTC)")


# ---------------------------------------------------------------------------
# HoldingPosition + HoldingSnapshot
# ---------------------------------------------------------------------------


class HoldingPosition(BaseModel):
    """A single holding line within a quarterly snapshot."""

    model_config = _BASE_CONFIG

    stock_code: str | None = Field(None, max_length=20, description="Underlying security code")
    stock_name: str | None = Field(None, max_length=100, description="Underlying security name")
    weight: DecimalStr | None = Field(
        None,
        ge=Decimal("0"),
        le=Decimal("2"),  # allow up to 200% for leveraged funds
        description="Position weight as fraction of NAV (e.g. 0.05 = 5%)",
    )
    shares: DecimalStr | None = Field(None, ge=Decimal("0"), description="Number of shares held")
    market_value: DecimalStr | None = Field(
        None, ge=Decimal("0"), description="Market value in CNY"
    )
    industry: str | None = Field(None, max_length=50, description="Industry classification")


class HoldingSnapshot(BaseModel):
    """Quarterly holding snapshot — mirrors ``fund_holdings`` table.

    Requirement 2.3: total weight validated in [0, 110%] by the validator
    layer (task 1.9); the DTO itself only enforces per-position bounds.
    """

    model_config = _BASE_CONFIG

    fund_code: str = Field(..., min_length=1, max_length=10)
    report_date: date = Field(..., description="Quarter-end report date")
    positions: list[HoldingPosition] = Field(
        default_factory=list,
        description="Top-N holdings (typically top 10 or top 20)",
    )


# ---------------------------------------------------------------------------
# DividendRecord
# ---------------------------------------------------------------------------


class DividendRecord(BaseModel):
    """Dividend / split event — mirrors ``fund_dividends`` table.

    Used by the adj_nav service (task 1.10) to recompute adj_nav.
    """

    model_config = _BASE_CONFIG

    fund_code: str = Field(..., min_length=1, max_length=10)
    ex_date: date = Field(..., description="Ex-dividend / ex-split date")
    record_date: date | None = Field(None, description="Record date (权益登记日)")
    pay_date: date | None = Field(None, description="Payment date (派息日)")
    dividend_per_share: DecimalStr = Field(
        default=Decimal("0"),
        ge=Decimal("0"),
        description="Cash dividend per share (CNY); 0 for pure splits",
    )
    split_ratio: DecimalStr = Field(
        default=Decimal("1"),
        gt=Decimal("0"),
        description="Split ratio (new shares / old shares); 1 = no split",
    )


# ---------------------------------------------------------------------------
# Announcement
# ---------------------------------------------------------------------------


class Announcement(BaseModel):
    """Fund announcement — mirrors ``fund_announcements`` table.

    The ``category`` field is populated by the LLM pipeline (task 7.6).
    """

    model_config = _BASE_CONFIG

    id: int | None = Field(None, description="DB-assigned surrogate key; None before insert")
    fund_code: str = Field(..., min_length=1, max_length=10)
    title: str | None = Field(None, description="Announcement title")
    category: AnnouncementCategory | None = Field(
        None, description="LLM-classified category; None until parsed"
    )
    publish_date: date | None = Field(None, description="Publication date")
    content_url: str | None = Field(None, description="URL to full announcement text")
    parsed_data: dict[str, Any] | None = Field(
        None, description="Structured fields extracted by LLM (JSONB)"
    )
    requires_review: bool = Field(
        default=False,
        description="True when LLM confidence is low or rule-engine cross-check failed",
    )


# ---------------------------------------------------------------------------
# FeeTier
# ---------------------------------------------------------------------------


class FeeTier(BaseModel):
    """A single fee tier row — mirrors ``fund_fees`` table.

    Subscribe tiers use ``min_amount`` / ``max_amount`` (CNY).
    Redeem tiers use ``min_holding_days`` / ``max_holding_days``.
    Both fields are present on the model; the fee calculator (task 3.5)
    selects the relevant pair based on ``fee_type``.
    """

    model_config = _BASE_CONFIG

    fund_code: str = Field(..., min_length=1, max_length=10)
    fee_type: FeeType = Field(..., description="subscribe or redeem")
    min_amount: DecimalStr = Field(
        default=Decimal("0"),
        ge=Decimal("0"),
        description="Lower bound of subscription amount bracket (CNY, inclusive)",
    )
    max_amount: DecimalStr | None = Field(
        None,
        ge=Decimal("0"),
        description="Upper bound of subscription amount bracket (CNY, exclusive); None = no cap",
    )
    min_holding_days: int = Field(
        default=0,
        ge=0,
        description="Lower bound of holding-period bracket (days, inclusive)",
    )
    max_holding_days: int | None = Field(
        None,
        ge=0,
        description="Upper bound of holding-period bracket (days, exclusive); None = no cap",
    )
    rate: DecimalStr = Field(
        ...,
        ge=Decimal("0"),
        le=Decimal("1"),
        description="Fee rate as a decimal (e.g. 0.015 = 1.5%)",
    )
