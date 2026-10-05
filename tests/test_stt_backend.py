from __future__ import annotations

import urllib.parse

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request


# ---------------------------------------------------------------------------
# legacy_auth marker
# ---------------------------------------------------------------------------
# Authentication moved from cookie-backed Google OAuth sessions to Supabase JWT.
# The published stt_backend therefore no longer defines SESSION_SECRET,
# SESSION_COOKIE_SECURE, SessionMiddleware or the OAuth state-token helpers, and
# the JWT guard reports "Missing Bearer token." rather than "Not authenticated".
#
# The tests marked `legacy_auth` below still describe that removed design. They
# are NOT satisfied by re-adding a session secret or a dev fallback secret —
# doing so would reintroduce exactly the insecure configuration the sanitized
# public build removes. They are kept, unmodified and still executed by the
# diagnostic workflow, pending a rewrite against the JWT contract.
#
# Tracked as repair item 4 in docs/TESTING_STATUS.md.


def load_stt_backend(monkeypatch, load_module, **overrides):
    blank_override_keys = {
        "APP_PUBLIC_HOST",
        "APP_PUBLIC_SCHEME",
        "CORS_ORIGINS",
        "FRONTEND_ORIGIN",
        "FRONTEND_PUBLIC_URL",
        "GOOGLE_REDIRECT_URI",
        "SESSION_COOKIE_SECURE",
        "SUPABASE_ENABLED",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_URL",
        "SUPABASE_USE_FOR_ADMIN",
        "VOICE_API_BASE_URL",
    }
    env = {
        "SESSION_SECRET": "test-session-secret",
        "SESSION_SECRET_FILE": None,
        "ALLOW_DEV_SESSION_SECRET_FALLBACK": "1",
        "SESSION_COOKIE_SECURE": "0",
        "FRONTEND_ORIGIN": "http://127.0.0.1:5500",
        "GOOGLE_CLIENT_ID": "client-id",
        "GOOGLE_CLIENT_ID_FILE": None,
        "GOOGLE_CLIENT_SECRET": "client-secret",
        "GOOGLE_CLIENT_SECRET_FILE": None,
        "GOOGLE_REDIRECT_URIS": (
            "http://127.0.0.1:8001/auth/callback,"
            "http://localhost:8001/auth/callback"
        ),
        "GOOGLE_REDIRECT_URI": None,
        "AZURE_TRANSLATOR_KEY": "azure-key",
        "AZURE_TRANSLATOR_KEY_FILE": None,
        "CORS_ORIGINS": None,
        "FRONTEND_PUBLIC_URL": None,
        "APP_PUBLIC_HOST": None,
        "APP_PUBLIC_SCHEME": None,
        "VOICE_API_BASE_URL": None,
        # Default Supabase OFF so .env values don't leak into tests.
        # Tests that need Supabase pass explicit overrides.
        "SUPABASE_ENABLED": "false",
        "SUPABASE_USE_FOR_ADMIN": "false",
        "SUPABASE_URL": "",
        "SUPABASE_SERVICE_ROLE_KEY": "",
    }
    env.update(overrides)

    for key, value in env.items():
        if value is None:
            if key in blank_override_keys:
                monkeypatch.setenv(key, "")
            else:
                monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)

    return load_module("stt_backend")


def make_request(host: str) -> Request:
    parsed = urllib.parse.urlparse(f"//{host}")
    host_name = parsed.hostname or "127.0.0.1"
    if ":" in host_name and not host_name.startswith("["):
        host_name = f"[{host_name}]"
    host_port = parsed.port or 80
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/auth/login",
        "query_string": b"",
        "headers": [(b"host", host.encode("utf-8"))],
        "scheme": "http",
        "server": (parsed.hostname or "127.0.0.1", int(host_port)),
        "client": ("testclient", 50000),
        "http_version": "1.1",
    }
    return Request(scope)


def test_safe_next_url_allows_relative_urls_and_blocks_other_origins(
    monkeypatch, load_module
):
    stt_backend = load_stt_backend(monkeypatch, load_module)

    assert (
        stt_backend._safe_next_url("/captions")
        == "http://127.0.0.1:5500/captions"
    )
    assert (
        stt_backend._safe_next_url("http://127.0.0.1:5500/profile")
        == "http://127.0.0.1:5500/profile"
    )
    assert stt_backend._safe_next_url("https://evil.example.com") == (
        "http://127.0.0.1:5500"
    )


