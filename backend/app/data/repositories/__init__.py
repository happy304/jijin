"""Repository layer for the fund data domain.

Each repository wraps a single ORM model and exposes a uniform async
interface:

* ``upsert_many(session, records)``   — insert-or-update a batch of rows
* ``get_by_date_range(session, ...)`` — fetch rows within a date window
* ``latest_date(session, ...)``       — most recent date stored for a fund
* ``missing_dates(session, ...)``     — dates in a range absent from the DB

Requirements: 2.6, 1.11
"""

from __future__ import annotations

from app.data.repositories.base import BaseRepo
from app.data.repositories.dividend_repo import DividendRepo
from app.data.repositories.fee_repo import FeeRepo
from app.data.repositories.fund_repo import FundRepo
from app.data.repositories.holding_repo import HoldingRepo
from app.data.repositories.nav_repo import NavRepo

__all__ = [
    "BaseRepo",
    "DividendRepo",
    "FeeRepo",
    "FundRepo",
    "HoldingRepo",
    "NavRepo",
]
