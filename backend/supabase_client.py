"""Backend-only Supabase client wrapping httpx for PostgREST access.

This module provides a thin, lazy client that talks to Supabase's PostgREST
API using the service-role key.  No network I/O occurs at import time or
during client construction — the underlying ``httpx.Client`` is created on
first use.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("supabase_client")


class SupabaseClient:
    """Lightweight PostgREST client for backend Supabase operations.

    The ``httpx.Client`` is created lazily on first request, not during
    ``__init__``, so constructing a ``SupabaseClient`` is side-effect free.
    """

    def __init__(
        self,
        base_url: str,
        service_role_key: str,
        schema: str = "public",
        timeout_sec: float = 10.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._service_role_key = service_role_key
        self._schema = schema
        self._timeout_sec = timeout_sec
        self._http: Optional[httpx.Client] = None

    # ── lazy httpx.Client ────────────────────────────────────────────────

    @property
    def _client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(
                base_url=f"{self._base_url}/rest/v1",
                headers={
                    "apikey": self._service_role_key,
                    "Authorization": f"Bearer {self._service_role_key}",
                    "Content-Type": "application/json",
                    "Accept-Profile": self._schema,
                    "Content-Profile": self._schema,
                    "Prefer": "return=representation",
                },
                timeout=httpx.Timeout(self._timeout_sec),
            )
        return self._http

    # ── PostgREST operations ─────────────────────────────────────────────

    def select(
        self, table: str, params: Optional[Dict[str, str]] = None
    ) -> List[Dict[str, Any]]:
        resp = self._client.get(f"/{table}", params=params or {})
        resp.raise_for_status()
        return resp.json()

    def insert(
        self, table: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        resp = self._client.post(f"/{table}", json=data)
        resp.raise_for_status()
        rows = resp.json()
        return rows[0] if rows else data

    def upsert(
        self,
        table: str,
        data: Dict[str, Any],
        on_conflict: str,
    ) -> Dict[str, Any]:
        resp = self._client.post(
            f"/{table}",
            json=data,
            params={"on_conflict": on_conflict},
            headers={
                "Prefer": "resolution=merge-duplicates,return=representation",
            },
        )
        resp.raise_for_status()
        rows = resp.json()
        return rows[0] if rows else data

    def update(
        self,
        table: str,
        match_params: Dict[str, str],
        data: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        resp = self._client.patch(f"/{table}", params=match_params, json=data)
        resp.raise_for_status()
        return resp.json()

    def delete(
        self, table: str, match_params: Dict[str, str]
    ) -> List[Dict[str, Any]]:
        resp = self._client.delete(f"/{table}", params=match_params)
        resp.raise_for_status()
        return resp.json()

    def head_count(
        self, table: str, params: Optional[Dict[str, str]] = None
    ) -> int:
        resp = self._client.head(
            f"/{table}",
            params=params or {},
            headers={"Prefer": "count=exact"},
        )
        resp.raise_for_status()
        content_range = resp.headers.get("content-range", "")
        # PostgREST format: "0-N/total" or "*/total"
        if "/" in content_range:
            total_str = content_range.rsplit("/", 1)[-1]
            try:
                return int(total_str)
            except ValueError:
                return 0
        return 0

    def rpc(
        self, function_name: str, params: Optional[Dict[str, Any]] = None
    ) -> Any:
        resp = self._client.post(f"/rpc/{function_name}", json=params or {})
        resp.raise_for_status()
        return resp.json()

    # ── lifecycle ─────────────────────────────────────────────────────────

    def close(self) -> None:
        if self._http is not None:
            self._http.close()
            self._http = None

    def __enter__(self) -> "SupabaseClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __repr__(self) -> str:
        key_hint = self._service_role_key[-4:] if self._service_role_key else ""
        return (
            f"SupabaseClient(url={self._base_url!r}, "
            f"schema={self._schema!r}, key=...{key_hint})"
        )

    __str__ = __repr__


# ── module-level helpers ─────────────────────────────────────────────────


def is_supabase_available(config: object) -> bool:
    """Return True if Supabase is enabled in the runtime config."""
    return bool(getattr(config, "supabase_enabled", False))


def get_supabase_client(config: object) -> Optional[SupabaseClient]:
    """Build a ``SupabaseClient`` from *config*, or return ``None`` when disabled.

    Raises ``RuntimeError`` if Supabase is enabled but required settings are
    missing.  No network I/O occurs — the returned client is lazy.
    """
    if not getattr(config, "supabase_enabled", False):
        return None

    url = getattr(config, "supabase_url", "") or ""
    key = getattr(config, "supabase_service_role_key", "") or ""
    schema = getattr(config, "supabase_schema", "public") or "public"
    timeout = float(getattr(config, "supabase_timeout_sec", 10.0) or 10.0)

    if not url:
        raise RuntimeError("SUPABASE_ENABLED is true but SUPABASE_URL is empty")
    if not key:
        raise RuntimeError(
            "SUPABASE_ENABLED is true but SUPABASE_SERVICE_ROLE_KEY is empty"
        )

    return SupabaseClient(
        base_url=url,
        service_role_key=key,
        schema=schema,
        timeout_sec=timeout,
    )