def test_safe_next_url_uses_request_host_when_frontend_origin_is_unset(
    monkeypatch, load_module
):
    stt_backend = load_stt_backend(monkeypatch, load_module, FRONTEND_ORIGIN=None)
    request = make_request("203.0.113.25:8001")

    assert (
        stt_backend._safe_next_url("/captions", request=request)
        == "http://203.0.113.25:5500/captions"
    )
    assert (
        stt_backend._safe_next_url(
            "http://203.0.113.25:5500/profile",
            request=request,
        )
        == "http://203.0.113.25:5500/profile"
    )
    assert (
        stt_backend._safe_next_url(
            "http://127.0.0.1:5500/profile",
            request=request,
        )
        == "http://203.0.113.25:5500"
    )


def test_safe_next_url_uses_configured_frontend_port(monkeypatch, load_module):
    stt_backend = load_stt_backend(
        monkeypatch,
        load_module,
        FRONTEND_ORIGIN=None,
        FRONTEND_PORT="6600",
    )
    request = make_request("203.0.113.25:8001")

    assert (
        stt_backend._safe_next_url("/captions", request=request)
        == "http://203.0.113.25:6600/captions"
    )
    assert (
        stt_backend._safe_next_url(
            "http://203.0.113.25:6600/profile",
            request=request,
        )
        == "http://203.0.113.25:6600/profile"
    )
    assert (
        stt_backend._safe_next_url(
            "http://127.0.0.1:6600/profile",
            request=request,
        )
        == "http://203.0.113.25:6600"
    )


def test_pick_redirect_uri_prefers_request_host(monkeypatch, load_module):
    stt_backend = load_stt_backend(monkeypatch, load_module)

    assert (
        stt_backend._pick_redirect_uri_for_request(make_request("localhost:8001"))
        == "http://localhost:8001/auth/callback"
    )


def test_pick_redirect_uri_prefers_proxy_visible_callback_when_configured(
    monkeypatch, load_module
):
    stt_backend = load_stt_backend(
        monkeypatch,
        load_module,
        FRONTEND_ORIGIN=None,
        APP_PUBLIC_HOST="captions.example.test",
        APP_PUBLIC_SCHEME="https",
        VOICE_API_BASE_URL="/voice-api",
        GOOGLE_REDIRECT_URIS=(
            "https://captions.example.test/voice-api/auth/callback,"
            "http://127.0.0.1:8001/auth/callback"
        ),
    )

    assert (
        stt_backend._pick_redirect_uri_for_request(make_request("captions.example.test"))
        == "https://captions.example.test/voice-api/auth/callback"
    )


@pytest.mark.legacy_auth
def test_state_token_round_trip_and_tamper_detection(monkeypatch, load_module):
    stt_backend = load_stt_backend(monkeypatch, load_module)
    token, nonce = stt_backend._make_state_token(
        "http://127.0.0.1:5500/captions",
        "http://127.0.0.1:8001/auth/callback",
    )

    payload = stt_backend._parse_state_token(token)

    assert payload["nonce"] == nonce
    assert payload["next"] == "http://127.0.0.1:5500/captions"
    assert payload["ru"] == "http://127.0.0.1:8001/auth/callback"

    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    assert stt_backend._parse_state_token(tampered) is None

    monkeypatch.setattr(stt_backend.time, "time", lambda: payload["ts"] + 3600)
    assert stt_backend._parse_state_token(token, max_age_sec=10) is None


def test_safe_next_url_prefers_configured_public_browser_origin(
    monkeypatch, load_module
):
    stt_backend = load_stt_backend(
        monkeypatch,
        load_module,
        FRONTEND_ORIGIN=None,
        APP_PUBLIC_HOST="captions.example.test",
        APP_PUBLIC_SCHEME="https",
        VOICE_API_BASE_URL="/voice-api",
    )
    request = make_request("127.0.0.1:8001")

    assert (
        stt_backend._safe_next_url("/captions", request=request)
        == "https://captions.example.test/captions"
    )
    assert (
        stt_backend._safe_next_url(
            "https://captions.example.test/profile",
            request=request,
        )
        == "https://captions.example.test/profile"
    )
    assert (
        stt_backend._safe_next_url(
            "https://evil.example.com/profile",
            request=request,
        )
        == "https://captions.example.test"
    )


