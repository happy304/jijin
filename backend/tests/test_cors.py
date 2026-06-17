"""Tests for CORS middleware configuration."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_allowed_origin_receives_cors_headers(client: TestClient) -> None:
    origin = "http://allowed.example"
    resp = client.get("/health", headers={"Origin": origin})
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == origin
    # Request ID must remain exposed so browser clients can read it.
    expose = resp.headers.get("access-control-expose-headers", "")
    assert "X-Request-ID" in expose


def test_disallowed_origin_does_not_receive_cors_headers(client: TestClient) -> None:
    resp = client.get("/health", headers={"Origin": "http://not-allowed.example"})
    # Starlette still processes the request, but does NOT echo the
    # Access-Control-Allow-Origin header back — browser-side same-origin
    # policy will therefore reject the response.
    assert resp.status_code == 200
    assert "access-control-allow-origin" not in {k.lower() for k in resp.headers}


def test_preflight_request_is_accepted_for_allowed_origin(client: TestClient) -> None:
    resp = client.options(
        "/api/v1/version",
        headers={
            "Origin": "http://allowed.example",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-Request-ID,Content-Type",
        },
    )
    # Starlette's CORSMiddleware answers OPTIONS preflight with 200.
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "http://allowed.example"
    allow_methods = resp.headers.get("access-control-allow-methods", "")
    assert "GET" in allow_methods or "*" in allow_methods
