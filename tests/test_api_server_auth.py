"""Auth enforcement regression tests for ASL API server (api_server.py).

Verifies that protected routes reject unauthenticated requests with 401,
public routes remain accessible, and authenticated callers still work.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

# Excluded from the blocking public scope.
#
# Every test here builds a TestClient over the real ASL app, which runs the
# FastAPI lifespan: it opens the host camera, contacts an Ollama endpoint, and
# starts the inference thread, which loads
# backend/checkpoint/.../best_model.pt. That checkpoint is intentionally not
# published (README, "Known public-release limitations") and CI runners have no
# camera, so these cannot pass here regardless of their assertions — locally
# they hang for minutes on hardware and network access.
#
# They also assert the pre-migration cookie-session contract (401 with detail
# "Not authenticated"); the published guard is Supabase JWT. Rewriting them
# against the JWT contract is tracked in docs/TESTING_STATUS.md.
pytestmark = pytest.mark.requires_private_assets


@pytest.fixture(autouse=True)
def _api_server_ml_stubs(ensure_api_server_importable):
    """Ensure rtmlib/torch are available before any test loads api_server."""
    ensure_api_server_importable()


def _load(monkeypatch, load_module, **env_overrides):
    for key, value in env_overrides.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)

    api_server = load_module("api_server")
    monkeypatch.setattr(api_server.pipeline, "stop", lambda: None)
    monkeypatch.setattr(api_server.pipeline, "release_gpu", lambda: None)
    return api_server


# ── Unauthenticated → 401 ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "method, path, json_body",
    [
        ("post", "/start", None),
        ("post", "/stop", None),
        ("get", "/status", None),
        ("get", "/runtime", None),
        ("get", "/stream", None),
        ("get", "/video_feed", None),
        ("post", "/upload_frame", None),
        ("post", "/manual_sign", None),
        ("post", "/threshold", {"value": 0.5}),
        ("post", "/clear", None),
        ("post", "/reset", None),
    ],
)
def test_protected_route_returns_401_without_session(
    monkeypatch, load_module, method, path, json_body
):
    api_server = _load(monkeypatch, load_module)
    with TestClient(api_server.app, raise_server_exceptions=False) as client:
        kwargs = {}
        if json_body is not None:
            kwargs["json"] = json_body
        response = getattr(client, method)(path, **kwargs)
    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated"


def test_ws_frames_rejects_unauthenticated(monkeypatch, load_module):
    api_server = _load(monkeypatch, load_module)
    with TestClient(api_server.app, raise_server_exceptions=False) as client:
        with pytest.raises(Exception):
            # WebSocket should be rejected before accept (close code 4401)
            with client.websocket_connect("/ws/frames"):
                pass


# ── Public routes remain accessible ──────────────────────────────────────


def test_root_is_public(monkeypatch, load_module):
    api_server = _load(monkeypatch, load_module)
    with TestClient(api_server.app, raise_server_exceptions=False) as client:
        response = client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "ASL Real-Time Translation API"


# ── Authenticated requests work ──────────────────────────────────────────


def test_start_works_when_authenticated(monkeypatch, load_module):
    api_server = _load(monkeypatch, load_module)
    monkeypatch.setattr(
        api_server, "_require_user_http", lambda request: {"sub": "test"}
    )
    monkeypatch.setattr(api_server.pipeline, "start", lambda **kwargs: None)

    with TestClient(api_server.app, raise_server_exceptions=False) as client:
        response = client.post("/start", json={"camera_source": "remote"})

    assert response.status_code == 200
    assert response.json()["status"] == "started"


def test_status_works_when_authenticated(monkeypatch, load_module):
    api_server = _load(monkeypatch, load_module)
    monkeypatch.setattr(
        api_server, "_require_user_http", lambda request: {"sub": "test"}
    )

    with TestClient(api_server.app, raise_server_exceptions=False) as client:
        response = client.get("/status")

    assert response.status_code == 200
