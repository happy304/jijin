"""企业微信 Webhook 通知通道。

通过企业微信群机器人 Webhook 发送通知。

环境变量:
    NOTIFY_WECOM_WEBHOOK_URL: 企业微信机器人 Webhook 地址

Requirements: 8.3, 8.4
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass
class WecomConfig:
    """企业微信通道配置。"""

    webhook_url: str

    @classmethod
    def from_env(cls) -> WecomConfig:
        """从环境变量加载配置。"""
        return cls(
            webhook_url=os.environ.get("NOTIFY_WECOM_WEBHOOK_URL", ""),
        )


@dataclass
class WecomResult:
    """企业微信发送结果。"""

    success: bool
    error: str | None = None


class WecomChannel:
    """企业微信 Webhook 通知通道。

    通过 HTTP POST 向企业微信群机器人 Webhook 发送 Markdown 或文本消息。
    """

    def __init__(self, config: WecomConfig | None = None) -> None:
        self.config = config or WecomConfig.from_env()

    def is_configured(self) -> bool:
        """检查通道是否已正确配置。"""
        return bool(self.config.webhook_url)

    def send(self, content: str, msg_type: str = "markdown") -> WecomResult:
        """发送企业微信消息。

        Args:
            content: 消息内容（支持 Markdown 格式）
            msg_type: 消息类型，"text" 或 "markdown"

        Returns:
            WecomResult 发送结果
        """
        if not self.is_configured():
            return WecomResult(success=False, error="WeCom channel not configured")

        try:
            if msg_type == "markdown":
                payload = {
                    "msgtype": "markdown",
                    "markdown": {"content": content},
                }
            else:
                payload = {
                    "msgtype": "text",
                    "text": {"content": content},
                }

            response = httpx.post(
                self.config.webhook_url,
                json=payload,
                timeout=10.0,
            )
            response.raise_for_status()

            data = response.json()
            if data.get("errcode", 0) != 0:
                err_msg = data.get("errmsg", "unknown error")
                log.error("notify.wecom.api_error", errcode=data.get("errcode"), errmsg=err_msg)
                return WecomResult(success=False, error=err_msg)

            log.info("notify.wecom.sent", msg_type=msg_type)
            return WecomResult(success=True)

        except Exception as exc:
            log.error("notify.wecom.failed", error=str(exc))
            return WecomResult(success=False, error=str(exc))

    def format_signal_message(
        self,
        strategy_name: str,
        fund_code: str,
        direction: str,
        signal_date: str,
        reason: str | None = None,
    ) -> str:
        """格式化信号通知为企业微信 Markdown 消息。

        Returns:
            Markdown 格式的消息内容
        """
        direction_cn = {"subscribe": "申购", "redeem": "赎回", "hold": "持有"}.get(
            direction, direction
        )
        lines = [
            f"## 📊 基金信号通知",
            f"> 策略: **{strategy_name}**",
            f"> 基金: **{fund_code}**",
            f"> 方向: <font color=\"{'info' if direction == 'subscribe' else 'warning'}\">"
            f"{direction_cn}</font>",
            f"> 日期: {signal_date}",
        ]
        if reason:
            lines.append(f"> 原因: {reason}")
        return "\n".join(lines)
