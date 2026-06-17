"""Tests for the request-ID middleware."""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

UUID_HEX_RE = re.compile(r"^[0-9a-f]{32}$")


def test_request_id_is_added_when_missing(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    request_id = resp.headers.get("X-Request-ID")
    assert request_id is not None
    # Default-generated IDs are UUID4 hex (32 lowercase hex chars).
    assert UUID_HEX_RE.match(request_id), f"unexpected generated ID: {request_id!r}"


def test_request_id_is_echoed_when_supplied(client: TestClient) -> None:
    supplied = "client-supplied-id-123"
    resp = client.get("/health", headers={"X-Request-ID": supplied})
    assert resp.status_code == 200
    assert resp.headers["X-Request-ID"] == supplied


def test_malformed_supplied_id_is_replaced(client: TestClient) -> None:
    # Contains spaces and special characters → rejected, replaced with a fresh UUID.
    resp = client.get("/health", headers={"X-Request-ID": "bad id with spaces!"})
    assert resp.status_code == 200
    returned = resp.headers["X-Request-ID"]
    assert returned != "bad id with spaces!"
    assert UUID_HEX_RE.match(returned)


def test_overly_long_supplied_id_is_replaced(client: TestClient) -> None:
    too_long = "a" * 500
    resp = client.get("/health", headers={"X-Request-ID": too_long})
    assert resp.status_code == 200
    returned = resp.headers["X-Request-ID"]
    assert returned != too_long
    assert UUID_HEX_RE.match(returned)


def test_each_request_gets_its_own_id(client: TestClient) -> None:
    r1 = client.get("/health")
    r2 = client.get("/health")
    assert r1.headers["X-Request-ID"] != r2.headers["X-Request-ID"]
