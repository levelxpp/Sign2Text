from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


SECURE_TOKEN = "public-test-admin-token-0123456789abcd"
SECURE_HEADERS = {"X-Admin-Token": SECURE_TOKEN}
PLACEHOLDER_TOKEN = "CHANGE_ME_TO_A_STRING_OF_AT_LEAST_16_CHARS"


def _load_admin_server(load_module, monkeypatch, tmp_path, token: str):
    monkeypatch.setenv("ADMIN_ACCESS_TOKEN", token)
    monkeypatch.delenv("TRUSTED_PROXY", raising=False)

    # Neutralise Supabase env vars so that _init_supabase_admin() during
    # module reload sees Supabase disabled (the real .env sets these).
    monkeypatch.delenv("SUPABASE_ENABLED", raising=False)
    monkeypatch.delenv("SUPABASE_USE_FOR_ADMIN", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)

    admin_server = load_module("backend.admin_server")

    data_dir = tmp_path / "data"
    monkeypatch.setattr(admin_server, "DATA_DIR", data_dir)
    monkeypatch.setattr(admin_server, "DB_PATH", data_dir / "admin.sqlite3")

    # Reset Supabase globals (safety net in case reload still picked them up)
    admin_server._use_supabase_admin = False
    admin_server._supabase_lead_repo = None
    admin_server._supabase_feedback_repo = None

    admin_server._admin_token_cache = None
    admin_server._admin_token_configured = False
    admin_server._admin_token_checked = False
    admin_server._admin_token_failure_reason = None
    with admin_server._rate_limit_lock:
        admin_server._rate_limit_db.clear()

    return admin_server


def _clear_rate_limits(admin_server) -> None:
    with admin_server._rate_limit_lock:
        admin_server._rate_limit_db.clear()


def _admin_logs(caplog) -> str:
    return "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name == "admin_server"
    )


def _create_lead(client: TestClient, admin_server, email: str = "lead@example.com") -> int:
    _clear_rate_limits(admin_server)
    response = client.post("/api/public/contributor-leads", json={"email": email})
    assert response.status_code == 200
    return response.json()["id"]


@pytest.fixture
def admin_server_module(tmp_path, monkeypatch, load_module):
    return _load_admin_server(load_module, monkeypatch, tmp_path, SECURE_TOKEN)


@pytest.fixture
def admin_client(admin_server_module):
    with TestClient(admin_server_module.app, raise_server_exceptions=False) as client:
        yield client


def test_admin_cors_falls_back_to_local_frontend_only(
    tmp_path, monkeypatch, load_module
):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    monkeypatch.delenv("FRONTEND_PUBLIC_URL", raising=False)
    monkeypatch.delenv("APP_PUBLIC_HOST", raising=False)
    monkeypatch.delenv("APP_PUBLIC_SCHEME", raising=False)
    monkeypatch.delenv("ADMIN_API_BASE_URL", raising=False)

    admin_server = _load_admin_server(load_module, monkeypatch, tmp_path, SECURE_TOKEN)

    settings = admin_server._build_cors_settings()

    assert "http://127.0.0.1:5500" in settings["allow_origins"]
    assert "http://localhost:5500" in settings["allow_origins"]
    assert settings["allow_origin_regex"] == r"^https?://[^/]+:5500$"
    assert settings["allow_credentials"] is False


def test_admin_cors_uses_public_browser_origin_when_available(
    tmp_path, monkeypatch, load_module
):
    monkeypatch.setenv("APP_PUBLIC_HOST", "captions.example.test")
    monkeypatch.setenv("APP_PUBLIC_SCHEME", "https")
    monkeypatch.setenv("ADMIN_API_BASE_URL", "/admin-api")
    monkeypatch.delenv("CORS_ORIGINS", raising=False)

    admin_server = _load_admin_server(load_module, monkeypatch, tmp_path, SECURE_TOKEN)

    settings = admin_server._build_cors_settings()

    assert settings["allow_origins"] == ["https://captions.example.test"]
    assert settings["allow_origin_regex"] is None
    assert settings["allow_credentials"] is False


def test_stats_success_is_low_noise(admin_client, admin_server_module, caplog):
    _create_lead(admin_client, admin_server_module, "quiet@example.com")

    caplog.set_level(logging.INFO, logger="admin_server")
    caplog.clear()

    response = admin_client.get("/api/admin/stats", headers=SECURE_HEADERS)

    assert response.status_code == 200
    assert response.json()["total_leads"] == 1
    assert _admin_logs(caplog) == ""


