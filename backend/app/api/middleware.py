"""ASGI middleware shared by the FastAPI app.

Currently:

* `RequestIDMiddleware` — assigns an `X-Request-ID` to every request,
  echoes client-supplied values, binds it into the structlog context so
  every log line in the request scope carries it, and sets it on the
  response so clients can correlate errors with logs.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.logging import get_logger

REQUEST_ID_HEADER = "X-Request-ID"
_MAX_SUPPLIED_ID_LENGTH = 128

log = get_logger(__name__)


def _sanitize_supplied_id(value: str | None) -> str | None:
    """Accept a client-supplied request ID only if it looks reasonable.

    We don't want unbounded or non-printable values in our logs. The ID
    must be non-empty, <= 128 chars, and printable ASCII (letters,
    digits, dashes, underscores). Anything else is ignored and we
    allocate a fresh UUID.
    """
    if not value:
        return None
    value = value.strip()
    if not value or len(value) > _MAX_SUPPLIED_ID_LENGTH:
        return None
    if not all(c.isalnum() or c in ("-", "_") for c in value):
        return None
    return value


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Populate a request-scoped ID, expose it on logs and responses.

    The middleware:

    1. Reads ``X-Request-ID`` from the incoming request. If absent or
       malformed, generates a UUID4.
    2. Stores it on ``request.state.request_id`` for handlers to read.
    3. Binds ``request_id``, ``method`` and ``path`` into the structlog
       context so every log line in this request carries them.
    4. Emits structured ``request.start`` / ``request.end`` events with
       latency.
    5. Sets ``X-Request-ID`` on the outgoing response (including error
       responses raised by downstream handlers).
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        supplied = _sanitize_supplied_id(request.headers.get(REQUEST_ID_HEADER))
        request_id = supplied or uuid.uuid4().hex
        request.state.request_id = request_id

        # Bind context for this request's entire lifecycle — clear at
        # the end so long-lived workers don't leak context across tasks.
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )
        start = time.perf_counter()
        log.info("request.start")
        try:
            response = await call_next(request)
        except Exception:
            # Exception handlers registered on the FastAPI app will
            # produce the final response, but if something slips past
            # them we still want the error logged with context.
            duration_ms = (time.perf_counter() - start) * 1000
            log.exception("request.unhandled_exception", duration_ms=round(duration_ms, 2))
            structlog.contextvars.clear_contextvars()
            raise
        else:
            duration_ms = (time.perf_counter() - start) * 1000
            response.headers[REQUEST_ID_HEADER] = request_id
            log.info(
                "request.end",
                status_code=response.status_code,
                duration_ms=round(duration_ms, 2),
            )
            return response
        finally:
            structlog.contextvars.clear_contextvars()
