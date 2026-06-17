"""Provider abstraction layer for fund data sources.

Defines the ``FundDataProvider`` Protocol that all data source adapters
must implement, plus the exception hierarchy used across the provider
chain.

Design notes
------------
* ``FundDataProvider`` is a structural Protocol (PEP 544) — concrete
  providers do **not** need to inherit from it; duck-typing is enough.
  This keeps third-party wrappers (e.g. AkShare) lightweight.
* ``priority`` is an integer where **lower = higher priority**.
  EastmoneyProvider uses priority=1, AkshareProvider uses priority=2.
* ``HealthStatus`` is a simple dataclass returned by ``health_check()``.
  It carries a boolean ``healthy`` flag plus an optional ``message``
  for diagnostics.
* Exception hierarchy:
    ProviderError (base)
    ├── ProviderTimeoutError   — request timed out
    ├── ProviderNotFoundError  — fund code not found at this source
    └── AllProvidersFailedError — all providers in the chain failed
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol, runtime_checkable

from app.data.schemas.funds import (
    Announcement,
    DividendRecord,
    FundMeta,
    HoldingSnapshot,
    NavRecord,
)


# ---------------------------------------------------------------------------
# Health status
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HealthStatus:
    """Result of a provider health check.

    Attributes:
        healthy: ``True`` if the provider is reachable and functional.
        message: Optional human-readable diagnostic message.
        latency_ms: Round-trip latency in milliseconds, if measured.
    """

    healthy: bool
    message: str = ""
    latency_ms: float | None = None


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class ProviderError(Exception):
    """Base exception for all data provider errors.

    Attributes:
        provider_name: Name of the provider that raised the error.
        fund_code: Fund code being fetched when the error occurred,
            if applicable.
    """

    def __init__(
        self,
        message: str,
        *,
        provider_name: str = "",
        fund_code: str = "",
    ) -> None:
        super().__init__(message)
        self.provider_name = provider_name
        self.fund_code = fund_code

    def __str__(self) -> str:
        parts = [super().__str__()]
        if self.provider_name:
            parts.append(f"provider={self.provider_name!r}")
        if self.fund_code:
            parts.append(f"fund_code={self.fund_code!r}")
        return " | ".join(parts)


class ProviderTimeoutError(ProviderError):
    """Raised when a provider request exceeds the configured timeout.

    This is a subclass of ``ProviderError`` so callers can catch either
    the specific timeout or any provider error with a single ``except``.
    """


class ProviderNotFoundError(ProviderError):
    """Raised when the requested fund code does not exist at this source.

    Callers should **not** retry on this error — the fund simply isn't
    available from this provider.
    """


class AllProvidersFailedError(Exception):
    """Raised by ``CompositeProvider`` when every provider in the chain fails.

    Attributes:
        errors: List of ``(provider_name, exception)`` tuples collected
            during the fallback chain.
    """

    def __init__(
        self,
        errors: list[tuple[str, Exception]],
        *,
        fund_code: str = "",
    ) -> None:
        self.errors = errors
        self.fund_code = fund_code
        provider_names = ", ".join(name for name, _ in errors)
        super().__init__(
            f"All providers failed for fund_code={fund_code!r}: [{provider_names}]"
        )

    @property
    def provider_names(self) -> list[str]:
        """Names of all providers that were attempted."""
        return [name for name, _ in self.errors]


# ---------------------------------------------------------------------------
# FundDataProvider Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class FundDataProvider(Protocol):
    """Structural protocol for fund data source adapters.

    All concrete providers (EastmoneyProvider, AkshareProvider, …) must
    expose these attributes and methods.  They do **not** need to inherit
    from this class — structural subtyping (duck-typing) is sufficient.

    Attributes:
        name: Unique identifier for this provider (e.g. ``"eastmoney"``).
        priority: Dispatch priority — lower numbers are tried first.
            EastmoneyProvider = 1, AkshareProvider = 2.
    """

    name: str
    priority: int  # lower = higher priority

    async def fetch_fund_meta(self, code: str) -> FundMeta:
        """Fetch basic fund metadata.

        Args:
            code: Fund code (e.g. ``"000001"``).

        Returns:
            A populated ``FundMeta`` instance.

        Raises:
            ProviderNotFoundError: If the fund code is unknown.
            ProviderTimeoutError: If the request times out.
            ProviderError: For any other provider-level failure.
        """
        ...

    async def fetch_nav_history(
        self,
        code: str,
        start: date,
        end: date,
    ) -> list[NavRecord]:
        """Fetch historical NAV records for a date range.

        Args:
            code: Fund code.
            start: Inclusive start date.
            end: Inclusive end date.

        Returns:
            List of ``NavRecord`` objects sorted by ``trade_date`` ascending.
            May be empty if no data exists for the range.

        Raises:
            ProviderNotFoundError: If the fund code is unknown.
            ProviderTimeoutError: If the request times out.
            ProviderError: For any other provider-level failure.
        """
        ...

    async def fetch_holdings(
        self,
        code: str,
        quarter: str,
    ) -> HoldingSnapshot:
        """Fetch quarterly holding snapshot.

        Args:
            code: Fund code.
            quarter: Quarter identifier in ``"YYYY-QN"`` format
                (e.g. ``"2024-Q1"`` for the quarter ending 2024-03-31).

        Returns:
            A ``HoldingSnapshot`` with the top-N positions.

        Raises:
            ProviderNotFoundError: If the fund or quarter is unknown.
            ProviderTimeoutError: If the request times out.
            ProviderError: For any other provider-level failure.
        """
        ...

    async def fetch_dividends(self, code: str) -> list[DividendRecord]:
        """Fetch all dividend and split events for a fund.

        Args:
            code: Fund code.

        Returns:
            List of ``DividendRecord`` objects sorted by ``ex_date`` ascending.

        Raises:
            ProviderNotFoundError: If the fund code is unknown.
            ProviderTimeoutError: If the request times out.
            ProviderError: For any other provider-level failure.
        """
        ...

    async def fetch_announcements(
        self,
        code: str,
        since: date,
    ) -> list[Announcement]:
        """Fetch fund announcements published on or after ``since``.

        Args:
            code: Fund code.
            since: Inclusive lower bound for ``publish_date``.

        Returns:
            List of ``Announcement`` objects sorted by ``publish_date``
            ascending.

        Raises:
            ProviderNotFoundError: If the fund code is unknown.
            ProviderTimeoutError: If the request times out.
            ProviderError: For any other provider-level failure.
        """
        ...

    async def health_check(self) -> HealthStatus:
        """Probe the provider to verify it is reachable and functional.

        Returns:
            A ``HealthStatus`` instance.  Implementations should catch
            all exceptions internally and return ``HealthStatus(healthy=False)``
            rather than propagating them.
        """
        ...
