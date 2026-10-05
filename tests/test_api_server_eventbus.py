from __future__ import annotations

import asyncio
import json
import queue
from datetime import datetime

import pytest


class FakeRequest:
    async def is_disconnected(self) -> bool:
        return False


@pytest.fixture(autouse=True)
def _api_server_ml_stubs(ensure_api_server_importable):
    """Ensure rtmlib/torch are available before any test loads api_server."""
    ensure_api_server_importable()


def test_event_bus_replays_recent_state_events_to_new_subscribers(load_module):
    """State-snapshot events are replayed so a reconnecting client catches up."""
    api_server = load_module("api_server")
    bus = api_server.EventBus(maxsize=2)
    bus.publish("pipeline_started", {"profile": "cpu"})

    subscriber = bus.subscribe()
    event = subscriber.get_nowait()

    assert event["event"] == "pipeline_started"
    assert event["data"] == {"profile": "cpu"}
    datetime.fromisoformat(event["timestamp"])


def test_event_bus_does_not_replay_append_style_events(load_module):
    """Append-style events must never be replayed to a re-subscribing client.

    Replaying them duplicated transcript entries: switching asl -> letter -> asl
    quickly re-opens the SSE stream, and a replayed `translation` / `caption.final`
    made the frontend append an already-signed word a second time. `EventBus`
    therefore records only `_REPLAYABLE_EVENT_TYPES` for catch-up.

    This module previously asserted that `translation` *was* replayed — exactly
    the behaviour the allowlist was introduced to remove — so the assertion has
    been re-pointed at the current contract rather than dropped.
    """
    api_server = load_module("api_server")
    bus = api_server.EventBus(maxsize=2)
    bus.publish("translation", {"english": "Hello"})

    subscriber = bus.subscribe()
    with pytest.raises(queue.Empty):
        subscriber.get_nowait()

    # Suppression applies to replay only — a live subscriber still receives it.
    live = bus.subscribe()
    bus.publish("translation", {"english": "Hello again"})
    delivered = live.get_nowait()
    assert delivered["event"] == "translation"
    assert delivered["data"] == {"english": "Hello again"}


def test_event_bus_drops_subscribers_with_full_queues(load_module):
    api_server = load_module("api_server")
    bus = api_server.EventBus(maxsize=1)
    bus.subscribe()

    bus.publish("first", {"value": 1})
    assert bus.subscriber_count == 1

    bus.publish("second", {"value": 2})
    assert bus.subscriber_count == 0


def test_sse_stream_serializes_event_payloads(monkeypatch, load_module):
    api_server = load_module("api_server")
    bus = api_server.EventBus()
    monkeypatch.setattr(api_server, "event_bus", bus)
    monkeypatch.setattr(api_server, "_require_user_http", lambda request: {"sub": "test"})

    async def collect_first_chunk():
        response = await api_server.sse_stream(FakeRequest())
        bus.publish("gloss_updated", {"gloss": ["HELLO"]})
        chunk = await response.body_iterator.__anext__()
        await response.body_iterator.aclose()
        return chunk

    chunk = asyncio.run(collect_first_chunk())
    if isinstance(chunk, bytes):
        chunk = chunk.decode("utf-8")

    assert chunk.startswith("data: ")
    payload = json.loads(chunk[len("data: ") :].strip())
    assert payload["event"] == "gloss_updated"
    assert payload["data"] == {"gloss": ["HELLO"]}


def test_build_cors_settings_wildcard_falls_back_to_local_frontend_only(
    monkeypatch, load_module
):
    monkeypatch.delenv("ASL_CORS_ORIGINS", raising=False)
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    monkeypatch.delenv("FRONTEND_PUBLIC_URL", raising=False)
    monkeypatch.delenv("APP_PUBLIC_HOST", raising=False)
    monkeypatch.delenv("APP_PUBLIC_SCHEME", raising=False)
    api_server = load_module("api_server")

    settings = api_server._build_cors_settings("*")

    assert "http://127.0.0.1:5500" in settings["allow_origins"]
    assert "http://localhost:5500" in settings["allow_origins"]
    assert settings["allow_origin_regex"] == r"^https?://[^/]+:5500$"
    assert settings["allow_credentials"] is True


def test_build_cors_settings_extends_local_loopback_origins(monkeypatch, load_module):
    monkeypatch.delenv("FRONTEND_PUBLIC_URL", raising=False)
    monkeypatch.delenv("APP_PUBLIC_HOST", raising=False)
    monkeypatch.delenv("APP_PUBLIC_SCHEME", raising=False)
    api_server = load_module("api_server")

    settings = api_server._build_cors_settings("http://localhost:5500")

    assert settings["allow_origin_regex"] == r"^https?://[^/]+:5500$"
    assert "http://localhost:5500" in settings["allow_origins"]
    assert "http://[::1]:5500" in settings["allow_origins"]


def test_build_cors_settings_uses_configured_frontend_port(monkeypatch, load_module):
    monkeypatch.setenv("FRONTEND_PORT", "6600")
    monkeypatch.delenv("FRONTEND_PUBLIC_URL", raising=False)
    monkeypatch.delenv("APP_PUBLIC_HOST", raising=False)
    monkeypatch.delenv("APP_PUBLIC_SCHEME", raising=False)
    api_server = load_module("api_server")

    settings = api_server._build_cors_settings("http://localhost:6600")

    assert settings["allow_origin_regex"] == r"^https?://[^/]+:6600$"
    assert "http://localhost:6600" in settings["allow_origins"]
    assert "http://[::1]:6600" in settings["allow_origins"]


def test_build_cors_settings_uses_public_browser_origin_when_available(
    monkeypatch, load_module
):
    monkeypatch.setenv("APP_PUBLIC_HOST", "captions.example.test")
    monkeypatch.setenv("APP_PUBLIC_SCHEME", "https")
    monkeypatch.setenv("ASL_API_BASE_URL", "/asl-api")
    monkeypatch.delenv("ASL_CORS_ORIGINS", raising=False)
    monkeypatch.delenv("CORS_ORIGINS", raising=False)

    api_server = load_module("api_server")

    settings = api_server._build_cors_settings()

    assert settings["allow_origins"] == ["https://captions.example.test"]
    assert settings["allow_origin_regex"] is None
    assert settings["allow_credentials"] is True
