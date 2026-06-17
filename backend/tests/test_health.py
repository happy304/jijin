"""Tests for the `/health` endpoint.

Validates Requirement 9.3 (deployment-level health probe) and the
Dockerfile healthcheck contract (exact `/health` path).
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_returns_200_with_expected_shape(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    # Required keys
    assert body["status"] == "ok"
    assert "service" in body
    assert "version" in body
    assert "environment" in body
    # Environment reflects the test settings
    assert body["environment"] == "test"


def test_health_is_not_under_api_prefix(client: TestClient) -> None:
    # Dockerfile HEALTHCHECK targets `/health` directly, without the
    # `/api/v1` prefix. Prove the root-level route is the live one.
    resp = client.get("/api/v1/health")
    assert resp.status_code == 404


def test_version_endpoint_is_reachable_under_prefix(client: TestClient) -> None:
    resp = client.get("/api/v1/version")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"]
    assert body["version"]
    assert body["environment"] == "test"
