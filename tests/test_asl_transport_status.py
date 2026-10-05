"""
Focused tests for Task 2: remote ASL transport + observability.

Verifies:
  * /start accepts skip_preview and threads it to pipeline.start(...)
  * /status exposes preview_mode, remote_transport, and frame counters
  * /upload_frame updates the http transport counters
  * /ws/frames updates the ws transport counters
  * Default /start (no skip_preview) → preview_mode == "mjpeg"
"""

from __future__ import annotations

import io
import json
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _api_server_ml_stubs(ensure_api_server_importable):
    ensure_api_server_importable()


def _make_jpeg_bytes():
    """Build a tiny real JPEG payload (2x2) so cv2.imdecode succeeds."""
    import numpy as np
    import cv2
    arr = np.zeros((2, 2, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", arr)
    assert ok
    return bytes(buf)


def _load_and_fake_pipeline(monkeypatch, load_module):
    api_server = load_module("api_server")
    monkeypatch.setattr(api_server.pipeline, "stop", lambda: None)
    monkeypatch.setattr(api_server.pipeline, "release_gpu", lambda: None)
    monkeypatch.setattr(api_server, "_require_user_http", lambda request: {"sub": "test"})
    monkeypatch.setattr(api_server, "_get_user_ws", lambda ws: {"sub": "test"})

    # Simulate a running remote pipeline without starting the inference thread.
    def fake_start(**kwargs):
        api_server.pipeline._running = True
        api_server.pipeline._camera_source = "remote"
        api_server.pipeline._camera = api_server.RemoteFrameSource()
        api_server.pipeline._skip_preview = bool(kwargs.get("skip_preview", False))
        api_server.pipeline._ws_frames_received = 0
        api_server.pipeline._http_frames_received = 0
        api_server.pipeline._last_frame_ts = 0.0
        api_server.pipeline._remote_transport = None

    monkeypatch.setattr(api_server.pipeline, "start", fake_start)
    # Make is_truly_running reflect our faked state
    monkeypatch.setattr(
        type(api_server.pipeline),
        "is_truly_running",
        property(lambda self: self._running),
    )
    return api_server


def test_start_accepts_skip_preview_and_status_reports_browser_native(
    monkeypatch, load_module
):
    api_server = _load_and_fake_pipeline(monkeypatch, load_module)

    with TestClient(api_server.app, raise_server_exceptions=False) as client:
        res = client.post(
            "/start",
            json={"camera_source": "remote", "skip_preview": True},
        )
        assert res.status_code == 200, res.text

        status = client.get("/status").json()
        assert status["skip_preview"] is True
        assert status["preview_mode"] == "browser-native"
        assert status["remote_transport"] is None
        assert status["ws_frames_received"] == 0
        assert status["http_frames_received"] == 0
        assert status["last_frame_age_sec"] is None


def test_upload_frame_updates_http_transport_counters(monkeypatch, load_module):
    api_server = _load_and_fake_pipeline(monkeypatch, load_module)

    with TestClient(api_server.app, raise_server_exceptions=False) as client:
        assert client.post(
            "/start", json={"camera_source": "remote", "skip_preview": True}
        ).status_code == 200

        res = client.post(
            "/upload_frame",
            content=_make_jpeg_bytes(),
            headers={"Content-Type": "image/jpeg"},
        )
        assert res.status_code == 204

        status = client.get("/status").json()
        assert status["http_frames_received"] >= 1
        assert status["ws_frames_received"] == 0
        assert status["remote_transport"] == "http"
        assert status["last_frame_age_sec"] is not None
        assert status["last_frame_age_sec"] < 5.0


def test_ws_frames_updates_ws_transport_counters(monkeypatch, load_module):
    api_server = _load_and_fake_pipeline(monkeypatch, load_module)

    with TestClient(api_server.app, raise_server_exceptions=False) as client:
        assert client.post(
            "/start", json={"camera_source": "remote", "skip_preview": True}
        ).status_code == 200

        jpeg = _make_jpeg_bytes()
        with client.websocket_connect("/ws/frames") as ws:
            ws.send_bytes(jpeg)
            # Give the server loop a moment to consume the frame
            time.sleep(0.05)

        status = client.get("/status").json()
        assert status["ws_frames_received"] >= 1
        assert status["remote_transport"] == "ws"
        assert status["last_frame_age_sec"] is not None


def test_status_serializes_numpy_scalars(monkeypatch, load_module):
    import numpy as np

    api_server = load_module("api_server")

    class _FakeSpotter:
        state = "idle"
        current_motion = np.float32(0.12345)
        dbg_hands_raised = np.bool_(True)
        dbg_hands_visible = np.bool_(False)
        buffer_len = 0

    monkeypatch.setattr(api_server.pipeline, "_spotter", _FakeSpotter())
    status = api_server.pipeline.get_status()

    # Must be serializable by fastapi's default JSON encoder
    payload = json.dumps(status)
    assert payload  # no exception

    assert isinstance(status["hands_raised"], bool)
    assert isinstance(status["hands_visible"], bool)
    assert isinstance(status["motion"], float)
    assert status["hands_raised"] is True
    assert status["hands_visible"] is False


def test_remote_dead_camera_reason_does_not_fire_on_stale_reads(
    monkeypatch, load_module
):
    api_server = load_module("api_server")
    fn = api_server._remote_dead_camera_reason

    now = 1000.0
    # Browser just connected, no frames yet, within grace window → None
    assert fn(now=now, last_frame_ts=0.0, loop_start_ts=now - 5.0) is None

    # Recent browser frame → None even if reads have been stale
    assert fn(now=now, last_frame_ts=now - 2.0, loop_start_ts=now - 100.0) is None

    # Browser silent past timeout → reason string
    reason = fn(
        now=now,
        last_frame_ts=now - 120.0,
        loop_start_ts=now - 500.0,
    )
    assert reason is not None and "no browser frame" in reason

    # Never connected, past connect grace → reason string
    reason = fn(now=now, last_frame_ts=0.0, loop_start_ts=now - 120.0)
    assert reason is not None and "connect grace" in reason


def test_remote_frame_source_only_returns_fresh_frames(monkeypatch, load_module):
    api_server = load_module("api_server")

    src = api_server.RemoteFrameSource()
    assert src.read() is None

    jpeg = _make_jpeg_bytes()
    assert src.push(jpeg) is True
    first = src.read()
    assert first is not None
    assert src.read() is None
    assert src.read() is None

    assert src.push(jpeg) is True
    second = src.read()
    assert second is not None
    assert src.read() is None


def test_default_start_reports_mjpeg_preview_mode(monkeypatch, load_module):
    api_server = _load_and_fake_pipeline(monkeypatch, load_module)

    with TestClient(api_server.app, raise_server_exceptions=False) as client:
        assert client.post(
            "/start", json={"camera_source": "remote"}
        ).status_code == 200

        status = client.get("/status").json()
        assert status["skip_preview"] is False
        assert status["preview_mode"] == "mjpeg"
