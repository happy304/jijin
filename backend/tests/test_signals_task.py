"""Unit tests for the signal generation task (task 6.2).

Tests cover:
- Signal ORM model creation
- Signal generation for strategies
- Notification dispatch
- Task orchestration logic
- Edge cases (no strategies, strategy errors, empty signals)

Requirements: 8.2
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import pytest

from app.data.models.signals import Signal
from app.domain.backtest.order import OrderIntent
from app.notify.service import (
    NotificationResult,
    NotificationService,
    SignalNotification,
    send_signal_notifications,
)


# ---------------------------------------------------------------------------
# Signal ORM Model Tests
# ---------------------------------------------------------------------------


class TestSignalModel:
    """Tests for the Signal ORM model."""

    def test_signal_model_creation(self):
        """Signal model can be instantiated with required fields."""
        signal = Signal(
            strategy_id=1,
            strategy_name="momentum_rotation",
            fund_code="000001",
            signal_date=date(2024, 6, 15),
            direction="subscribe",
            notified=False,
        )
        assert signal.strategy_id == 1
        assert signal.strategy_name == "momentum_rotation"
        assert signal.fund_code == "000001"
        assert signal.signal_date == date(2024, 6, 15)
        assert signal.direction == "subscribe"
        assert signal.notified is False

    def test_signal_model_with_optional_fields(self):
        """Signal model accepts all optional fields."""
        signal = Signal(
            strategy_id=2,
            strategy_name="risk_parity",
            fund_code="110011",
            signal_date=date(2024, 6, 15),
            direction="redeem",
            strength=0.85,
            target_weight=0.15,
            amount=50000.00,
            shares=3200.50,
            reason="动量因子排名下降",
            metadata_json={"strategy_type": "momentum", "lookback": 6},
            notified=True,
        )
        assert signal.strength == 0.85
        assert signal.target_weight == 0.15
        assert signal.amount == 50000.00
        assert signal.shares == 3200.50
        assert signal.reason == "动量因子排名下降"
        assert signal.metadata_json == {"strategy_type": "momentum", "lookback": 6}
        assert signal.notified is True

    def test_signal_tablename(self):
        """Signal model maps to 'signals' table."""
        assert Signal.__tablename__ == "signals"

    def test_signal_unique_constraint_exists(self):
        """Signal model should define a composite unique constraint for idempotency."""
        constraints = list(Signal.__table__.constraints)
        names = {getattr(c, "name", None) for c in constraints}
        assert "uq_signals_strategy_fund_date_direction" in names


# ---------------------------------------------------------------------------
# Notification Service Tests
# ---------------------------------------------------------------------------


class TestNotificationService:
    """Tests for the notification service stub."""

    def test_empty_notifications(self):
        """Sending empty list returns zero counts."""
        service = NotificationService()
        result = service.send([])
        assert result.total == 0
        assert result.sent == 0
        assert result.failed == 0

    def test_send_notifications_no_channels(self):
        """Service with no channels configured reports notifications as failed."""
        notifications = [
            SignalNotification(
                strategy_id=1,
                strategy_name="momentum",
                fund_code="000001",
                direction="subscribe",
                signal_date="2024-06-15",
            ),
            SignalNotification(
                strategy_id=1,
                strategy_name="momentum",
                fund_code="110011",
                direction="redeem",
                signal_date="2024-06-15",
            ),
        ]
        service = NotificationService(channels=[])
        result = service.send(notifications)
        assert result.total == 2
        assert result.sent == 0
        assert result.failed == 2

    def test_send_signal_notifications_convenience(self):
        """Convenience function works correctly with no channels."""
        notifications = [
            SignalNotification(
                strategy_id=1,
                strategy_name="test",
                fund_code="000001",
                direction="hold",
                signal_date="2024-06-15",
            ),
        ]
        result = send_signal_notifications(notifications, channels=[])
        assert result.total == 1
        assert result.failed == 1

    def test_notification_result_defaults(self):
        """NotificationResult has sensible defaults."""
        result = NotificationResult()
        assert result.total == 0
        assert result.sent == 0
        assert result.failed == 0
        assert result.channels_used == []


# ---------------------------------------------------------------------------
# Signal Generation Logic Tests
# ---------------------------------------------------------------------------


class TestSignalGeneration:
    """Tests for the signal generation logic."""

    @patch("app.tasks.signals._load_active_strategies")
    @patch("app.tasks.signals._generate_signals_for_strategy")
    @patch("app.tasks.signals._store_signals")
    @patch("app.tasks.signals._send_notifications")
    @patch("app.tasks.signals._get_today")
    def test_generate_signals_full_flow(
        self,
        mock_today,
        mock_notify,
        mock_store,
        mock_generate,
        mock_load,
    ):
        """Full signal generation flow processes strategies and stores signals."""
        mock_today.return_value = date(2024, 6, 15)
        mock_load.return_value = [
            {
                "id": 1,
                "name": "momentum_rotation",
                "strategy_type": "momentum",
                "params": {"lookback_months": 6, "top_n": 3},
                "universe": {"fund_codes": ["000001", "110011"]},
                "benchmark": None,
            },
        ]
        mock_generate.return_value = [
            {
                "strategy_id": 1,
                "strategy_name": "momentum_rotation",
                "fund_code": "000001",
                "signal_date": date(2024, 6, 15),
                "direction": "subscribe",
                "strength": None,
                "target_weight": 0.5,
                "amount": 50000.0,
                "shares": None,
                "reason": "策略 momentum_rotation 生成的 subscribe 信号",
                "metadata_json": {"strategy_type": "momentum"},
                "notified": False,
            },
        ]
        mock_store.return_value = [mock_generate.return_value[0]]
        mock_notify.return_value = {"total": 1, "sent": 1, "failed": 0, "channels_used": ["stub"]}

        from app.tasks.signals import generate_strategy_signals

        result = generate_strategy_signals()

        assert result["status"] == "success"
        assert result["strategies_processed"] == 1
        assert result["signals_generated"] == 1
        assert result["signals_stored"] == 1
        assert result["signal_date"] == "2024-06-15"
        mock_store.assert_called_once()
        mock_notify.assert_called_once()

    @patch("app.tasks.signals._load_active_strategies")
    @patch("app.tasks.signals._get_today")
    def test_generate_signals_no_strategies(self, mock_today, mock_load):
        """Returns success with zero counts when no strategies exist."""
        mock_today.return_value = date(2024, 6, 15)
        mock_load.return_value = []

        from app.tasks.signals import generate_strategy_signals

        result = generate_strategy_signals()

        assert result["status"] == "success"
        assert result["strategies_processed"] == 0
        assert result["signals_generated"] == 0
        assert result["message"] == "无活跃策略需要处理"

    @patch("app.tasks.signals._load_active_strategies")
    @patch("app.tasks.signals._get_today")
    def test_generate_signals_load_error(self, mock_today, mock_load):
        """Returns error status when strategy loading fails."""
        mock_today.return_value = date(2024, 6, 15)
        mock_load.side_effect = RuntimeError("DB connection failed")

        from app.tasks.signals import generate_strategy_signals

        result = generate_strategy_signals()

        assert result["status"] == "error"
        assert "加载策略失败" in result["error"]
        assert result["strategies_processed"] == 0

    @patch("app.tasks.signals._load_active_strategies")
    @patch("app.tasks.signals._generate_signals_for_strategy")
    @patch("app.tasks.signals._store_signals")
    @patch("app.tasks.signals._send_notifications")
    @patch("app.tasks.signals._get_today")
    def test_generate_signals_strategy_error_continues(
        self,
        mock_today,
        mock_notify,
        mock_store,
        mock_generate,
        mock_load,
    ):
        """Strategy errors are caught and processing continues."""
        mock_today.return_value = date(2024, 6, 15)
        mock_load.return_value = [
            {"id": 1, "name": "bad_strategy", "strategy_type": "unknown",
             "params": {}, "universe": {}, "benchmark": None},
            {"id": 2, "name": "good_strategy", "strategy_type": "momentum",
             "params": {}, "universe": {}, "benchmark": None},
        ]

        # First strategy raises, second succeeds
        mock_generate.side_effect = [
            RuntimeError("Strategy instantiation failed"),
            [{"strategy_id": 2, "strategy_name": "good_strategy",
              "fund_code": "000001", "signal_date": date(2024, 6, 15),
              "direction": "subscribe", "strength": None,
              "target_weight": None, "amount": None, "shares": None,
              "reason": "test", "metadata_json": {}, "notified": False}],
        ]
        mock_store.return_value = [{"strategy_id": 2, "strategy_name": "good_strategy", "fund_code": "000001", "signal_date": date(2024, 6, 15), "direction": "subscribe", "strength": None, "target_weight": None, "amount": None, "shares": None, "reason": "test", "metadata_json": {}, "notified": False}]
        mock_notify.return_value = {"total": 1, "sent": 1, "failed": 0}

        from app.tasks.signals import generate_strategy_signals

        result = generate_strategy_signals()

        assert result["status"] == "success"
        assert result["strategies_processed"] == 1
        assert result["strategies_failed"] == 1
        assert result["signals_generated"] == 1

    @patch("app.tasks.signals._load_active_strategies")
    @patch("app.tasks.signals._generate_signals_for_strategy")
    @patch("app.tasks.signals._store_signals")
    @patch("app.tasks.signals._send_notifications")
    @patch("app.tasks.signals._get_today")
    def test_generate_signals_store_error_skips_notifications(
        self,
        mock_today,
        mock_notify,
        mock_store,
        mock_generate,
        mock_load,
    ):
        """Store failure should prevent notification attempt."""
        mock_today.return_value = date(2024, 6, 15)
        mock_load.return_value = [
            {"id": 1, "name": "test", "strategy_type": "momentum",
             "params": {}, "universe": {}, "benchmark": None},
        ]
        mock_generate.return_value = [
            {"strategy_id": 1, "strategy_name": "test",
             "fund_code": "000001", "signal_date": date(2024, 6, 15),
             "direction": "subscribe", "strength": None,
             "target_weight": None, "amount": None, "shares": None,
             "reason": "test", "metadata_json": {}, "notified": False},
        ]
        mock_store.side_effect = RuntimeError("DB write failed")
        mock_notify.return_value = {"total": 1, "sent": 1, "failed": 0}

        from app.tasks.signals import generate_strategy_signals

        result = generate_strategy_signals()

        assert result["status"] == "success"
        assert result["signals_generated"] == 1
        assert result["signals_stored"] == 0
        mock_notify.assert_not_called()


# ---------------------------------------------------------------------------
# _generate_signals_for_strategy Tests
# ---------------------------------------------------------------------------


class TestGenerateSignalsForStrategy:
    """Tests for the per-strategy signal generation function."""

    @patch("app.tasks.signals._build_signal_context")
    @patch("app.domain.strategy.base.create_strategy_from_config")
    def test_generates_signals_from_order_intents(
        self, mock_create, mock_context
    ):
        """Converts OrderIntents from strategy.on_bar to signal dicts."""
        # Mock strategy
        mock_strategy = MagicMock()
        mock_strategy.on_bar.return_value = [
            OrderIntent(
                fund_code="000001",
                direction="subscribe",
                amount=Decimal("10000"),
                target_weight=Decimal("0.5"),
            ),
            OrderIntent(
                fund_code="110011",
                direction="redeem",
                shares=Decimal("500"),
            ),
        ]
        mock_create.return_value = mock_strategy

        # Mock context
        mock_ctx = MagicMock()
        mock_context.return_value = mock_ctx

        from app.tasks.signals import _generate_signals_for_strategy

        strategy_config = {
            "id": 1,
            "name": "test_strategy",
            "strategy_type": "momentum",
            "params": {"lookback_months": 6},
            "universe": {"fund_codes": ["000001", "110011"]},
        }

        signals = _generate_signals_for_strategy(strategy_config, date(2024, 6, 15))

        assert len(signals) == 2
        assert signals[0]["fund_code"] == "000001"
        assert signals[0]["direction"] == "subscribe"
        assert signals[0]["amount"] == 10000.0
        assert signals[0]["target_weight"] == 0.5
        assert signals[0]["strategy_id"] == 1
        assert signals[0]["strategy_name"] == "test_strategy"
        assert signals[0]["signal_date"] == date(2024, 6, 15)

        assert signals[1]["fund_code"] == "110011"
        assert signals[1]["direction"] == "redeem"
        assert signals[1]["shares"] == 500.0

    @patch("app.tasks.signals._build_signal_context")
    @patch("app.domain.strategy.base.create_strategy_from_config")
    def test_returns_empty_when_no_context(self, mock_create, mock_context):
        """Returns empty list when context cannot be built."""
        mock_strategy = MagicMock()
        mock_create.return_value = mock_strategy
        mock_context.return_value = None

        from app.tasks.signals import _generate_signals_for_strategy

        strategy_config = {
            "id": 1,
            "name": "test",
            "strategy_type": "momentum",
            "params": {},
            "universe": {"fund_codes": []},
        }

        signals = _generate_signals_for_strategy(strategy_config, date(2024, 6, 15))
        assert signals == []

    @patch("app.tasks.signals._build_signal_context")
    @patch("app.domain.strategy.base.create_strategy_from_config")
    def test_returns_empty_on_strategy_error(self, mock_create, mock_context):
        """Returns empty list when strategy raises an error."""
        mock_create.side_effect = ValueError("Unknown strategy type")

        from app.tasks.signals import _generate_signals_for_strategy

        strategy_config = {
            "id": 1,
            "name": "bad",
            "strategy_type": "nonexistent",
            "params": {},
            "universe": {"fund_codes": []},
        }

        signals = _generate_signals_for_strategy(strategy_config, date(2024, 6, 15))
        assert signals == []

    @patch("app.tasks.signals._build_signal_context")
    @patch("app.domain.strategy.base.create_strategy_from_config")
    def test_returns_empty_when_strategy_returns_no_intents(
        self, mock_create, mock_context
    ):
        """Returns empty list when strategy generates no order intents."""
        mock_strategy = MagicMock()
        mock_strategy.on_bar.return_value = []
        mock_create.return_value = mock_strategy
        mock_context.return_value = MagicMock()

        from app.tasks.signals import _generate_signals_for_strategy

        strategy_config = {
            "id": 1,
            "name": "passive",
            "strategy_type": "dca",
            "params": {},
            "universe": {"fund_codes": ["000001"]},
        }

        signals = _generate_signals_for_strategy(strategy_config, date(2024, 6, 15))
        assert signals == []


# ---------------------------------------------------------------------------
# _send_notifications Tests
# ---------------------------------------------------------------------------


class TestSendNotifications:
    """Tests for the notification dispatch function."""

    def test_empty_signals_returns_zero(self):
        """Empty signal list returns zero notification counts."""
        from app.tasks.signals import _send_notifications

        result = _send_notifications([])
        assert result["total"] == 0
        assert result["sent"] == 0

    def test_sends_notifications_for_signals(self):
        """Notifications are created for each signal (no channels = all fail)."""
        from app.tasks.signals import _send_notifications

        signals = [
            {
                "strategy_id": 1,
                "strategy_name": "momentum",
                "fund_code": "000001",
                "signal_date": date(2024, 6, 15),
                "direction": "subscribe",
                "strength": 0.8,
                "target_weight": 0.3,
                "reason": "Top-3 momentum",
            },
            {
                "strategy_id": 1,
                "strategy_name": "momentum",
                "fund_code": "110011",
                "signal_date": date(2024, 6, 15),
                "direction": "redeem",
                "strength": None,
                "target_weight": None,
                "reason": "Dropped from top-3",
            },
        ]

        result = _send_notifications(signals)
        assert result["total"] == 2
        # No channels configured in test env, so all fail
        assert result["sent"] == 0
        assert result["failed"] == 2


# ---------------------------------------------------------------------------
# Integration-style test with mocked DB
# ---------------------------------------------------------------------------


class TestStoreSignals:
    """Tests for signal storage function."""

    def test_empty_signals_returns_zero(self):
        """Storing empty list returns empty list."""
        from app.tasks.signals import _store_signals

        result = _store_signals([])
        assert result == []

    def test_deduplicate_signals_within_batch(self):
        """批内重复信号应被去重。"""
        from app.tasks.signals import _deduplicate_signals

        signals = [
            {
                "strategy_id": 1,
                "strategy_name": "test",
                "fund_code": "000001",
                "signal_date": date(2024, 6, 15),
                "direction": "subscribe",
            },
            {
                "strategy_id": 1,
                "strategy_name": "test",
                "fund_code": "000001",
                "signal_date": date(2024, 6, 15),
                "direction": "subscribe",
            },
            {
                "strategy_id": 1,
                "strategy_name": "test",
                "fund_code": "000001",
                "signal_date": date(2024, 6, 15),
                "direction": "redeem",
            },
        ]

        result = _deduplicate_signals(signals)
        assert len(result) == 2

    def test_filter_existing_signals_skips_already_stored(self, monkeypatch):
        """数据库已存在的信号应被过滤掉。"""
        from app.core.config import get_settings
        from app.tasks.signals import _filter_existing_signals

        settings = get_settings()
        engine = create_engine(settings.database_sync_url)
        try:
            with Session(engine) as session:
                existing = Signal(
                    strategy_id=1,
                    strategy_name="test",
                    fund_code="000001",
                    signal_date=date(2024, 6, 15),
                    direction="subscribe",
                    notified=False,
                )
                session.add(existing)
                session.commit()

                signals = [
                    {
                        "strategy_id": 1,
                        "strategy_name": "test",
                        "fund_code": "000001",
                        "signal_date": date(2024, 6, 15),
                        "direction": "subscribe",
                    },
                    {
                        "strategy_id": 1,
                        "strategy_name": "test",
                        "fund_code": "000002",
                        "signal_date": date(2024, 6, 15),
                        "direction": "subscribe",
                    },
                ]
                result = _filter_existing_signals(session, signals)
                assert len(result) == 1
                assert result[0]["fund_code"] == "000002"
        finally:
            engine.dispose()