def test_voice_cors_falls_back_to_configured_public_origin(monkeypatch, load_module):
    stt_backend = load_stt_backend(
        monkeypatch,
        load_module,
        CORS_ORIGINS=None,
        FRONTEND_ORIGIN="https://captions.example.test",
    )

    settings = stt_backend._build_cors_settings()

    assert settings["allow_origins"] == ["https://captions.example.test"]
    assert settings["allow_origin_regex"] is None
    assert settings["allow_credentials"] is True


def test_voice_cors_falls_back_to_local_frontend_only(monkeypatch, load_module):
    stt_backend = load_stt_backend(
        monkeypatch,
        load_module,
        CORS_ORIGINS=None,
        FRONTEND_ORIGIN=None,
    )

    settings = stt_backend._build_cors_settings()

    assert "http://127.0.0.1:5500" in settings["allow_origins"]
    assert "http://localhost:5500" in settings["allow_origins"]
    assert "http://[::1]:5500" in settings["allow_origins"]
    assert settings["allow_origin_regex"] == r"^https?://[^/]+:5500$"
    assert settings["allow_credentials"] is True


@pytest.mark.legacy_auth
def test_session_cookie_secure_auto_defaults_for_https_public_origin(
    monkeypatch, load_module
):
    stt_backend = load_stt_backend(
        monkeypatch,
        load_module,
        SESSION_COOKIE_SECURE=None,
        FRONTEND_ORIGIN="https://captions.example.test",
    )

    assert stt_backend.SESSION_COOKIE_SECURE is True


@pytest.mark.legacy_auth
def test_session_cookie_secure_stays_disabled_for_local_http_defaults(
    monkeypatch, load_module
):
    stt_backend = load_stt_backend(
        monkeypatch,
        load_module,
        SESSION_COOKIE_SECURE=None,
        FRONTEND_ORIGIN=None,
    )

    assert stt_backend.SESSION_COOKIE_SECURE is False


def test_health_endpoint_reports_dependency_status(monkeypatch, load_module):
    stt_backend = load_stt_backend(monkeypatch, load_module)
    monkeypatch.setattr(
        stt_backend, "_build_speech_client", lambda: (object(), "stt-creds.json")
    )
    monkeypatch.setattr(
        stt_backend,
        "tts_status",
        lambda: {"configured": True, "creds": "tts-creds.json"},
    )

    client = TestClient(stt_backend.app)
    response = client.get("/health")

    assert response.status_code == 200
    # /health reports `authProvider` since the move to Supabase JWT. The three
    # cookie-session fields it used to expose (authConfigured,
    # sessionUsesDevFallback, sessionCookieSecure) describe state that no longer
    # exists. This stays a strict equality assertion — the expected payload was
    # updated to the current contract, not relaxed to a subset.
    assert response.json() == {
        "ok": True,
        "authProvider": "supabase",
        "sttConfigured": True,
        "sttCreds": "stt-creds.json",
        "azureConfigured": True,
        "ttsConfigured": True,
        "ttsCreds": "tts-creds.json",
    }


def test_health_endpoint_respects_azure_key_file(monkeypatch, tmp_path, load_module):
    key_file = tmp_path / "azure-key.txt"
    key_file.write_text("file-azure-key\n", encoding="utf-8")

    stt_backend = load_stt_backend(
        monkeypatch,
        load_module,
        AZURE_TRANSLATOR_KEY="",
        AZURE_TRANSLATOR_KEY_FILE=str(key_file),
    )
    monkeypatch.setattr(
        stt_backend, "_build_speech_client", lambda: (object(), "stt-creds.json")
    )
    monkeypatch.setattr(
        stt_backend,
        "tts_status",
        lambda: {"configured": True, "creds": "tts-creds.json"},
    )

    client = TestClient(stt_backend.app)
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["azureConfigured"] is True


