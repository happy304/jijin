"""Structured logging configuration.

The platform emits JSON logs in production and pretty console logs in
development. Both formats flow through the same `structlog` pipeline so
handler code can bind request-scoped context (request_id, fund_code,
strategy_id, …) via `structlog.contextvars.bind_contextvars(...)` and
have those fields appear automatically on every log line.

Usage:
    from app.core.logging import configure_logging, get_logger

    configure_logging(settings)
    log = get_logger(__name__)
    log.info("service started", port=8000)
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.types import EventDict, Processor

from app.core.config import Settings


def _drop_color_message_key(
    _logger: Any, _method_name: str, event_dict: EventDict
) -> EventDict:
    """Uvicorn duplicates the message under `color_message`; drop it."""
    event_dict.pop("color_message", None)
    return event_dict


def _build_processors(*, json_output: bool) -> list[Processor]:
    """Return the ordered processor chain for structlog.

    The chain is identical for both JSON and console output until the
    final renderer. Keeping it in one place ensures context bound via
    `bind_contextvars` propagates uniformly.
    """
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        _drop_color_message_key,
    ]
    if json_output:
        shared.append(structlog.processors.EventRenamer("message"))
        shared.append(structlog.processors.JSONRenderer())
    else:
        shared.append(structlog.dev.ConsoleRenderer(colors=False))
    return shared


def configure_logging(settings: Settings) -> None:
    """Configure stdlib logging + structlog using `settings`.

    Call this once during application startup (FastAPI lifespan). It is
    safe (idempotent) to call multiple times — the previous handlers on
    the root logger are replaced.
    """
    # In dev we render colourless console lines for easy reading;
    # everywhere else we emit JSON so log aggregators can parse the
    # payload directly.
    json_output = not settings.is_development

    processors = _build_processors(json_output=json_output)

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(settings.log_level)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Route stdlib logs (uvicorn, sqlalchemy, httpx, ...) through the
    # same destination. We keep the simple stdlib formatter because the
    # real formatting happens in the structlog chain above for our own
    # loggers; third-party libs remain readable in dev and grep-able in
    # prod.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(settings.log_level)

    # Calm down a couple of very chatty libraries by default.
    for noisy in ("uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(
            logging.INFO if settings.log_level == "DEBUG" else logging.WARNING
        )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger bound to the given logical name.

    Prefer the module's `__name__` so the `logger` field in JSON lines
    tells you exactly which module produced the event.
    """
    return structlog.get_logger(name)
