"""FastAPI exception handlers that render the standard error envelope.

The envelope shape is defined in `app.core.errors.build_error_envelope`.
Handlers cover four classes of errors:

* `AppError` subclasses — our own domain errors with explicit HTTP status.
* `StarletteHTTPException` — raised by FastAPI/Starlette itself (e.g.
  404 on unknown routes, 405 on wrong method).
* `RequestValidationError` — Pydantic payload validation failures.
* `Exception` — catch-all for anything we didn't anticipate; logged with
  traceback and reported to the client as a generic 500.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.middleware import REQUEST_ID_HEADER
from app.core.errors import AppError, build_error_envelope
from app.core.logging import get_logger

log = get_logger(__name__)


def _request_id(request: Request) -> str | None:
    """Return the request-scoped ID populated by `RequestIDMiddleware`."""
    return getattr(request.state, "request_id", None)


def _json_error(
    *,
    status_code: int,
    code: str,
    message: str,
    request_id: str | None,
    details: Any | None = None,
) -> JSONResponse:
    envelope = build_error_envelope(
        code=code, message=message, request_id=request_id, details=details
    )
    headers = {REQUEST_ID_HEADER: request_id} if request_id else None
    return JSONResponse(status_code=status_code, content=envelope, headers=headers)


async def app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle explicit `AppError` subclasses (4xx business errors)."""
    assert isinstance(exc, AppError)
    request_id = _request_id(request)
    log.info(
        "request.app_error",
        error_code=exc.code,
        status_code=exc.status_code,
        message=exc.message,
    )
    return _json_error(
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        request_id=request_id,
        details=exc.details,
    )


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Translate FastAPI/Starlette HTTPException into the error envelope."""
    assert isinstance(exc, StarletteHTTPException)
    request_id = _request_id(request)
    # Map a small set of common status codes to stable business codes;
    # for everything else we fall back to the canonical HTTP phrase.
    code_by_status = {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        405: "METHOD_NOT_ALLOWED",
        409: "CONFLICT",
        422: "VALIDATION_ERROR",
        429: "RATE_LIMITED",
    }
    code = code_by_status.get(exc.status_code, f"HTTP_{exc.status_code}")
    message = str(exc.detail) if exc.detail else code.replace("_", " ").title()
    log.info(
        "request.http_exception",
        status_code=exc.status_code,
        detail=exc.detail,
    )
    return _json_error(
        status_code=exc.status_code,
        code=code,
        message=message,
        request_id=request_id,
    )


async def validation_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Render Pydantic payload validation errors in the envelope shape."""
    assert isinstance(exc, RequestValidationError)
    request_id = _request_id(request)
    # `exc.errors()` produces a JSON-serialisable list of per-field
    # errors. Expose it verbatim under `details` so the frontend can
    # highlight fields; keep the top-level message short.
    log.info("request.validation_error", errors=exc.errors())
    return _json_error(
        status_code=422,
        code="VALIDATION_ERROR",
        message="Request payload failed validation",
        request_id=request_id,
        details=exc.errors(),
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last-resort handler: log the full traceback, return a generic 500."""
    request_id = _request_id(request)
    log.exception("request.unhandled_exception", error_type=type(exc).__name__)
    return _json_error(
        status_code=500,
        code="INTERNAL_ERROR",
        message="Internal server error",
        request_id=request_id,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Wire all handlers onto the given FastAPI app."""
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
