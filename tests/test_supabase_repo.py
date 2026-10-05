from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest

from backend.supabase_client import SupabaseClient
from backend.supabase_repo import (
    ContributorLeadRepo,
    FeedbackRepo,
    ProfileRepo,
    TranscriptRepo,
)


@pytest.fixture
def mock_client():
    return MagicMock(spec=SupabaseClient)


# ── ProfileRepo ──────────────────────────────────────────────────────────


class TestProfileRepo:
    def test_upsert_from_google_user(self, mock_client):
        mock_client.upsert.return_value = {"google_sub": "sub123"}
        repo = ProfileRepo(mock_client)

        result = repo.upsert_from_google_user(
            google_sub="sub123",
            email="alice@example.com",
            name="Alice",
            picture="https://pic.example.com/alice.jpg",
            email_verified=True,
        )

        mock_client.upsert.assert_called_once()
        args, kwargs = mock_client.upsert.call_args
        assert args[0] == "profiles"
        data = args[1]
        assert data["google_sub"] == "sub123"
        assert data["email"] == "alice@example.com"
        assert data["display_name"] == "Alice"
        assert data["avatar_url"] == "https://pic.example.com/alice.jpg"
        assert data["email_verified"] is True
        assert "last_login_at" in data
        assert "updated_at" in data
        assert kwargs.get("on_conflict") == "google_sub" or args[2] == "google_sub"
        assert result == {"google_sub": "sub123"}

    def test_upsert_defaults_email_verified_false(self, mock_client):
        mock_client.upsert.return_value = {}
        repo = ProfileRepo(mock_client)

        repo.upsert_from_google_user(
            google_sub="sub456",
            email="bob@example.com",
            name="Bob",
            picture="",
        )

        data = mock_client.upsert.call_args[0][1]
        assert data["email_verified"] is False
        assert "last_login_at" in data


# ── ContributorLeadRepo ─────────────────────────────────────────────────


class TestContributorLeadRepo:
    def test_insert(self, mock_client):
        mock_client.insert.return_value = {"id": 1, "email": "bob@b.com"}
        repo = ContributorLeadRepo(mock_client)

        result = repo.insert("bob@b.com", "landing_contribute")

        mock_client.insert.assert_called_once_with(
            "contributor_leads",
            {"email": "bob@b.com", "source": "landing_contribute"},
        )
        assert result["email"] == "bob@b.com"

    def test_list_all(self, mock_client):
        mock_client.select.return_value = [{"id": 1}, {"id": 2}]
        repo = ContributorLeadRepo(mock_client)

        result = repo.list_all()

        mock_client.select.assert_called_once_with(
            "contributor_leads",
            {"order": "created_at.desc"},
        )
        assert len(result) == 2

    def test_get_by_id_found(self, mock_client):
        mock_client.select.return_value = [{"id": 5, "email": "x@y.com"}]
        repo = ContributorLeadRepo(mock_client)

        result = repo.get_by_id(5)

        mock_client.select.assert_called_once_with(
            "contributor_leads", {"id": "eq.5"}
        )
        assert result == {"id": 5, "email": "x@y.com"}

    def test_get_by_id_not_found(self, mock_client):
        mock_client.select.return_value = []
        repo = ContributorLeadRepo(mock_client)

        assert repo.get_by_id(999) is None

    def test_update(self, mock_client):
        mock_client.update.return_value = [{"id": 3, "status": "contacted"}]
        repo = ContributorLeadRepo(mock_client)

        result = repo.update(3, status="contacted", notes="Called")

        mock_client.update.assert_called_once_with(
            "contributor_leads",
            {"id": "eq.3"},
            {"status": "contacted", "notes": "Called"},
        )
        assert result["status"] == "contacted"

    def test_delete_found(self, mock_client):
        mock_client.delete.return_value = [{"id": 7}]
        repo = ContributorLeadRepo(mock_client)

        assert repo.delete(7) is True

    def test_delete_not_found(self, mock_client):
        mock_client.delete.return_value = []
        repo = ContributorLeadRepo(mock_client)

        assert repo.delete(999) is False

    def test_delete_all(self, mock_client):
        mock_client.delete.return_value = [{"id": 1}, {"id": 2}, {"id": 3}]
        repo = ContributorLeadRepo(mock_client)

        assert repo.delete_all() == 3
        mock_client.delete.assert_called_once_with(
            "contributor_leads", {"id": "gt.0"}
        )

    def test_count(self, mock_client):
        mock_client.head_count.return_value = 12
        repo = ContributorLeadRepo(mock_client)

        assert repo.count() == 12
        mock_client.head_count.assert_called_once_with("contributor_leads")

    def test_count_by_status(self, mock_client):
        mock_client.head_count.return_value = 5
        repo = ContributorLeadRepo(mock_client)

        assert repo.count_by_status("new") == 5
        mock_client.head_count.assert_called_once_with(
            "contributor_leads", {"status": "eq.new"}
        )

    def test_count_recent(self, mock_client):
        mock_client.head_count.return_value = 3
        repo = ContributorLeadRepo(mock_client)

        assert repo.count_recent(7) == 3
        args, kwargs = mock_client.head_count.call_args
        assert args[0] == "contributor_leads"
        params = args[1] if len(args) > 1 else kwargs.get("params", {})
        assert "created_at" in params
        assert params["created_at"].startswith("gte.")

    def test_find_by_email_found(self, mock_client):
        mock_client.select.return_value = [{"id": 1, "email": "a@b.com"}]
        repo = ContributorLeadRepo(mock_client)

        result = repo.find_by_email("a@b.com")

        mock_client.select.assert_called_once_with(
            "contributor_leads", {"email": "eq.a@b.com"}
        )
        assert result["email"] == "a@b.com"

    def test_find_by_email_not_found(self, mock_client):
        mock_client.select.return_value = []
        repo = ContributorLeadRepo(mock_client)

        assert repo.find_by_email("nobody@nowhere.com") is None


