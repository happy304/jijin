"""Alertmanager webhook receiver endpoint.

Receives alert notifications from Prometheus Alertmanager and routes
them to the platform's notification module (email, WeChat Work,
Telegram) based on user configuration.

Requirements: 8.4, 8.7
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.notify.service import NotificationService, SignalNotification

log = get_logger(__name__)

router = APIRouter(prefix="/alerts", tags=["alerts"])


# =====================================================================
# Pydantic models for Alertmanager webhook payload
# =====================================================================


class AlertLabel(BaseModel):
    """Alert labels from Alertmanager."""

    alertname: str = ""
    severity: str = ""
    category: str = ""
    # Allow extra labels without strict validation
    model_config = {"extra": "allow"}


class AlertAnnotation(BaseModel):
    """Alert annotations from Alertmanager."""

    summary: str = ""
    description: str = ""
    model_config = {"extra": "allow"}


class Alert(BaseModel):
    """Single alert from Alertmanager webhook payload."""

    status: str = "firing"
    labels: AlertLabel = Field(default_factory=AlertLabel)
    annotations: AlertAnnotation = Field(default_factory=AlertAnnotation)
    startsAt: str = ""
    endsAt: str = ""
    generatorURL: str = ""
    fingerprint: str = ""


class AlertmanagerWebhook(BaseModel):
    """Alertmanager webhook payload structure.

    See: https://prometheus.io/docs/alerting/latest/configuration/#webhook_config
    """

    version: str = "4"
    groupKey: str = ""
    truncatedAlerts: int = 0
    status: str = "firing"
    receiver: str = ""
    groupLabels: dict[str, str] = Field(default_factory=dict)
    commonLabels: dict[str, str] = Field(default_factory=dict)
    commonAnnotations: dict[str, str] = Field(default_factory=dict)
    externalURL: str = ""
    alerts: list[Alert] = Field(default_factory=list)


class WebhookResponse(BaseModel):
    """Response from the webhook endpoint."""

    status: str
    received: int
    processed: int
    errors: list[str] = Field(default_factory=list)


# =====================================================================
# Webhook endpoint
# =====================================================================


@router.post(
    "/webhook",
    response_model=WebhookResponse,
    status_code=status.HTTP_200_OK,
    summary="Receive Alertmanager webhook notifications",
    description=(
        "Receives alert notifications from Prometheus Alertmanager "
        "and routes them to configured notification channels."
    ),
)
async def receive_alertmanager_webhook(
    payload: AlertmanagerWebhook,
) -> WebhookResponse:
    """Process incoming Alertmanager webhook and dispatch notifications.

    This endpoint:
    1. Parses the Alertmanager webhook payload
    2. Converts alerts to platform notification format
    3. Dispatches to configured notification channels (email/wecom/telegram)
    4. Returns a summary of processing results
    """
    log.info(
        "alerts.webhook.received",
        status=payload.status,
        receiver=payload.receiver,
        alert_count=len(payload.alerts),
        group_key=payload.groupKey,
    )

    if not payload.alerts:
        return WebhookResponse(status="ok", received=0, processed=0)

    notifications: list[SignalNotification] = []
    errors: list[str] = []

    for alert in payload.alerts:
        try:
            notification = _alert_to_notification(alert, payload.status)
            notifications.append(notification)
        except Exception as exc:
            error_msg = (
                f"Failed to convert alert "
                f"{alert.labels.alertname}: {exc}"
            )
            log.warning("alerts.webhook.convert_error", error=error_msg)
            errors.append(error_msg)

    # Dispatch notifications through the unified notification service
    processed = 0
    if notifications:
        try:
            service = NotificationService()
            result = service.send(notifications)
            processed = result.sent
            if result.failed > 0:
                errors.append(
                    f"{result.failed} notification(s) failed to send"
                )
        except Exception as exc:
            log.error("alerts.webhook.dispatch_error", error=str(exc))
            errors.append(f"Notification dispatch error: {exc}")

    log.info(
        "alerts.webhook.processed",
        received=len(payload.alerts),
        processed=processed,
        errors=len(errors),
    )

    return WebhookResponse(
        status="ok",
        received=len(payload.alerts),
        processed=processed,
        errors=errors,
    )


def _alert_to_notification(
    alert: Alert, group_status: str
) -> SignalNotification:
    """Convert an Alertmanager alert to a platform SignalNotification.

    We reuse the existing SignalNotification structure, mapping alert
    fields to the closest semantic equivalents:
    - strategy_name → alert name + severity
    - fund_code → alert category
    - direction → alert status (firing/resolved)
    - reason → alert summary + description
    """
    alert_name = alert.labels.alertname or "UnknownAlert"
    severity = alert.labels.severity or "unknown"
    category = alert.labels.category or "system"
    alert_status = alert.status or group_status

    # Build a human-readable reason from annotations
    summary = alert.annotations.summary or alert_name
    description = alert.annotations.description or ""
    reason_parts = [f"[{severity.upper()}] {summary}"]
    if description:
        reason_parts.append(description[:200])  # Truncate long descriptions
    reason = " | ".join(reason_parts)

    # Determine signal_date from alert start time
    signal_date = _parse_alert_time(alert.startsAt)

    return SignalNotification(
        strategy_id=0,  # System alert, not strategy-specific
        strategy_name=f"Alert: {alert_name}",
        fund_code=category,
        direction=alert_status,  # "firing" or "resolved"
        signal_date=signal_date,
        strength=1.0 if severity == "critical" else 0.5,
        reason=reason,
    )


def _parse_alert_time(time_str: str) -> str:
    """Parse Alertmanager timestamp to date string.

    Alertmanager sends ISO 8601 timestamps like:
    '2024-01-15T10:30:00.000Z'

    Returns YYYY-MM-DD format, or today's date on parse failure.
    """
    if not time_str:
        return datetime.now().strftime("%Y-%m-%d")
    try:
        # Handle various ISO 8601 formats
        dt = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return datetime.now().strftime("%Y-%m-%d")
