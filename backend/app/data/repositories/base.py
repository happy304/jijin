"""Abstract base repository with the shared async interface.

All concrete repositories inherit from ``BaseRepo`` and must implement
the four standard methods. The base class provides shared helpers for
building SQLAlchemy ``insert … on conflict do update`` (upsert) statements
that work with both PostgreSQL and SQLite (used in tests).

Requirements: 2.6, 1.11
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Any, Generic, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepo(ABC, Generic[ModelT]):
    """Abstract repository that every concrete repo must implement.

    Type parameter ``ModelT`` is the SQLAlchemy ORM model class.
    """

    # ------------------------------------------------------------------
    # Abstract interface — all four methods must be implemented
    # ------------------------------------------------------------------

    @abstractmethod
    async def upsert_many(
        self,
        session: AsyncSession,
        records: list[dict[str, Any]],
    ) -> int:
        """Insert or update *records* in bulk.

        Parameters
        ----------
        session:
            Active ``AsyncSession`` — the caller is responsible for
            committing or rolling back the transaction.
        records:
            List of dicts whose keys match the ORM model's column names.

        Returns
        -------
        int
            Number of rows affected (inserted + updated).
        """

    @abstractmethod
    async def get_by_date_range(
        self,
        session: AsyncSession,
        fund_code: str,
        start: date,
        end: date,
    ) -> list[ModelT]:
        """Return all rows for *fund_code* with a date in [start, end].

        The concrete date column (``trade_date``, ``report_date``, …)
        is determined by each subclass.
        """

    @abstractmethod
    async def latest_date(
        self,
        session: AsyncSession,
        fund_code: str,
    ) -> date | None:
        """Return the most recent date stored for *fund_code*, or None."""

    @abstractmethod
    async def missing_dates(
        self,
        session: AsyncSession,
        fund_code: str,
        expected_dates: list[date],
    ) -> list[date]:
        """Return dates from *expected_dates* that are absent in the DB.

        Useful for incremental ingestion: the caller supplies the full
        set of expected trading/report dates and receives back only the
        gaps that need to be fetched.
        """
