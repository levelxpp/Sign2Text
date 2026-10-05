from __future__ import annotations

import cv2
import numpy as np
import pytest


LIVE_FRAME_STEP_SECONDS = 0.4

# Reason attached to the two tests below that drive a real start/stop cycle.
# `LetterPipeline.start()` calls `load_letter_model()` from backend/letter_mlp.py,
# a teammate-authored module intentionally not published here, so those tests
# raise ModuleNotFoundError. The rest of this module exercises the frame,
# candidate-stability and word-boundary logic through stubs and stays blocking.
_NEEDS_LETTER_MODEL = pytest.mark.requires_private_assets


def _make_jpeg_bytes() -> bytes:
    image = np.zeros((8, 8, 3), dtype=np.uint8)
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    return encoded.tobytes()


class FakeClock:
    def __init__(self, now: float = 100.0):
        self._now = now

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class StubDetector:
    def __init__(self, outputs):
        self._outputs = list(outputs)
        # The real HandDetector publishes the normalised landmark array so the
        # service can build the keypoint-overlay payload — letter_server.py
        # reads `last_landmarks_norm` on every processed frame. None is the
        # documented "no hand in view" state, which is what this stub models.
        # Without this attribute the stub no longer satisfies the detector
        # interface and every frame-processing test errors out.
        self.last_landmarks_norm = None

    def detect(self, frame):
        item = self._outputs.pop(0)
        if item is None:
            return None
        return np.zeros(42, dtype=np.float64)


class StubModel:
    def __init__(self, outputs):
        self._outputs = list(outputs)

    def predict_proba(self, features):
        confidence = self._outputs[0][1]
        return np.array([[confidence]], dtype=float)

    def predict(self, features):
        letter, _confidence = self._outputs.pop(0)
        return np.array([letter])


class FakeHandDetector:
    def __init__(self, *args, **kwargs):
        pass

    def close(self):
        pass


def _capture_events(monkeypatch, letter_server):
    events = []
    monkeypatch.setattr(
        letter_server.event_bus,
        "publish",
        lambda event_type, data=None: events.append((event_type, data)),
    )
    return events


def _install_clock(monkeypatch, letter_server) -> FakeClock:
    clock = FakeClock()
    monkeypatch.setattr(letter_server.time, "monotonic", clock.now)
    return clock


def _build_pipeline(letter_server, sequence, detector_outputs=None):
    pipe = letter_server.LetterPipeline()
    pipe._model = StubModel(sequence)
    pipe._detector = StubDetector(detector_outputs or [True] * len(sequence))
    pipe._running = True
    return pipe


def _run_frames(pipe, frame, clock: FakeClock, total_frames: int, step: float = LIVE_FRAME_STEP_SECONDS):
    for _ in range(total_frames):
        pipe.process_frame(frame)
        clock.advance(step)


def _accepted_words(events):
    return [data["word_so_far"] for event, data in events if event == "letter_accepted"]


def test_letter_cors_falls_back_to_local_frontend_only(monkeypatch, load_module):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    monkeypatch.delenv("FRONTEND_PUBLIC_URL", raising=False)
    monkeypatch.delenv("APP_PUBLIC_HOST", raising=False)
    monkeypatch.delenv("APP_PUBLIC_SCHEME", raising=False)
    monkeypatch.delenv("LETTER_API_BASE_URL", raising=False)

    letter_server = load_module("letter_server")

    settings = letter_server._build_cors_settings()

    assert "http://127.0.0.1:5500" in settings["allow_origins"]
    assert "http://localhost:5500" in settings["allow_origins"]
    assert settings["allow_origin_regex"] == r"^https?://[^/]+:5500$"
    assert settings["allow_credentials"] is True


def test_letter_cors_uses_public_browser_origin_when_available(
    monkeypatch, load_module
):
    monkeypatch.setenv("APP_PUBLIC_HOST", "captions.example.test")
    monkeypatch.setenv("APP_PUBLIC_SCHEME", "https")
    monkeypatch.setenv("LETTER_API_BASE_URL", "/letter-api")
    monkeypatch.delenv("CORS_ORIGINS", raising=False)

    letter_server = load_module("letter_server")

    settings = letter_server._build_cors_settings()

    assert settings["allow_origins"] == ["https://captions.example.test"]
    assert settings["allow_origin_regex"] is None
    assert settings["allow_credentials"] is True


