from __future__ import annotations

import logging
import urllib.parse
from typing import Any, Dict, List, Optional


LOCALISH_HOSTS = {
    "127.0.0.1",
    "localhost",
    "0.0.0.0",
    "::1",
    "::",
}


def normalize_origin(value: Optional[str]) -> str:
    raw = str(value or "").strip().rstrip("/")
    if not raw:
        return ""
    try:
        parsed = urllib.parse.urlparse(raw)
    except Exception:
        return ""
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def is_localish_origin(origin: str) -> bool:
    try:
        host = (urllib.parse.urlparse(origin).hostname or "").strip().lower()
    except Exception:
        return False
    return host in LOCALISH_HOSTS


def local_frontend_origin_regex(frontend_port: int) -> str:
    return rf"^https?://[^/]+:{frontend_port}$"


def local_frontend_origins(frontend_port: int) -> List[str]:
    return [
        f"http://127.0.0.1:{frontend_port}",
        f"http://localhost:{frontend_port}",
        f"http://[::]:{frontend_port}",
        f"http://[::1]:{frontend_port}",
    ]


def configured_browser_origin(
    *,
    frontend_origin: str = "",
    frontend_public_url: str = "",
    frontend_public_url_explicit: bool = False,
    public_host: str = "",
    public_scheme: str = "http",
    public_host_explicit: bool = False,
    same_origin_base_url: str = "",
) -> str:
    explicit_origin = normalize_origin(frontend_origin)
    if explicit_origin:
        return explicit_origin

    if frontend_public_url_explicit:
        public_origin = normalize_origin(frontend_public_url)
        if public_origin:
            return public_origin

    base_url = str(same_origin_base_url or "").strip()
    if public_host_explicit and base_url.startswith("/"):
        host = str(public_host or "").strip()
        scheme = str(public_scheme or "http").strip().lower() or "http"
        if host:
            return f"{scheme}://{host}"

    return ""


def join_url_path(base: str, path: str) -> str:
    normalized_base = str(base or "").rstrip("/")
    normalized_path = "/" + str(path or "").lstrip("/")
    if not normalized_base:
        return normalized_path
    return f"{normalized_base}{normalized_path}"


def callback_url_for_base(base_url: str, *, origin: str = "") -> str:
    raw = str(base_url or "").strip()
    if not raw:
        return ""

    if raw.startswith("/"):
        base_origin = normalize_origin(origin)
        if not base_origin:
            return ""
        return join_url_path(join_url_path(base_origin, raw), "/auth/callback")

    parsed = urllib.parse.urlparse(raw.rstrip("/"))
    if not parsed.scheme or not parsed.netloc:
        return ""

    base_origin = f"{parsed.scheme}://{parsed.netloc}"
    base_path = parsed.path.rstrip("/")
    callback_path = f"{base_path}/auth/callback" if base_path else "/auth/callback"
    return join_url_path(base_origin, callback_path)


def build_cors_settings(
    *,
    explicit_origins: Optional[str],
    frontend_port: int,
    allow_credentials: bool,
    fallback_origin: str = "",
    log_label: str = "CORS",
) -> Dict[str, Any]:
    logger = logging.getLogger(log_label)
    raw = str(explicit_origins or "").strip()
    allow_origin_regex: Optional[str] = None

    if raw and raw != "*":
        allow_origins = []
        for item in raw.split(","):
            stripped = item.strip()
            if not stripped:
                continue
            allow_origins.append(normalize_origin(stripped) or stripped.rstrip("/"))
    else:
        allow_origins = []
        if raw == "*":
            logger.warning(
                "%s set to '*' - restricting to explicit deployment origin or local frontend loopback.",
                log_label,
            )
        if fallback_origin:
            allow_origins = [fallback_origin]
        else:
            allow_origins = local_frontend_origins(frontend_port)
            logger.warning(
                "%s not explicitly configured. Restricting CORS to local frontend origins; set an explicit deployment origin for public hosting.",
                log_label,
            )

    if allow_origins and all(is_localish_origin(origin) for origin in allow_origins):
        allow_origin_regex = local_frontend_origin_regex(frontend_port)

    has_localhost = any(
        "localhost" in origin or "127.0.0.1" in origin for origin in allow_origins
    )
    if has_localhost:
        for extra in local_frontend_origins(frontend_port)[2:]:
            if extra not in allow_origins:
                allow_origins.append(extra)

    return {
        "allow_origins": allow_origins,
        "allow_origin_regex": allow_origin_regex,
        "allow_credentials": allow_credentials,
        "allow_methods": ["*"],
        "allow_headers": ["*"],
    }
