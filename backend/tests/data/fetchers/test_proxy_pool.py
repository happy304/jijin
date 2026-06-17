"""Unit tests for app.data.fetchers.proxy_pool."""

from __future__ import annotations

import pytest

from app.data.fetchers.proxy_pool import InMemoryProxyPool, ProxyPool


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProxyPoolProtocol:
    def test_in_memory_pool_satisfies_protocol(self) -> None:
        pool = InMemoryProxyPool()
        assert isinstance(pool, ProxyPool)


# ---------------------------------------------------------------------------
# InMemoryProxyPool — construction
# ---------------------------------------------------------------------------


class TestInMemoryProxyPoolInit:
    def test_empty_pool_by_default(self) -> None:
        pool = InMemoryProxyPool()
        assert pool.size() == 0

    def test_initial_proxies(self) -> None:
        pool = InMemoryProxyPool(proxies=["http://p1:8080", "http://p2:8080"])
        assert pool.size() == 2

    def test_invalid_max_failures_raises(self) -> None:
        with pytest.raises(ValueError, match="max_failures"):
            InMemoryProxyPool(max_failures=0)

    def test_default_max_failures(self) -> None:
        pool = InMemoryProxyPool()
        assert pool._max_failures == 3


# ---------------------------------------------------------------------------
# InMemoryProxyPool — next()
# ---------------------------------------------------------------------------


class TestInMemoryProxyPoolNext:
    def test_returns_none_when_empty(self) -> None:
        pool = InMemoryProxyPool()
        assert pool.next() is None

    def test_returns_proxy_when_one_available(self) -> None:
        pool = InMemoryProxyPool(proxies=["http://proxy:8080"])
        assert pool.next() == "http://proxy:8080"

    def test_round_robin_rotation(self) -> None:
        proxies = ["http://p1:8080", "http://p2:8080", "http://p3:8080"]
        pool = InMemoryProxyPool(proxies=proxies)
        seen = [pool.next() for _ in range(6)]
        # Should cycle through all proxies
        assert set(seen) == set(proxies)

    def test_single_proxy_always_returned(self) -> None:
        pool = InMemoryProxyPool(proxies=["http://only:8080"])
        for _ in range(5):
            assert pool.next() == "http://only:8080"

    def test_returns_none_after_all_removed(self) -> None:
        pool = InMemoryProxyPool(proxies=["http://p1:8080"])
        pool.remove("http://p1:8080")
        assert pool.next() is None


# ---------------------------------------------------------------------------
# InMemoryProxyPool — add() / remove()
# ---------------------------------------------------------------------------


class TestInMemoryProxyPoolAddRemove:
    def test_add_proxy(self) -> None:
        pool = InMemoryProxyPool()
        pool.add("http://new:8080")
        assert pool.size() == 1
        assert "http://new:8080" in pool.all_proxies()

    def test_add_duplicate_is_noop(self) -> None:
        pool = InMemoryProxyPool(proxies=["http://p1:8080"])
        pool.add("http://p1:8080")
        assert pool.size() == 1

    def test_remove_existing_proxy(self) -> None:
        pool = InMemoryProxyPool(proxies=["http://p1:8080", "http://p2:8080"])
        pool.remove("http://p1:8080")
        assert pool.size() == 1
        assert "http://p1:8080" not in pool.all_proxies()

    def test_remove_nonexistent_is_noop(self) -> None:
        pool = InMemoryProxyPool(proxies=["http://p1:8080"])
        pool.remove("http://nonexistent:8080")  # should not raise
        assert pool.size() == 1

    def test_add_then_remove_leaves_empty(self) -> None:
        pool = InMemoryProxyPool()
        pool.add("http://p1:8080")
        pool.remove("http://p1:8080")
        assert pool.size() == 0
        assert pool.next() is None


# ---------------------------------------------------------------------------
# InMemoryProxyPool — report_success() / report_failure()
# ---------------------------------------------------------------------------


class TestInMemoryProxyPoolReporting:
    def test_report_success_resets_failure_count(self) -> None:
        pool = InMemoryProxyPool(proxies=["http://p1:8080"], max_failures=5)
        pool.report_failure("http://p1:8080")
        pool.report_failure("http://p1:8080")
        assert pool.failure_count("http://p1:8080") == 2
        pool.report_success("http://p1:8080")
        assert pool.failure_count("http://p1:8080") == 0

    def test_report_failure_increments_count(self) -> None:
        pool = InMemoryProxyPool(proxies=["http://p1:8080"], max_failures=5)
        pool.report_failure("http://p1:8080")
        assert pool.failure_count("http://p1:8080") == 1

    def test_proxy_removed_after_max_failures(self) -> None:
        pool = InMemoryProxyPool(proxies=["http://p1:8080"], max_failures=3)
        for _ in range(3):
            pool.report_failure("http://p1:8080")
        assert pool.size() == 0
        assert "http://p1:8080" not in pool.all_proxies()

    def test_proxy_not_removed_before_max_failures(self) -> None:
        pool = InMemoryProxyPool(proxies=["http://p1:8080"], max_failures=3)
        pool.report_failure("http://p1:8080")
        pool.report_failure("http://p1:8080")
        assert pool.size() == 1  # still present

    def test_report_failure_for_nonexistent_proxy_is_noop(self) -> None:
        pool = InMemoryProxyPool()
        pool.report_failure("http://ghost:8080")  # should not raise
        assert pool.size() == 0

    def test_report_success_for_nonexistent_proxy_is_noop(self) -> None:
        pool = InMemoryProxyPool()
        pool.report_success("http://ghost:8080")  # should not raise

    def test_failure_count_zero_for_unknown_proxy(self) -> None:
        pool = InMemoryProxyPool()
        assert pool.failure_count("http://unknown:8080") == 0

    def test_other_proxies_unaffected_by_failure(self) -> None:
        pool = InMemoryProxyPool(
            proxies=["http://p1:8080", "http://p2:8080"], max_failures=2
        )
        pool.report_failure("http://p1:8080")
        pool.report_failure("http://p1:8080")
        # p1 removed, p2 still present
        assert pool.size() == 1
        assert "http://p2:8080" in pool.all_proxies()


# ---------------------------------------------------------------------------
# InMemoryProxyPool — all_proxies() / repr
# ---------------------------------------------------------------------------


class TestInMemoryProxyPoolMisc:
    def test_all_proxies_returns_copy(self) -> None:
        pool = InMemoryProxyPool(proxies=["http://p1:8080"])
        proxies = pool.all_proxies()
        proxies.append("http://injected:8080")
        assert pool.size() == 1  # original unaffected

    def test_repr_contains_size(self) -> None:
        pool = InMemoryProxyPool(proxies=["http://p1:8080", "http://p2:8080"])
        r = repr(pool)
        assert "size=2" in r

    def test_repr_contains_max_failures(self) -> None:
        pool = InMemoryProxyPool(max_failures=5)
        r = repr(pool)
        assert "max_failures=5" in r
