"""邮件通知通道。

通过 SMTP 发送邮件通知。支持 TLS 加密连接。

环境变量:
    NOTIFY_EMAIL_SMTP_HOST: SMTP 服务器地址
    NOTIFY_EMAIL_SMTP_PORT: SMTP 端口（默认 587）
    NOTIFY_EMAIL_FROM: 发件人地址
    NOTIFY_EMAIL_TO: 收件人地址（多个用逗号分隔）
    NOTIFY_EMAIL_PASSWORD: SMTP 认证密码（可选）

Requirements: 8.3, 8.4
"""

from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass
class EmailConfig:
    """邮件通道配置。"""

    smtp_host: str
    smtp_port: int
    from_addr: str
    to_addrs: list[str]
    password: str | None = None
    use_tls: bool = True

    @classmethod
    def from_env(cls) -> EmailConfig:
        """从环境变量加载配置。"""
        to_raw = os.environ.get("NOTIFY_EMAIL_TO", "")
        to_addrs = [addr.strip() for addr in to_raw.split(",") if addr.strip()]
        return cls(
            smtp_host=os.environ.get("NOTIFY_EMAIL_SMTP_HOST", ""),
            smtp_port=int(os.environ.get("NOTIFY_EMAIL_SMTP_PORT", "587")),
            from_addr=os.environ.get("NOTIFY_EMAIL_FROM", ""),
            to_addrs=to_addrs,
            password=os.environ.get("NOTIFY_EMAIL_PASSWORD"),
            use_tls=os.environ.get("NOTIFY_EMAIL_USE_TLS", "true").lower() == "true",
        )


@dataclass
class EmailResult:
    """邮件发送结果。"""

    success: bool
    error: str | None = None


class EmailChannel:
    """邮件通知通道。

    使用 smtplib 发送邮件通知。支持 TLS 加密和密码认证。
    """

    def __init__(self, config: EmailConfig | None = None) -> None:
        self.config = config or EmailConfig.from_env()

    def is_configured(self) -> bool:
        """检查通道是否已正确配置。"""
        return bool(
            self.config.smtp_host
            and self.config.from_addr
            and self.config.to_addrs
        )

    def send(self, subject: str, body: str, html: bool = False) -> EmailResult:
        """发送邮件。

        Args:
            subject: 邮件主题
            body: 邮件正文
            html: 是否为 HTML 格式

        Returns:
            EmailResult 发送结果
        """
        if not self.is_configured():
            return EmailResult(success=False, error="Email channel not configured")

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = self.config.from_addr
            msg["To"] = ", ".join(self.config.to_addrs)

            content_type = "html" if html else "plain"
            msg.attach(MIMEText(body, content_type, "utf-8"))

            with smtplib.SMTP(self.config.smtp_host, self.config.smtp_port) as server:
                if self.config.use_tls:
                    server.starttls()
                if self.config.password:
                    server.login(self.config.from_addr, self.config.password)
                server.sendmail(
                    self.config.from_addr,
                    self.config.to_addrs,
                    msg.as_string(),
                )

            log.info(
                "notify.email.sent",
                subject=subject,
                to=self.config.to_addrs,
            )
            return EmailResult(success=True)

        except Exception as exc:
            log.error(
                "notify.email.failed",
                subject=subject,
                error=str(exc),
            )
            return EmailResult(success=False, error=str(exc))

    def format_signal_message(
        self,
        strategy_name: str,
        fund_code: str,
        direction: str,
        signal_date: str,
        reason: str | None = None,
    ) -> tuple[str, str]:
        """格式化信号通知为邮件内容。

        Returns:
            (subject, body) 元组
        """
        direction_cn = {"subscribe": "申购", "redeem": "赎回", "hold": "持有"}.get(
            direction, direction
        )
        subject = f"[基金信号] {strategy_name} - {fund_code} {direction_cn}"
        lines = [
            f"策略: {strategy_name}",
            f"基金: {fund_code}",
            f"方向: {direction_cn}",
            f"日期: {signal_date}",
        ]
        if reason:
            lines.append(f"原因: {reason}")
        body = "\n".join(lines)
        return subject, body
