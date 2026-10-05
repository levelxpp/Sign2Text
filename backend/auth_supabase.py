"""auth_supabase.py — Supabase JWT verification guards for Sign2Text services.

PUBLIC-REPOSITORY NOTE
----------------------
The implementation used in the private project was written by Stepan Karapetyan
and is not mine to publish. This file is an independently written,
interface-compatible replacement so that the services in this repository remain
importable and the test suite remains runnable.

It implements the same contract the services depend on:

    require_user(request)      -> dict   FastAPI dependency, raises 401
    get_user_from_ws(websocket)-> dict | None
    verify_jwt(token)          -> dict

Verification calls the Supabase ``GET /auth/v1/user`` endpoint, which is the
supported server-side approach when the project's JWKS endpoint is not exposed
at a standard path. Successful lookups are cached briefly so that a burst of
SSE/WebSocket reconnects does not produce one round-trip per request.

When ``SUPABASE_ENABLED`` is not truthy, auth is disabled and an anonymous
principal is returned — this is what allows the app to run locally without a
Supabase project.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Any, Dict, Optional, Tuple

import httpx
from fastapi import HTTPException, Request, WebSocket, status

logger = logging.getLogger("auth_supabase")

_TOKEN_CACHE_TTL_SECONDS = 60.0
_TOKEN_CACHE_MAX_ENTRIES = 500

# sha256(token) -> (payload, expiry_monotonic)
_token_cache: Dict[str, Tuple[Dict[str, Any], float]] = {}

_ANON_USER: Dict[str, Any] = {"sub": "anon", "email": None, "role": "anon"}

_TRUTHY = {"1", "true", "yes", "on"}


def _auth_disabled() -> bool:
    return os.environ.get("SUPABASE_ENABLED", "").strip().lower() not in _TRUTHY


def _cache_get(key: str) -> Optional[Dict[str, Any]]:
    entry = _token_cache.get(key)
    if not entry:
        return None
    payload, expires_at = entry
    if time.monotonic() >= expires_at:
        _token_cache.pop(key, None)
        return None
    return payload


def _cache_put(key: str, payload: Dict[str, Any]) -> None:
    _token_cache[key] = (payload, time.monotonic() + _TOKEN_CACHE_TTL_SECONDS)
    if len(_token_cache) > _TOKEN_CACHE_MAX_ENTRIES:
        now = time.monotonic()
        for stale in [k for k, (_, exp) in list(_token_cache.items()) if exp < now]:
            _token_cache.pop(stale, None)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def verify_jwt(token: str) -> Dict[str, Any]:
    """Validate a Supabase access token and return a normalized user payload.

    Raises HTTPException(401) for any failure — an unreachable auth service is
    treated as a rejection rather than an allow, so a network problem can never
    silently open a protected route.
    """
    cache_key = hashlib.sha256(token.encode()).hexdigest()
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    base_url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    if not base_url:
        raise _unauthorized("SUPABASE_URL is not configured.")

    headers = {"Authorization": f"Bearer {token}"}
    anon_key = os.environ.get("SUPABASE_ANON_KEY", "").strip()
    if anon_key:
        headers["apikey"] = anon_key

    try:
        response = httpx.get(
            f"{base_url}/auth/v1/user",
            headers=headers,
            timeout=10,
            follow_redirects=True,
        )
    except httpx.RequestError as exc:
        logger.warning("Supabase auth endpoint unreachable: %s", exc)
        raise _unauthorized(f"Auth service unreachable: {exc}")

    if response.status_code == status.HTTP_401_UNAUTHORIZED:
        raise _unauthorized("Invalid or expired token.")

    if response.status_code >= 400:
        logger.warning("Supabase auth returned HTTP %s", response.status_code)
        raise _unauthorized(f"Auth verification failed: HTTP {response.status_code}")

    user = response.json()
    payload: Dict[str, Any] = {
        "sub": user.get("id"),
        "email": user.get("email"),
        "role": user.get("role", "authenticated"),
    }
    _cache_put(cache_key, payload)
    return payload


def _extract_bearer(request: Request) -> Optional[str]:
    """Pull a token from the Authorization header, falling back to ?token=.

    The query-parameter fallback exists because the browser EventSource and
    WebSocket APIs cannot set custom headers.
    """
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[7:].strip() or None
    return request.query_params.get("token", "").strip() or None


def require_user(request: Request) -> Dict[str, Any]:
    """FastAPI dependency enforcing a valid Supabase session."""
    if _auth_disabled():
        return _ANON_USER

    token = _extract_bearer(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return verify_jwt(token)


def get_user_from_ws(websocket: WebSocket) -> Optional[Dict[str, Any]]:
    """Resolve the principal for a WebSocket connection, or None if unauthenticated.

    Returns None rather than raising: the caller decides whether to close the
    socket with a policy-violation code or downgrade the session.
    """
    if _auth_disabled():
        return _ANON_USER

    header = websocket.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        token = header[7:].strip()
    else:
        token = websocket.query_params.get("token", "").strip()

    if not token:
        return None

    try:
        return verify_jwt(token)
    except HTTPException:
        return None
