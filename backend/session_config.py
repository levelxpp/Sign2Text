"""
session_config.py — Shared session middleware configuration for all Sign2Text services.

Provides a single source of truth for SessionMiddleware settings so that
Voice, ASL, and Letter services decode the same session cookie identically.

IMPORTANT: For cross-service session sharing to work, all services MUST use
the same SESSION_SECRET.  The ephemeral dev fallback generates a random secret
per process and is NOT valid for multi-service auth.
"""

from __future__ import annotations

import logging
import os
import secrets
import urllib.parse
from typing import Any, Dict

from starlette.middleware.sessions import SessionMiddleware

try:
    from backend.secret_config import read_bool_env, read_secret_value
except ImportError:
    from secret_config import read_bool_env, read_secret_value

logger = logging.getLogger("session_config")


def resolve_session_config(
    *,
    browser_origin: str = "",
) -> Dict[str, Any]:
    """Read session-related env vars and return a middleware config dict.

    Parameters:
        browser_origin: The configured browser origin URL, used to auto-derive
            ``https_only`` when ``SESSION_COOKIE_SECURE`` is not explicitly set.

    Returns:
        Dict with keys: ``secret_key``, ``session_cookie``, ``same_site``,
        ``https_only``, ``domain``, ``uses_dev_fallback``.
    """
    session_cookie = (
        os.environ.get("SESSION_COOKIE_NAME", "").strip() or "sign2text_session"
    )
    domain = os.environ.get("SESSION_COOKIE_DOMAIN", "").strip() or None

    same_site = (
        os.environ.get("SESSION_COOKIE_SAMESITE", "").strip().lower() or "lax"
    )
    if same_site not in {"lax", "strict", "none"}:
        logger.warning(
            "Invalid SESSION_COOKIE_SAMESITE=%r. Falling back to 'lax'.",
            same_site,
        )
        same_site = "lax"

    secure_raw = os.environ.get("SESSION_COOKIE_SECURE", "").strip()
    if secure_raw:
        https_only = read_bool_env("SESSION_COOKIE_SECURE", default=False)
    else:
        https_only = bool(
            browser_origin
            and urllib.parse.urlparse(browser_origin).scheme == "https"
        )

    allow_dev_fallback = read_bool_env(
        "ALLOW_DEV_SESSION_SECRET_FALLBACK",
        default=True,
    )
    secret_key = read_secret_value("SESSION_SECRET")
    uses_dev_fallback = False

    if not secret_key:
        if not allow_dev_fallback:
            raise RuntimeError(
                "SESSION_SECRET is required when ALLOW_DEV_SESSION_SECRET_FALLBACK=0. "
                "Set SESSION_SECRET or SESSION_SECRET_FILE."
            )
        secret_key = secrets.token_urlsafe(32)
        uses_dev_fallback = True
        logger.warning(
            "SESSION_SECRET not set. Using an ephemeral dev fallback. "
            "Ephemeral session secret is not valid for multi-service auth. "
            "Set SESSION_SECRET for cross-service session sharing."
        )

    return {
        "secret_key": secret_key,
        "session_cookie": session_cookie,
        "same_site": same_site,
        "https_only": https_only,
        "domain": domain,
        "uses_dev_fallback": uses_dev_fallback,
    }


def add_session_middleware(
    app: Any,
    *,
    browser_origin: str = "",
) -> Dict[str, Any]:
    """Apply ``SessionMiddleware`` to *app* with shared session config.

    Returns the resolved config dict (useful for logging / health checks).
    """
    cfg = resolve_session_config(browser_origin=browser_origin)
    app.add_middleware(
        SessionMiddleware,
        secret_key=cfg["secret_key"],
        session_cookie=cfg["session_cookie"],
        same_site=cfg["same_site"],
        https_only=cfg["https_only"],
        domain=cfg["domain"],
    )
    return cfg
