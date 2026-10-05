from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _api_server_ml_stubs(ensure_api_server_importable):
    ensure_api_server_importable()


class _Scalar:
    def __init__(self, value):
        self._value = value

    def item(self):
        return self._value


class _FakeProbs:
    """Minimal stand-in for the torch tensor returned by classify_segment."""

    def __init__(self, label_idx: int, conf: float):
        self._label_idx = label_idx
        self._conf = conf

    def max(self, _dim):
        return _Scalar(self._conf), _Scalar(self._label_idx)

    def topk(self, k):
        return [_Scalar(self._conf)], [_Scalar(self._label_idx)]


def _build_pipeline(load_module, monkeypatch, *, disable_translation: bool):
    api_server = load_module("api_server")
    inf_mod = api_server.inf

    bus = api_server.EventBus()
    pipeline = api_server.InferencePipeline(bus)

    pipeline._idx2label = {0: "HELLO", 1: "HOW"}
    pipeline._label2idx = {"HELLO": 0, "HOW": 1}
    pipeline._cfg = {
        "max_frames": 128,
        "val_num_clips": 1,
        "val_max_frames_cap": 128,
    }
    pipeline._device = None
    pipeline._conf_threshold = 0.0
    pipeline._disable_translation = disable_translation
    pipeline._gloss_buf = inf_mod.GlossBuffer()
    pipeline._sentence_det = inf_mod.SentenceBoundaryDetector(
        rest_frames_threshold=10, min_signs=1
    )
    pipeline._translator = MagicMock()
    pipeline._translator.is_available = True

    captured_events = []
    original_publish = bus.publish

    def _capturing_publish(event, payload):
        captured_events.append((event, payload))
        return original_publish(event, payload)

    monkeypatch.setattr(bus, "publish", _capturing_publish)

    return api_server, pipeline, captured_events


def _drive_one_sign(api_server, pipeline, monkeypatch, label_idx: int):
    monkeypatch.setattr(
        api_server.inf,
        "classify_segment",
        lambda *args, **kwargs: _FakeProbs(label_idx, 0.95),
    )
    pipeline._do_classify(seg=MagicMock(shape=(64,)))


def test_off_mode_emits_per_sign_translation_event_with_latest_label(
    monkeypatch, load_module
):
    api_server, pipeline, events = _build_pipeline(
        load_module, monkeypatch, disable_translation=True
    )

    _drive_one_sign(api_server, pipeline, monkeypatch, label_idx=0)

    translation_events = [
        payload for (event, payload) in events if event == "translation"
    ]
    assert len(translation_events) == 1
    assert translation_events[0]["english"] == "HELLO"

    _drive_one_sign(api_server, pipeline, monkeypatch, label_idx=1)
    translation_events = [
        payload for (event, payload) in events if event == "translation"
    ]
    assert len(translation_events) == 2
    assert translation_events[1]["english"] == "HOW"


def test_off_mode_does_not_call_ollama_translate(monkeypatch, load_module):
    api_server, pipeline, _events = _build_pipeline(
        load_module, monkeypatch, disable_translation=True
    )

    _drive_one_sign(api_server, pipeline, monkeypatch, label_idx=0)
    _drive_one_sign(api_server, pipeline, monkeypatch, label_idx=1)

    pipeline._translator.translate_async.assert_not_called()


def test_off_mode_emits_single_sign_not_cumulative_gloss(
    monkeypatch, load_module
):
    """Per-sign emits must be the latest sign, not the running gloss_str.

    Otherwise the frontend's appending caption renderer would duplicate
    earlier tokens (HELLO, HELLO HOW, HELLO HOW ARE...).
    """
    api_server, pipeline, events = _build_pipeline(
        load_module, monkeypatch, disable_translation=True
    )

    _drive_one_sign(api_server, pipeline, monkeypatch, label_idx=0)
    _drive_one_sign(api_server, pipeline, monkeypatch, label_idx=1)

    translation_payloads = [
        payload["english"]
        for (event, payload) in events
        if event == "translation"
    ]
    assert translation_payloads == ["HELLO", "HOW"]


def test_off_mode_translation_events_have_tts_suppress_flag(
    monkeypatch, load_module
):
    """OFF-mode translation events must carry tts_suppress=True so the
    frontend skips TTS for raw per-sign captions."""
    api_server, pipeline, events = _build_pipeline(
        load_module, monkeypatch, disable_translation=True
    )

    _drive_one_sign(api_server, pipeline, monkeypatch, label_idx=0)
    _drive_one_sign(api_server, pipeline, monkeypatch, label_idx=1)

    translation_events = [
        payload for (event, payload) in events if event == "translation"
    ]
    assert len(translation_events) == 2
    for payload in translation_events:
        assert payload.get("tts_suppress") is True, (
            f"Expected tts_suppress=True in OFF-mode translation event, got: {payload}"
        )


def test_on_mode_translation_events_have_no_tts_suppress_flag(
    monkeypatch, load_module
):
    """ON-mode Ollama-triggered translation events must NOT carry tts_suppress."""
    api_server, pipeline, events = _build_pipeline(
        load_module, monkeypatch, disable_translation=False
    )

    # Simulate a sentence-boundary Ollama callback
    pipeline._on_translation_done("Hello how are you")

    translation_events = [
        payload for (event, payload) in events if event == "translation"
    ]
    assert len(translation_events) == 1
    assert "tts_suppress" not in translation_events[0], (
        "ON-mode translation events must not carry tts_suppress"
    )


def test_on_mode_does_not_emit_per_sign_translation(
    monkeypatch, load_module
):
    api_server, pipeline, events = _build_pipeline(
        load_module, monkeypatch, disable_translation=False
    )

    _drive_one_sign(api_server, pipeline, monkeypatch, label_idx=0)
    _drive_one_sign(api_server, pipeline, monkeypatch, label_idx=1)

    translation_events = [
        payload for (event, payload) in events if event == "translation"
    ]
    assert translation_events == [], (
        "ON mode must not emit per-sign translation events; "
        "translation should only fire at sentence boundary via the "
        "translator callback."
    )
    pipeline._translator.translate_async.assert_not_called()