def test_valid_update_still_works_and_logs_success(admin_client, admin_server_module, caplog):
    lead_id = _create_lead(admin_client, admin_server_module, "update@example.com")

    caplog.set_level(logging.INFO, logger="admin_server")
    caplog.clear()

    response = admin_client.patch(
        f"/api/admin/contributor-leads/{lead_id}",
        headers=SECURE_HEADERS,
        json={"status": "contacted", "notes": "  Hello there  "},
    )

    assert response.status_code == 200
    payload = response.json()["lead"]
    assert payload["status"] == "contacted"
    assert payload["notes"] == "Hello there"

    log_text = _admin_logs(caplog)
    assert "action=update_lead" in log_text
    assert "outcome=success" in log_text
    assert f"lead_id={lead_id}" in log_text
    assert "changed_fields=status,notes" in log_text


def test_notes_null_normalizes_to_empty_string(admin_client, admin_server_module):
    lead_id = _create_lead(admin_client, admin_server_module, "nullnotes@example.com")

    response = admin_client.patch(
        f"/api/admin/contributor-leads/{lead_id}",
        headers=SECURE_HEADERS,
        json={"notes": None},
    )

    assert response.status_code == 200
    assert response.json()["lead"]["notes"] == ""


def test_invalid_status_returns_400_detail_and_rejected_log(admin_client, admin_server_module, caplog):
    lead_id = _create_lead(admin_client, admin_server_module, "badstatus@example.com")

    caplog.set_level(logging.INFO, logger="admin_server")
    caplog.clear()

    response = admin_client.patch(
        f"/api/admin/contributor-leads/{lead_id}",
        headers=SECURE_HEADERS,
        json={"status": "invalid_status"},
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": "Invalid status. Must be one of: new, contacted, archived."
    }

    log_text = _admin_logs(caplog)
    assert "action=update_lead" in log_text
    assert "outcome=rejected" in log_text
    assert "reason=invalid_status" in log_text


