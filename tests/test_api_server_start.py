from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _api_server_ml_stubs(ensure_api_server_importable):
    """Ensure rtmlib/torch are available before any test loads api_server."""
    ensure_api_server_importable()


def load_api_server(monkeypatch, load_module, **env_overrides):
    for key, value in env_overrides.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)

    api_server = load_module("api_server")
    monkeypatch.setattr(api_server.pipeline, "stop", lambda: None)
    monkeypatch.setattr(api_server.pipeline, "release_gpu", lambda: None)
    # Bypass auth for tests that focus on non-auth behavior
    monkeypatch.setattr(api_server, "_require_user_http", lambda request: {"sub": "test"})
    return api_server


def test_start_ignores_public_ollama_transport_overrides(monkeypatch, load_module):
    api_server = load_api_server(monkeypatch, load_module)
    captured = {}

    def fake_start(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(api_server.pipeline, "start", fake_start)

    with TestClient(api_server.app, raise_server_exceptions=False) as client:
        response = client.post(
            "/start",
            json={
                "camera_source": "remote",
                "conf_threshold": 0.42,
                "disable_translation": True,
                "ollama_url": "https://attacker.example.invalid/api/chat",
                "ollama_model": "evil-model",
                "ollama_timeout": 999.0,
            },
        )

    assert response.status_code == 200
    body = response.json()

    assert captured["camera_source"] == "remote"
    assert captured["conf_threshold"] == 0.42
    assert captured["disable_translation"] is True
    assert captured["ollama_url"] == api_server._default_config["ollama_url"]
    assert captured["ollama_model"] == api_server._default_config["ollama_model"]
    assert captured["ollama_timeout"] == api_server._default_config["ollama_timeout"]

    assert body["config"]["camera_source"] == "remote"
    assert body["config"]["conf_threshold"] == 0.42
    assert body["config"]["disable_translation"] is True
    assert "ollama_url" not in body["config"]
    assert "ollama_model" not in body["config"]
    assert "ollama_timeout" not in body["config"]


def test_start_response_does_not_leak_ollama_token_or_transport(
    monkeypatch, load_module
):
    api_server = load_api_server(
        monkeypatch,
        load_module,
        OLLAMA_AUTH_TOKEN="super-secret-token",
    )
    monkeypatch.setattr(api_server.pipeline, "start", lambda **kwargs: None)

    with TestClient(api_server.app, raise_server_exceptions=False) as client:
        response = client.post("/start", json={"camera_source": "remote"})

    assert response.status_code == 200
    body = response.json()
    serialized = json.dumps(body)

    assert "super-secret-token" not in serialized
    assert "OLLAMA_AUTH_TOKEN" not in serialized
    assert "auth_token" not in serialized
    assert "ollama_url" not in serialized
    assert "ollama_model" not in serialized
    assert "ollama_timeout" not in serialized
    assert body["config"] == {
        "checkpoint": api_server._default_config["checkpoint"],
        "camera_id": api_server._default_config["camera_id"],
        "camera_source": "remote",
        "conf_threshold": api_server._default_config["conf_threshold"],
        "disable_translation": api_server._default_config["disable_translation"],
    }


# ── OLLAMA_ASL_ENABLED env-driven toggle ─────────────────────────────────


def test_start_default_disable_translation_when_ollama_asl_enabled(
    monkeypatch, load_module
):
    api_server = load_api_server(
        monkeypatch, load_module, OLLAMA_ASL_ENABLED="true"
    )
    captured = {}

    def fake_start(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(api_server.pipeline, "start", fake_start)

    with TestClient(api_server.app, raise_server_exceptions=False) as client:
        response = client.post("/start", json={})

    assert response.status_code == 200
    assert captured["disable_translation"] is False


def test_start_default_disable_translation_when_ollama_asl_disabled(
    monkeypatch, load_module
):
    api_server = load_api_server(
        monkeypatch, load_module, OLLAMA_ASL_ENABLED="false"
    )
    captured = {}

    def fake_start(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(api_server.pipeline, "start", fake_start)

    with TestClient(api_server.app, raise_server_exceptions=False) as client:
        response = client.post("/start", json={})

    assert response.status_code == 200
    assert captured["disable_translation"] is True


def test_start_hard_gate_when_env_disables_asl_ollama(monkeypatch, load_module):
    """Env OLLAMA_ASL_ENABLED=false must NOT be bypassable by request body."""
    api_server = load_api_server(
        monkeypatch, load_module, OLLAMA_ASL_ENABLED="false"
    )
    captured = {}

    def fake_start(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(api_server.pipeline, "start", fake_start)

    with TestClient(api_server.app, raise_server_exceptions=False) as client:
        response = client.post(
            "/start", json={"disable_translation": False}
        )

    assert response.status_code == 200
    body = response.json()
    assert captured["disable_translation"] is True
    assert body["config"]["disable_translation"] is True