# ── FeedbackRepo ─────────────────────────────────────────────────────────


class TestFeedbackRepo:
    def test_insert(self, mock_client):
        mock_client.insert.return_value = {"id": 1, "message": "Great!"}
        repo = FeedbackRepo(mock_client)

        result = repo.insert("Alice", "Great!", "home")

        mock_client.insert.assert_called_once_with(
            "feedback",
            {"name": "Alice", "message": "Great!", "source": "home"},
        )
        assert result["message"] == "Great!"

    def test_list_all(self, mock_client):
        mock_client.select.return_value = [{"id": 1}]
        repo = FeedbackRepo(mock_client)

        result = repo.list_all()

        mock_client.select.assert_called_once_with(
            "feedback",
            {"order": "created_at.desc"},
        )
        assert len(result) == 1

    def test_count(self, mock_client):
        mock_client.head_count.return_value = 7
        repo = FeedbackRepo(mock_client)

        assert repo.count() == 7
        mock_client.head_count.assert_called_once_with("feedback")

    def test_count_recent(self, mock_client):
        mock_client.head_count.return_value = 2
        repo = FeedbackRepo(mock_client)

        assert repo.count_recent(7) == 2
        args, kwargs = mock_client.head_count.call_args
        assert args[0] == "feedback"
        params = args[1] if len(args) > 1 else kwargs.get("params", {})
        assert "created_at" in params
        assert params["created_at"].startswith("gte.")


# ── TranscriptRepo ─────────────────────────────────────────────────────