@pytest.mark.legacy_auth
def test_stt_backend_reads_secrets_from_file_envs(tmp_path, monkeypatch, load_module):
    session_file = tmp_path / "session-secret.txt"
    client_secret_file = tmp_path / "google-client-secret.txt"
    session_file.write_text("file-session-secret\n", encoding="utf-8")
    client_secret_file.write_text("file-google-client-secret\n", encoding="utf-8")

    stt_backend = load_stt_backend(
        monkeypatch,
        load_module,
        SESSION_SECRET="",
        SESSION_SECRET_FILE=str(session_file),
        GOOGLE_CLIENT_SECRET="",
        GOOGLE_CLIENT_SECRET_FILE=str(client_secret_file),
    )

    assert stt_backend.SESSION_SECRET == "file-session-secret"
    assert stt_backend.GOOGLE_CLIENT_SECRET == "file-google-client-secret"
    assert stt_backend.SESSION_SECRET_USES_DEV_FALLBACK is False


@pytest.mark.legacy_auth
def test_stt_backend_can_disable_dev_session_fallback(
    monkeypatch, load_module
):
    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        load_stt_backend(
            monkeypatch,
            load_module,
            SESSION_SECRET="",
            ALLOW_DEV_SESSION_SECRET_FALLBACK="0",
        )


