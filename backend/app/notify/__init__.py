"""通知推送模块。

提供统一的通知服务接口，支持多通道推送（邮件、企业微信、Telegram）。
通过环境变量 NOTIFY_CHANNELS 配置启用的通道。

Requirements: 8.3, 8.4
"""

from __future__ import annotations

from app.notify.email import EmailChannel, EmailConfig
from app.notify.service import NotificationService, send_signal_notifications
from app.notify.telegram import TelegramChannel, TelegramConfig
from app.notify.wecom import WecomChannel, WecomConfig

__all__ = [
    "EmailChannel",
    "EmailConfig",
    "NotificationService",
    "TelegramChannel",
    "TelegramConfig",
    "WecomChannel",
    "WecomConfig",
    "send_signal_notifications",
]
