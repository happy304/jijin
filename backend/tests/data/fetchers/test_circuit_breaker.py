"""Unit tests for app.data.fetchers.circuit_breaker."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from app.data.fetchers.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerRegistry,
    CircuitState,
)


# ---------------------------------------------------------------------------
# CircuitBreaker — construction
# ---------------------------------------------------------------------------


class TestCircuitBreakerInit:
    def test_starts_closed(self) -> None:
        cb = CircuitBreaker("test")
        assert cb.state == CircuitState.CLOSED

    def test_default_failure_threshold(self) -> None:
        cb = CircuitBreaker("test")
        assert cb.failure_threshold == 5

    def test_default_recovery_timeout(self) -> None:
        cb = CircuitBreaker("test")
        assert cb.recovery_timeout == 60.0

    def test_custom_failure_threshold(self) -> None:
        cb = CircuitBreaker("test", failure_threshold=3)
        assert cb.failure_threshold == 3

    def test_custom_recovery_timeout(self) -> None:
        cb = CircuitBreaker("test", recovery_timeout=30.0)
        assert cb.recovery_timeout == 30.0

    def test_invalid_failure_threshold_raises(self) -> None:
        with pytest.raises(ValueError, match="failure_threshold"):
            CircuitBreaker("test", failure_threshold=0)

    def test_invalid_recovery_timeout_raises(self) -> None:
        with pytest.raises(ValueError, match="recovery_timeout"):
            CircuitBreaker("test", recovery_timeout=0)


# ---------------------------------------------------------------------------
# CircuitBreaker — CLOSED state
# ---------------------------------------------------------------------------


class TestCircuitBreakerClosed:
    def test_is_closed_initially(self) -> None:
        cb = CircuitBreaker("test")
        assert cb.is_closed()
        assert not cb.is_open()
        assert not cb.is_half_open()

    def test_allow_request_when_closed(self) -> None:
        cb = CircuitBreaker("test")
        assert cb.allow_request() is True

    def test_success_resets_failure_count(self) -> None:
        cb = CircuitBreaker("test", failure_threshold=5)
        for _ in range(3):
            cb.record_failure()
        assert cb.consecutive_failures == 3
        cb.record_success()
        assert cb.consecutive_failures == 0

    def test_failure_increments_counter(self) -> None:
        cb = CircuitBreaker("test", failure_threshold=5)
        cb.record_failure()
        assert cb.consecutive_failures == 1

    def test_stays_closed_below_threshold(self) -> None:
        cb = CircuitBreaker("test", failure_threshold=5)
        for _ in range(4):
            cb.record_failure()
        assert cb.is_closed()

    def test_trips_to_open_at_threshold(self) -> None:
        cb = CircuitBreaker("test", failure_threshold=5)
        for _ in range(5):
            cb.record_failure()
        assert cb.is_open()

    def test_trips_to_open_exactly_at_threshold(self) -> None:
        cb = CircuitBreaker("test", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.is_closed()
        cb.record_failure()  # 3rd failure
        assert cb.is_open()


# ---------------------------------------------------------------------------
# CircuitBreaker — OPEN state
# ---------------------------------------------------------------------------


class TestCircuitBreakerOpen:
    def _open_breaker(self, threshold: int = 5) -> CircuitBreaker:
        cb = CircuitBreaker("test", failure_threshold=threshold, recovery_timeout=60.0)
        for _ in range(threshold):
            cb.record_failure()
        assert cb.is_open()
        return cb

    def test_is_open_after_threshold(self) -> None:
        cb = self._open_breaker()
        assert cb.is_open()
        assert not cb.is_closed()

    def test_allow_request_returns_false_when_open(self) -> None:
        cb = self._open_breaker()
        assert cb.allow_request() is False

    def test_record_failure_in_open_state_is_noop(self) -> None:
        cb = self._open_breaker()
        failures_before = cb.consecutive_failures
        cb.record_failure()
        assert cb.consecutive_failures == failures_before

    def test_transitions_to_half_open_after_timeout(self) -> None:
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.05)
        cb.record_failure()
        assert cb.is_open()
        time.sleep(0.1)
        assert cb.state == CircuitState.HALF_OPEN

    def test_does_not_transition_before_timeout(self) -> None:
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=60.0)
        cb.record_failure()
        assert cb.is_open()
        # Immediately check — should still be OPEN
        assert cb.state == CircuitState.OPEN


# ---------------------------------------------------------------------------
# CircuitBreaker — HALF_OPEN state
# ---------------------------------------------------------------------------


class TestCircuitBreakerHalfOpen:
    def _half_open_breaker(self) -> CircuitBreaker:
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.05)
        cb.record_failure()
        time.sleep(0.1)
        assert cb.state == CircuitState.HALF_OPEN
        return cb

    def test_allow_request_in_half_open(self) -> None:
        cb = self._half_open_breaker()
        assert cb.allow_request() is True

    def test_success_in_half_open_closes_circuit(self) -> None:
        cb = self._half_open_breaker()
        cb.allow_request()  # consume the probe slot
        cb.record_success()
        assert cb.is_closed()

    def test_failure_in_half_open_reopens_circuit(self) -> None:
        cb = self._half_open_breaker()
        cb.record_failure()
        assert cb.is_open()

    def test_max_probe_calls_respected(self) -> None:
        cb = CircuitBreaker(
            "test",
            failure_threshold=1,
            recovery_timeout=0.05,
            half_open_max_calls=1,
        )
        cb.record_failure()
        time.sleep(0.1)
        # First probe allowed
        assert cb.allow_request() is True
        # Second probe blocked
        assert cb.allow_request() is False

    def test_closed_after_successful_probe_resets_failures(self) -> None:
        cb = self._half_open_breaker()
        cb.record_success()
        assert cb.consecutive_failures == 0


# ---------------------------------------------------------------------------
# CircuitBreaker — manual reset
# ---------------------------------------------------------------------------


class TestCircuitBreakerReset:
    def test_reset_from_open_to_closed(self) -> None:
        cb = CircuitBreaker("test", failure_threshold=1)
        cb.record_failure()
        assert cb.is_open()
        cb.reset()
        assert cb.is_closed()

    def test_reset_clears_failure_count(self) -> None:
        cb = CircuitBreaker("test", failure_threshold=5)
        for _ in range(3):
            cb.record_failure()
        cb.reset()
        assert cb.consecutive_failures == 0


# ---------------------------------------------------------------------------
# CircuitBreaker — repr
# ---------------------------------------------------------------------------


class TestCircuitBreakerRepr:
    def test_repr_contains_name_and_state(self) -> None:
        cb = CircuitBreaker("eastmoney")
        r = repr(cb)
        assert "eastmoney" in r
        assert "CLOSED" in r


# ---------------------------------------------------------------------------
# CircuitBreakerRegistry
# ---------------------------------------------------------------------------


class TestCircuitBreakerRegistry:
    def test_default_state_is_closed(self) -> None:
        registry = CircuitBreakerRegistry()
        assert registry.get_state("new_provider") == CircuitState.CLOSED

    def test_is_open_false_initially(self) -> None:
        registry = CircuitBreakerRegistry()
        assert registry.is_open("provider") is False

    def test_allow_request_true_initially(self) -> None:
        registry = CircuitBreakerRegistry()
        assert registry.allow_request("provider") is True

    def test_record_failures_opens_circuit(self) -> None:
        registry = CircuitBreakerRegistry(default_failure_threshold=3)
        for _ in range(3):
            registry.record_failure("prov")
        assert registry.is_open("prov")

    def test_record_success_resets_failures(self) -> None:
        registry = CircuitBreakerRegistry(default_failure_threshold=5)
        for _ in range(3):
            registry.record_failure("prov")
        registry.record_success("prov")
        assert registry.get_state("prov") == CircuitState.CLOSED

    def test_configure_overrides_defaults(self) -> None:
        registry = CircuitBreakerRegistry(default_failure_threshold=5)
        registry.configure("prov", failure_threshold=2)
        registry.record_failure("prov")
        assert registry.get_state("prov") == CircuitState.CLOSED
        registry.record_failure("prov")
        assert registry.is_open("prov")

    def test_providers_are_isolated(self) -> None:
        registry = CircuitBreakerRegistry(default_failure_threshold=2)
        registry.record_failure("prov_a")
        registry.record_failure("prov_a")
        assert registry.is_open("prov_a")
        assert not registry.is_open("prov_b")

    def test_reset_closes_open_circuit(self) -> None:
        registry = CircuitBreakerRegistry(default_failure_threshold=1)
        registry.record_failure("prov")
        assert registry.is_open("prov")
        registry.reset("prov")
        assert not registry.is_open("prov")

    def test_all_states_returns_snapshot(self) -> None:
        registry = CircuitBreakerRegistry(default_failure_threshold=1)
        registry.record_failure("prov_a")
        registry.record_success("prov_b")  # creates entry
        states = registry.all_states()
        assert "prov_a" in states
        assert states["prov_a"] == CircuitState.OPEN

    def test_5_failures_open_circuit_per_requirement(self) -> None:
        """Requirement 1.8: 5 consecutive failures should trip the breaker."""
        registry = CircuitBreakerRegistry(default_failure_threshold=5)
        for i in range(4):
            registry.record_failure("eastmoney")
            assert not registry.is_open("eastmoney"), f"Should not open after {i+1} failures"
        registry.record_failure("eastmoney")
        assert registry.is_open("eastmoney"), "Should open after 5 failures"
