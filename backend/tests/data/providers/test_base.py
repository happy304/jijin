"""Unit tests for app.data.providers.base.

Tests cover:
- HealthStatus dataclass
- ProviderError and its subclasses
- AllProvidersFailedError
- FundDataProvider Protocol runtime-checkable behaviour
"""

from __future__ import annotations

from datetime import date

import pytest

from app.data.providers.base import (
    AllProvidersFailedError,
    FundDataProvider,
    HealthStatus,
    ProviderError,
    ProviderNotFoundError,
    ProviderTimeoutError,
)
from app.data.schemas.funds import (
    Announcement,
    DividendRecord,
    FundMeta,
    HoldingSnapshot,
    NavRecord,
)


# ---------------------------------------------------------------------------
# HealthStatus
# ---------------------------------------------------------------------------


class TestHealthStatus:
    def test_healthy_true(self) -> None:
        hs = HealthStatus(healthy=True)
        assert hs.healthy is True

    def test_healthy_false(self) -> None:
        hs = HealthStatus(healthy=False)
        assert hs.healthy is False

    def test_default_message_empty(self) -> None:
        hs = HealthStatus(healthy=True)
        assert hs.message == ""

    def test_custom_message(self) -> None:
        hs = HealthStatus(healthy=False, message="connection refused")
        assert hs.message == "connection refused"

    def test_latency_ms_default_none(self) -> None:
        hs = HealthStatus(healthy=True)
        assert hs.latency_ms is None

    def test_latency_ms_set(self) -> None:
        hs = HealthStatus(healthy=True, latency_ms=42.5)
        assert hs.latency_ms == 42.5

    def test_frozen_immutable(self) -> None:
        hs = HealthStatus(healthy=True)
        with pytest.raises((AttributeError, TypeError)):
            hs.healthy = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ProviderError
# ---------------------------------------------------------------------------


class TestProviderError:
    def test_basic_message(self) -> None:
        err = ProviderError("something went wrong")
        assert "something went wrong" in str(err)

    def test_provider_name_in_str(self) -> None:
        err = ProviderError("oops", provider_name="eastmoney")
        assert "eastmoney" in str(err)

    def test_fund_code_in_str(self) -> None:
        err = ProviderError("oops", fund_code="000001")
        assert "000001" in str(err)

    def test_both_attrs_in_str(self) -> None:
        err = ProviderError("oops", provider_name="akshare", fund_code="110022")
        s = str(err)
        assert "akshare" in s
        assert "110022" in s

    def test_default_provider_name_empty(self) -> None:
        err = ProviderError("oops")
        assert err.provider_name == ""

    def test_default_fund_code_empty(self) -> None:
        err = ProviderError("oops")
        assert err.fund_code == ""

    def test_is_exception(self) -> None:
        err = ProviderError("oops")
        assert isinstance(err, Exception)

    def test_can_be_raised_and_caught(self) -> None:
        with pytest.raises(ProviderError):
            raise ProviderError("test")


# ---------------------------------------------------------------------------
# ProviderTimeoutError
# ---------------------------------------------------------------------------


class TestProviderTimeoutError:
    def test_is_provider_error(self) -> None:
        err = ProviderTimeoutError("timed out")
        assert isinstance(err, ProviderError)

    def test_message_preserved(self) -> None:
        err = ProviderTimeoutError("request timed out after 30s")
        assert "30s" in str(err)

    def test_provider_name_attribute(self) -> None:
        err = ProviderTimeoutError("timeout", provider_name="eastmoney")
        assert err.provider_name == "eastmoney"

    def test_caught_as_provider_error(self) -> None:
        with pytest.raises(ProviderError):
            raise ProviderTimeoutError("timeout")


# ---------------------------------------------------------------------------
# ProviderNotFoundError
# ---------------------------------------------------------------------------


class TestProviderNotFoundError:
    def test_is_provider_error(self) -> None:
        err = ProviderNotFoundError("not found")
        assert isinstance(err, ProviderError)

    def test_fund_code_attribute(self) -> None:
        err = ProviderNotFoundError("not found", fund_code="999999")
        assert err.fund_code == "999999"

    def test_caught_as_provider_error(self) -> None:
        with pytest.raises(ProviderError):
            raise ProviderNotFoundError("not found")

    def test_not_caught_as_timeout(self) -> None:
        """ProviderNotFoundError should NOT be caught as ProviderTimeoutError."""
        with pytest.raises(ProviderNotFoundError):
            try:
                raise ProviderNotFoundError("not found")
            except ProviderTimeoutError:
                pass  # should not reach here


# ---------------------------------------------------------------------------
# AllProvidersFailedError
# ---------------------------------------------------------------------------