class TestTranscriptRepo:
    def test_upsert_insert(self, mock_client):
        mock_client.upsert.return_value = {
            "id": 1,
            "google_sub": "sub123",
            "client_history_id": "hist-abc",
        }
        repo = TranscriptRepo(mock_client)

        result = repo.upsert(
            google_sub="sub123",
            client_history_id="hist-abc",
            mode="voice",
            started_at="2026-04-12T10:00:00+00:00",
            text="Hello world",
        )

        mock_client.upsert.assert_called_once()
        args, kwargs = mock_client.upsert.call_args
        assert args[0] == "transcripts"
        data = args[1]
        assert data["google_sub"] == "sub123"
        assert data["client_history_id"] == "hist-abc"
        assert data["mode"] == "voice"
        assert data["started_at"] == "2026-04-12T10:00:00+00:00"
        assert data["text"] == "Hello world"
        assert "updated_at" in data
        assert kwargs.get("on_conflict") == "google_sub,client_history_id" or (
            len(args) > 2 and args[2] == "google_sub,client_history_id"
        )
        assert result["id"] == 1

    def test_upsert_idempotent(self, mock_client):
        mock_client.upsert.return_value = {"id": 1, "text": "Updated"}
        repo = TranscriptRepo(mock_client)

        repo.upsert("sub1", "hist-1", "asl", None, "First")
        repo.upsert("sub1", "hist-1", "asl", None, "Updated")

        assert mock_client.upsert.call_count == 2
        # Both calls use the same conflict key
        for c in mock_client.upsert.call_args_list:
            args, kwargs = c
            assert args[0] == "transcripts"
            assert kwargs.get("on_conflict") == "google_sub,client_history_id" or (
                len(args) > 2 and args[2] == "google_sub,client_history_id"
            )

    def test_upsert_started_at_none_omitted(self, mock_client):
        mock_client.upsert.return_value = {"id": 2}
        repo = TranscriptRepo(mock_client)

        repo.upsert("sub1", "hist-2", "letter", None, "Some text")

        data = mock_client.upsert.call_args[0][1]
        assert "started_at" not in data

    def test_list_by_user_returns_user_rows(self, mock_client):
        mock_client.select.return_value = [{"id": 1}, {"id": 2}]
        repo = TranscriptRepo(mock_client)

        result = repo.list_by_user("sub123")

        mock_client.select.assert_called_once_with(
            "transcripts",
            {
                "google_sub": "eq.sub123",
                "order": "created_at.desc",
                "limit": "20",
            },
        )
        assert len(result) == 2

    def test_list_by_user_custom_limit(self, mock_client):
        mock_client.select.return_value = [{"id": 1}]
        repo = TranscriptRepo(mock_client)

        repo.list_by_user("sub123", limit=5)

        params = mock_client.select.call_args[0][1]
        assert params["limit"] == "5"

    def test_get_by_id_found(self, mock_client):
        mock_client.select.return_value = [{"id": 10, "text": "Hello"}]
        repo = TranscriptRepo(mock_client)

        result = repo.get_by_id(10)

        mock_client.select.assert_called_once_with(
            "transcripts", {"id": "eq.10"}
        )
        assert result == {"id": 10, "text": "Hello"}

    def test_get_by_id_not_found(self, mock_client):
        mock_client.select.return_value = []
        repo = TranscriptRepo(mock_client)

        assert repo.get_by_id(999) is None

    def test_delete_by_id_enforces_ownership(self, mock_client):
        mock_client.delete.return_value = [{"id": 5}]
        repo = TranscriptRepo(mock_client)

        repo.delete_by_id(5, "sub123")

        mock_client.delete.assert_called_once_with(
            "transcripts",
            {"id": "eq.5", "google_sub": "eq.sub123"},
        )

    def test_delete_by_id_found(self, mock_client):
        mock_client.delete.return_value = [{"id": 5}]
        repo = TranscriptRepo(mock_client)

        assert repo.delete_by_id(5, "sub123") is True

    def test_delete_by_id_not_found(self, mock_client):
        mock_client.delete.return_value = []
        repo = TranscriptRepo(mock_client)

        assert repo.delete_by_id(999, "sub123") is False

    def test_delete_all_by_user_returns_count(self, mock_client):
        mock_client.delete.return_value = [{"id": 1}, {"id": 2}, {"id": 3}]
        repo = TranscriptRepo(mock_client)

        assert repo.delete_all_by_user("sub123") == 3

    def test_delete_all_by_user_empty(self, mock_client):
        mock_client.delete.return_value = []
        repo = TranscriptRepo(mock_client)

        assert repo.delete_all_by_user("sub123") == 0

    def test_delete_all_by_user_filters_by_google_sub(self, mock_client):
        mock_client.delete.return_value = [{"id": 5}]
        repo = TranscriptRepo(mock_client)

        repo.delete_all_by_user("sub456")

        mock_client.delete.assert_called_once_with(
            "transcripts",
            {"google_sub": "eq.sub456"},
        )

    def test_count_by_user(self, mock_client):
        mock_client.head_count.return_value = 8
        repo = TranscriptRepo(mock_client)

        assert repo.count_by_user("sub123") == 8
        mock_client.head_count.assert_called_once_with(
            "transcripts",
            {"google_sub": "eq.sub123"},
        )
