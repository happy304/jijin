"""告警通知模块单元测试。

测试邮件、企业微信、Telegram 三个通道以及统一 NotificationService 路由逻辑。
所有外部调用均使用 mock 替代。

Requirements: 8.3, 8.4
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.notify.email import EmailChannel, EmailConfig, EmailResult
from app.notify.service import NotificationResult, NotificationService, SignalNotification
from app.notify.telegram import TelegramChannel, TelegramConfig, TelegramResult
from app.notify.wecom import WecomChannel, WecomConfig, WecomResult


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def sample_notification() -> SignalNotification:
    """创建一个示例信号通知。"""
    return SignalNotification(
        strategy_id=1,
        strategy_name="动量轮动",
        fund_code="110011",
        direction="subscribe",
        signal_date="2024-01-15",
        strength=0.85,
        target_weight=0.33,
        reason="6个月动量排名前3",
    )


@pytest.fixture
def email_config() -> EmailConfig:
    """创建测试用邮件配置。"""
    return EmailConfig(
        smtp_host="smtp.example.com",
        smtp_port=587,
        from_addr="alert@example.com",
        to_addrs=["user@example.com"],
        password="test-password",
        use_tls=True,
    )


@pytest.fixture
def wecom_config() -> WecomConfig:
    """创建测试用企业微信配置。"""
    return WecomConfig(
        webhook_url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test-key",
    )


@pytest.fixture
def telegram_config() -> TelegramConfig:
    """创建测试用 Telegram 配置。"""
    return TelegramConfig(
        bot_token="123456:ABC-DEF",
        chat_id="-1001234567890",
    )


# ============================================================
# Email Channel Tests
# ============================================================


class TestEmailChannel:
    """邮件通道测试。"""

    def test_is_configured_true(self, email_config: EmailConfig) -> None:
        channel = EmailChannel(config=email_config)
        assert channel.is_configured() is True

    def test_is_configured_false_missing_host(self) -> None:
        config = EmailConfig(
            smtp_host="",
            smtp_port=587,
            from_addr="a@b.com",
            to_addrs=["c@d.com"],
        )
        channel = EmailChannel(config=config)
        assert channel.is_configured() is False

    def test_is_configured_false_missing_to(self) -> None:
        config = EmailConfig(
            smtp_host="smtp.example.com",
            smtp_port=587,
            from_addr="a@b.com",
            to_addrs=[],
        )
        channel = EmailChannel(config=config)
        assert channel.is_configured() is False

    def test_send_not_configured(self) -> None:
        config = EmailConfig(smtp_host="", smtp_port=587, from_addr="", to_addrs=[])
        channel = EmailChannel(config=config)
        result = channel.send(subject="Test", body="Hello")
        assert result.success is False
        assert result.error == "Email channel not configured"

    @patch("app.notify.email.smtplib.SMTP")
    def test_send_success(self, mock_smtp_cls: MagicMock, email_config: EmailConfig) -> None:
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        channel = EmailChannel(config=email_config)
        result = channel.send(subject="Test Signal", body="Buy fund 110011")

        assert result.success is True
        assert result.error is None
        mock_smtp_cls.assert_called_once_with("smtp.example.com", 587)
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once_with("alert@example.com", "test-password")
        mock_server.sendmail.assert_called_once()

    @patch("app.notify.email.smtplib.SMTP")
    def test_send_no_tls_no_password(self, mock_smtp_cls: MagicMock) -> None:
        config = EmailConfig(
            smtp_host="smtp.local",
            smtp_port=25,
            from_addr="a@b.com",
            to_addrs=["c@d.com"],
            password=None,
            use_tls=False,
        )
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        channel = EmailChannel(config=config)
        result = channel.send(subject="Test", body="Hello")

        assert result.success is True
        mock_server.starttls.assert_not_called()
        mock_server.login.assert_not_called()

    @patch("app.notify.email.smtplib.SMTP")
    def test_send_failure(self, mock_smtp_cls: MagicMock, email_config: EmailConfig) -> None:
        mock_smtp_cls.side_effect = ConnectionRefusedError("Connection refused")

        channel = EmailChannel(config=email_config)
        result = channel.send(subject="Test", body="Hello")

        assert result.success is False
        assert "Connection refused" in (result.error or "")

    def test_format_signal_message(self, email_config: EmailConfig) -> None:
        channel = EmailChannel(config=email_config)
        subject, body = channel.format_signal_message(
            strategy_name="动量轮动",
            fund_code="110011",
            direction="subscribe",
            signal_date="2024-01-15",
            reason="动量排名前3",
        )
        assert "动量轮动" in subject
        assert "110011" in subject
        assert "申购" in subject
        assert "动量排名前3" in body

    def test_format_signal_message_redeem(self, email_config: EmailConfig) -> None:
        channel = EmailChannel(config=email_config)
        subject, body = channel.format_signal_message(
            strategy_name="择时",
            fund_code="000001",
            direction="redeem",
            signal_date="2024-02-01",
        )
        assert "赎回" in subject


# ============================================================
# WeCom Channel Tests
# ============================================================


class TestWecomChannel:
    """企业微信通道测试。"""

    def test_is_configured_true(self, wecom_config: WecomConfig) -> None:
        channel = WecomChannel(config=wecom_config)
        assert channel.is_configured() is True

    def test_is_configured_false(self) -> None:
        config = WecomConfig(webhook_url="")
        channel = WecomChannel(config=config)
        assert channel.is_configured() is False

    def test_send_not_configured(self) -> None:
        config = WecomConfig(webhook_url="")
        channel = WecomChannel(config=config)
        result = channel.send(content="Hello")
        assert result.success is False
        assert result.error == "WeCom channel not configured"

    @patch("app.notify.wecom.httpx.post")
    def test_send_markdown_success(
        self, mock_post: MagicMock, wecom_config: WecomConfig
    ) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"errcode": 0, "errmsg": "ok"}
        mock_post.return_value = mock_response

        channel = WecomChannel(config=wecom_config)
        result = channel.send(content="## Test Message", msg_type="markdown")

        assert result.success is True
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert payload["msgtype"] == "markdown"
        assert payload["markdown"]["content"] == "## Test Message"

    @patch("app.notify.wecom.httpx.post")
    def test_send_text_success(self, mock_post: MagicMock, wecom_config: WecomConfig) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"errcode": 0, "errmsg": "ok"}
        mock_post.return_value = mock_response

        channel = WecomChannel(config=wecom_config)
        result = channel.send(content="Plain text", msg_type="text")

        assert result.success is True
        call_kwargs = mock_post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert payload["msgtype"] == "text"
        assert payload["text"]["content"] == "Plain text"

    @patch("app.notify.wecom.httpx.post")
    def test_send_api_error(self, mock_post: MagicMock, wecom_config: WecomConfig) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"errcode": 93000, "errmsg": "invalid webhook url"}
        mock_post.return_value = mock_response

        channel = WecomChannel(config=wecom_config)
        result = channel.send(content="Test")

        assert result.success is False
        assert "invalid webhook url" in (result.error or "")

    @patch("app.notify.wecom.httpx.post")
    def test_send_network_error(self, mock_post: MagicMock, wecom_config: WecomConfig) -> None:
        mock_post.side_effect = Exception("Connection timeout")

        channel = WecomChannel(config=wecom_config)
        result = channel.send(content="Test")

        assert result.success is False
        assert "Connection timeout" in (result.error or "")

    def test_format_signal_message(self, wecom_config: WecomConfig) -> None:
        channel = WecomChannel(config=wecom_config)
        content = channel.format_signal_message(
            strategy_name="风险平价",
            fund_code="000001",
            direction="subscribe",
            signal_date="2024-03-01",
            reason="再平衡触发",
        )
        assert "风险平价" in content
        assert "000001" in content
        assert "申购" in content
        assert "再平衡触发" in content


# ============================================================
# Telegram Channel Tests
# ============================================================


class TestTelegramChannel:
    """Telegram 通道测试。"""

    def test_is_configured_true(self, telegram_config: TelegramConfig) -> None:
        channel = TelegramChannel(config=telegram_config)
        assert channel.is_configured() is True

    def test_is_configured_false_missing_token(self) -> None:
        config = TelegramConfig(bot_token="", chat_id="123")
        channel = TelegramChannel(config=config)
        assert channel.is_configured() is False

    def test_is_configured_false_missing_chat_id(self) -> None:
        config = TelegramConfig(bot_token="token", chat_id="")
        channel = TelegramChannel(config=config)
        assert channel.is_configured() is False

    def test_send_not_configured(self) -> None:
        config = TelegramConfig(bot_token="", chat_id="")
        channel = TelegramChannel(config=config)
        result = channel.send(text="Hello")
        assert result.success is False
        assert result.error == "Telegram channel not configured"

    @patch("app.notify.telegram.httpx.post")
    def test_send_success(self, mock_post: MagicMock, telegram_config: TelegramConfig) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "ok": True,
            "result": {"message_id": 42},
        }
        mock_post.return_value = mock_response

        channel = TelegramChannel(config=telegram_config)
        result = channel.send(text="Test message")

        assert result.success is True
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        url = call_args[0][0] if call_args[0] else call_args.kwargs.get("url", "")
        assert "123456:ABC-DEF" in url
        assert "sendMessage" in url

    @patch("app.notify.telegram.httpx.post")
    def test_send_api_error(self, mock_post: MagicMock, telegram_config: TelegramConfig) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "ok": False,
            "error_code": 400,
            "description": "Bad Request: chat not found",
        }
        mock_post.return_value = mock_response

        channel = TelegramChannel(config=telegram_config)
        result = channel.send(text="Test")

        assert result.success is False
        assert "chat not found" in (result.error or "")

    @patch("app.notify.telegram.httpx.post")
    def test_send_network_error(
        self, mock_post: MagicMock, telegram_config: TelegramConfig
    ) -> None:
        mock_post.side_effect = Exception("Network unreachable")

        channel = TelegramChannel(config=telegram_config)
        result = channel.send(text="Test")

        assert result.success is False
        assert "Network unreachable" in (result.error or "")

    def test_format_signal_message(self, telegram_config: TelegramConfig) -> None:
        channel = TelegramChannel(config=telegram_config)
        text = channel.format_signal_message(
            strategy_name="定投策略",
            fund_code="519300",
            direction="subscribe",
            signal_date="2024-01-20",
            reason="定投日",
        )
        assert "定投策略" in text
        assert "519300" in text
        assert "申购" in text
        assert "定投日" in text

    def test_format_signal_message_redeem(self, telegram_config: TelegramConfig) -> None:
        channel = TelegramChannel(config=telegram_config)
        text = channel.format_signal_message(
            strategy_name="择时",
            fund_code="000001",
            direction="redeem",
            signal_date="2024-02-01",
        )
        assert "赎回" in text


# ============================================================
# NotificationService Tests
# ============================================================


class TestNotificationService:
    """统一通知服务测试。"""

    def test_empty_notifications(self) -> None:
        service = NotificationService(channels=["email"])
        result = service.send([])
        assert result.total == 0
        assert result.sent == 0
        assert result.failed == 0

    def test_no_channels_configured(self, sample_notification: SignalNotification) -> None:
        service = NotificationService(channels=[])
        result = service.send([sample_notification])
        assert result.total == 1
        assert result.sent == 0
        assert result.failed == 1

    def test_unsupported_channel_filtered(self) -> None:
        service = NotificationService(channels=["sms", "email", "unknown"])
        # Only "email" should remain
        assert service.channels == ["email"]

    @patch("app.notify.email.smtplib.SMTP")
    def test_route_to_email(
        self, mock_smtp_cls: MagicMock, sample_notification: SignalNotification, email_config: EmailConfig
    ) -> None:
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        service = NotificationService(channels=["email"], email_config=email_config)
        result = service.send([sample_notification])

        assert result.total == 1
        assert result.sent == 1
        assert result.failed == 0
        assert "email" in result.channels_used

    @patch("app.notify.wecom.httpx.post")
    def test_route_to_wecom(
        self, mock_post: MagicMock, sample_notification: SignalNotification, wecom_config: WecomConfig
    ) -> None:
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"errcode": 0, "errmsg": "ok"}
        mock_post.return_value = mock_response

        service = NotificationService(channels=["wecom"], wecom_config=wecom_config)
        result = service.send([sample_notification])

        assert result.total == 1
        assert result.sent == 1
        assert "wecom" in result.channels_used

    @patch("app.notify.telegram.httpx.post")
    def test_route_to_telegram(
        self, mock_post: MagicMock, sample_notification: SignalNotification, telegram_config: TelegramConfig
    ) -> None:
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"ok": True, "result": {"message_id": 1}}
        mock_post.return_value = mock_response

        service = NotificationService(channels=["telegram"], telegram_config=telegram_config)
        result = service.send([sample_notification])

        assert result.total == 1
        assert result.sent == 1
        assert "telegram" in result.channels_used

    @patch("app.notify.telegram.httpx.post")
    @patch("app.notify.wecom.httpx.post")
    @patch("app.notify.email.smtplib.SMTP")
    def test_route_to_multiple_channels(
        self,
        mock_smtp_cls: MagicMock,
        mock_wecom_post: MagicMock,
        mock_tg_post: MagicMock,
        sample_notification: SignalNotification,
        email_config: EmailConfig,
        wecom_config: WecomConfig,
        telegram_config: TelegramConfig,
    ) -> None:
        # Email mock
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        # WeCom mock
        wecom_resp = MagicMock()
        wecom_resp.raise_for_status = MagicMock()
        wecom_resp.json.return_value = {"errcode": 0, "errmsg": "ok"}
        mock_wecom_post.return_value = wecom_resp

        # Telegram mock
        tg_resp = MagicMock()
        tg_resp.raise_for_status = MagicMock()
        tg_resp.json.return_value = {"ok": True, "result": {"message_id": 1}}
        mock_tg_post.return_value = tg_resp

        service = NotificationService(
            channels=["email", "wecom", "telegram"],
            email_config=email_config,
            wecom_config=wecom_config,
            telegram_config=telegram_config,
        )
        result = service.send([sample_notification])

        assert result.total == 1
        assert result.sent == 1
        assert result.failed == 0
        assert set(result.channels_used) == {"email", "wecom", "telegram"}

    @patch("app.notify.email.smtplib.SMTP")
    def test_partial_failure_still_counts_as_sent(
        self,
        mock_smtp_cls: MagicMock,
        sample_notification: SignalNotification,
        email_config: EmailConfig,
        wecom_config: WecomConfig,
    ) -> None:
        """If at least one channel succeeds, the notification counts as sent."""
        # Email succeeds
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        # WeCom not configured (empty URL)
        bad_wecom = WecomConfig(webhook_url="")

        service = NotificationService(
            channels=["email", "wecom"],
            email_config=email_config,
            wecom_config=bad_wecom,
        )
        result = service.send([sample_notification])

        # WeCom is in channels list but not configured, so _send_to_channel returns False
        # But email succeeds, so notification counts as sent
        assert result.sent == 1

    @patch("app.notify.email.smtplib.SMTP")
    def test_multiple_notifications(
        self, mock_smtp_cls: MagicMock, email_config: EmailConfig
    ) -> None:
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        notifications = [
            SignalNotification(
                strategy_id=1,
                strategy_name="动量",
                fund_code=f"00000{i}",
                direction="subscribe",
                signal_date="2024-01-15",
            )
            for i in range(3)
        ]

        service = NotificationService(channels=["email"], email_config=email_config)
        result = service.send(notifications)

        assert result.total == 3
        assert result.sent == 3
        assert result.failed == 0

    def test_send_signal_notifications_convenience(self) -> None:
        """Test the convenience function with no channels (should not crash)."""
        from app.notify.service import send_signal_notifications

        notification = SignalNotification(
            strategy_id=1,
            strategy_name="Test",
            fund_code="000001",
            direction="hold",
            signal_date="2024-01-01",
        )
        # With empty channels, should return gracefully
        result = send_signal_notifications([notification], channels=[])
        assert result.total == 1
        assert result.failed == 1

    @patch("app.notify.email.smtplib.SMTP")
    def test_all_channels_fail(
        self,
        mock_smtp_cls: MagicMock,
        sample_notification: SignalNotification,
        email_config: EmailConfig,
    ) -> None:
        """When all channels fail, notification counts as failed."""
        mock_smtp_cls.side_effect = ConnectionRefusedError("refused")

        service = NotificationService(channels=["email"], email_config=email_config)
        result = service.send([sample_notification])

        assert result.total == 1
        assert result.sent == 0
        assert result.failed == 1


# ============================================================
# Config from_env Tests
# ============================================================


class TestConfigFromEnv:
    """测试从环境变量加载配置。"""

    def test_email_config_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NOTIFY_EMAIL_SMTP_HOST", "smtp.test.com")
        monkeypatch.setenv("NOTIFY_EMAIL_SMTP_PORT", "465")
        monkeypatch.setenv("NOTIFY_EMAIL_FROM", "sender@test.com")
        monkeypatch.setenv("NOTIFY_EMAIL_TO", "a@b.com, c@d.com")
        monkeypatch.setenv("NOTIFY_EMAIL_PASSWORD", "secret")

        config = EmailConfig.from_env()
        assert config.smtp_host == "smtp.test.com"
        assert config.smtp_port == 465
        assert config.from_addr == "sender@test.com"
        assert config.to_addrs == ["a@b.com", "c@d.com"]
        assert config.password == "secret"

    def test_wecom_config_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "NOTIFY_WECOM_WEBHOOK_URL",
            "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abc",
        )
        config = WecomConfig.from_env()
        assert "abc" in config.webhook_url

    def test_telegram_config_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NOTIFY_TELEGRAM_BOT_TOKEN", "111:AAA")
        monkeypatch.setenv("NOTIFY_TELEGRAM_CHAT_ID", "-100999")
        config = TelegramConfig.from_env()
        assert config.bot_token == "111:AAA"
        assert config.chat_id == "-100999"

    def test_notification_service_channels_from_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NOTIFY_CHANNELS", "email,telegram")
        service = NotificationService(channels=None)
        assert service.channels == ["email", "telegram"]

    def test_notification_service_empty_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NOTIFY_CHANNELS", "")
        service = NotificationService(channels=None)
        assert service.channels == []
