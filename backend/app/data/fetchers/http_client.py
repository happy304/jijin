"""Async HTTP client based on httpx.

Features:
- User-Agent pool with round-robin rotation
- Default headers (Referer, Accept, Accept-Language)
- Configurable timeout
- Optional proxy support
- Context manager support for connection pooling

Design notes
------------
* A single `AsyncHttpClient` instance should be shared across requests
  to benefit from connection pooling. Use it as an async context manager
  or call `aclose()` explicitly on shutdown.
* The UA pool rotates deterministically (round-robin) so tests can
  predict which UA will be used without mocking random.
"""

from __future__ import annotations

import itertools
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Default User-Agent pool (desktop browsers, Windows/Mac/Linux mix)
# ---------------------------------------------------------------------------
_DEFAULT_UA_POOL: list[str] = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.4.1 Safari/605.1.15"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
        "Gecko/20100101 Firefox/125.0"
    ),
]

# Default Referer used for Eastmoney requests
_DEFAULT_REFERER = "http://fundf10.eastmoney.com/"


def build_default_headers(
    *,
    referer: str = _DEFAULT_REFERER,
    user_agent: str | None = None,
) -> dict[str, str]:
    """Build the default request headers.

    Args:
        referer: Referer header value. Defaults to Eastmoney fund page.
        user_agent: Explicit UA string. If None, the first UA from the
            default pool is used.

    Returns:
        A dict of HTTP headers suitable for passing to httpx.
    """
    ua = user_agent or _DEFAULT_UA_POOL[0]
    return {
        "User-Agent": ua,
        "Referer": referer,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    }


class AsyncHttpClient:
    """Async HTTP client with UA rotation and default headers.

    Usage::

        async with AsyncHttpClient() as client:
            resp = await client.get("https://example.com")

    Or manage lifecycle manually::

        client = AsyncHttpClient()
        resp = await client.get("https://example.com")
        await client.aclose()
    """

    def __init__(
        self,
        *,
        ua_pool: list[str] | None = None,
        referer: str = _DEFAULT_REFERER,
        timeout: float = 30.0,
        proxy: str | None = None,
        extra_headers: dict[str, str] | None = None,
        http2: bool = True,
    ) -> None:
        """Initialise the client.

        Args:
            ua_pool: List of User-Agent strings to rotate through.
                Defaults to the built-in pool.
            referer: Default Referer header value.
            timeout: Request timeout in seconds.
            proxy: Optional proxy URL (e.g. "http://user:pass@host:port").
            extra_headers: Additional headers merged into every request.
            http2: Whether to enable HTTP/2 support.
        """
        self._ua_pool = ua_pool or _DEFAULT_UA_POOL
        self._ua_cycle = itertools.cycle(self._ua_pool)
        self._referer = referer
        self._timeout = timeout
        self._proxy = proxy
        self._extra_headers = extra_headers or {}
        self._http2 = http2
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _build_client(self) -> httpx.AsyncClient:
        """Create the underlying httpx.AsyncClient."""
        base_headers = build_default_headers(
            referer=self._referer,
            user_agent=next(self._ua_cycle),
        )
        base_headers.update(self._extra_headers)

        kwargs: dict[str, Any] = {
            "headers": base_headers,
            "timeout": httpx.Timeout(self._timeout),
            "follow_redirects": True,
            "http2": self._http2,
        }
        if self._proxy:
            kwargs["proxy"] = self._proxy

        return httpx.AsyncClient(**kwargs)

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Return the shared client, creating it on first call."""
        if self._client is None or self._client.is_closed:
            self._client = self._build_client()
        return self._client

    async def aclose(self) -> None:
        """Close the underlying httpx client and release connections."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def __aenter__(self) -> "AsyncHttpClient":
        await self._ensure_client()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------
    # Request helpers
    # ------------------------------------------------------------------

    def _rotate_ua(self, headers: dict[str, str]) -> dict[str, str]:
        """Return headers with the next UA from the pool injected."""
        rotated = dict(headers)
        rotated["User-Agent"] = next(self._ua_cycle)
        return rotated

    async def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        rotate_ua: bool = True,
    ) -> httpx.Response:
        """Perform an async GET request.

        Args:
            url: Target URL.
            params: Query parameters.
            headers: Per-request headers (merged with defaults).
            rotate_ua: If True, rotate the User-Agent for this request.

        Returns:
            The httpx.Response object.

        Raises:
            httpx.HTTPError: On network or HTTP errors.
        """
        client = await self._ensure_client()
        merged: dict[str, str] = {}
        if headers:
            merged.update(headers)
        if rotate_ua:
            merged["User-Agent"] = next(self._ua_cycle)
        return await client.get(url, params=params, headers=merged or None)

    async def post(
        self,
        url: str,
        *,
        data: dict[str, Any] | None = None,
        json: Any | None = None,
        headers: dict[str, str] | None = None,
        rotate_ua: bool = True,
    ) -> httpx.Response:
        """Perform an async POST request.

        Args:
            url: Target URL.
            data: Form-encoded body.
            json: JSON body (mutually exclusive with data).
            headers: Per-request headers.
            rotate_ua: If True, rotate the User-Agent for this request.

        Returns:
            The httpx.Response object.
        """
        client = await self._ensure_client()
        merged: dict[str, str] = {}
        if headers:
            merged.update(headers)
        if rotate_ua:
            merged["User-Agent"] = next(self._ua_cycle)
        return await client.post(
            url, data=data, json=json, headers=merged or None
        )
