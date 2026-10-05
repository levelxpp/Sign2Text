from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr

try:
    from backend.notifications.email_config import ContributorAutoReplyConfig
except Exception:
    from notifications.email_config import ContributorAutoReplyConfig


DEFAULT_SMTP_TIMEOUT_SEC = 15.0


@dataclass(frozen=True)
class EmailMessagePayload:
    to_email: str
    subject: str
    text_body: str


@dataclass(frozen=True)
class EmailSendResult:
    status: str
    detail: str = ""


class SmtpEmailSender:
    """Small SMTP sender with safe failure behavior for optional notifications."""

    def __init__(
        self,
        config: ContributorAutoReplyConfig,
        timeout_sec: float = DEFAULT_SMTP_TIMEOUT_SEC,
    ) -> None:
        self._config = config
        self._timeout_sec = timeout_sec

    def send(self, payload: EmailMessagePayload) -> EmailSendResult:
        if not self._config.is_ready:
            return EmailSendResult(
                status="skipped",
                detail=self._config.disabled_reason or "smtp_not_configured",
            )

        message = EmailMessage()
        message["From"] = formataddr(
            (self._config.from_name, self._config.from_email)
        )
        message["To"] = payload.to_email
        message["Subject"] = payload.subject
        message.set_content(payload.text_body)

        try:
            with smtplib.SMTP(
                self._config.smtp_host,
                self._config.smtp_port,
                timeout=self._timeout_sec,
            ) as server:
                server.ehlo()
                if self._config.smtp_use_tls:
                    server.starttls(context=ssl.create_default_context())
                    server.ehlo()
                if self._config.smtp_username and self._config.smtp_password:
                    server.login(
                        self._config.smtp_username,
                        self._config.smtp_password,
                    )
                server.send_message(message)
        except Exception as exc:
            return EmailSendResult(
                status="failed",
                detail=f"{type(exc).__name__}: email_send_failed",
            )

        return EmailSendResult(status="sent", detail="email_sent")
