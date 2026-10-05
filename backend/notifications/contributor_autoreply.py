from __future__ import annotations

import logging
from pathlib import Path
from typing import Mapping, Optional

try:
    from backend.notifications.email_config import (
        ContributorAutoReplyConfig,
        load_contributor_email_config,
    )
    from backend.notifications.email_sender import (
        EmailMessagePayload,
        EmailSendResult,
        SmtpEmailSender,
    )
except Exception:
    from notifications.email_config import (
        ContributorAutoReplyConfig,
        load_contributor_email_config,
    )
    from notifications.email_sender import (
        EmailMessagePayload,
        EmailSendResult,
        SmtpEmailSender,
    )


logger = logging.getLogger("contributor_autoreply")

DEFAULT_AUTOREPLY_SUBJECT = "Thank you for your interest in contributing to Sign2Text"
DEFAULT_AUTOREPLY_BODY = (
    "Thank you for your interest in contributing to Sign2Text.\n\n"
    "We received your submission and will get back to you shortly."
)


def _mask_email(email: str) -> str:
    parts = str(email or "").split("@")
    if len(parts) != 2:
        return "***@***.***"
    user, domain = parts
    if not user:
        user_masked = "***"
    elif len(user) <= 2:
        user_masked = user[0] + "***"
    else:
        user_masked = user[0] + "***" + user[-1]
    return f"{user_masked}@{domain}"


def build_contributor_acknowledgement(
    recipient_email: str,
) -> EmailMessagePayload:
    return EmailMessagePayload(
        to_email=recipient_email,
        subject=DEFAULT_AUTOREPLY_SUBJECT,
        text_body=DEFAULT_AUTOREPLY_BODY,
    )


class ContributorAutoReplyService:
    def __init__(
        self,
        config: ContributorAutoReplyConfig,
        sender: Optional[SmtpEmailSender] = None,
    ) -> None:
        self._config = config
        self._sender = sender or SmtpEmailSender(config)

    @classmethod
    def from_env(
        cls,
        environ: Optional[Mapping[str, str]] = None,
        project_root: Optional[Path] = None,
    ) -> "ContributorAutoReplyService":
        config = load_contributor_email_config(
            environ=environ,
            project_root=project_root,
        )
        return cls(config=config)

    def send_acknowledgement(self, recipient_email: str) -> EmailSendResult:
        masked_email = _mask_email(recipient_email)

        if not self._config.enabled:
            logger.info(
                "Contributor auto-reply skipped for %s: %s",
                masked_email,
                self._config.disabled_reason or "autoreply_disabled",
            )
            return EmailSendResult(
                status="skipped",
                detail=self._config.disabled_reason or "autoreply_disabled",
            )

        if not self._config.is_ready:
            logger.warning(
                "Contributor auto-reply skipped for %s: %s",
                masked_email,
                self._config.disabled_reason or "smtp_not_configured",
            )
            return EmailSendResult(
                status="skipped",
                detail=self._config.disabled_reason or "smtp_not_configured",
            )

        result = self._sender.send(
            build_contributor_acknowledgement(recipient_email)
        )
        if result.status == "sent":
            logger.info("Contributor auto-reply sent for %s", masked_email)
        elif result.status == "failed":
            logger.warning(
                "Contributor auto-reply failed for %s: %s",
                masked_email,
                result.detail,
            )
        else:
            logger.info(
                "Contributor auto-reply skipped for %s: %s",
                masked_email,
                result.detail,
            )
        return result
