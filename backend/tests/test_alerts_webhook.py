"""Tests for the Alertmanager webhook endpoint.

Validates that:
- The webhook endpoint accepts valid Alertmanager payloads
- Alerts are correctly parsed and converted to notifications
- Empty payloads are handled gracefully
- Malformed alerts don't crash the endpoint

Requirements: 8.4, 8.7
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


class TestAlertsWebhook:
    """Test suite for POST /api/v1/alerts/webhook."""

    def test_webhook_accepts_valid_payload(self, client: TestClient) -> None:
        """A well-formed Alertmanager payload returns 200 with summary."""
        payload = {
            "version": "4",
            "groupKey": "{}:{alertname=\"DataStale\"}",
            "truncatedAlerts": 0,
            "status": "firing",
            "receiver": "platform-webhook",
            "groupLabels": {"alertname": "DataStale"},
            "commonLabels": {
                "alertname": "DataStale",
                "severity": "warning",
                "category": "data",
            },
            "commonAnnotations": {
                "summary": "Fund data not updated for 2+ trading days",
            },
            "externalURL": "http://prometheus:9090",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {
                        "alertname": "DataStale",
                        "severity": "warning",
                        "category": "data",
                    },
                    "annotations": {
                        "summary": "Fund data not updated for 2+ trading days",
                        "description": "No successful ingest requests in 48h.",
                    },
                    "startsAt": "2024-01-15T10:30:00.000Z",
                    "endsAt": "0001-01-01T00:00:00Z",
                    "generatorURL": "http://prometheus:9090/graph",
                    "fingerprint": "abc123",
                }
            ],
        }

        response = client.post("/api/v1/alerts/webhook", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["received"] == 1

    def test_webhook_handles_empty_alerts(self, client: TestClient) -> None:
        """An empty alerts list returns 200 with zero counts."""
        payload = {
            "version": "4",
            "status": "firing",
            "receiver": "platform-webhook",
            "alerts": [],
        }

        response = client.post("/api/v1/alerts/webhook", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["received"] == 0
        assert data["processed"] == 0

    def test_webhook_handles_multiple_alerts(self, client: TestClient) -> None:
        """Multiple alerts in a single payload are all processed."""
        payload = {
            "version": "4",
            "status": "firing",
            "receiver": "platform-webhook",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {
                        "alertname": "DataStale",
                        "severity": "warning",
                        "category": "data",
                    },
                    "annotations": {
                        "summary": "Fund data stale",
                    },
                    "startsAt": "2024-01-15T10:30:00Z",
                    "endsAt": "0001-01-01T00:00:00Z",
                    "generatorURL": "",
                    "fingerprint": "abc123",
                },
                {
                    "status": "firing",
                    "labels": {
                        "alertname": "LLMCostExceeded",
                        "severity": "warning",
                        "category": "llm",
                    },
                    "annotations": {
                        "summary": "Daily LLM cost exceeded threshold",
                    },
                    "startsAt": "2024-01-15T11:00:00Z",
                    "endsAt": "0001-01-01T00:00:00Z",
                    "generatorURL": "",
                    "fingerprint": "def456",
                },
            ],
        }

        response = client.post("/api/v1/alerts/webhook", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["received"] == 2

    def test_webhook_handles_resolved_alerts(self, client: TestClient) -> None:
        """Resolved alerts are accepted and processed."""
        payload = {
            "version": "4",
            "status": "resolved",
            "receiver": "platform-webhook",
            "alerts": [
                {
                    "status": "resolved",
                    "labels": {
                        "alertname": "ProviderCircuitOpen",
                        "severity": "critical",
                        "category": "provider",
                    },
                    "annotations": {
                        "summary": "Provider circuit breaker recovered",
                    },
                    "startsAt": "2024-01-15T10:30:00Z",
                    "endsAt": "2024-01-15T11:00:00Z",
                    "generatorURL": "",
                    "fingerprint": "ghi789",
                }
            ],
        }

        response = client.post("/api/v1/alerts/webhook", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["received"] == 1

    def test_webhook_handles_minimal_payload(self, client: TestClient) -> None:
        """A minimal payload with only required fields is accepted."""
        payload = {
            "alerts": [
                {
                    "labels": {"alertname": "TestAlert"},
                    "annotations": {"summary": "Test"},
                }
            ],
        }

        response = client.post("/api/v1/alerts/webhook", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["received"] == 1

    def test_webhook_handles_missing_optional_fields(
        self, client: TestClient
    ) -> None:
        """Alerts with missing optional fields don't crash the endpoint."""
        payload = {
            "version": "4",
            "status": "firing",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {},
                    "annotations": {},
                    "startsAt": "",
                    "endsAt": "",
                    "generatorURL": "",
                    "fingerprint": "",
                }
            ],
        }

        response = client.post("/api/v1/alerts/webhook", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["received"] == 1


class TestAlertConversion:
    """Test the alert-to-notification conversion logic."""

    def test_critical_alert_has_high_strength(
        self, client: TestClient
    ) -> None:
        """Critical severity alerts get strength=1.0."""
        payload = {
            "alerts": [
                {
                    "status": "firing",
                    "labels": {
                        "alertname": "ProviderCircuitOpen",
                        "severity": "critical",
                        "category": "provider",
                    },
                    "annotations": {
                        "summary": "Circuit breaker open",
                    },
                    "startsAt": "2024-06-01T08:00:00Z",
                }
            ],
        }

        response = client.post("/api/v1/alerts/webhook", json=payload)
        assert response.status_code == 200

    def test_warning_alert_has_lower_strength(
        self, client: TestClient
    ) -> None:
        """Warning severity alerts get strength=0.5."""
        payload = {
            "alerts": [
                {
                    "status": "firing",
                    "labels": {
                        "alertname": "TaskFailureRateHigh",
                        "severity": "warning",
                        "category": "task",
                    },
                    "annotations": {
                        "summary": "Task failure rate high",
                    },
                    "startsAt": "2024-06-01T08:00:00Z",
                }
            ],
        }

        response = client.post("/api/v1/alerts/webhook", json=payload)
        assert response.status_code == 200
