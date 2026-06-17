"""FastAPI ↔ Prometheus instrumentation wiring.

``prometheus-fastapi-instrumentator`` takes care of the boilerplate
(HTTP request count, latency histograms, in-progress gauges); we just
need to apply our defaults and expose the `/metrics` endpoint. This
module stays thin on purpose — domain metrics live in
:mod:`app.observability.metrics`.

Gating
------
Instrumentation is controlled by two settings (see
:mod:`app.core.config`):

* ``PROMETHEUS_ENABLED`` — when false, ``setup_metrics()`` is a no-op so
  the ``/metrics`` endpoint is not exposed. This lets production
  deployments behind a sidecar scraper opt out without pulling the
  dependency.
* ``PROMETHEUS_PATH`` — where to mount the scrape endpoint (defaults to
  ``/metrics``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from prometheus_fastapi_instrumentator import Instrumentator

from app.core.config import Settings
from app.core.logging import get_logger

if TYPE_CHECKING:
    from fastapi import FastAPI


log = get_logger("app.observability.instrumentation")


def _build_instrumentator() -> Instrumentator:
    """Construct an ``Instrumentator`` with platform-wide defaults.

    We exclude the health probe and the metrics endpoint itself from
    HTTP instrumentation so they don't inflate scrape counts (the
    scraper hits `/metrics` every 15 seconds; self-instrumenting the
    endpoint is noise).

    ``should_instrument_requests_inprogress`` is intentionally left at
    its ``False`` default: the underlying library creates the
    in-progress :class:`~prometheus_client.Gauge` directly inside the
    middleware constructor, without the duplicate-registration guard
    that protects the default counters/histograms. That makes it
    impossible to build more than one FastAPI app per process (a
    pattern the test suite relies on). Percent-in-progress can still be
    derived at query time from ``rate(http_requests_total[...])`` if
    needed.
    """
    return Instrumentator(
        should_group_status_codes=True,
        # Untemplated paths (404s for bogus URLs) are grouped under a
        # single `unhandled` label so we don't leak per-path cardinality
        # from crawler traffic.
        should_ignore_untemplated=False,
        should_group_untemplated=True,
        should_instrument_requests_inprogress=False,
        excluded_handlers=["/health", "/metrics"],
    )


def setup_metrics(app: "FastAPI", settings: Settings) -> Instrumentator | None:
    """Install Prometheus instrumentation on ``app`` if enabled.

    Returns the configured ``Instrumentator`` (useful for tests and for
    registering extra metric emitters later on), or ``None`` when
    ``PROMETHEUS_ENABLED`` is false.

    The function is idempotent: calling it twice on the same app
    attaches the middleware twice, which is rarely what anyone wants,
    so we guard on a sentinel set on ``app.state``.
    """
    if not settings.prometheus_enabled:
        log.info("metrics.disabled", reason="PROMETHEUS_ENABLED=false")
        return None

    if getattr(app.state, "prometheus_installed", False):
        log.debug("metrics.already_installed")
        # Return whatever was stored so callers can still introspect.
        return getattr(app.state, "instrumentator", None)

    instrumentator = _build_instrumentator()
    instrumentator.instrument(app)
    instrumentator.expose(
        app,
        endpoint=settings.prometheus_path,
        include_in_schema=False,
        tags=["meta"],
    )

    app.state.instrumentator = instrumentator
    app.state.prometheus_installed = True

    log.info(
        "metrics.installed",
        path=settings.prometheus_path,
    )
    return instrumentator


__all__ = ["setup_metrics"]
