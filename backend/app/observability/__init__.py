"""Observability package.

Hosts Prometheus metric definitions (`metrics`), the FastAPI
instrumentation wiring (`instrumentation`), and eventually OpenTelemetry
tracing setup. Importing this package is free of side effects; metric
objects are created lazily when `app.observability.metrics` is imported
(task 0.6), and the HTTP instrumentation is installed only when
`setup_metrics()` is called from the FastAPI factory.
"""

from __future__ import annotations

from app.observability.instrumentation import setup_metrics
from app.observability.metrics import ALL_METRICS

__all__ = ["ALL_METRICS", "setup_metrics"]