def test_letter_pipeline_builds_word_across_letters_at_low_live_style_cadence(
    monkeypatch, load_module
):
    letter_server = load_module("letter_server")
    clock = _install_clock(monkeypatch, letter_server)
    events = _capture_events(monkeypatch, letter_server)
    pipe = _build_pipeline(
        letter_server,
        [("c", 0.91)] * 3 + [("a", 0.92)] * 3 + [("t", 0.93)] * 3,
    )
    frame = _make_jpeg_bytes()

    _run_frames(pipe, frame, clock, total_frames=9)

    assert _accepted_words(events) == ["c", "ca", "cat"]
    assert pipe._current_word == "cat"


def test_second_letter_commits_without_requiring_high_frame_count(monkeypatch, load_module):
    letter_server = load_module("letter_server")
    clock = _install_clock(monkeypatch, letter_server)
    events = _capture_events(monkeypatch, letter_server)
    pipe = _build_pipeline(
        letter_server,
        [("c", 0.9)] * 3 + [("a", 0.9)] * 3,
    )
    frame = _make_jpeg_bytes()

    _run_frames(pipe, frame, clock, total_frames=6)

    assert _accepted_words(events) == ["c", "ca"]
    assert pipe._current_word == "ca"


def test_brief_transition_noise_does_not_kill_the_next_letter(monkeypatch, load_module):
    """A brief low-confidence blip between letters must not inject a letter.

    Note on the noise confidence: the pipeline deliberately fast-accepts any
    single prediction at or above `_fast_accept_threshold` (0.85), because at
    MLP_256x3's accuracy such a prediction is essentially never wrong and the
    web client can run at 15 fps. A 0.9-confidence frame is therefore *not*
    noise by the current contract — it is an accept.

    Real mid-transition frames, where the hand is between two shapes, are
    low-confidence. That is what this test now feeds, which is both the
    realistic signal and the case the noise guard actually exists for.
    """
    letter_server = load_module("letter_server")
    clock = _install_clock(monkeypatch, letter_server)
    events = _capture_events(monkeypatch, letter_server)
    pipe = _build_pipeline(
        letter_server,
        [("c", 0.9)] * 3 + [("a", 0.9), ("o", 0.40), ("a", 0.9)],
    )
    frame = _make_jpeg_bytes()

    _run_frames(pipe, frame, clock, total_frames=6)

    assert _accepted_words(events) == ["c", "ca"]
    assert pipe._current_word == "ca"


def test_holding_the_same_accepted_letter_does_not_spam_duplicates(monkeypatch, load_module):
    letter_server = load_module("letter_server")
    clock = _install_clock(monkeypatch, letter_server)
    events = _capture_events(monkeypatch, letter_server)
    pipe = _build_pipeline(letter_server, [("c", 0.9)] * 6)
    frame = _make_jpeg_bytes()

    _run_frames(pipe, frame, clock, total_frames=6)

    assert _accepted_words(events) == ["c"]
    assert pipe._current_word == "c"


def test_intentional_double_letters_still_work_after_release_reset(monkeypatch, load_module):
    letter_server = load_module("letter_server")
    clock = _install_clock(monkeypatch, letter_server)
    events = _capture_events(monkeypatch, letter_server)
    pipe = _build_pipeline(
        letter_server,
        [("l", 0.9)] * 6,
        detector_outputs=[True] * 3 + [None] * 3 + [True] * 3,
    )
    frame = _make_jpeg_bytes()

    _run_frames(pipe, frame, clock, total_frames=9)

    assert _accepted_words(events) == ["l", "ll"]
    assert pipe._current_word == "ll"


