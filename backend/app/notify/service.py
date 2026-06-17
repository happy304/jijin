"""统一通知服务。

NotificationService 按用户配置路由通知到不同通道（邮件、企业微信、Telegram）。
通过环境变量 NOTIFY_CHANNELS 配置启用的通道（逗号分隔）。

Requirements: 8.3, 8.4
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger
from app.notify.email import EmailChannel, EmailConfig
from app.notify.telegram import TelegramChannel, TelegramConfig
from app.notify.wecom import WecomChannel, WecomConfig

log = get_logger(__name__)


@dataclass
class SignalNotification:
    """信号通知数据结构。

    Attributes:
        strategy_id: 策略 ID
        strategy_name: 策略名称
        fund_code: 基金代码
        direction: 信号方向 (subscribe/redeem/hold)
        signal_date: 信号日期
        strength: 信号强度
        target_weight: 目标权重
        reason: 信号原因
    """

    strategy_id: int
    strategy_name: str
    fund_code: str
    direction: str
    signal_date: str
    strength: float | None = None
    target_weight: float | None = None
    reason: str | None = None


@dataclass
class NotificationResult:
    """通知发送结果。

    Attributes:
        total: 总通知数
        sent: 成功发送数
        failed: 失败数
        channels_used: 使用的通道列表
        errors: 各通道的错误信息
    """

    total: int = 0
    sent: int = 0
    failed: int = 0
    channels_used: list[str] = field(default_factory=list)
    errors: dict[str, list[str]] = field(default_factory=dict)


class NotificationService:
    """统一通知服务。

    按用户配置路由通知到不同通道（邮件、企业微信、Telegram）。
    通过 NOTIFY_CHANNELS 环境变量控制启用的通道。
    """

    SUPPORTED_CHANNELS = ("email", "wecom", "telegram")

    def __init__(
        self,
        channels: list[str] | None = None,
        *,
        email_config: EmailConfig | None = None,
        wecom_config: WecomConfig | None = None,
        telegram_config: TelegramConfig | None = None,
    ) -> None:
        """初始化通知服务。

        Args:
            channels: 启用的通知通道列表。为 None 时从环境变量 NOTIFY_CHANNELS 读取。
            email_config: 邮件通道配置（可选，默认从环境变量加载）
            wecom_config: 企业微信通道配置（可选，默认从环境变量加载）
            telegram_config: Telegram 通道配置（可选，默认从环境变量加载）
        """
        if channels is None:
            raw = os.environ.get("NOTIFY_CHANNELS", "")
            channels = [ch.strip() for ch in raw.split(",") if ch.strip()]
        self.channels = [ch for ch in channels if ch in self.SUPPORTED_CHANNELS]

        # Initialize channel instances lazily based on configuration
        self._email: EmailChannel | None = None
        self._wecom: WecomChannel | None = None
        self._telegram: TelegramChannel | None = None

        if "email" in self.channels:
            self._email = EmailChannel(config=email_config)
        if "wecom" in self.channels:
            self._wecom = WecomChannel(config=wecom_config)
        if "telegram" in self.channels:
            self._telegram = TelegramChannel(config=telegram_config)

    def send(self, notifications: list[SignalNotification]) -> NotificationResult:
        """发送信号通知到所有已配置的通道。

        Args:
            notifications: 待发送的通知列表

        Returns:
            NotificationResult 发送结果摘要
        """
        result = NotificationResult(total=len(notifications))

        if not notifications:
            return result

        if not self.channels:
            # No channels configured — log and return
            log.warning("notify.no_channels", msg="No notification channels configured")
            for notification in notifications:
                log.info(
                    "notify.signal.skipped",
                    strategy_id=notification.strategy_id,
                    fund_code=notification.fund_code,
                    direction=notification.direction,
                )
            result.failed = len(notifications)
            return result

        for notification in notifications:
            sent_any = False
            for channel_name in self.channels:
                success = self._send_to_channel(channel_name, notification)
                if success:
                    sent_any = True
                    if channel_name not in result.channels_used:
                        result.channels_used.append(channel_name)
                else:
                    if channel_name not in result.errors:
                        result.errors[channel_name] = []
                    result.errors[channel_name].append(
                        f"{notification.fund_code}@{notification.signal_date}"
                    )

            if sent_any:
                result.sent += 1
            else:
                result.failed += 1

        log.info(
            "notify.batch_complete",
            total=result.total,
            sent=result.sent,
            failed=result.failed,
            channels=result.channels_used,
        )

        return result

    def _send_to_channel(self, channel_name: str, notification: SignalNotification) -> bool:
        """向指定通道发送单条通知。

        Returns:
            True if sent successfully, False otherwise.
        """
        if channel_name == "email" and self._email:
            return self._send_email(notification)
        elif channel_name == "wecom" and self._wecom:
            return self._send_wecom(notification)
        elif channel_name == "telegram" and self._telegram:
            return self._send_telegram(notification)
        return False

    def _send_email(self, notification: SignalNotification) -> bool:
        """通过邮件通道发送通知。"""
        assert self._email is not None
        subject, body = self._email.format_signal_message(
            strategy_name=notification.strategy_name,
            fund_code=notification.fund_code,
            direction=notification.direction,
            signal_date=notification.signal_date,
            reason=notification.reason,
        )
        result = self._email.send(subject=subject, body=body)
        return result.success

    def _send_wecom(self, notification: SignalNotification) -> bool:
        """通过企业微信通道发送通知。"""
        assert self._wecom is not None
        content = self._wecom.format_signal_message(
            strategy_name=notification.strategy_name,
            fund_code=notification.fund_code,
            direction=notification.direction,
            signal_date=notification.signal_date,
            reason=notification.reason,
        )
        result = self._wecom.send(content=content)
        return result.success

    def _send_telegram(self, notification: SignalNotification) -> bool:
        """通过 Telegram 通道发送通知。"""
        assert self._telegram is not None
        text = self._telegram.format_signal_message(
            strategy_name=notification.strategy_name,
            fund_code=notification.fund_code,
            direction=notification.direction,
            signal_date=notification.signal_date,
            reason=notification.reason,
        )
        result = self._telegram.send(text=text)
        return result.success


def send_signal_notifications(
    notifications: list[SignalNotification],
    channels: list[str] | None = None,
) -> NotificationResult:
    """便捷函数：发送信号通知。

    Args:
        notifications: 待发送的通知列表
        channels: 启用的通知通道列表

    Returns:
        NotificationResult 发送结果摘要
    """
    service = NotificationService(channels=channels)
    return service.send(notifications)
