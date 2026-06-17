"""Telegram Bot 通知通道。

通过 Telegram Bot API 发送通知消息。

环境变量:
    NOTIFY_TELEGRAM_BOT_TOKEN: Telegram Bot Token
    NOTIFY_TELEGRAM_CHAT_ID: 目标 Chat ID

Requirements: 8.3, 8.4
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

from app.core.logging import get_logger

log = get_logger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"


@dataclass
class TelegramConfig:
    """Telegram 通道配置。"""

    bot_token: str
    chat_id: str

    @classmethod
    def from_env(cls) -> TelegramConfig:
        """从环境变量加载配置。"""
        return cls(
            bot_token=os.environ.get("NOTIFY_TELEGRAM_BOT_TOKEN", ""),
            chat_id=os.environ.get("NOTIFY_TELEGRAM_CHAT_ID", ""),
        )


@dataclass
class TelegramResult:
    """Telegram 发送结果。"""

    success: bool
    error: str | None = None


class TelegramChannel:
    """Telegram Bot 通知通道。

    通过 Telegram Bot API 的 sendMessage 接口发送消息。
    支持 Markdown 和 HTML 格式。
    """

    def __init__(self, config: TelegramConfig | None = None) -> None:
        self.config = config or TelegramConfig.from_env()

    def is_configured(self) -> bool:
        """检查通道是否已正确配置。"""
        return bool(self.config.bot_token and self.config.chat_id)

    def send(self, text: str, parse_mode: str = "Markdown") -> TelegramResult:
        """发送 Telegram 消息。

        Args:
            text: 消息文本
            parse_mode: 解析模式，"Markdown" 或 "HTML"

        Returns:
            TelegramResult 发送结果
        """
        if not self.is_configured():
            return TelegramResult(success=False, error="Telegram channel not configured")

        try:
            url = f"{TELEGRAM_API_BASE}/bot{self.config.bot_token}/sendMessage"
            payload = {
                "chat_id": self.config.chat_id,
                "text": text,
                "parse_mode": parse_mode,
            }

            response = httpx.post(url, json=payload, timeout=10.0)
            response.raise_for_status()

            data = response.json()
            if not data.get("ok", False):
                err_msg = data.get("description", "unknown error")
                log.error(
                    "notify.telegram.api_error",
                    error_code=data.get("error_code"),
                    description=err_msg,
                )
                return TelegramResult(success=False, error=err_msg)

            log.info("notify.telegram.sent", chat_id=self.config.chat_id)
            return TelegramResult(success=True)

        except Exception as exc:
            log.error("notify.telegram.failed", error=str(exc))
            return TelegramResult(success=False, error=str(exc))

    def format_signal_message(
        self,
        strategy_name: str,
        fund_code: str,
        direction: str,
        signal_date: str,
        reason: str | None = None,
    ) -> str:
        """格式化信号通知为 Telegram Markdown 消息。

        Returns:
            Markdown 格式的消息文本
        """
        direction_cn = {"subscribe": "申购", "redeem": "赎回", "hold": "持有"}.get(
            direction, direction
        )
        lines = [
            "📊 *基金信号通知*",
            "",
            f"策略: *{strategy_name}*",
            f"基金: `{fund_code}`",
            f"方向: {direction_cn}",
            f"日期: {signal_date}",
        ]
        if reason:
            lines.append(f"原因: {reason}")
        return "\n".join(lines)
