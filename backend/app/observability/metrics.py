"""Prometheus metric definitions for the Fund Quant Platform.

This module declares the *domain* metrics — business-level counters,
histograms and gauges that surface through the ``/metrics`` endpoint.
Generic HTTP metrics (``http_requests_total``, request duration, …) are
supplied by :mod:`prometheus_fastapi_instrumentator` in
:mod:`app.observability.instrumentation`; those are deliberately kept
out of this file.

Design notes
------------
* Every metric is registered on the default
  :data:`prometheus_client.REGISTRY` at import time. Importing this
  module twice would normally raise ``ValueError: Duplicated
  timeseries``; the helper functions below catch that case so tests
  can reload the module freely (e.g. when clearing the registry
  between parametrised cases).
* Metric names follow the `Prometheus naming conventions
  <https://prometheus.io/docs/practices/naming/>`_ — lower-case with
  underscores, ``_total`` suffix for counters, unit suffix (``_seconds``,
  ``_ratio``, ``_usd``, ``_bytes``) on histograms/gauges.
* Helper functions are provided for task code to record metrics
  without needing to know the label structure.

References
----------
* Requirement 8.5 (Prometheus instrumentation)
* Requirement 8.6 (Grafana dashboard consumes these series)
* Design §6 (observability stack)
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Final, Generator

from prometheus_client import REGISTRY, Counter, Gauge, Histogram
from prometheus_client.metrics import MetricWrapperBase

# ---------------------------------------------------------------------
# Histogram buckets
# ---------------------------------------------------------------------
# Data-fetch latency: ingest requests can stretch into tens of seconds
# for large pingzhongdata payloads or recovering-from-timeout retries.
_INGEST_LATENCY_BUCKETS: Final = (
    0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0,
)

# Backtest duration: event-driven engine runs from seconds (small
# universe, short window) to tens of minutes (100+ funds, 10y window).
_BACKTEST_DURATION_BUCKETS: Final = (
    0.5, 1.0, 5.0, 15.0, 30.0, 60.0, 300.0, 900.0, 1800.0, 3600.0,
)


def _get_or_create(
    factory: type[MetricWrapperBase],
    name: str,
    documentation: str,
    labelnames: tuple[str, ...] = (),
    **kwargs: object,
) -> MetricWrapperBase:
    """Return an existing metric if already registered, else create one.

    ``prometheus_client`` raises ``ValueError`` on duplicate metric
    names, which breaks ``importlib.reload`` patterns in tests. We
    probe the default registry for an existing collector with the
    same base name and return it when found — matching the behaviour
    the test suite expects.
    """
    # Counters register an additional <name>_total sample; the
    # registered collector name drops that suffix, so we normalise
    # before looking it up.
    lookup = name.removesuffix("_total") if factory is Counter else name
    existing = REGISTRY._names_to_collectors.get(lookup)  # noqa: SLF001
    if existing is not None:
        return existing  # type: ignore[return-value]
    return factory(name, documentation, labelnames=labelnames, **kwargs)


# =====================================================================
# Data ingestion (requirement 8.5, design §1)
# =====================================================================

INGEST_REQUESTS_TOTAL: Final[Counter] = _get_or_create(  # type: ignore[assignment]
    Counter,
    "ingest_requests_total",
    "Count of upstream data-provider requests, labelled by provider, "
    "endpoint and outcome.",
    labelnames=("provider", "endpoint", "status"),
)

INGEST_LATENCY_SECONDS: Final[Histogram] = _get_or_create(  # type: ignore[assignment]
    Histogram,
    "ingest_latency_seconds",
    "Latency of upstream data-provider requests in seconds.",
    labelnames=("provider", "endpoint"),
    buckets=_INGEST_LATENCY_BUCKETS,
)

DATA_COMPLETENESS_RATIO: Final[Gauge] = _get_or_create(  # type: ignore[assignment]
    Gauge,
    "data_completeness_ratio",
    "Fraction of expected fund-day observations present in storage. "
    "Range [0, 1].",
    labelnames=("fund_code",),
)


# =====================================================================
# Backtest engine (requirement 8.5, design §4)
# =====================================================================

BACKTEST_RUNS_TOTAL: Final[Counter] = _get_or_create(  # type: ignore[assignment]
    Counter,
    "backtest_runs_total",
    "Count of backtest runs by strategy type and terminal status.",
    labelnames=("strategy_type", "status"),
)

BACKTEST_DURATION_SECONDS: Final[Histogram] = _get_or_create(  # type: ignore[assignment]
    Histogram,
    "backtest_duration_seconds",
    "Wall-clock duration of a backtest run in seconds.",
    labelnames=("strategy_type",),
    buckets=_BACKTEST_DURATION_BUCKETS,
)


# =====================================================================
# LLM layer (requirement 11.5/11.6, design §9)
# =====================================================================

LLM_CALLS_TOTAL: Final[Counter] = _get_or_create(  # type: ignore[assignment]
    Counter,
    "llm_calls_total",
    "Count of LLM API invocations, labelled by provider, model, "
    "use-case and outcome.",
    labelnames=("provider", "model", "use_case", "status"),
)

LLM_COST_USD_TOTAL: Final[Counter] = _get_or_create(  # type: ignore[assignment]
    Counter,
    "llm_cost_usd_total",
    "Cumulative estimated LLM API cost in USD.",
    labelnames=("provider", "model"),
)

LLM_TOKENS_TOTAL: Final[Counter] = _get_or_create(  # type: ignore[assignment]
    Counter,
    "llm_tokens_total",
    "Cumulative LLM token usage, split by direction "
    "(prompt vs completion).",
    labelnames=("provider", "model", "direction"),
)


# =====================================================================
# Task queues (requirement 8.5, design §6)
# =====================================================================

TASK_QUEUE_DEPTH: Final[Gauge] = _get_or_create(  # type: ignore[assignment]
    Gauge,
    "task_queue_depth",
    "Current depth (pending message count) of a Celery queue.",
    labelnames=("queue_name",),
)


# =====================================================================
# Aggregated view — stable mapping for tests and dashboard generators.
# =====================================================================

ALL_METRICS: Final[dict[str, MetricWrapperBase]] = {
    "ingest_requests_total": INGEST_REQUESTS_TOTAL,
    "ingest_latency_seconds": INGEST_LATENCY_SECONDS,
    "data_completeness_ratio": DATA_COMPLETENESS_RATIO,
    "backtest_runs_total": BACKTEST_RUNS_TOTAL,
    "backtest_duration_seconds": BACKTEST_DURATION_SECONDS,
    "llm_calls_total": LLM_CALLS_TOTAL,
    "llm_cost_usd_total": LLM_COST_USD_TOTAL,
    "llm_tokens_total": LLM_TOKENS_TOTAL,
    "task_queue_depth": TASK_QUEUE_DEPTH,
}


# =====================================================================
# Helper functions — convenient API for task code to record metrics
# =====================================================================


def record_ingest_request(
    provider: str,
    endpoint: str,
    status: str,
    latency_seconds: float | None = None,
) -> None:
    """Record a data-ingestion request outcome.

    Parameters
    ----------
    provider : str
        Data provider name (e.g. "eastmoney", "akshare").
    endpoint : str
        Logical endpoint name (e.g. "nav_history", "fund_meta").
    status : str
        Outcome — typically "success" or "error".
    latency_seconds : float, optional
        Request duration. If provided, also records the histogram.
    """
    INGEST_REQUESTS_TOTAL.labels(
        provider=provider, endpoint=endpoint, status=status
    ).inc()
    if latency_seconds is not None:
        INGEST_LATENCY_SECONDS.labels(
            provider=provider, endpoint=endpoint
        ).observe(latency_seconds)


@contextmanager
def track_ingest_latency(
    provider: str, endpoint: str
) -> Generator[None, None, None]:
    """Context manager that measures and records ingest latency.

    Usage::

        with track_ingest_latency("eastmoney", "nav_history"):
            data = await provider.fetch_nav_history(...)
    """
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - start
        INGEST_LATENCY_SECONDS.labels(
            provider=provider, endpoint=endpoint
        ).observe(elapsed)


def set_data_completeness(fund_code: str, ratio: float) -> None:
    """Set the data completeness ratio for a fund.

    Parameters
    ----------
    fund_code : str
        Fund code (e.g. "000001").
    ratio : float
        Completeness ratio in [0, 1].
    """
    DATA_COMPLETENESS_RATIO.labels(fund_code=fund_code).set(ratio)


def record_backtest_run(
    strategy_type: str,
    status: str,
    duration_seconds: float | None = None,
) -> None:
    """Record a completed backtest run.

    Parameters
    ----------
    strategy_type : str
        Strategy type (e.g. "dca", "momentum", "risk_parity").
    status : str
        Terminal status — "success", "failed", or "cancelled".
    duration_seconds : float, optional
        Wall-clock duration. If provided, also records the histogram.
    """
    BACKTEST_RUNS_TOTAL.labels(
        strategy_type=strategy_type, status=status
    ).inc()
    if duration_seconds is not None:
        BACKTEST_DURATION_SECONDS.labels(
            strategy_type=strategy_type
        ).observe(duration_seconds)


@contextmanager
def track_backtest_duration(
    strategy_type: str,
) -> Generator[None, None, None]:
    """Context manager that measures and records backtest duration.

    Usage::

        with track_backtest_duration("momentum"):
            result = engine.run(...)
    """
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - start
        BACKTEST_DURATION_SECONDS.labels(
            strategy_type=strategy_type
        ).observe(elapsed)


def record_llm_call(
    provider: str,
    model: str,
    use_case: str,
    status: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cost_usd: float = 0.0,
) -> None:
    """Record an LLM API call with all associated metrics.

    Parameters
    ----------
    provider : str
        LLM provider name (e.g. "openai", "deepseek", "anthropic").
    model : str
        Model identifier (e.g. "gpt-4o", "deepseek-chat").
    use_case : str
        Use case identifier (e.g. "announcement_parse", "nl_query").
    status : str
        Outcome — "success" or "error".
    prompt_tokens : int
        Number of prompt tokens consumed.
    completion_tokens : int
        Number of completion tokens generated.
    cost_usd : float
        Estimated cost in USD for this call.
    """
    LLM_CALLS_TOTAL.labels(
        provider=provider, model=model, use_case=use_case, status=status
    ).inc()

    if cost_usd > 0:
        LLM_COST_USD_TOTAL.labels(
            provider=provider, model=model
        ).inc(cost_usd)

    if prompt_tokens > 0:
        LLM_TOKENS_TOTAL.labels(
            provider=provider, model=model, direction="prompt"
        ).inc(prompt_tokens)

    if completion_tokens > 0:
        LLM_TOKENS_TOTAL.labels(
            provider=provider, model=model, direction="completion"
        ).inc(completion_tokens)


def set_task_queue_depth(queue_name: str, depth: int) -> None:
    """Set the current depth of a Celery task queue.

    Parameters
    ----------
    queue_name : str
        Queue name (e.g. "ingest", "backtest", "ai", "notify").
    depth : int
        Number of pending messages in the queue.
    """
    TASK_QUEUE_DEPTH.labels(queue_name=queue_name).set(depth)


__all__ = [
    # Metric objects
    "ALL_METRICS",
    "BACKTEST_DURATION_SECONDS",
    "BACKTEST_RUNS_TOTAL",
    "DATA_COMPLETENESS_RATIO",
    "INGEST_LATENCY_SECONDS",
    "INGEST_REQUESTS_TOTAL",
    "LLM_CALLS_TOTAL",
    "LLM_COST_USD_TOTAL",
    "LLM_TOKENS_TOTAL",
    "TASK_QUEUE_DEPTH",
    # Helper functions
    "record_ingest_request",
    "track_ingest_latency",
    "set_data_completeness",
    "record_backtest_run",
    "track_backtest_duration",
    "record_llm_call",
    "set_task_queue_depth",
]
