"""Circuit breaker with CLOSED / OPEN / HALF_OPEN state machine.

Design notes
------------
State transitions:

    CLOSED ──(consecutive_failures >= threshold)──▶ OPEN
    OPEN   ──(recovery_timeout elapsed)───────────▶ HALF_OPEN
    HALF_OPEN ──(success)──────────────────────────▶ CLOSED
    HALF_OPEN ──(failure)──────────────────────────▶ OPEN

* ``failure_threshold``: number of consecutive failures that trip the
  breaker (default 5, per requirement 1.8).
* ``recovery_timeout``: seconds the breaker stays OPEN before allowing
  a probe request (default 60 s, per requirement 1.8).
* ``half_open_max_calls``: how many probe calls are allowed in HALF_OPEN
  state before a decision is made (default 1).
* The breaker is per-provider: instantiate one ``CircuitBreaker`` per
  provider name, or use ``CircuitBreakerRegistry`` to manage them all.

Usage::

    breaker = CircuitBreaker(name="eastmoney")

    if breaker.is_open():
        raise ProviderUnavailableError("Circuit is OPEN")

    try:
        result = await fetch_data()
        breaker.record_success()
    except Exception as exc:
        breaker.record_failure()
        raise
"""

from __future__ import annotations

import logging
import time
from enum import Enum

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    """Possible states of a circuit breaker."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    """Single-provider circuit breaker.

    Args:
        name: Human-readable name (used in log messages).
        failure_threshold: Consecutive failures needed to open the circuit.
        recovery_timeout: Seconds to wait in OPEN state before probing.
        half_open_max_calls: Max probe calls allowed in HALF_OPEN state.
    """

    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_max_calls: int = 1,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError(f"failure_threshold must be >= 1, got {failure_threshold}")
        if recovery_timeout <= 0:
            raise ValueError(f"recovery_timeout must be > 0, got {recovery_timeout}")

        self.name = name
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._half_open_max_calls = half_open_max_calls

        self._state: CircuitState = CircuitState.CLOSED
        self._consecutive_failures: int = 0
        self._opened_at: float | None = None
        self._half_open_calls: int = 0

    # ------------------------------------------------------------------
    # State queries
    # ------------------------------------------------------------------

    @property
    def state(self) -> CircuitState:
        """Current circuit state (may transition OPEN → HALF_OPEN on read)."""
        self._maybe_transition_to_half_open()
        return self._state

    def is_open(self) -> bool:
        """Return True if the circuit is OPEN (requests should be blocked)."""
        return self.state == CircuitState.OPEN

    def is_closed(self) -> bool:
        """Return True if the circuit is CLOSED (normal operation)."""
        return self.state == CircuitState.CLOSED

    def is_half_open(self) -> bool:
        """Return True if the circuit is HALF_OPEN (probe allowed)."""
        return self.state == CircuitState.HALF_OPEN

    def allow_request(self) -> bool:
        """Return True if a request should be allowed through.

        * CLOSED: always True
        * OPEN: always False
        * HALF_OPEN: True only if fewer than ``half_open_max_calls``
          probe calls have been issued since entering HALF_OPEN.
        """
        state = self.state  # triggers OPEN → HALF_OPEN transition if due
        if state == CircuitState.CLOSED:
            return True
        if state == CircuitState.OPEN:
            return False
        # HALF_OPEN
        if self._half_open_calls < self._half_open_max_calls:
            self._half_open_calls += 1
            return True
        return False

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def record_success(self) -> None:
        """Record a successful call.

        * CLOSED: resets consecutive failure counter.
        * HALF_OPEN: closes the circuit (probe succeeded).
        * OPEN: no-op (should not happen in normal usage).
        """
        prev_state = self._state
        if self._state == CircuitState.HALF_OPEN:
            self._transition_to_closed()
        elif self._state == CircuitState.CLOSED:
            self._consecutive_failures = 0
        if prev_state != self._state:
            logger.info(
                "CircuitBreaker[%s]: %s → %s (success)",
                self.name,
                prev_state.value,
                self._state.value,
            )

    def record_failure(self) -> None:
        """Record a failed call.

        * CLOSED: increments counter; trips to OPEN if threshold reached.
        * HALF_OPEN: probe failed, re-opens the circuit.
        * OPEN: no-op.
        """
        prev_state = self._state
        if self._state == CircuitState.OPEN:
            return
        self._consecutive_failures += 1
        if self._state == CircuitState.HALF_OPEN:
            self._transition_to_open()
        elif self._consecutive_failures >= self._failure_threshold:
            self._transition_to_open()
        if prev_state != self._state:
            logger.warning(
                "CircuitBreaker[%s]: %s → %s (failures=%d)",
                self.name,
                prev_state.value,
                self._state.value,
                self._consecutive_failures,
            )

    def reset(self) -> None:
        """Manually reset the circuit breaker to CLOSED state."""
        self._transition_to_closed()
        logger.info("CircuitBreaker[%s]: manually reset to CLOSED", self.name)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _maybe_transition_to_half_open(self) -> None:
        """Transition OPEN → HALF_OPEN if the recovery timeout has elapsed."""
        if self._state == CircuitState.OPEN and self._opened_at is not None:
            elapsed = time.monotonic() - self._opened_at
            if elapsed >= self._recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                self._half_open_calls = 0
                logger.info(
                    "CircuitBreaker[%s]: OPEN → HALF_OPEN (elapsed=%.1fs)",
                    self.name,
                    elapsed,
                )

    def _transition_to_open(self) -> None:
        self._state = CircuitState.OPEN
        self._opened_at = time.monotonic()
        self._half_open_calls = 0

    def _transition_to_closed(self) -> None:
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at = None
        self._half_open_calls = 0

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    @property
    def consecutive_failures(self) -> int:
        """Current consecutive failure count."""
        return self._consecutive_failures

    @property
    def failure_threshold(self) -> int:
        """Configured failure threshold."""
        return self._failure_threshold

    @property
    def recovery_timeout(self) -> float:
        """Configured recovery timeout in seconds."""
        return self._recovery_timeout

    def __repr__(self) -> str:
        return (
            f"CircuitBreaker(name={self.name!r}, state={self._state.value}, "
            f"failures={self._consecutive_failures}/{self._failure_threshold})"
        )


class CircuitBreakerRegistry:
    """Registry that manages one ``CircuitBreaker`` per provider name.

    Usage::

        registry = CircuitBreakerRegistry()
        registry.configure("eastmoney", failure_threshold=5, recovery_timeout=60)

        if registry.is_open("eastmoney"):
            skip_provider()

        try:
            result = await fetch()
            registry.record_success("eastmoney")
        except Exception:
            registry.record_failure("eastmoney")
            raise
    """

    def __init__(
        self,
        *,
        default_failure_threshold: int = 5,
        default_recovery_timeout: float = 60.0,
    ) -> None:
        self._default_failure_threshold = default_failure_threshold
        self._default_recovery_timeout = default_recovery_timeout
        self._breakers: dict[str, CircuitBreaker] = {}

    def configure(
        self,
        provider: str,
        *,
        failure_threshold: int | None = None,
        recovery_timeout: float | None = None,
    ) -> None:
        """Configure (or reconfigure) the breaker for a provider."""
        self._breakers[provider] = CircuitBreaker(
            name=provider,
            failure_threshold=failure_threshold or self._default_failure_threshold,
            recovery_timeout=recovery_timeout or self._default_recovery_timeout,
        )

    def _get(self, provider: str) -> CircuitBreaker:
        if provider not in self._breakers:
            self._breakers[provider] = CircuitBreaker(
                name=provider,
                failure_threshold=self._default_failure_threshold,
                recovery_timeout=self._default_recovery_timeout,
            )
        return self._breakers[provider]

    def is_open(self, provider: str) -> bool:
        """Return True if the circuit for ``provider`` is OPEN."""
        return self._get(provider).is_open()

    def allow_request(self, provider: str) -> bool:
        """Return True if a request to ``provider`` should be allowed."""
        return self._get(provider).allow_request()

    def record_success(self, provider: str) -> None:
        """Record a successful call to ``provider``."""
        self._get(provider).record_success()

    def record_failure(self, provider: str) -> None:
        """Record a failed call to ``provider``."""
        self._get(provider).record_failure()

    def get_state(self, provider: str) -> CircuitState:
        """Return the current state of the circuit for ``provider``."""
        return self._get(provider).state

    def reset(self, provider: str) -> None:
        """Manually reset the circuit for ``provider`` to CLOSED."""
        self._get(provider).reset()

    def all_states(self) -> dict[str, CircuitState]:
        """Return a snapshot of all provider states."""
        return {name: cb.state for name, cb in self._breakers.items()}
