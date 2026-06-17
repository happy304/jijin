"""Tests for the standardised error envelope.

The envelope is defined in `app.core.errors.build_error_envelope` and
rendered by the handlers registered in `app.api.errors`.
"""

from __future__ import annotations

from typing import Iterator

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.errors import AppError, NotFoundError, ValidationAppError
from app.main import create_app


@pytest.fixture
def app_with_error_routes(settings: Settings) -> Iterator[FastAPI]:
    """An app variant with extra test-only endpoints that raise errors."""
    application = create_app(settings)
    application.dependency_overrides[get_settings] = lambda: settings

    router = APIRouter(prefix="/__test__", tags=["__test__"])

    @router.get("/app-error")
    async def raise_app_error() -> None:
        raise NotFoundError("fund not found", details={"code": "000001"})

    @router.get("/validation-app-error")
    async def raise_validation_error() -> None:
        raise ValidationAppError("bad business payload")

    @router.get("/custom-app-error")
    async def raise_custom() -> None:
        raise AppError(
            "custom failure",
            code="CUSTOM_ERROR",
            status_code=418,
            details={"reason": "teapot"},
        )

    @router.get("/boom")
    async def raise_unexpected() -> None:
        raise RuntimeError("unexpected failure")

    application.include_router(router)
    try:
        yield application
    finally:
        application.dependency_overrides.clear()


@pytest.fixture
def error_client(app_with_error_routes: FastAPI) -> Iterator[TestClient]:
    # `raise_server_exceptions=False` lets us assert the 500 response
    # body produced by our handler instead of the TestClient re-raising.
    with TestClient(app_with_error_routes, raise_server_exceptions=False) as tc:
        yield tc


def _assert_envelope(body: dict, *, code: str, request_id_must_match: str | None = None) -> None:
    assert "error" in body, body
    err = body["error"]
    assert err["code"] == code
    assert isinstance(err["message"], str) and err["message"]
    assert "request_id" in err and err["request_id"]
    if request_id_must_match is not None:
        assert err["request_id"] == request_id_must_match


def test_app_error_renders_envelope_with_status_and_details(error_client: TestClient) -> None:
    resp = error_client.get("/__test__/app-error", headers={"X-Request-ID": "fixed-id-abc"})
    assert resp.status_code == 404
    assert resp.headers["X-Request-ID"] == "fixed-id-abc"
    body = resp.json()
    _assert_envelope(body, code="NOT_FOUND", request_id_must_match="fixed-id-abc")
    assert body["error"]["details"] == {"code": "000001"}


def test_custom_app_error_allows_custom_status_and_code(error_client: TestClient) -> None:
    resp = error_client.get("/__test__/custom-app-error")
    assert resp.status_code == 418
    body = resp.json()
    _assert_envelope(body, code="CUSTOM_ERROR")
    assert body["error"]["details"] == {"reason": "teapot"}


def test_unknown_route_renders_404_envelope(error_client: TestClient) -> None:
    resp = error_client.get("/no-such-path")
    assert resp.status_code == 404
    body = resp.json()
    _assert_envelope(body, code="NOT_FOUND")


def test_unhandled_exception_renders_500_envelope(error_client: TestClient) -> None:
    resp = error_client.get("/__test__/boom")
    assert resp.status_code == 500
    body = resp.json()
    _assert_envelope(body, code="INTERNAL_ERROR")
    # Never leak internal error details to the client.
    assert "unexpected failure" not in body["error"]["message"]


def test_validation_app_error_renders_422(error_client: TestClient) -> None:
    resp = error_client.get("/__test__/validation-app-error")
    assert resp.status_code == 422
    body = resp.json()
    _assert_envelope(body, code="VALIDATION_ERROR")
