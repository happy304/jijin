"""Shared provider factory helpers.

Keeps the default provider chain in one place so API endpoints, Celery tasks
and other entrypoints reuse the same fallback behaviour.
"""

from __future__ import annotations

import logging
from typing import Any

from app.data.fetchers.circuit_breaker import CircuitBreakerRegistry
from app.data.providers.composite import CompositeProvider
from app.data.providers.eastmoney import EastmoneyProvider
from app.data.providers.snapshot import SnapshotArchive

logger = logging.getLogger(__name__)


def _warn(logger_obj: Any | None, event: str, reason: str) -> None:
    """Log a warning with either structlog or stdlib logging."""
    if logger_obj is None:
        logger.warning("%s: %s", event, reason)
        return

    try:
        logger_obj.warning(event, reason=reason)
    except TypeError:
        logger_obj.warning("%s: %s", event, reason)


def build_default_composite_provider(*, logger: Any | None = None) -> CompositeProvider:
    """Build the default provider chain used across the application."""
    providers = [EastmoneyProvider()]

    try:
        from app.data.providers.akshare import AkshareProvider

        providers.append(AkshareProvider())
    except (ImportError, Exception):
        _warn(logger, "providers.akshare_unavailable", "import failed")

    try:
        from app.data.providers.cninfo import CnInfoProvider

        providers.append(CnInfoProvider())
    except (ImportError, Exception):
        _warn(logger, "providers.cninfo_unavailable", "import failed")

    return CompositeProvider(
        providers=providers,
        circuit_breaker=CircuitBreakerRegistry(),
        snapshot=SnapshotArchive(),
    )


__all__ = ["build_default_composite_provider"]
