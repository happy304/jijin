"""Unit tests for app.data.fetchers.retry."""

from __future__ import annotations

import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, call, patch

from tenacity import RetryError

from app.data.fetchers.retry import make_retry_decorator, retry_on_network_error


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_failing_then_succeeding(
    fail_count: int,
    exception: Exception,
    return_value: object = "ok",
) -> AsyncMock:
    """Return an AsyncMock that raises ``exception`` ``fail_count`` times,
    then returns ``return_value``."""
    side_effects: list[object] = [exception] * fail_count + [return_value]
    mock = AsyncMock(side_effect=side_effects)
    return mock


# ---------------------------------------------------------------------------
# retry_on_network_error — default decorator
# ---------------------------------------------------------------------------


class TestRetryOnNetworkError:
    @pytest.mark.asyncio
    async def test_succeeds_on_first_attempt(self) -> None:
        mock_fn = AsyncMock(return_value="data")

        @retry_on_network_error
        async def fetch() -> str:
            return await mock_fn()

        result = await fetch()
        assert result == "data"
        assert mock_fn.call_count == 1

    @pytest.mark.asyncio
    async def test_retries_on_httpx_error(self) -> None:
        """Should retry on httpx.HTTPError and succeed on 2nd attempt."""
        mock_fn = make_failing_then_succeeding(
            fail_count=1,
            exception=httpx.ConnectError("connection refused"),
            return_value="data",
        )

        @retry_on_network_error
        async def fetch() -> str:
            return await mock_fn()

        result = await fetch()
        assert result == "data"
        assert mock_fn.call_count == 2

    @pytest.mark.asyncio
    async def test_retries_on_timeout_error(self) -> None:
        """Should retry on TimeoutError."""
        mock_fn = make_failing_then_succeeding(
            fail_count=1,
            exception=TimeoutError("timed out"),
            return_value="response",
        )

        @retry_on_network_error
        async def fetch() -> str:
            return await mock_fn()

        result = await fetch()
        assert result == "response"
        assert mock_fn.call_count == 2

    @pytest.mark.asyncio
    async def test_retries_on_connection_error(self) -> None:
        """Should retry on ConnectionError."""
        mock_fn = make_failing_then_succeeding(
            fail_count=1,
            exception=ConnectionError("reset by peer"),
            return_value="ok",
        )

        @retry_on_network_error
        async def fetch() -> str:
            return await mock_fn()

        result = await fetch()
        assert result == "ok"
        assert mock_fn.call_count == 2

    @pytest.mark.asyncio
    async def test_max_3_attempts_total(self) -> None:
        """After 3 total attempts (1 + 2 retries), the exception is re-raised."""
        exc = httpx.ConnectError("always fails")
        mock_fn = AsyncMock(side_effect=exc)

        @retry_on_network_error
        async def fetch() -> str:
            return await mock_fn()

        with pytest.raises(httpx.ConnectError):
            await fetch()

        assert mock_fn.call_count == 3

    @pytest.mark.asyncio
    async def test_non_retryable_exception_not_retried(self) -> None:
        """ValueError should not be retried."""
        mock_fn = AsyncMock(side_effect=ValueError("bad input"))

        @retry_on_network_error
        async def fetch() -> str:
            return await mock_fn()

        with pytest.raises(ValueError):
            await fetch()

        assert mock_fn.call_count == 1

    @pytest.mark.asyncio
    async def test_reraises_last_exception(self) -> None:
        """The original exception type should be re-raised, not RetryError."""
        exc = httpx.ReadTimeout("read timeout")
        mock_fn = AsyncMock(side_effect=exc)

        @retry_on_network_error
        async def fetch() -> str:
            return await mock_fn()

        with pytest.raises(httpx.ReadTimeout):
            await fetch()

    @pytest.mark.asyncio
    async def test_succeeds_on_third_attempt(self) -> None:
        """Should succeed if the 3rd attempt (last allowed) succeeds."""
        mock_fn = make_failing_then_succeeding(
            fail_count=2,
            exception=httpx.ConnectError("fail"),
            return_value="success",
        )

        @retry_on_network_error
        async def fetch() -> str:
            return await mock_fn()

        result = await fetch()
        assert result == "success"
        assert mock_fn.call_count == 3


# ---------------------------------------------------------------------------
# make_retry_decorator — custom configuration
# ---------------------------------------------------------------------------


class TestMakeRetryDecorator:
    @pytest.mark.asyncio
    async def test_custom_max_attempts(self) -> None:
        """Custom max_attempts=5 should allow up to 5 total attempts."""
        exc = httpx.ConnectError("fail")
        mock_fn = AsyncMock(side_effect=exc)

        decorator = make_retry_decorator(max_attempts=5, min_wait=0, max_wait=0)

        @decorator
        async def fetch() -> str:
            return await mock_fn()

        with pytest.raises(httpx.ConnectError):
            await fetch()

        assert mock_fn.call_count == 5

    @pytest.mark.asyncio
    async def test_reraise_false_raises_retry_error(self) -> None:
        """With reraise=False, tenacity raises RetryError instead."""
        exc = httpx.ConnectError("fail")
        mock_fn = AsyncMock(side_effect=exc)

        decorator = make_retry_decorator(
            max_attempts=2, min_wait=0, max_wait=0, reraise=False
        )

        @decorator
        async def fetch() -> str:
            return await mock_fn()

        with pytest.raises(RetryError):
            await fetch()

    @pytest.mark.asyncio
    async def test_zero_wait_retries_immediately(self) -> None:
        """With min_wait=0 and max_wait=0, retries happen without delay."""
        import time

        exc = httpx.ConnectError("fail")
        mock_fn = AsyncMock(side_effect=exc)

        decorator = make_retry_decorator(max_attempts=3, min_wait=0, max_wait=0)

        @decorator
        async def fetch() -> str:
            return await mock_fn()

        start = time.monotonic()
        with pytest.raises(httpx.ConnectError):
            await fetch()
        elapsed = time.monotonic() - start

        assert mock_fn.call_count == 3
        assert elapsed < 1.0  # should be very fast with no wait