class TestAllProvidersFailedError:
    def _make_errors(self) -> list[tuple[str, Exception]]:
        return [
            ("eastmoney", ProviderTimeoutError("timeout")),
            ("akshare", ProviderError("parse error")),
        ]

    def test_is_exception(self) -> None:
        err = AllProvidersFailedError(self._make_errors())
        assert isinstance(err, Exception)

    def test_not_provider_error(self) -> None:
        """AllProvidersFailedError is a distinct exception, not a ProviderError."""
        err = AllProvidersFailedError(self._make_errors())
        assert not isinstance(err, ProviderError)

    def test_errors_attribute(self) -> None:
        errors = self._make_errors()
        err = AllProvidersFailedError(errors)
        assert err.errors is errors

    def test_provider_names_property(self) -> None:
        err = AllProvidersFailedError(self._make_errors())
        assert err.provider_names == ["eastmoney", "akshare"]

    def test_fund_code_in_message(self) -> None:
        err = AllProvidersFailedError(self._make_errors(), fund_code="000001")
        assert "000001" in str(err)

    def test_provider_names_in_message(self) -> None:
        err = AllProvidersFailedError(self._make_errors())
        msg = str(err)
        assert "eastmoney" in msg
        assert "akshare" in msg

    def test_empty_errors_list(self) -> None:
        err = AllProvidersFailedError([])
        assert err.provider_names == []

    def test_can_be_raised_and_caught(self) -> None:
        with pytest.raises(AllProvidersFailedError):
            raise AllProvidersFailedError(self._make_errors())


# ---------------------------------------------------------------------------
# FundDataProvider Protocol — runtime_checkable
# ---------------------------------------------------------------------------


class TestFundDataProviderProtocol:
    """Verify that the Protocol is runtime-checkable and that a minimal
    concrete implementation satisfies it."""

    def _make_minimal_provider(self) -> object:
        """Return an object that structurally satisfies FundDataProvider."""

        class _MinimalProvider:
            name = "test"
            priority = 99

            async def fetch_fund_meta(self, code: str) -> FundMeta:
                raise NotImplementedError

            async def fetch_nav_history(
                self, code: str, start: date, end: date
            ) -> list[NavRecord]:
                raise NotImplementedError

            async def fetch_holdings(
                self, code: str, quarter: str
            ) -> HoldingSnapshot:
                raise NotImplementedError

            async def fetch_dividends(self, code: str) -> list[DividendRecord]:
                raise NotImplementedError

            async def fetch_announcements(
                self, code: str, since: date
            ) -> list[Announcement]:
                raise NotImplementedError

            async def health_check(self) -> HealthStatus:
                raise NotImplementedError

        return _MinimalProvider()

    def test_isinstance_check_passes_for_conforming_class(self) -> None:
        provider = self._make_minimal_provider()
        assert isinstance(provider, FundDataProvider)

    def test_isinstance_check_fails_for_missing_name(self) -> None:
        class _NoName:
            priority = 1

            async def fetch_fund_meta(self, code: str) -> FundMeta:
                raise NotImplementedError

            async def fetch_nav_history(
                self, code: str, start: date, end: date
            ) -> list[NavRecord]:
                raise NotImplementedError

            async def fetch_holdings(
                self, code: str, quarter: str
            ) -> HoldingSnapshot:
                raise NotImplementedError

            async def fetch_dividends(self, code: str) -> list[DividendRecord]:
                raise NotImplementedError

            async def fetch_announcements(
                self, code: str, since: date
            ) -> list[Announcement]:
                raise NotImplementedError

            async def health_check(self) -> HealthStatus:
                raise NotImplementedError

        # Missing `name` attribute — should NOT satisfy the Protocol
        assert not isinstance(_NoName(), FundDataProvider)

    def test_isinstance_check_fails_for_missing_method(self) -> None:
        class _MissingMethod:
            name = "test"
            priority = 1
            # fetch_fund_meta is missing

            async def fetch_nav_history(
                self, code: str, start: date, end: date
            ) -> list[NavRecord]:
                raise NotImplementedError

            async def fetch_holdings(
                self, code: str, quarter: str
            ) -> HoldingSnapshot:
                raise NotImplementedError

            async def fetch_dividends(self, code: str) -> list[DividendRecord]:
                raise NotImplementedError

            async def fetch_announcements(
                self, code: str, since: date
            ) -> list[Announcement]:
                raise NotImplementedError

            async def health_check(self) -> HealthStatus:
                raise NotImplementedError

        assert not isinstance(_MissingMethod(), FundDataProvider)

    def test_priority_attribute_accessible(self) -> None:
        provider = self._make_minimal_provider()
        assert hasattr(provider, "priority")
        assert provider.priority == 99  # type: ignore[union-attr]

    def test_name_attribute_accessible(self) -> None:
        provider = self._make_minimal_provider()
        assert hasattr(provider, "name")
        assert provider.name == "test"  # type: ignore[union-attr]
