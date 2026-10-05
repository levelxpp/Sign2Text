"""Auth enforcement regression tests for Letter server (letter_server.py).

Verifies that protected routes reject unauthenticated requests with 401,
public routes remain accessible, and authenticated callers still work.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

# Excluded from the blocking public scope — same reasons as
# tests/test_api_server_auth.py: these drive the real Letter app through its
# lifespan, which needs the excluded letter-model weights and camera access,
# and they assert the pre-migration cookie-session contract rather than the
# published Supabase JWT guard. See docs/TESTING_STATUS.md.
pytestmark = pytest.mark.requires_private_assets


def _load(monkeypatch, load_module):
    letter_server = load_module("letter_server")
    return letter_server


# ── Unauthenticated → 401 ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "method, path",
    [
        ("post", "/start"),
        ("post", "/stop"),
        ("get", "/status"),
        ("post", "/upload_frame"),
        ("get", "/stream"),
    ],
)
def test_protected_route_returns_401_without_session(
    monkeypatch, load_module, method, path
):
    letter_server = _load(monkeypatch, load_module)
    with TestClient(letter_server.app, raise_server_exceptions=False) as client:
        response = getattr(client, method)(path)
    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated"


# ── Public routes remain accessible ──────────────────────────────────────


def test_root_is_public(monkeypatch, load_module):
    letter_server = _load(monkeypatch, load_module)
    with TestClient(letter_server.app, raise_server_exceptions=False) as client:
        response = client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "ASL Letter Recognition"


def test_health_is_public(monkeypatch, load_module):
    letter_server = _load(monkeypatch, load_module)
    with TestClient(letter_server.app, raise_server_exceptions=False) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


# ── Authenticated requests work ──────────────────────────────────────────


class _FakeHandDetector:
    def __init__(self, *args, **kwargs):
        pass

    def close(self):
        pass


def test_start_works_when_authenticated(monkeypatch, load_module):
    letter_server = _load(monkeypatch, load_module)
    monkeypatch.setattr(
        letter_server, "_require_user_http", lambda request: {"sub": "test"}
    )
    # Auth scope only — bypass real MediaPipe + sklearn so test isn't coupled
    # to mediapipe import shape that other tests can pollute via sys.modules.
    monkeypatch.setattr(letter_server, "HandDetector", _FakeHandDetector)
    monkeypatch.setattr(
        letter_server.LetterPipeline, "_train_model", lambda self: object()
    )

    with TestClient(letter_server.app, raise_server_exceptions=False) as client:
        response = client.post("/start")

    assert response.status_code == 200
