"""Repository abstractions for Supabase-backed persistence.

These classes define the data-access contract for profiles, contributor leads,
feedback, and transcript history.  They are thin PostgREST wrappers — no
migrations, no table creation, no startup validation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from backend.supabase_client import SupabaseClient


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_cutoff_iso(days: int) -> str:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return cutoff.isoformat()


# ── Profiles ─────────────────────────────────────────────────────────────


class ProfileRepo:
    """Upsert Google-authenticated users into the ``profiles`` table."""

    TABLE = "profiles"

    def __init__(self, client: SupabaseClient) -> None:
        self._client = client

    def upsert_from_google_user(
        self,
        google_sub: str,
        email: str,
        name: str,
        picture: str,
        email_verified: bool = False,
        last_login_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._client.upsert(
            self.TABLE,
            {
                "google_sub": google_sub,
                "email": email,
                "display_name": name,
                "avatar_url": picture,
                "email_verified": email_verified,
                "last_login_at": last_login_at or _utc_now_iso(),
                "updated_at": _utc_now_iso(),
            },
            on_conflict="google_sub",
        )


# ── Contributor Leads ────────────────────────────────────────────────────


class ContributorLeadRepo:
    """CRUD operations on the ``contributor_leads`` table."""

    TABLE = "contributor_leads"

    def __init__(self, client: SupabaseClient) -> None:
        self._client = client

    def insert(self, email: str, source: str) -> Dict[str, Any]:
        return self._client.insert(
            self.TABLE,
            {"email": email, "source": source},
        )

    def list_all(self) -> List[Dict[str, Any]]:
        return self._client.select(
            self.TABLE,
            {"order": "created_at.desc"},
        )

    def get_by_id(self, lead_id: int) -> Optional[Dict[str, Any]]:
        rows = self._client.select(self.TABLE, {"id": f"eq.{lead_id}"})
        return rows[0] if rows else None

    def update(self, lead_id: int, **fields: Any) -> Dict[str, Any]:
        rows = self._client.update(
            self.TABLE,
            {"id": f"eq.{lead_id}"},
            fields,
        )
        return rows[0] if rows else {}

    def delete(self, lead_id: int) -> bool:
        rows = self._client.delete(self.TABLE, {"id": f"eq.{lead_id}"})
        return len(rows) > 0

    def delete_all(self) -> int:
        # PostgREST requires a filter on DELETE; id > 0 matches all rows.
        rows = self._client.delete(self.TABLE, {"id": "gt.0"})
        return len(rows)

    def count(self) -> int:
        return self._client.head_count(self.TABLE)

    def count_by_status(self, status: str) -> int:
        return self._client.head_count(
            self.TABLE,
            {"status": f"eq.{status}"},
        )

    def count_recent(self, days: int = 7) -> int:
        return self._client.head_count(
            self.TABLE,
            {"created_at": f"gte.{_utc_cutoff_iso(days)}"},
        )

    def find_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        rows = self._client.select(self.TABLE, {"email": f"eq.{email}"})
        return rows[0] if rows else None


# ── Feedback ─────────────────────────────────────────────────────────────


class FeedbackRepo:
    """Read/write operations on the ``feedback`` table."""

    TABLE = "feedback"

    def __init__(self, client: SupabaseClient) -> None:
        self._client = client

    def insert(self, name: str, message: str, source: str) -> Dict[str, Any]:
        return self._client.insert(
            self.TABLE,
            {"name": name, "message": message, "source": source},
        )

    def list_all(self) -> List[Dict[str, Any]]:
        return self._client.select(
            self.TABLE,
            {"order": "created_at.desc"},
        )

    def count(self) -> int:
        return self._client.head_count(self.TABLE)

    def count_recent(self, days: int = 7) -> int:
        return self._client.head_count(
            self.TABLE,
            {"created_at": f"gte.{_utc_cutoff_iso(days)}"},
        )


# ── Transcripts ─────────────────────────────────────────────────────────


class TranscriptRepo:
    """CRUD operations on the ``transcripts`` table.

    Transcript ownership is enforced by ``google_sub``.  The upsert conflict
    key is ``(google_sub, client_history_id)`` so that the frontend can
    re-save the same history entry without creating duplicates.
    """

    TABLE = "transcripts"

    def __init__(self, client: SupabaseClient) -> None:
        self._client = client

    def upsert(
        self,
        google_sub: str,
        client_history_id: str,
        mode: str,
        started_at: Optional[str],
        text: str,
    ) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "google_sub": google_sub,
            "client_history_id": client_history_id,
            "mode": mode,
            "text": text,
            "updated_at": _utc_now_iso(),
        }
        if started_at is not None:
            data["started_at"] = started_at
        return self._client.upsert(
            self.TABLE,
            data,
            on_conflict="google_sub,client_history_id",
        )

    def list_by_user(
        self, google_sub: str, limit: int = 20
    ) -> List[Dict[str, Any]]:
        return self._client.select(
            self.TABLE,
            {
                "google_sub": f"eq.{google_sub}",
                "order": "created_at.desc",
                "limit": str(limit),
            },
        )

    def get_by_id(self, transcript_id: int) -> Optional[Dict[str, Any]]:
        rows = self._client.select(
            self.TABLE, {"id": f"eq.{transcript_id}"}
        )
        return rows[0] if rows else None

    def delete_by_id(self, transcript_id: int, google_sub: str) -> bool:
        rows = self._client.delete(
            self.TABLE,
            {"id": f"eq.{transcript_id}", "google_sub": f"eq.{google_sub}"},
        )
        return len(rows) > 0

    def delete_all_by_user(self, google_sub: str) -> int:
        rows = self._client.delete(
            self.TABLE,
            {"google_sub": f"eq.{google_sub}"},
        )
        return len(rows)

    def count_by_user(self, google_sub: str) -> int:
        return self._client.head_count(
            self.TABLE,
            {"google_sub": f"eq.{google_sub}"},
        )
