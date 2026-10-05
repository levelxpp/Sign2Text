"""Tests for the one-time SQLite-to-Supabase migration utility.

All Supabase calls are mocked — no real network I/O.
SQLite fixtures use tmp_path on-disk databases with the same schema as
admin_server.py.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.supabase_client import SupabaseClient
from tools.migrate_admin_sqlite_to_supabase import (
    main,
    migrate_contributor_leads,
    migrate_feedback,
    read_contributor_leads,
    read_feedback,
)

# ── SQLite schema (mirrors admin_server.py) ──────────────────────────────

CREATE_LEADS = """
CREATE TABLE IF NOT EXISTS contributor_leads (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    email      TEXT    NOT NULL,
    source     TEXT    NOT NULL DEFAULT 'landing_contribute',
    status     TEXT    NOT NULL DEFAULT 'new',
    notes      TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL
);
"""

CREATE_FEEDBACK = """
CREATE TABLE IF NOT EXISTS feedback (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL DEFAULT '',
    message    TEXT    NOT NULL DEFAULT '',
    source     TEXT    NOT NULL DEFAULT 'home',
    created_at TEXT    NOT NULL
);
"""


@pytest.fixture
def sample_db(tmp_path):
    """Create a temporary SQLite database with sample data."""
    db_path = tmp_path / "admin.sqlite3"
    conn = sqlite3.connect(str(db_path))
    conn.execute(CREATE_LEADS)
    conn.execute(CREATE_FEEDBACK)
    conn.executemany(
        "INSERT INTO contributor_leads (email, source, status, notes, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("alice@example.com", "landing_contribute", "new", "", "2026-04-01T10:00:00Z", "2026-04-01T10:00:00Z"),
            ("bob@example.com", "landing_contribute", "contacted", "called back", "2026-04-02T11:00:00Z", "2026-04-03T09:00:00Z"),
            ("carol@example.com", "landing_contribute", "archived", "not interested", "2026-04-03T12:00:00Z", "2026-04-04T08:00:00Z"),
        ],
    )
    conn.executemany(
        "INSERT INTO feedback (name, message, source, created_at) VALUES (?, ?, ?, ?)",
        [
            ("Dave", "Great project!", "home", "2026-04-01T14:00:00Z"),
            ("", "Needs dark mode", "home", "2026-04-02T15:00:00Z"),
        ],
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def mock_client():
    return MagicMock(spec=SupabaseClient)


# ── SQLite readers ───────────────────────────────���───────────────────────


class TestReadContributorLeads:
    def test_reads_all_rows(self, sample_db):
        rows = read_contributor_leads(sample_db)
        assert len(rows) == 3
        assert rows[0]["email"] == "alice@example.com"
        assert rows[2]["email"] == "carol@example.com"

    def test_empty_table(self, tmp_path):
        db_path = tmp_path / "empty.sqlite3"
        conn = sqlite3.connect(str(db_path))
        conn.execute(CREATE_LEADS)
        conn.commit()
        conn.close()
        assert read_contributor_leads(db_path) == []


class TestReadFeedback:
    def test_reads_all_rows(self, sample_db):
        rows = read_feedback(sample_db)
        assert len(rows) == 2
        assert rows[0]["name"] == "Dave"


# ── Contributor leads migration ───────��──────────────────────────────────


class TestMigrateContributorLeads:
    def test_migrates_all_rows(self, sample_db, mock_client):
        rows = read_contributor_leads(sample_db)
        mock_client.upsert.return_value = {}
        stats = migrate_contributor_leads(rows, mock_client, dry_run=False)
        assert stats["read"] == 3
        assert stats["migrated"] == 3
        assert stats["failed"] == 0
        assert mock_client.upsert.call_count == 3

    def test_upsert_idempotent(self, sample_db, mock_client):
        """Running migration twice succeeds — upsert merges duplicates."""
        rows = read_contributor_leads(sample_db)
        mock_client.upsert.return_value = {}
        stats1 = migrate_contributor_leads(rows, mock_client, dry_run=False)
        stats2 = migrate_contributor_leads(rows, mock_client, dry_run=False)
        assert stats1["migrated"] == 3
        assert stats2["migrated"] == 3
        assert stats1["failed"] == 0
        assert stats2["failed"] == 0

    def test_preserves_all_fields(self, sample_db, mock_client):
        rows = read_contributor_leads(sample_db)
        mock_client.upsert.return_value = {}
        migrate_contributor_leads(rows, mock_client, dry_run=False)
        call_args = mock_client.upsert.call_args_list[1]
        data = call_args[0][1]
        assert data["email"] == "bob@example.com"
        assert data["source"] == "landing_contribute"
        assert data["status"] == "contacted"
        assert data["notes"] == "called back"
        assert data["created_at"] == "2026-04-02T11:00:00Z"
        assert data["updated_at"] == "2026-04-03T09:00:00Z"
        assert call_args[1].get("on_conflict") == "email" or call_args[0][2] == "email"

    def test_dry_run_no_writes(self, sample_db, mock_client):
        rows = read_contributor_leads(sample_db)
        stats = migrate_contributor_leads(rows, mock_client, dry_run=True)
        assert stats["read"] == 3
        assert stats["migrated"] == 3
        mock_client.upsert.assert_not_called()

    def test_counts_failures(self, sample_db, mock_client):
        rows = read_contributor_leads(sample_db)
        mock_client.upsert.side_effect = RuntimeError("network error")
        stats = migrate_contributor_leads(rows, mock_client, dry_run=False)
        assert stats["failed"] == 3
        assert stats["migrated"] == 0


# ── Feedback migration ───────────────────────────────────────────────────


class TestMigrateFeedback:
    def test_migrates_all_rows(self, sample_db, mock_client):
        rows = read_feedback(sample_db)
        mock_client.select.return_value = []
        mock_client.insert.return_value = {}
        stats = migrate_feedback(rows, mock_client, dry_run=False)
        assert stats["read"] == 2
        assert stats["migrated"] == 2
        assert stats["skipped"] == 0
        assert stats["failed"] == 0

    def test_preserves_feedback_fields(self, sample_db, mock_client):
        rows = read_feedback(sample_db)
        mock_client.select.return_value = []
        mock_client.insert.return_value = {}
        migrate_feedback(rows, mock_client, dry_run=False)
        data = mock_client.insert.call_args_list[0][0][1]
        assert data["name"] == "Dave"
        assert data["message"] == "Great project!"
        assert data["source"] == "home"
        assert data["created_at"] == "2026-04-01T14:00:00Z"

    def test_skips_existing_feedback(self, sample_db, mock_client):
        rows = read_feedback(sample_db)
        mock_client.select.return_value = [{"id": 1}]
        stats = migrate_feedback(rows, mock_client, dry_run=False)
        assert stats["skipped"] == 2
        assert stats["migrated"] == 0
        mock_client.insert.assert_not_called()

    def test_dry_run_no_writes(self, sample_db, mock_client):
        rows = read_feedback(sample_db)
        stats = migrate_feedback(rows, mock_client, dry_run=True)
        assert stats["read"] == 2
        assert stats["migrated"] == 2
        mock_client.select.assert_not_called()
        mock_client.insert.assert_not_called()


# ── Fail-fast checks ────────────────────────────────────────────────────


class TestFailFast:
    def test_exits_when_supabase_not_enabled(self, monkeypatch):
        # Use setenv (not delenv) so os.environ overrides the .env file's
        # SUPABASE_ENABLED=true.  _load_runtime_env merges .env first,
        # then os.environ on top — delenv would let .env win.
        monkeypatch.setenv("SUPABASE_ENABLED", "false")
        monkeypatch.setenv("SUPABASE_URL", "")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
        with pytest.raises(SystemExit) as exc_info:
            main(["--db-path", "data/admin.sqlite3"])
        assert exc_info.value.code == 1

    def test_exits_when_sqlite_missing(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SUPABASE_ENABLED", "true")
        monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-value-1234")
        missing = tmp_path / "nonexistent.sqlite3"
        with pytest.raises(SystemExit) as exc_info:
            main(["--db-path", str(missing)])
        assert exc_info.value.code == 1