@pytest.mark.legacy_auth
def test_api_translate_requires_authenticated_session(monkeypatch, load_module):
    stt_backend = load_stt_backend(monkeypatch, load_module)
    client = TestClient(stt_backend.app)

    response = client.post(
        "/api/translate",
        json={"text": "Hello", "from_lang": "en", "to_lang": "es"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated"


def test_api_translate_short_circuits_when_languages_match(monkeypatch, load_module):
    stt_backend = load_stt_backend(monkeypatch, load_module)
    monkeypatch.setattr(stt_backend, "_require_user_http", lambda request: {"sub": "1"})
    monkeypatch.setattr(
        stt_backend,
        "_get_translator",
        lambda: (_ for _ in ()).throw(AssertionError("translator should not be used")),
    )

    client = TestClient(stt_backend.app)
    response = client.post(
        "/api/translate",
        json={"text": "  Hello  ", "from_lang": "en-US", "to_lang": "english"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "translatedText": "Hello",
        "to": "en",
        "from": "en",
        "sourceText": "Hello",
    }


def test_api_translate_uses_translator_result(monkeypatch, load_module):
    stt_backend = load_stt_backend(monkeypatch, load_module)
    monkeypatch.setattr(stt_backend, "_require_user_http", lambda request: {"sub": "1"})

    class FakeTranslator:
        async def translate(self, text, to_lang, from_lang=None):
            assert text == "Hello"
            assert to_lang == "es"
            assert from_lang == "en"
            return {
                "translatedText": " Hola ",
                "to": "es",
                "from": "en",
                "sourceText": "Hello",
            }

    monkeypatch.setattr(stt_backend, "_get_translator", lambda: FakeTranslator())

    client = TestClient(stt_backend.app)
    response = client.post(
        "/api/translate",
        json={"text": "Hello", "from_lang": "english", "to_lang": "spanish"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "translatedText": "Hola",
        "to": "es",
        "from": "en",
        "sourceText": "Hello",
    }


# â”€â”€ Supabase profile sync tests â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


@pytest.mark.legacy_auth
def test_profile_sync_disabled_when_supabase_off(monkeypatch, load_module):
    """When SUPABASE_ENABLED is not set, _profile_repo is None and auth still works."""
    stt_backend = load_stt_backend(monkeypatch, load_module)
    assert stt_backend._profile_repo is None
    client = TestClient(stt_backend.app)
    resp = client.get("/auth/me")
    assert resp.status_code == 401


def test_profile_repo_created_when_supabase_enabled(monkeypatch, load_module):
    """When Supabase is enabled and configured, _profile_repo is created."""
    stt_backend = load_stt_backend(
        monkeypatch,
        load_module,
        SUPABASE_ENABLED="1",
        SUPABASE_URL="https://fake.supabase.co",
        SUPABASE_SERVICE_ROLE_KEY="fake-key-1234",
    )
    assert stt_backend._profile_repo is not None


def test_profile_repo_safe_init_on_misconfig(monkeypatch, load_module):
    """When SUPABASE_ENABLED=1 but URL missing, _profile_repo is None (no crash)."""
    stt_backend = load_stt_backend(
        monkeypatch,
        load_module,
        SUPABASE_ENABLED="1",
        SUPABASE_URL="",
        SUPABASE_SERVICE_ROLE_KEY="fake-key-1234",
    )
    assert stt_backend._profile_repo is None
    # Auth still works
    client = TestClient(stt_backend.app)
    resp = client.get("/auth/me")
    assert resp.status_code == 401


def _mock_auth_callback(monkeypatch, stt_backend, *, upsert_side_effect=None):
    """Set up mocks for a full auth_callback test and return the mock repo."""
    from unittest.mock import MagicMock

    fake_id_info = {
        "sub": "google-123",
        "email": "user@example.com",
        "email_verified": True,
        "name": "Test User",
        "picture": "https://photo.example.com/user.jpg",
    }
    monkeypatch.setattr(
        stt_backend.google_id_token,
        "verify_oauth2_token",
        lambda *a, **kw: fake_id_info,
    )

    mock_repo = MagicMock()
    if upsert_side_effect is not None:
        mock_repo.upsert_from_google_user.side_effect = upsert_side_effect
    else:
        mock_repo.upsert_from_google_user.return_value = {}
    monkeypatch.setattr(stt_backend, "_profile_repo", mock_repo)

    # Replace _sync_profile_background with an inline (non-threaded) version
    # so that the upsert runs synchronously and we can verify the mock.
    def _sync_inline(user):
        try:
            mock_repo.upsert_from_google_user(
                google_sub=user["sub"],
                email=user["email"],
                name=user["name"],
                picture=user["picture"],
                email_verified=user["email_verified"],
            )
        except Exception:
            import logging
            logging.getLogger("stt_backend").warning(
                "Supabase profile sync failed (non-fatal)", exc_info=True,
            )

    monkeypatch.setattr(stt_backend, "_sync_profile_background", _sync_inline)

    state_token, _nonce = stt_backend._make_state_token(
        "http://127.0.0.1:5500/",
        "http://127.0.0.1:8001/auth/callback",
    )
    return mock_repo, state_token


@pytest.mark.legacy_auth
def test_profile_sync_called_on_login(monkeypatch, load_module):
    """Profile upsert is called with the correct user data on successful login."""
    from unittest.mock import patch

    stt_backend = load_stt_backend(
        monkeypatch,
        load_module,
        SUPABASE_ENABLED="1",
        SUPABASE_URL="https://fake.supabase.co",
        SUPABASE_SERVICE_ROLE_KEY="fake-key-1234",
    )
    mock_repo, state_token = _mock_auth_callback(monkeypatch, stt_backend)

    class FakeTokenResp:
        status_code = 200
        def json(self):
            return {"id_token": "fake.jwt.token", "access_token": "at"}

    async def mock_post(*args, **kwargs):
        return FakeTokenResp()

    with patch("httpx.AsyncClient.post", side_effect=mock_post):
        client = TestClient(stt_backend.app)
        resp = client.get(
            f"/auth/callback?code=authcode&state={state_token}",
            follow_redirects=False,
        )

    assert resp.status_code == 302
    mock_repo.upsert_from_google_user.assert_called_once()
    call_kw = mock_repo.upsert_from_google_user.call_args
    # Verify field mapping
    assert call_kw.kwargs.get("google_sub") == "google-123"
    assert call_kw.kwargs.get("email") == "user@example.com"
    assert call_kw.kwargs.get("name") == "Test User"
    assert call_kw.kwargs.get("picture") == "https://photo.example.com/user.jpg"
    assert call_kw.kwargs.get("email_verified") is True


@pytest.mark.legacy_auth
def test_profile_sync_failure_does_not_break_login(monkeypatch, load_module):
    """Login returns 302 even when the Supabase upsert raises."""
    from unittest.mock import patch

    stt_backend = load_stt_backend(
        monkeypatch,
        load_module,
        SUPABASE_ENABLED="1",
        SUPABASE_URL="https://fake.supabase.co",
        SUPABASE_SERVICE_ROLE_KEY="fake-key-1234",
    )
    mock_repo, state_token = _mock_auth_callback(
        monkeypatch,
        stt_backend,
        upsert_side_effect=RuntimeError("Supabase is down"),
    )

    class FakeTokenResp:
        status_code = 200
        def json(self):
            return {"id_token": "fake.jwt.token", "access_token": "at"}

    async def mock_post(*args, **kwargs):
        return FakeTokenResp()

    with patch("httpx.AsyncClient.post", side_effect=mock_post):
        client = TestClient(stt_backend.app)
        resp = client.get(
            f"/auth/callback?code=authcode&state={state_token}",
            follow_redirects=False,
        )

    # Login succeeds despite Supabase failure
    assert resp.status_code == 302
    mock_repo.upsert_from_google_user.assert_called_once()


@pytest.mark.legacy_auth
def test_profile_sync_no_secrets_in_warning(monkeypatch, load_module, caplog):
    """Warning log from failed sync does not contain the service role key."""
    import logging
    from unittest.mock import patch

    stt_backend = load_stt_backend(
        monkeypatch,
        load_module,
        SUPABASE_ENABLED="1",
        SUPABASE_URL="https://fake.supabase.co",
        SUPABASE_SERVICE_ROLE_KEY="super-secret-key-9999",
    )
    _mock_repo, state_token = _mock_auth_callback(
        monkeypatch,
        stt_backend,
        upsert_side_effect=RuntimeError("connection refused"),
    )

    class FakeTokenResp:
        status_code = 200
        def json(self):
            return {"id_token": "fake.jwt.token", "access_token": "at"}

    async def mock_post(*args, **kwargs):
        return FakeTokenResp()

    with caplog.at_level(logging.WARNING), patch(
        "httpx.AsyncClient.post", side_effect=mock_post
    ):
        client = TestClient(stt_backend.app)
        client.get(
            f"/auth/callback?code=authcode&state={state_token}",
            follow_redirects=False,
        )

    assert "super-secret-key-9999" not in caplog.text


# ---------------------------------------------------------------------------
# Task 2 regression: shared session middleware still attached
# ---------------------------------------------------------------------------


@pytest.mark.legacy_auth
def test_session_middleware_still_present(monkeypatch, load_module):
    """After Task 2 refactor, SessionMiddleware must still be on the Voice app."""
    from starlette.middleware.sessions import SessionMiddleware

    stt_backend = load_stt_backend(monkeypatch, load_module)
    middleware_classes = [m.cls for m in stt_backend.app.user_middleware]
    assert SessionMiddleware in middleware_classes


# ---------------------------------------------------------------------------
# Task 3: Transcript CRUD route tests
# ---------------------------------------------------------------------------


def _mock_transcript_routes(monkeypatch, stt_backend):
    """Set up auth + transcript repo mocks for transcript route tests."""
    from unittest.mock import MagicMock

    monkeypatch.setattr(
        stt_backend, "_require_user_http", lambda request: {"sub": "google-123"}
    )
    mock_repo = MagicMock()
    monkeypatch.setattr(stt_backend, "_transcript_repo", mock_repo)
    return mock_repo


@pytest.mark.legacy_auth
def test_transcripts_post_requires_auth(monkeypatch, load_module):
    """POST /api/transcripts returns 401 without session."""
    stt_backend = load_stt_backend(monkeypatch, load_module)
    client = TestClient(stt_backend.app)
    resp = client.post("/api/transcripts", json={
        "client_history_id": "h1", "mode": "asl", "text": "Hello",
    })
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Not authenticated"


@pytest.mark.legacy_auth
def test_transcripts_list_requires_auth(monkeypatch, load_module):
    """GET /api/transcripts returns 401 without session."""
    stt_backend = load_stt_backend(monkeypatch, load_module)
    client = TestClient(stt_backend.app)
    resp = client.get("/api/transcripts")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Not authenticated"


@pytest.mark.legacy_auth
def test_transcripts_delete_requires_auth(monkeypatch, load_module):
    """DELETE /api/transcripts/1 returns 401 without session."""
    stt_backend = load_stt_backend(monkeypatch, load_module)
    client = TestClient(stt_backend.app)
    resp = client.delete("/api/transcripts/1")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Not authenticated"


def test_transcripts_post_returns_503_when_supabase_off(monkeypatch, load_module):
    """POST /api/transcripts returns 503 when _transcript_repo is None."""
    stt_backend = load_stt_backend(monkeypatch, load_module)
    monkeypatch.setattr(
        stt_backend, "_require_user_http", lambda request: {"sub": "1"}
    )
    monkeypatch.setattr(stt_backend, "_transcript_repo", None)
    client = TestClient(stt_backend.app)
    resp = client.post("/api/transcripts", json={
        "client_history_id": "h1", "mode": "asl", "text": "Hello",
    })
    assert resp.status_code == 503
    assert "not available" in resp.json()["detail"]


def test_transcripts_list_returns_503_when_supabase_off(monkeypatch, load_module):
    """GET /api/transcripts returns 503 when _transcript_repo is None."""
    stt_backend = load_stt_backend(monkeypatch, load_module)
    monkeypatch.setattr(
        stt_backend, "_require_user_http", lambda request: {"sub": "1"}
    )
    monkeypatch.setattr(stt_backend, "_transcript_repo", None)
    client = TestClient(stt_backend.app)
    resp = client.get("/api/transcripts")
    assert resp.status_code == 503
    assert "not available" in resp.json()["detail"]


def test_transcripts_post_upserts_and_returns_row(monkeypatch, load_module):
    """POST /api/transcripts calls repo.upsert with correct args and returns the row."""
    stt_backend = load_stt_backend(monkeypatch, load_module)
    mock_repo = _mock_transcript_routes(monkeypatch, stt_backend)
    mock_repo.upsert.return_value = {"id": 42, "text": "Hello world"}

    client = TestClient(stt_backend.app)
    resp = client.post("/api/transcripts", json={
        "client_history_id": "hist-abc",
        "mode": "asl",
        "started_at": "2026-01-01T00:00:00Z",
        "text": "Hello world",
    })

    assert resp.status_code == 200
    assert resp.json() == {"id": 42, "text": "Hello world"}
    mock_repo.upsert.assert_called_once_with(
        google_sub="google-123",
        client_history_id="hist-abc",
        mode="asl",
        started_at="2026-01-01T00:00:00Z",
        text="Hello world",
    )


def test_transcripts_post_ignores_client_google_sub(monkeypatch, load_module):
    """POST body includes google_sub; backend still uses user['sub'] from session."""
    stt_backend = load_stt_backend(monkeypatch, load_module)
    mock_repo = _mock_transcript_routes(monkeypatch, stt_backend)
    mock_repo.upsert.return_value = {"id": 99}

    client = TestClient(stt_backend.app)
    resp = client.post("/api/transcripts", json={
        "client_history_id": "h1",
        "mode": "asl",
        "text": "test",
        "google_sub": "attacker-sub-value",
    })

    assert resp.status_code == 200
    call_kw = mock_repo.upsert.call_args
    assert call_kw.kwargs["google_sub"] == "google-123"


def test_transcripts_post_started_at_optional(monkeypatch, load_module):
    """POST /api/transcripts passes None for started_at when omitted."""
    stt_backend = load_stt_backend(monkeypatch, load_module)
    mock_repo = _mock_transcript_routes(monkeypatch, stt_backend)
    mock_repo.upsert.return_value = {"id": 43}

    client = TestClient(stt_backend.app)
    resp = client.post("/api/transcripts", json={
        "client_history_id": "hist-xyz",
        "mode": "letter",
        "text": "Test",
    })

    assert resp.status_code == 200
    call_kw = mock_repo.upsert.call_args
    assert call_kw.kwargs.get("started_at") is None


def test_transcripts_post_rejects_empty_fields(monkeypatch, load_module):
    """POST returns 400 for empty client_history_id or mode."""
    stt_backend = load_stt_backend(monkeypatch, load_module)
    mock_repo = _mock_transcript_routes(monkeypatch, stt_backend)

    client = TestClient(stt_backend.app)

    resp = client.post("/api/transcripts", json={
        "client_history_id": "", "mode": "asl", "text": "Hello",
    })
    assert resp.status_code == 400
    assert "client_history_id" in resp.json()["detail"]

    resp = client.post("/api/transcripts", json={
        "client_history_id": "h1", "mode": "  ", "text": "Hello",
    })
    assert resp.status_code == 400
    assert "mode" in resp.json()["detail"]

    mock_repo.upsert.assert_not_called()


def test_transcripts_list_returns_rows(monkeypatch, load_module):
    """GET /api/transcripts returns list from repo, passes limit."""
    stt_backend = load_stt_backend(monkeypatch, load_module)
    mock_repo = _mock_transcript_routes(monkeypatch, stt_backend)
    mock_repo.list_by_user.return_value = [{"id": 1}, {"id": 2}]

    client = TestClient(stt_backend.app)
    resp = client.get("/api/transcripts?limit=10")

    assert resp.status_code == 200
    assert resp.json() == [{"id": 1}, {"id": 2}]
    mock_repo.list_by_user.assert_called_once_with(
        google_sub="google-123", limit=10,
    )


def test_transcripts_delete_success(monkeypatch, load_module):
    """DELETE /api/transcripts/5 returns 200 when repo confirms deletion."""
    stt_backend = load_stt_backend(monkeypatch, load_module)
    mock_repo = _mock_transcript_routes(monkeypatch, stt_backend)
    mock_repo.delete_by_id.return_value = True

    client = TestClient(stt_backend.app)
    resp = client.delete("/api/transcripts/5")

    assert resp.status_code == 200
    assert resp.json() == {"deleted": True}
    mock_repo.delete_by_id.assert_called_once_with(
        transcript_id=5, google_sub="google-123",
    )


def test_transcripts_delete_not_found(monkeypatch, load_module):
    """DELETE /api/transcripts/999 returns 404 when repo says not deleted."""
    stt_backend = load_stt_backend(monkeypatch, load_module)
    mock_repo = _mock_transcript_routes(monkeypatch, stt_backend)
    mock_repo.delete_by_id.return_value = False

    client = TestClient(stt_backend.app)
    resp = client.delete("/api/transcripts/999")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Transcript not found"


# ---- Bulk-delete (DELETE /api/transcripts) ----


@pytest.mark.legacy_auth
def test_transcripts_delete_all_requires_auth(monkeypatch, load_module):
    """DELETE /api/transcripts (bulk) returns 401 without session."""
    stt_backend = load_stt_backend(monkeypatch, load_module)
    client = TestClient(stt_backend.app)
    resp = client.delete("/api/transcripts")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Not authenticated"


def test_transcripts_delete_all_returns_503_when_supabase_off(monkeypatch, load_module):
    """DELETE /api/transcripts returns 503 when _transcript_repo is None."""
    stt_backend = load_stt_backend(monkeypatch, load_module)
    monkeypatch.setattr(
        stt_backend, "_require_user_http", lambda request: {"sub": "1"}
    )
    monkeypatch.setattr(stt_backend, "_transcript_repo", None)
    client = TestClient(stt_backend.app)
    resp = client.delete("/api/transcripts")
    assert resp.status_code == 503
    assert "not available" in resp.json()["detail"]


def test_transcripts_delete_all_success(monkeypatch, load_module):
    """DELETE /api/transcripts returns 200 with count of deleted rows."""
    stt_backend = load_stt_backend(monkeypatch, load_module)
    mock_repo = _mock_transcript_routes(monkeypatch, stt_backend)
    mock_repo.delete_all_by_user.return_value = 3

    client = TestClient(stt_backend.app)
    resp = client.delete("/api/transcripts")

    assert resp.status_code == 200
    assert resp.json() == {"deleted": True, "count": 3}
    mock_repo.delete_all_by_user.assert_called_once_with(google_sub="google-123")


def test_transcripts_delete_all_empty(monkeypatch, load_module):
    """DELETE /api/transcripts returns 200 with count=0 when user has no transcripts."""
    stt_backend = load_stt_backend(monkeypatch, load_module)
    mock_repo = _mock_transcript_routes(monkeypatch, stt_backend)
    mock_repo.delete_all_by_user.return_value = 0

    client = TestClient(stt_backend.app)
    resp = client.delete("/api/transcripts")

    assert resp.status_code == 200
    assert resp.json() == {"deleted": True, "count": 0}
