"""Network infrastructure for data fetching.

Provides:
- AsyncHttpClient: httpx-based async client with UA pool and default headers
- RateLimiter: token-bucket rate limiter, isolated per provider name
- retry_on_network_error: tenacity decorator with exponential backoff (max 3 retries)
- CircuitBreaker: failure counter + OPEN/HALF_OPEN/CLOSED state machine
- ProxyPool: optional proxy pool interface with in-memory implementation
"""

from app.data.fetchers.circuit_breaker import CircuitBreaker, CircuitState
from app.data.fetchers.http_client import AsyncHttpClient, build_default_headers
from app.data.fetchers.proxy_pool import InMemoryProxyPool, ProxyPool
from app.data.fetchers.rate_limiter import RateLimiter
from app.data.fetchers.retry import retry_on_network_error

__all__ = [
    "AsyncHttpClient",
    "build_default_headers",
    "RateLimiter",
    "retry_on_network_error",
    "CircuitBreaker",
    "CircuitState",
    "ProxyPool",
    "InMemoryProxyPool",
]
