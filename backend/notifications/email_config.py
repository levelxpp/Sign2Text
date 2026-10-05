from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

try:
    from backend.secret_config import (
        apply_project_dotenv,
        read_bool_env,
        read_secret_value,
    )
except Exception:
    from secret_config import apply_project_dotenv, read_bool_env, read_secret_value


DEFAULT_CONTRIB_FROM_NAME = "Sign2Text Team"
DEFAULT_CONTRIB_SMTP_PORT = 587
DEFAULT_CONTRIB_SMTP_USE_TLS = True


@dataclass(frozen=True)
class ContributorAutoReplyConfig:
    enabled: bool
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_use_tls: bool
    from_email: str
    from_name: str
    is_ready: bool
    disabled_reason: str


def _read_smtp_port(source: Mapping[str, str]) -> int:
    raw = str(source.get("CONTRIB_SMTP_PORT", "")).strip()
    if not raw:
        return DEFAULT_CONTRIB_SMTP_PORT
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_CONTRIB_SMTP_PORT


def load_contributor_email_config(
    environ: Optional[Mapping[str, str]] = None,
    project_root: Optional[Path] = None,
) -> ContributorAutoReplyConfig:
    """
    Load contributor acknowledgement email settings from the environment.

    Dotenv loading is explicit and only happens when `environ` is not provided.
    This keeps module import side-effect free and makes tests easy to control.

    SMTP authentication and the visible From address are intentionally parsed
    separately. Some SMTP providers require the From address to match the
    authenticated account or an approved alias, so callers should treat
    mismatches as a deployment/configuration concern rather than assuming
    arbitrary From addresses are accepted.
    """

    if environ is None:
        apply_project_dotenv(project_root=project_root)
        source: Mapping[str, str] = os.environ
    else:
        source = environ

    enabled = read_bool_env(
        "CONTRIB_AUTOREPLY_ENABLED",
        default=False,
        environ=source,
    )
    smtp_host = str(source.get("CONTRIB_SMTP_HOST", "")).strip()
    smtp_port = _read_smtp_port(source)
    smtp_username = str(source.get("CONTRIB_SMTP_USERNAME", "")).strip()
    smtp_password = read_secret_value(
        "CONTRIB_SMTP_PASSWORD",
        default="",
        environ=source,
        project_root=project_root,
    ).strip()
    smtp_use_tls = read_bool_env(
        "CONTRIB_SMTP_USE_TLS",
        default=DEFAULT_CONTRIB_SMTP_USE_TLS,
        environ=source,
    )
    from_email = str(source.get("CONTRIB_FROM_EMAIL", "")).strip()
    from_name = (
        str(source.get("CONTRIB_FROM_NAME", "")).strip()
        or DEFAULT_CONTRIB_FROM_NAME
    )

    if not enabled:
        return ContributorAutoReplyConfig(
            enabled=enabled,
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            smtp_username=smtp_username,
            smtp_password=smtp_password,
            smtp_use_tls=smtp_use_tls,
            from_email=from_email,
            from_name=from_name,
            is_ready=False,
            disabled_reason="autoreply_disabled",
        )

    if not smtp_host:
        disabled_reason = "missing_smtp_host"
    elif not from_email:
        disabled_reason = "missing_from_email"
    elif bool(smtp_username) ^ bool(smtp_password):
        disabled_reason = "smtp_auth_incomplete"
    else:
        disabled_reason = ""

    return ContributorAutoReplyConfig(
        enabled=enabled,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_username=smtp_username,
        smtp_password=smtp_password,
        smtp_use_tls=smtp_use_tls,
        from_email=from_email,
        from_name=from_name,
        is_ready=not disabled_reason,
        disabled_reason=disabled_reason,
    )
