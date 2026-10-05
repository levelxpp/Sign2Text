"""Tests for backend/session_config.py — shared session middleware foundation."""

from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SESSION_ENV_KEYS = (
    "SESSION_SECRET",
    "SESSION_SECRET_FILE",
    "SESSION_COOKIE_NAME",
    "SESSION_COOKIE_DOMAIN",
    "SESSION_COOKIE_SAMESITE",
    "SESSION_COOKIE_SECURE",
    "ALLOW_DEV_SESSION_SECRET_FALLBACK",
)


@pytest.fixture(autouse=True)
def _clean_session_env(monkeypatch):
    """Clear all session env vars before each test."""
    for key in _SESSION_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _import_session_config(load_module):
    return load_module("session_config")


# ---------------------------------------------------------------------------
# resolve_session_config
# ---------------------------------------------------------------------------


class TestResolveDefaults:
    def test_defaults_with_explicit_secret(self, monkeypatch, load_module):
        monkeypatch.setenv("SESSION_SECRET", "test-secret-123")
        mod = _import_session_config(load_module)
        cfg = mod.resolve_session_config()

        assert cfg["secret_key"] == "test-secret-123"
        assert cfg["session_cookie"] == "sign2text_session"
        assert cfg["same_site"] == "lax"
        assert cfg["domain"] is None
        assert cfg["https_only"] is False
        assert cfg["uses_dev_fallback"] is False


class TestResolveReadsEnvVars:
    def test_reads_all_env_vars(self, monkeypatch, load_module):
        monkeypatch.setenv("SESSION_SECRET", "my-secret")
        monkeypatch.setenv("SESSION_COOKIE_NAME", "my_cookie")
        monkeypatch.setenv("SESSION_COOKIE_DOMAIN", ".example.com")
        monkeypatch.setenv("SESSION_COOKIE_SAMESITE", "strict")
        monkeypatch.setenv("SESSION_COOKIE_SECURE", "1")
        mod = _import_session_config(load_module)
        cfg = mod.resolve_session_config()

        assert cfg["secret_key"] == "my-secret"
        assert cfg["session_cookie"] == "my_cookie"
        assert cfg["domain"] == ".example.com"
        assert cfg["same_site"] == "strict"
        assert cfg["https_only"] is True


class TestAutoDerivesHttps:
    def test_https_origin_sets_secure(self, monkeypatch, load_module):
        monkeypatch.setenv("SESSION_SECRET", "test-secret")
        mod = _import_session_config(load_module)
        cfg = mod.resolve_session_config(browser_origin="https://example.com")
        assert cfg["https_only"] is True

    def test_http_origin_does_not_set_secure(self, monkeypatch, load_module):
        monkeypatch.setenv("SESSION_SECRET", "test-secret")
        mod = _import_session_config(load_module)
        cfg = mod.resolve_session_config(browser_origin="http://localhost:5500")
        assert cfg["https_only"] is False

    def test_explicit_secure_overrides_origin(self, monkeypatch, load_module):
        monkeypatch.setenv("SESSION_SECRET", "test-secret")
        monkeypatch.setenv("SESSION_COOKIE_SECURE", "0")
        mod = _import_session_config(load_module)
        cfg = mod.resolve_session_config(browser_origin="https://example.com")
        assert cfg["https_only"] is False


class TestDevFallbackSecret:
    def test_generates_fallback_when_allowed(self, monkeypatch, load_module):
        monkeypatch.setenv("ALLOW_DEV_SESSION_SECRET_FALLBACK", "1")
        mod = _import_session_config(load_module)
        cfg = mod.resolve_session_config()

        assert cfg["secret_key"]  # non-empty
        assert cfg["uses_dev_fallback"] is True

    def test_raises_when_no_secret_and_fallback_disabled(
        self, monkeypatch, load_module
    ):
        monkeypatch.setenv("ALLOW_DEV_SESSION_SECRET_FALLBACK", "0")
        mod = _import_session_config(load_module)

        with pytest.raises(RuntimeError, match="SESSION_SECRET is required"):
            mod.resolve_session_config()


class TestInvalidSamesiteFallback:
    def test_falls_back_to_lax(self, monkeypatch, load_module):
        monkeypatch.setenv("SESSION_SECRET", "test-secret")
        monkeypatch.setenv("SESSION_COOKIE_SAMESITE", "bogus")
        mod = _import_session_config(load_module)
        cfg = mod.resolve_session_config()
        assert cfg["same_site"] == "lax"


# ---------------------------------------------------------------------------
# add_session_middleware
# ---------------------------------------------------------------------------