@_NEEDS_LETTER_MODEL
def test_start_and_stop_reset_candidate_timing_and_word_state(monkeypatch, load_module):
    letter_server = load_module("letter_server")
    _install_clock(monkeypatch, letter_server)
    events = _capture_events(monkeypatch, letter_server)
    monkeypatch.setattr(letter_server, "HandDetector", FakeHandDetector)
    monkeypatch.setattr(letter_server.LetterPipeline, "_train_model", lambda self: object())

    pipe = letter_server.LetterPipeline()

    started = pipe.start()
    assert started["status"] == "started"

    pipe._current_word = "cat"
    pipe._current_candidate = "a"
    pipe._current_candidate_count = 2
    pipe._current_candidate_started_at = 111.0
    pipe._switch_candidate = "t"
    pipe._switch_candidate_count = 1
    pipe._switch_candidate_started_at = 112.0
    pipe._last_accepted_letter = "c"
    pipe._weak_frame_count = 2
    pipe._last_detection_time = 123.0

    stopped = pipe.stop()
    assert stopped["status"] == "stopped"
    assert pipe._current_word == ""
    assert pipe._current_candidate is None
    assert pipe._current_candidate_count == 0
    assert pipe._current_candidate_started_at is None
    assert pipe._switch_candidate is None
    assert pipe._switch_candidate_count == 0
    assert pipe._switch_candidate_started_at is None
    assert pipe._last_accepted_letter is None
    assert pipe._weak_frame_count == 0
    assert pipe._last_detection_time == 0.0

    pipe._current_word = "temp"
    pipe._current_candidate = "z"
    pipe._current_candidate_count = 2
    pipe._current_candidate_started_at = 200.0
    pipe._switch_candidate = "y"
    pipe._switch_candidate_count = 1
    pipe._switch_candidate_started_at = 201.0
    pipe._last_accepted_letter = "x"
    pipe._weak_frame_count = 1
    pipe._last_detection_time = 999.0

    restarted = pipe.start()
    assert restarted["status"] == "started"
    assert pipe._current_word == ""
    assert pipe._current_candidate is None
    assert pipe._current_candidate_count == 0
    assert pipe._current_candidate_started_at is None
    assert pipe._switch_candidate is None
    assert pipe._switch_candidate_count == 0
    assert pipe._switch_candidate_started_at is None
    assert pipe._last_accepted_letter is None
    assert pipe._weak_frame_count == 0
    assert pipe._last_detection_time == 0.0
    assert [event for event, _data in events if event == "pipeline_started"] == [
        "pipeline_started",
        "pipeline_started",
    ]


def test_word_boundary_resets_current_word(monkeypatch, load_module):
    """After idle timeout fires word_completed, _current_word must reset to ''
    so the next accepted letter starts a fresh word (not 'old_word new_letter')."""
    letter_server = load_module("letter_server")
    clock = _install_clock(monkeypatch, letter_server)
    events = _capture_events(monkeypatch, letter_server)

    # 3 frames of "c" → accepted, then 10 no-hand frames (4s idle > 3s timeout),
    # then 3 frames of "a", 3 frames of "t"
    pipe = _build_pipeline(
        letter_server,
        [("c", 0.9)] * 3 + [("a", 0.9)] * 3 + [("t", 0.9)] * 3,
        detector_outputs=[True] * 3 + [None] * 10 + [True] * 6,
    )
    frame = _make_jpeg_bytes()

    # Spell "c"
    _run_frames(pipe, frame, clock, total_frames=3)
    # Idle for >3s (10 frames at 0.4s each = 4s)
    _run_frames(pipe, frame, clock, total_frames=10)

    # Verify word_completed fired and _current_word reset
    completed = [d["word"] for ev, d in events if ev == "word_completed"]
    assert completed == ["c"]
    assert pipe._current_word == ""  # NOT "c "

    # Spell "a", "t"
    _run_frames(pipe, frame, clock, total_frames=6)

    accepted = _accepted_words(events)
    # "c" for first word, then "a", "at" for second word (fresh accumulation)
    assert accepted == ["c", "a", "at"]
    assert pipe._current_word == "at"


@_NEEDS_LETTER_MODEL
def test_stop_after_idle_does_not_double_publish_word(monkeypatch, load_module):
    """Calling stop() after idle timeout already flushed the word must not
    produce a second word_completed event."""
    letter_server = load_module("letter_server")
    clock = _install_clock(monkeypatch, letter_server)
    events = _capture_events(monkeypatch, letter_server)
    monkeypatch.setattr(letter_server, "HandDetector", FakeHandDetector)
    monkeypatch.setattr(letter_server.LetterPipeline, "_train_model", lambda self: object())

    pipe = letter_server.LetterPipeline()
    pipe.start()

    # Inject stubs for frame processing
    pipe._model = StubModel([("h", 0.9)] * 3)
    pipe._detector = StubDetector([True] * 3 + [None] * 10)

    # Accept "h"
    _run_frames(pipe, frame=_make_jpeg_bytes(), clock=clock, total_frames=3)
    # Idle → word_completed
    _run_frames(pipe, frame=_make_jpeg_bytes(), clock=clock, total_frames=10)

    # Now stop — should NOT double-publish
    pipe.stop()

    completed = [d["word"] for ev, d in events if ev == "word_completed"]
    assert completed == ["h"]  # exactly once, not ["h", "h"] or ["h", ""]
