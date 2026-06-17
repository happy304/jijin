"""Optional proxy pool interface with in-memory implementation.

Design notes
------------
* ``ProxyPool`` is a Protocol (structural subtyping) so alternative
  implementations (Redis-backed, external API, etc.) can be swapped in
  without changing call sites.
* ``InMemoryProxyPool`` is the default implementation. It stores proxies
  in memory, rotates them round-robin, and automatically removes proxies
  that fail too many times.
* Proxies are represented as plain URL strings, e.g.
  ``"http://user:pass@host:port"`` or ``"socks5://host:port"``.
* When the pool is empty (no proxies configured or all removed), ``next()``
  returns ``None`` so callers can fall back to a direct connection.

Usage::

    pool = InMemoryProxyPool(proxies=[
        "http://proxy1:8080",
        "http://proxy2:8080",
    ])

    proxy = pool.next()          # round-robin selection
    try:
        resp = await client.get(url, proxy=proxy)
        pool.report_success(proxy)
    except Exception:
        pool.report_failure(proxy)  # auto-removes after max_failures
        raise
"""

from __future__ import annotations

import itertools
import logging
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class ProxyPool(Protocol):
    """Interface for proxy pool implementations.

    Implementations must provide ``next()``, ``report_success()``,
    ``report_failure()``, ``add()``, and ``remove()`` methods.
    """

    def next(self) -> str | None:
        """Return the next proxy URL, or None if the pool is empty."""
        ...

    def report_success(self, proxy: str) -> None:
        """Record a successful request through ``proxy``."""
        ...

    def report_failure(self, proxy: str) -> None:
        """Record a failed request through ``proxy``.

        Implementations may remove the proxy after too many failures.
        """
        ...

    def add(self, proxy: str) -> None:
        """Add a proxy URL to the pool."""
        ...

    def remove(self, proxy: str) -> None:
        """Remove a proxy URL from the pool."""
        ...

    def size(self) -> int:
        """Return the number of proxies currently in the pool."""
        ...


class InMemoryProxyPool:
    """In-memory proxy pool with round-robin rotation and auto-eviction.

    Args:
        proxies: Initial list of proxy URLs.
        max_failures: Number of consecutive failures before a proxy is
            automatically removed from the pool. Defaults to 3.
    """

    def __init__(
        self,
        proxies: list[str] | None = None,
        *,
        max_failures: int = 3,
    ) -> None:
        if max_failures < 1:
            raise ValueError(f"max_failures must be >= 1, got {max_failures}")
        self._max_failures = max_failures
        self._proxies: list[str] = list(proxies or [])
        self._failure_counts: dict[str, int] = {}
        self._cycle: itertools.cycle[str] | None = self._make_cycle()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _make_cycle(self) -> itertools.cycle[str] | None:
        """Rebuild the round-robin cycle from the current proxy list."""
        if not self._proxies:
            return None
        return itertools.cycle(self._proxies)

    def _rebuild_cycle(self) -> None:
        """Rebuild the cycle after the proxy list changes."""
        self._cycle = self._make_cycle()

    # ------------------------------------------------------------------
    # ProxyPool interface
    # ------------------------------------------------------------------

    def next(self) -> str | None:
        """Return the next proxy URL in round-robin order.

        Returns:
            A proxy URL string, or ``None`` if the pool is empty.
        """
        if not self._proxies or self._cycle is None:
            return None
        # Advance the cycle; skip proxies that were removed mid-cycle
        for _ in range(len(self._proxies)):
            proxy = next(self._cycle)
            if proxy in self._proxies:
                return proxy
        return None

    def report_success(self, proxy: str) -> None:
        """Reset the failure counter for ``proxy`` on success."""
        if proxy in self._failure_counts:
            self._failure_counts[proxy] = 0

    def report_failure(self, proxy: str) -> None:
        """Increment the failure counter; remove proxy if threshold reached.

        Args:
            proxy: The proxy URL that failed.
        """
        if proxy not in self._proxies:
            return
        self._failure_counts[proxy] = self._failure_counts.get(proxy, 0) + 1
        if self._failure_counts[proxy] >= self._max_failures:
            logger.warning(
                "ProxyPool: removing proxy %r after %d consecutive failures",
                proxy,
                self._failure_counts[proxy],
            )
            self.remove(proxy)

    def add(self, proxy: str) -> None:
        """Add a proxy URL to the pool.

        If the proxy is already present, this is a no-op.

        Args:
            proxy: Proxy URL to add.
        """
        if proxy not in self._proxies:
            self._proxies.append(proxy)
            self._failure_counts.setdefault(proxy, 0)
            self._rebuild_cycle()
            logger.debug("ProxyPool: added proxy %r (pool size=%d)", proxy, len(self._proxies))

    def remove(self, proxy: str) -> None:
        """Remove a proxy URL from the pool.

        If the proxy is not present, this is a no-op.

        Args:
            proxy: Proxy URL to remove.
        """
        if proxy in self._proxies:
            self._proxies.remove(proxy)
            self._failure_counts.pop(proxy, None)
            self._rebuild_cycle()
            logger.debug(
                "ProxyPool: removed proxy %r (pool size=%d)", proxy, len(self._proxies)
            )

    def size(self) -> int:
        """Return the number of proxies currently in the pool."""
        return len(self._proxies)

    def all_proxies(self) -> list[str]:
        """Return a copy of the current proxy list (for inspection/testing)."""
        return list(self._proxies)

    def failure_count(self, proxy: str) -> int:
        """Return the current failure count for ``proxy``."""
        return self._failure_counts.get(proxy, 0)

    def __repr__(self) -> str:
        return f"InMemoryProxyPool(size={self.size()}, max_failures={self._max_failures})"