def test_invalid_notes_type_returns_400_detail_and_rejected_log(admin_client, admin_server_module, caplog):
    lead_id = _create_lead(admin_client, admin_server_module, "badnotes@example.com")

    caplog.set_level(logging.INFO, logger="admin_server")
    caplog.clear()

    response = admin_client.patch(
        f"/api/admin/contributor-leads/{lead_id}",
        headers=SECURE_HEADERS,
        json={"notes": 123},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Notes must be a string or null."}

    log_text = _admin_logs(caplog)
    assert "action=update_lead" in log_text
    assert "outcome=rejected" in log_text
    assert "reason=notes_must_be_string_or_null" in log_text


def test_oversized_notes_returns_400_detail_and_rejected_log(admin_client, admin_server_module, caplog):
    lead_id = _create_lead(admin_client, admin_server_module, "longnotes@example.com")

    caplog.set_level(logging.INFO, logger="admin_server")
    caplog.clear()

    response = admin_client.patch(
        f"/api/admin/contributor-leads/{lead_id}",
        headers=SECURE_HEADERS,
        json={"notes": "x" * 2001},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Notes cannot exceed 2000 characters."}

    log_text = _admin_logs(caplog)
    assert "action=update_lead" in log_text
    assert "outcome=rejected" in log_text
    assert "reason=notes_too_long" in log_text


def test_malformed_json_returns_400_detail_and_rejected_log(admin_client, admin_server_module, caplog):
    lead_id = _create_lead(admin_client, admin_server_module, "malformed@example.com")

    caplog.set_level(logging.INFO, logger="admin_server")
    caplog.clear()

    response = admin_client.patch(
        f"/api/admin/contributor-leads/{lead_id}",
        headers={**SECURE_HEADERS, "Content-Type": "application/json"},
        content='{"status":',
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Malformed JSON body."}

    log_text = _admin_logs(caplog)
    assert "action=update_lead" in log_text
    assert "outcome=rejected" in log_text
    assert "reason=malformed_json" in log_text


@pytest.mark.parametrize(
    ("request_kwargs", "email_suffix"),
    [
        ({"json": []}, "list"),
        ({"json": "status"}, "string"),
        ({"json": 123}, "number"),
        (
            {
                "content": "null",
                "headers": {**SECURE_HEADERS, "Content-Type": "application/json"},
            },
            "null",
        ),
    ],
)
def test_non_object_json_payloads_return_400_detail_and_rejected_log(
    admin_client,
    admin_server_module,
    caplog,
    request_kwargs,
    email_suffix,
):
    request_kwargs = dict(request_kwargs)
    lead_id = _create_lead(admin_client, admin_server_module, f"nonobject-{email_suffix}@example.com")

    caplog.set_level(logging.INFO, logger="admin_server")
    caplog.clear()

    response = admin_client.patch(
        f"/api/admin/contributor-leads/{lead_id}",
        headers=request_kwargs.pop("headers", SECURE_HEADERS),
        **request_kwargs,
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Request body must be a JSON object."}

    log_text = _admin_logs(caplog)
    assert "action=update_lead" in log_text
    assert "outcome=rejected" in log_text
    assert "reason=request_body_must_be_json_object" in log_text


@pytest.mark.parametrize("payload", [{}, {"unexpected": "value"}])
def test_empty_or_invalid_update_fields_return_400_detail_and_rejected_log(
    admin_client,
    admin_server_module,
    caplog,
    payload,
):
    lead_id = _create_lead(admin_client, admin_server_module, "novalidfields@example.com")

    caplog.set_level(logging.INFO, logger="admin_server")
    caplog.clear()

    response = admin_client.patch(
        f"/api/admin/contributor-leads/{lead_id}",
        headers=SECURE_HEADERS,
        json=payload,
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": "No valid fields to update. Allowed: status, notes."
    }

    log_text = _admin_logs(caplog)
    assert "action=update_lead" in log_text
    assert "outcome=rejected" in log_text
    assert "reason=no_valid_fields" in log_text


def test_delete_existing_row_logs_target_id(admin_client, admin_server_module, caplog):
    lead_id = _create_lead(admin_client, admin_server_module, "delete@example.com")

    caplog.set_level(logging.INFO, logger="admin_server")
    caplog.clear()

    response = admin_client.delete(
        f"/api/admin/contributor-leads/{lead_id}",
        headers=SECURE_HEADERS,
    )

    assert response.status_code == 200
    assert response.json() == {"status": "success", "detail": f"Lead {lead_id} deleted."}

    log_text = _admin_logs(caplog)
    assert "action=delete_lead" in log_text
    assert "outcome=success" in log_text
    assert f"lead_id={lead_id}" in log_text
    assert "row_count=1" in log_text


def test_delete_missing_row_returns_404_and_logs_rejected(admin_client, caplog):
    caplog.set_level(logging.INFO, logger="admin_server")
    caplog.clear()

    response = admin_client.delete(
        "/api/admin/contributor-leads/999999",
        headers=SECURE_HEADERS,
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Lead not found."}

    log_text = _admin_logs(caplog)
    assert "action=delete_lead" in log_text
    assert "outcome=rejected" in log_text
    assert "lead_id=999999" in log_text
    assert "reason=lead_not_found" in log_text


def test_delete_all_logs_row_count(admin_client, admin_server_module, caplog):
    _create_lead(admin_client, admin_server_module, "deleteall-1@example.com")
    _create_lead(admin_client, admin_server_module, "deleteall-2@example.com")

    caplog.set_level(logging.INFO, logger="admin_server")
    caplog.clear()

    response = admin_client.delete("/api/admin/contributor-leads", headers=SECURE_HEADERS)

    assert response.status_code == 200
    assert response.json() == {"status": "success", "detail": "All 2 leads deleted."}

    log_text = _admin_logs(caplog)
    assert "action=delete_all_leads" in log_text
    assert "outcome=success" in log_text
    assert "row_count=2" in log_text


def test_export_logs_row_count_and_returns_csv(admin_client, admin_server_module, caplog):
    _create_lead(admin_client, admin_server_module, "export-1@example.com")
    _create_lead(admin_client, admin_server_module, "export-2@example.com")

    caplog.set_level(logging.INFO, logger="admin_server")
    caplog.clear()

    response = admin_client.get("/api/admin/contributor-leads/export", headers=SECURE_HEADERS)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment; filename=contributor_leads_" in response.headers["content-disposition"]
    assert "id,email,source,status,notes,created_at,updated_at" in response.text
    assert "export-1@example.com" in response.text
    assert "export-2@example.com" in response.text

    log_text = _admin_logs(caplog)
    assert "action=export_leads" in log_text
    assert "outcome=success" in log_text
    assert "row_count=2" in log_text


def test_invalid_token_returns_401_and_logs_rejected_attempt(admin_client, caplog):
    caplog.set_level(logging.INFO, logger="admin_server")
    caplog.clear()

    response = admin_client.get(
        "/api/admin/stats",
        headers={"X-Admin-Token": "wrong_token_here_something"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or missing admin token."}

    log_text = _admin_logs(caplog)
    assert "action=admin_auth" in log_text
    assert "outcome=rejected" in log_text
    assert "method=GET" in log_text
    assert "path=/api/admin/stats" in log_text
    assert "reason=invalid_or_missing_admin_token" in log_text
    assert "wrong_token_here_something" not in log_text


def test_insecure_placeholder_token_returns_503_and_logs_blocked(
    tmp_path,
    monkeypatch,
    load_module,
    caplog,
):
    admin_server = _load_admin_server(load_module, monkeypatch, tmp_path, PLACEHOLDER_TOKEN)

    caplog.set_level(logging.INFO, logger="admin_server")

    with TestClient(admin_server.app, raise_server_exceptions=False) as client:
        caplog.clear()
        response = client.get(
            "/api/admin/stats",
            headers={"X-Admin-Token": PLACEHOLDER_TOKEN},
        )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Admin access is disabled due to insecure server configuration."
    }

    log_text = _admin_logs(caplog)
    assert "action=admin_access" in log_text
    assert "outcome=rejected" in log_text
    assert "path=/api/admin/stats" in log_text
    assert "reason=insecure_admin_access_token" in log_text
    assert PLACEHOLDER_TOKEN not in log_text


def test_public_submission_flow_still_accepts_and_deduplicates(admin_client, admin_server_module):
    first = admin_client.post(
        "/api/public/contributor-leads",
        json={"email": "duplicate@example.com"},
    )
    assert first.status_code == 200
    first_id = first.json()["id"]

    _clear_rate_limits(admin_server_module)

    second = admin_client.post(
        "/api/public/contributor-leads",
        json={"email": "duplicate@example.com"},
    )
    assert second.status_code == 200
    assert second.json() == {"status": "success", "id": first_id}


def test_public_new_submission_schedules_autoreply_once_with_normalized_email(
    admin_client,
    admin_server_module,
    monkeypatch,
):
    calls = []

    def spy_send(email: str) -> None:
        calls.append(email)

    monkeypatch.setattr(admin_server_module, "_send_contributor_autoreply", spy_send)

    response = admin_client.post(
        "/api/public/contributor-leads",
        json={"email": "  NewLead@Example.com  "},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "success", "id": 1}
    assert calls == ["newlead@example.com"]


def test_public_duplicate_submission_does_not_schedule_second_autoreply(
    admin_client,
    admin_server_module,
    monkeypatch,
):
    calls = []

    def spy_send(email: str) -> None:
        calls.append(email)

    monkeypatch.setattr(admin_server_module, "_send_contributor_autoreply", spy_send)

    first = admin_client.post(
        "/api/public/contributor-leads",
        json={"email": "duplicate@example.com"},
    )
    assert first.status_code == 200
    first_id = first.json()["id"]

    _clear_rate_limits(admin_server_module)

    second = admin_client.post(
        "/api/public/contributor-leads",
        json={"email": " Duplicate@Example.com "},
    )

    assert second.status_code == 200
    assert second.json() == {"status": "success", "id": first_id}
    assert calls == ["duplicate@example.com"]


def test_public_honeypot_still_returns_safe_success(admin_client):
    response = admin_client.post(
        "/api/public/contributor-leads",
        json={"email": "honeypot@example.com", "website": "https://spam.example"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "success", "id": -1}


def test_public_honeypot_does_not_schedule_autoreply(
    admin_client,
    admin_server_module,
    monkeypatch,
):
    calls = []

    def spy_send(email: str) -> None:
        calls.append(email)

    monkeypatch.setattr(admin_server_module, "_send_contributor_autoreply", spy_send)

    response = admin_client.post(
        "/api/public/contributor-leads",
        json={"email": "honeypot@example.com", "website": "https://spam.example"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "success", "id": -1}
    assert calls == []


def test_public_rate_limit_still_returns_429_with_detail_and_retry_after(admin_client):
    first = admin_client.post(
        "/api/public/contributor-leads",
        json={"email": "ratelimit-1@example.com"},
    )
    assert first.status_code == 200

    second = admin_client.post(
        "/api/public/contributor-leads",
        json={"email": "ratelimit-2@example.com"},
    )

    assert second.status_code == 429
    assert second.json() == {
        "detail": "Too many requests. Please wait and try again."
    }
    assert "Retry-After" in second.headers


def test_public_rate_limited_request_does_not_schedule_autoreply(
    admin_client,
    admin_server_module,
    monkeypatch,
):
    calls = []

    def spy_send(email: str) -> None:
        calls.append(email)

    monkeypatch.setattr(admin_server_module, "_send_contributor_autoreply", spy_send)

    first = admin_client.post(
        "/api/public/contributor-leads",
        json={"email": "ratelimit-1@example.com"},
    )
    assert first.status_code == 200

    second = admin_client.post(
        "/api/public/contributor-leads",
        json={"email": "ratelimit-2@example.com"},
    )

    assert second.status_code == 429
    assert second.json() == {
        "detail": "Too many requests. Please wait and try again."
    }
    assert calls == ["ratelimit-1@example.com"]


def test_public_invalid_email_still_returns_400_detail(admin_client):
    response = admin_client.post(
        "/api/public/contributor-leads",
        json={"email": "not-an-email"},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid email address."}


def test_public_invalid_email_does_not_schedule_autoreply(
    admin_client,
    admin_server_module,
    monkeypatch,
):
    calls = []

    def spy_send(email: str) -> None:
        calls.append(email)

    monkeypatch.setattr(admin_server_module, "_send_contributor_autoreply", spy_send)

    response = admin_client.post(
        "/api/public/contributor-leads",
        json={"email": "not-an-email"},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid email address."}
    assert calls == []


def test_public_new_submission_returns_success_when_autoreply_scheduling_fails(
    admin_client,
    admin_server_module,
    monkeypatch,
    caplog,
):
    def fail_add_task(self, func, *args, **kwargs):
        raise RuntimeError("background task scheduling failed")

    monkeypatch.setattr(admin_server_module.BackgroundTasks, "add_task", fail_add_task)

    caplog.set_level(logging.INFO, logger="admin_server")
    caplog.clear()

    response = admin_client.post(
        "/api/public/contributor-leads",
        json={"email": "schedulefail@example.com"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "success", "id": 1}

    log_text = _admin_logs(caplog)
    assert "[contributor autoreply scheduling failed]" in log_text
    assert "s***l@example.com" in log_text
    assert "schedulefail@example.com" not in log_text


def test_public_new_submission_returns_success_when_autoreply_helper_fails(
    admin_client,
    admin_server_module,
    monkeypatch,
    caplog,
    load_module,
):
    contributor_autoreply = load_module("backend.notifications.contributor_autoreply")

    class FailingService:
        def send_acknowledgement(self, recipient_email: str):
            raise RuntimeError("smtp exploded")

    def fake_from_env(cls, environ=None, project_root=None):
        return FailingService()

    monkeypatch.setattr(
        contributor_autoreply.ContributorAutoReplyService,
        "from_env",
        classmethod(fake_from_env),
    )

    caplog.set_level(logging.INFO, logger="admin_server")
    caplog.clear()

    response = admin_client.post(
        "/api/public/contributor-leads",
        json={"email": "helperfail@example.com"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "success", "id": 1}

    log_text = _admin_logs(caplog)
    assert "[contributor autoreply failed unexpectedly]" in log_text
    assert "h***l@example.com" in log_text
    assert "helperfail@example.com" not in log_text


# â”€â”€ Supabase-backed admin persistence tests â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def _enable_supabase_admin(admin_server, monkeypatch):
    """Activate Supabase admin path with mock repos, return (lead_repo, feedback_repo)."""
    lead_repo = MagicMock()
    feedback_repo = MagicMock()
    monkeypatch.setattr(admin_server, "_use_supabase_admin", True)
    monkeypatch.setattr(admin_server, "_supabase_lead_repo", lead_repo)
    monkeypatch.setattr(admin_server, "_supabase_feedback_repo", feedback_repo)
    return lead_repo, feedback_repo


# --- Fallback / flag behaviour ---


def test_sqlite_path_unchanged_when_supabase_admin_off(admin_client, admin_server_module):
    """When _use_supabase_admin is False (default), SQLite path is used."""
    assert admin_server_module._use_supabase_admin is False

    response = admin_client.post(
        "/api/public/contributor-leads",
        json={"email": "sqlite-test@example.com"},
    )
    assert response.status_code == 200
    lead_id = response.json()["id"]
    assert isinstance(lead_id, int) and lead_id > 0


def test_safe_fallback_to_sqlite_when_supabase_misconfigured(
    tmp_path, monkeypatch, load_module
):
    """SUPABASE_USE_FOR_ADMIN=true but SUPABASE_ENABLED=false => SQLite fallback."""
    monkeypatch.setenv("SUPABASE_USE_FOR_ADMIN", "true")
    monkeypatch.setenv("SUPABASE_ENABLED", "false")
    admin_server = _load_admin_server(load_module, monkeypatch, tmp_path, SECURE_TOKEN)
    assert admin_server._use_supabase_admin is False


def test_safe_fallback_when_supabase_enabled_but_url_missing(
    tmp_path, monkeypatch, load_module, caplog
):
    """SUPABASE_USE_FOR_ADMIN=true, SUPABASE_ENABLED=true but URL empty => fallback."""
    monkeypatch.setenv("SUPABASE_USE_FOR_ADMIN", "true")
    monkeypatch.setenv("SUPABASE_ENABLED", "true")
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-value-here")
    caplog.set_level(logging.WARNING, logger="admin_server")
    admin_server = _load_admin_server(load_module, monkeypatch, tmp_path, SECURE_TOKEN)
    assert admin_server._use_supabase_admin is False


# --- Public lead insert via Supabase ---


def test_supabase_public_lead_insert(admin_server_module, monkeypatch):
    lead_repo, _ = _enable_supabase_admin(admin_server_module, monkeypatch)
    lead_repo.find_by_email.return_value = None
    lead_repo.insert.return_value = {"id": 42, "email": "new@example.com"}
    monkeypatch.setattr(admin_server_module, "_send_contributor_autoreply", lambda e: None)

    with TestClient(admin_server_module.app, raise_server_exceptions=False) as client:
        _clear_rate_limits(admin_server_module)
        response = client.post(
            "/api/public/contributor-leads",
            json={"email": "New@Example.com"},
        )

    assert response.status_code == 200
    assert response.json() == {"status": "success", "id": 42}
    lead_repo.find_by_email.assert_called_once_with("new@example.com")
    lead_repo.insert.assert_called_once_with("new@example.com", "landing_contribute")


def test_supabase_duplicate_email_idempotency(admin_server_module, monkeypatch):
    lead_repo, _ = _enable_supabase_admin(admin_server_module, monkeypatch)
    lead_repo.find_by_email.return_value = {"id": 7, "email": "dup@example.com"}

    with TestClient(admin_server_module.app, raise_server_exceptions=False) as client:
        _clear_rate_limits(admin_server_module)
        response = client.post(
            "/api/public/contributor-leads",
            json={"email": "dup@example.com"},
        )

    assert response.status_code == 200
    assert response.json() == {"status": "success", "id": 7}
    lead_repo.insert.assert_not_called()


# --- Public feedback insert via Supabase ---


def test_supabase_public_feedback_insert(admin_server_module, monkeypatch):
    _, feedback_repo = _enable_supabase_admin(admin_server_module, monkeypatch)
    feedback_repo.insert.return_value = {"id": 99, "name": "Tester", "message": "Great!"}

    with TestClient(admin_server_module.app, raise_server_exceptions=False) as client:
        _clear_rate_limits(admin_server_module)
        response = client.post(
            "/api/public/feedback",
            json={"name": "Tester", "message": "Great!", "source": "home"},
        )

    assert response.status_code == 200
    assert response.json() == {"status": "success", "id": 99}
    feedback_repo.insert.assert_called_once_with("Tester", "Great!", "home")


# --- Admin stats via Supabase ---


def test_supabase_admin_stats(admin_server_module, monkeypatch):
    lead_repo, feedback_repo = _enable_supabase_admin(admin_server_module, monkeypatch)
    lead_repo.count.return_value = 10
    lead_repo.count_by_status.side_effect = lambda s: {"new": 5, "contacted": 3}.get(s, 0)
    lead_repo.count_recent.return_value = 2
    feedback_repo.count.return_value = 20
    feedback_repo.count_recent.return_value = 4

    with TestClient(admin_server_module.app, raise_server_exceptions=False) as client:
        response = client.get("/api/admin/stats", headers=SECURE_HEADERS)

    assert response.status_code == 200
    data = response.json()
    assert data == {
        "total_leads": 10,
        "new_leads": 5,
        "contacted_leads": 3,
        "recent_7d": 2,
        "total_feedback": 20,
        "recent_feedback_7d": 4,
    }


# --- Admin list leads via Supabase ---


def test_supabase_admin_list_leads(admin_server_module, monkeypatch):
    lead_repo, _ = _enable_supabase_admin(admin_server_module, monkeypatch)
    lead_repo.list_all.return_value = [
        {"id": 1, "email": "a@example.com", "source": "landing_contribute",
         "status": "new", "notes": "", "created_at": "2026-01-01T00:00:00Z",
         "updated_at": "2026-01-01T00:00:00Z"},
    ]

    with TestClient(admin_server_module.app, raise_server_exceptions=False) as client:
        response = client.get("/api/admin/contributor-leads", headers=SECURE_HEADERS)

    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 1
    assert data["leads"][0]["email"] == "a@example.com"


# --- Admin export leads CSV via Supabase ---


def test_supabase_admin_export_leads_csv(admin_server_module, monkeypatch):
    lead_repo, _ = _enable_supabase_admin(admin_server_module, monkeypatch)
    lead_repo.list_all.return_value = [
        {"id": 1, "email": "csv@example.com", "source": "landing_contribute",
         "status": "new", "notes": "test note", "created_at": "2026-01-01T00:00:00Z",
         "updated_at": "2026-01-01T00:00:00Z"},
    ]

    with TestClient(admin_server_module.app, raise_server_exceptions=False) as client:
        response = client.get(
            "/api/admin/contributor-leads/export", headers=SECURE_HEADERS
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "id,email,source,status,notes,created_at,updated_at" in response.text
    assert "csv@example.com" in response.text
    assert "test note" in response.text


# --- Admin update lead via Supabase ---


def test_supabase_admin_update_lead(admin_server_module, monkeypatch):
    lead_repo, _ = _enable_supabase_admin(admin_server_module, monkeypatch)
    lead_repo.update.return_value = {
        "id": 5, "email": "u@example.com", "source": "landing_contribute",
        "status": "contacted", "notes": "called",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-04-11T12:00:00Z",
    }

    with TestClient(admin_server_module.app, raise_server_exceptions=False) as client:
        response = client.patch(
            "/api/admin/contributor-leads/5",
            headers=SECURE_HEADERS,
            json={"status": "contacted", "notes": "called"},
        )

    assert response.status_code == 200
    assert response.json()["lead"]["status"] == "contacted"
    assert response.json()["lead"]["notes"] == "called"


def test_supabase_admin_update_lead_sets_updated_at(admin_server_module, monkeypatch):
    """Verify that updated_at is explicitly passed to repo.update() on every PATCH."""
    lead_repo, _ = _enable_supabase_admin(admin_server_module, monkeypatch)
    lead_repo.update.return_value = {
        "id": 5, "email": "u@example.com", "source": "landing_contribute",
        "status": "contacted", "notes": "",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-04-11T12:00:00Z",
    }

    with TestClient(admin_server_module.app, raise_server_exceptions=False) as client:
        response = client.patch(
            "/api/admin/contributor-leads/5",
            headers=SECURE_HEADERS,
            json={"status": "contacted"},
        )

    assert response.status_code == 200
    call_kwargs = lead_repo.update.call_args
    # update(lead_id, **fields) â€” fields must include updated_at
    assert "updated_at" in call_kwargs.kwargs
    # updated_at must be a non-empty ISO-ish string
    assert len(call_kwargs.kwargs["updated_at"]) >= 20


def test_supabase_admin_update_lead_not_found(admin_server_module, monkeypatch):
    lead_repo, _ = _enable_supabase_admin(admin_server_module, monkeypatch)
    lead_repo.update.return_value = {}

    with TestClient(admin_server_module.app, raise_server_exceptions=False) as client:
        response = client.patch(
            "/api/admin/contributor-leads/999",
            headers=SECURE_HEADERS,
            json={"status": "contacted"},
        )

    assert response.status_code == 404
    assert response.json() == {"detail": "Lead not found."}


# --- Admin delete lead via Supabase ---


def test_supabase_admin_delete_lead(admin_server_module, monkeypatch):
    lead_repo, _ = _enable_supabase_admin(admin_server_module, monkeypatch)
    lead_repo.delete.return_value = True

    with TestClient(admin_server_module.app, raise_server_exceptions=False) as client:
        response = client.delete(
            "/api/admin/contributor-leads/5", headers=SECURE_HEADERS
        )

    assert response.status_code == 200
    assert response.json() == {"status": "success", "detail": "Lead 5 deleted."}
    lead_repo.delete.assert_called_once_with(5)


def test_supabase_admin_delete_lead_not_found(admin_server_module, monkeypatch):
    lead_repo, _ = _enable_supabase_admin(admin_server_module, monkeypatch)
    lead_repo.delete.return_value = False

    with TestClient(admin_server_module.app, raise_server_exceptions=False) as client:
        response = client.delete(
            "/api/admin/contributor-leads/999", headers=SECURE_HEADERS
        )

    assert response.status_code == 404
    assert response.json() == {"detail": "Lead not found."}


# --- Admin delete all leads via Supabase ---


def test_supabase_admin_delete_all(admin_server_module, monkeypatch):
    lead_repo, _ = _enable_supabase_admin(admin_server_module, monkeypatch)
    lead_repo.delete_all.return_value = 3

    with TestClient(admin_server_module.app, raise_server_exceptions=False) as client:
        response = client.delete(
            "/api/admin/contributor-leads", headers=SECURE_HEADERS
        )

    assert response.status_code == 200
    assert response.json() == {"status": "success", "detail": "All 3 leads deleted."}


# --- Admin list / export feedback via Supabase ---


def test_supabase_admin_list_feedback(admin_server_module, monkeypatch):
    _, feedback_repo = _enable_supabase_admin(admin_server_module, monkeypatch)
    feedback_repo.list_all.return_value = [
        {"id": 1, "name": "Alice", "message": "Nice!", "source": "home",
         "created_at": "2026-01-01T00:00:00Z"},
    ]

    with TestClient(admin_server_module.app, raise_server_exceptions=False) as client:
        response = client.get("/api/admin/feedback", headers=SECURE_HEADERS)

    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 1
    assert data["feedback"][0]["name"] == "Alice"


def test_supabase_admin_export_feedback_csv(admin_server_module, monkeypatch):
    _, feedback_repo = _enable_supabase_admin(admin_server_module, monkeypatch)
    feedback_repo.list_all.return_value = [
        {"id": 1, "name": "Bob", "message": "Good app", "source": "home",
         "created_at": "2026-02-01T00:00:00Z"},
    ]

    with TestClient(admin_server_module.app, raise_server_exceptions=False) as client:
        response = client.get(
            "/api/admin/feedback/export", headers=SECURE_HEADERS
        )

    assert response.status_code == 200
    assert "id,name,message,source,created_at" in response.text
    assert "Bob" in response.text
    assert "Good app" in response.text


# --- Route contract and secrets ---


def test_no_route_contract_changes(admin_server_module):
    """Verify all expected routes still exist with the correct methods."""
    from collections import defaultdict

    routes = defaultdict(set)
    for route in admin_server_module.app.routes:
        if hasattr(route, "path") and hasattr(route, "methods"):
            routes[route.path].update(route.methods)

    assert "POST" in routes["/api/public/contributor-leads"]
    assert "POST" in routes["/api/public/feedback"]
    assert "GET" in routes["/api/admin/stats"]
    assert "GET" in routes["/api/admin/contributor-leads"]
    assert "GET" in routes["/api/admin/contributor-leads/export"]
    assert "PATCH" in routes["/api/admin/contributor-leads/{lead_id}"]
    assert "DELETE" in routes["/api/admin/contributor-leads/{lead_id}"]
    assert "DELETE" in routes["/api/admin/contributor-leads"]
    assert "GET" in routes["/api/admin/feedback"]
    assert "GET" in routes["/api/admin/feedback/export"]


def test_no_secrets_in_supabase_init_logs(
    tmp_path, monkeypatch, load_module, caplog
):
    """Verify SUPABASE_SERVICE_ROLE_KEY does not appear in log output."""
    secret = "sbp_super_secret_key_1234567890abcdef"
    monkeypatch.setenv("SUPABASE_USE_FOR_ADMIN", "true")
    monkeypatch.setenv("SUPABASE_ENABLED", "true")
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", secret)

    caplog.set_level(logging.DEBUG)
    _load_admin_server(load_module, monkeypatch, tmp_path, SECURE_TOKEN)

    full_log = "\n".join(r.getMessage() for r in caplog.records)
    assert secret not in full_log
