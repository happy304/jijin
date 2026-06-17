"""Unit tests for app.data.fetchers.http_client."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.data.fetchers.http_client import (
    AsyncHttpClient,
    _DEFAULT_REFERER,
    _DEFAULT_UA_POOL,
    build_default_headers,
)


# ---------------------------------------------------------------------------
# build_default_headers
# ---------------------------------------------------------------------------


class TestBuildDefaultHeaders:
    def test_contains_user_agent(self) -> None:
        headers = build_default_headers()
        assert "User-Agent" in headers
        assert len(headers["User-Agent"]) > 10

    def test_contains_referer(self) -> None:
        headers = build_default_headers()
        assert headers["Referer"] == _DEFAULT_REFERER

    def test_custom_referer(self) -> None:
        headers = build_default_headers(referer="http://custom.example/")
        assert headers["Referer"] == "http://custom.example/"

    def test_custom_user_agent(self) -> None:
        headers = build_default_headers(user_agent="MyBot/1.0")
        assert headers["User-Agent"] == "MyBot/1.0"

    def test_contains_accept(self) -> None:
        headers = build_default_headers()
        assert "Accept" in headers

    def test_contains_accept_language(self) -> None:
        headers = build_default_headers()
        assert "Accept-Language" in headers


# ---------------------------------------------------------------------------
# AsyncHttpClient — construction
# ---------------------------------------------------------------------------


class TestAsyncHttpClientInit:
    def test_default_ua_pool(self) -> None:
        client = AsyncHttpClient()
        assert client._ua_pool == _DEFAULT_UA_POOL

    def test_custom_ua_pool(self) -> None:
        pool = ["UA1", "UA2"]
        client = AsyncHttpClient(ua_pool=pool)
        assert client._ua_pool == pool

    def test_default_timeout(self) -> None:
        client = AsyncHttpClient()
        assert client._timeout == 30.0

    def test_custom_timeout(self) -> None:
        client = AsyncHttpClient(timeout=10.0)
        assert client._timeout == 10.0

    def test_no_proxy_by_default(self) -> None:
        client = AsyncHttpClient()
        assert client._proxy is None

    def test_custom_proxy(self) -> None:
        client = AsyncHttpClient(proxy="http://proxy:8080")
        assert client._proxy == "http://proxy:8080"


# ---------------------------------------------------------------------------
# AsyncHttpClient — UA rotation
# ---------------------------------------------------------------------------


class TestUARotation:
    def test_ua_rotates_across_requests(self) -> None:
        """Each call to _rotate_ua should advance the cycle."""
        pool = ["UA-A", "UA-B", "UA-C"]
        client = AsyncHttpClient(ua_pool=pool)
        # Drain the initial position set during _build_client (not called yet)
        # Directly test the cycle via _rotate_ua helper
        seen: set[str] = set()
        for _ in range(len(pool) * 2):
            headers = client._rotate_ua({})
            seen.add(headers["User-Agent"])
        assert seen == set(pool)

    def test_single_ua_pool_always_same(self) -> None:
        client = AsyncHttpClient(ua_pool=["OnlyUA"])
        for _ in range(5):
            headers = client._rotate_ua({})
            assert headers["User-Agent"] == "OnlyUA"


# ---------------------------------------------------------------------------
# AsyncHttpClient — lifecycle
# ---------------------------------------------------------------------------


class TestAsyncHttpClientLifecycle:
    @pytest.mark.asyncio
    async def test_context_manager_creates_and_closes_client(self) -> None:
        async with AsyncHttpClient(http2=False) as client:
            assert client._client is not None
            assert not client._client.is_closed
        # After exiting, client should be closed
        assert client._client is None

    @pytest.mark.asyncio
    async def test_aclose_idempotent(self) -> None:
        client = AsyncHttpClient(http2=False)
        await client.aclose()  # no client created yet — should not raise
        await client.aclose()  # second call also safe

    @pytest.mark.asyncio
    async def test_ensure_client_creates_on_first_call(self) -> None:
        client = AsyncHttpClient(http2=False)
        assert client._client is None
        inner = await client._ensure_client()
        assert inner is not None
        await client.aclose()

    @pytest.mark.asyncio
    async def test_ensure_client_reuses_existing(self) -> None:
        client = AsyncHttpClient(http2=False)
        first = await client._ensure_client()
        second = await client._ensure_client()
        assert first is second
        await client.aclose()


# ---------------------------------------------------------------------------
# AsyncHttpClient — HTTP requests (mocked with unittest.mock)
# ---------------------------------------------------------------------------


class TestAsyncHttpClientRequests:
    @pytest.mark.asyncio
    async def test_get_calls_underlying_client(self) -> None:
        """get() should delegate to the underlying httpx.AsyncClient.get()."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200

        mock_inner = MagicMock(spec=httpx.AsyncClient)
        mock_inner.is_closed = False
        mock_inner.get = AsyncMock(return_value=mock_response)

        client = AsyncHttpClient()
        client._client = mock_inner

        resp = await client.get("https://example.com/api")
        assert resp.status_code == 200
        mock_inner.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_passes_params(self) -> None:
        """get() should forward params to the underlying client."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200

        mock_inner = MagicMock(spec=httpx.AsyncClient)
        mock_inner.is_closed = False
        mock_inner.get = AsyncMock(return_value=mock_response)

        client = AsyncHttpClient()
        client._client = mock_inner

        await client.get("https://example.com/search", params={"q": "fund"})
        call_kwargs = mock_inner.get.call_args
        assert call_kwargs.kwargs.get("params") == {"q": "fund"}

    @pytest.mark.asyncio
    async def test_post_calls_underlying_client(self) -> None:
        """post() should delegate to the underlying httpx.AsyncClient.post()."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 201

        mock_inner = MagicMock(spec=httpx.AsyncClient)
        mock_inner.is_closed = False
        mock_inner.post = AsyncMock(return_value=mock_response)

        client = AsyncHttpClient()
        client._client = mock_inner

        resp = await client.post("https://example.com/submit", json={"name": "test"})
        assert resp.status_code == 201
        mock_inner.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_injects_rotated_ua_header(self) -> None:
        """get() with rotate_ua=True should inject a User-Agent header."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200

        captured_headers: list[dict[str, str] | None] = []

        async def capture_get(url: str, **kwargs: object) -> MagicMock:
            captured_headers.append(kwargs.get("headers"))  # type: ignore[arg-type]
            return mock_response

        mock_inner = MagicMock(spec=httpx.AsyncClient)
        mock_inner.is_closed = False
        mock_inner.get = capture_get

        pool = ["UA-X", "UA-Y"]
        client = AsyncHttpClient(ua_pool=pool)
        client._client = mock_inner

        await client.get("https://example.com/")
        await client.get("https://example.com/")

        # Both calls should have a User-Agent from the pool
        for headers in captured_headers:
            assert headers is not None
            assert headers.get("User-Agent") in pool

    @pytest.mark.asyncio
    async def test_per_request_headers_passed(self) -> None:
        """Per-request headers should be forwarded to the underlying client."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200

        captured_headers: list[dict[str, str] | None] = []

        async def capture_get(url: str, **kwargs: object) -> MagicMock:
            captured_headers.append(kwargs.get("headers"))  # type: ignore[arg-type]
            return mock_response

        mock_inner = MagicMock(spec=httpx.AsyncClient)
        mock_inner.is_closed = False
        mock_inner.get = capture_get

        client = AsyncHttpClient()
        client._client = mock_inner

        await client.get("https://example.com/", headers={"X-Per-Request": "yes"})
        assert captured_headers[0] is not None
        assert captured_headers[0].get("X-Per-Request") == "yes"

    @pytest.mark.asyncio
    async def test_extra_headers_included_in_base_client(self) -> None:
        """Extra headers passed at construction should be in the base client headers."""
        client = AsyncHttpClient(extra_headers={"X-Custom": "value"}, http2=False)
        inner = await client._ensure_client()
        # The extra header should be in the default headers of the inner client
        assert inner.headers.get("x-custom") == "value"
        await client.aclose()