class TestAddSessionMiddleware:
    def test_attaches_middleware_to_app(self, monkeypatch, load_module):
        monkeypatch.setenv("SESSION_SECRET", "test-secret")
        mod = _import_session_config(load_module)
        app = FastAPI()
        cfg = mod.add_session_middleware(app)

        middleware_classes = [m.cls for m in app.user_middleware]
        assert SessionMiddleware in middleware_classes
        assert cfg["secret_key"] == "test-secret"


# ---------------------------------------------------------------------------
# auth_supabase.get_user_from_ws — lightweight checks that don't require a
# live Supabase instance. Full JWT validation is covered separately.
# ---------------------------------------------------------------------------


class _FakeHeaders(dict):
    def get(self, key, default=""):
        return super().get(key, default)


class _FakeQueryParams(dict):
    def get(self, key, default=""):
        return super().get(key, default)


class _FakeWS:
    def __init__(self, authorization: str = "", token_qp: str = ""):
        self.headers = _FakeHeaders()
        if authorization:
            self.headers["Authorization"] = authorization
        self.query_params = _FakeQueryParams()
        if token_qp:
            self.query_params["token"] = token_qp


class TestGetUserFromWs:
    """Token handling for WebSocket connections.

    `get_user_from_ws` short-circuits to an anonymous principal whenever
    SUPABASE_ENABLED is not truthy, so the app runs locally without a Supabase
    project. These tests exercise the *token* path and therefore enable auth
    explicitly; `test_anonymous_principal_when_auth_disabled` covers the other
    branch, so both halves of the documented contract are pinned.
    """

    @pytest.fixture(autouse=True)
    def _enable_auth(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_ENABLED", "1")
        monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")

    def test_anonymous_principal_when_auth_disabled(self, monkeypatch, load_module):
        monkeypatch.delenv("SUPABASE_ENABLED", raising=False)
        mod = load_module("auth_supabase")
        assert mod.get_user_from_ws(_FakeWS()) == {
            "sub": "anon",
            "email": None,
            "role": "anon",
        }

    def test_returns_none_when_no_token_anywhere(self, load_module):
        mod = load_module("auth_supabase")
        assert mod.get_user_from_ws(_FakeWS()) is None

    def test_returns_none_when_authorization_is_not_bearer(self, load_module):
        mod = load_module("auth_supabase")
        ws = _FakeWS(authorization="Basic abc")
        assert mod.get_user_from_ws(ws) is None

    def test_returns_none_when_token_is_rejected(self, monkeypatch, load_module):
        mod = load_module("auth_supabase")
        from fastapi import HTTPException

        def fake_verify(token):
            raise HTTPException(status_code=401, detail="bad token")

        monkeypatch.setattr(mod, "verify_jwt", fake_verify)
        ws = _FakeWS(authorization="Bearer not-a-real-token")
        assert mod.get_user_from_ws(ws) is None

    def test_uses_query_param_fallback(self, monkeypatch, load_module):
        mod = load_module("auth_supabase")

        captured = {}

        def fake_verify(token):
            captured["token"] = token
            return {"sub": "abc", "email": "x@example.com", "role": "authenticated"}

        monkeypatch.setattr(mod, "verify_jwt", fake_verify)
        ws = _FakeWS(token_qp="from-query-param")
        user = mod.get_user_from_ws(ws)
        assert user and user["sub"] == "abc"
        assert captured["token"] == "from-query-param"


# ---------------------------------------------------------------------------
# CORS + credentials safety
# ---------------------------------------------------------------------------


class TestCorsNoWildcardWithCredentials:
    """Verify build_cors_settings never produces '*' in allow_origins when
    allow_credentials=True — the browser rejects that combination."""

    def test_wildcard_input_is_resolved_to_explicit_origins(self, load_module):
        mod = load_module("browser_origin")
        settings = mod.build_cors_settings(
            explicit_origins="*",
            frontend_port=5500,
            allow_credentials=True,
        )
        assert "*" not in settings["allow_origins"]
        assert settings["allow_credentials"] is True

    def test_empty_input_is_resolved_to_explicit_origins(self, load_module):
        mod = load_module("browser_origin")
        settings = mod.build_cors_settings(
            explicit_origins="",
            frontend_port=5500,
            allow_credentials=True,
        )
        assert "*" not in settings["allow_origins"]

    def test_explicit_origins_are_preserved(self, load_module):
        mod = load_module("browser_origin")
        settings = mod.build_cors_settings(
            explicit_origins="https://app.example.com,https://staging.example.com",
            frontend_port=5500,
            allow_credentials=True,
        )
        assert "*" not in settings["allow_origins"]
        assert "https://app.example.com" in settings["allow_origins"]
        assert "https://staging.example.com" in settings["allow_origins"]
