"""Application error types and the standardised HTTP error envelope.

Every error response emitted by the API has the shape:

    {
        "error": {
            "code": "<UPPER_SNAKE_CODE>",
            "message": "<human readable>",
            "details": <optional JSON-compatible value>,
            "request_id": "<uuid4>"
        }
    }

Downstream clients (frontend, CLI, integration tests) can rely on this
structure instead of parsing ad-hoc FastAPI `{"detail": ...}` payloads.
The actual registration of exception handlers lives in
`app.api.errors` so this module stays framework-free.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for all expected, HTTP-translatable application errors.

    Raising `AppError` (or any subclass) anywhere in request handling
    code will be converted by the FastAPI exception handler into the
    standard error envelope described in this module's docstring.
    """

    #: Default HTTP status for this error class.
    status_code: int = 500
    #: Default machine-readable error code (UPPER_SNAKE_CASE).
    code: str = "INTERNAL_ERROR"

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: Any | None = None,
    ) -> None:
        self.message = message or self.__class__.__doc__ or self.code
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.details = details
        super().__init__(self.message)


class BadRequestError(AppError):
    """Client submitted an invalid request."""

    status_code = 400
    code = "BAD_REQUEST"


class UnauthorizedError(AppError):
    """Authentication is required or has failed."""

    status_code = 401
    code = "UNAUTHORIZED"


class ForbiddenError(AppError):
    """Authenticated client is not allowed to perform this action."""

    status_code = 403
    code = "FORBIDDEN"


class NotFoundError(AppError):
    """Requested resource does not exist."""

    status_code = 404
    code = "NOT_FOUND"


class ConflictError(AppError):
    """Request conflicts with the current state of the resource."""

    status_code = 409
    code = "CONFLICT"


class ValidationAppError(AppError):
    """Payload failed business-rule validation (distinct from Pydantic)."""

    status_code = 422
    code = "VALIDATION_ERROR"


class RateLimitedError(AppError):
    """Caller is being rate-limited."""

    status_code = 429
    code = "RATE_LIMITED"


class InternalError(AppError):
    """Unexpected internal failure. Fallback for unhandled exceptions."""

    status_code = 500
    code = "INTERNAL_ERROR"


def build_error_envelope(
    *,
    code: str,
    message: str,
    request_id: str | None,
    details: Any | None = None,
) -> dict[str, Any]:
    """Build the canonical error payload dict (no framework dependency)."""
    payload: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        payload["details"] = details
    if request_id is not None:
        payload["request_id"] = request_id
    return {"error": payload}
