#!/usr/bin/env python3
"""
letter_server.py — FastAPI backend for ASL Letter Recognition (Letter Mode)

Lightweight service on port 8002 that receives browser camera frames via HTTP POST,
runs MediaPipe Hands + Logistic Regression to classify individual ASL letters,
and streams results back to the frontend via Server-Sent Events (SSE).

Architecture mirrors the ASL backend (api_server.py) for frontend compatibility:
    POST /start        → load model, begin accepting frames
    POST /stop         → stop pipeline
    GET  /status       → JSON snapshot
    GET  /stream       → SSE (letter_detected events)
    POST /upload_frame → receive JPEG frame, run inference
    GET  /health       → health check
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import os
import queue
import sys
import threading
import time
import warnings
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2

# Suppress uvicorn access log spam from the per-frame upload endpoint —
# at ~1-6 fps these otherwise drown the log. Same filter the tunnel proxy
# uses; defined here too so direct mobile → letter_server traffic (no proxy)
# also stays quiet.
class _SuppressUploadAccessLogs(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        return "/upload_frame" not in msg
logging.getLogger("uvicorn.access").addFilter(_SuppressUploadAccessLogs())


# Mask the bottom fraction of mobile portrait frames so MediaPipe Hands
# doesn't see the user's hand resting on the table just below the visible
# "active" area. Web (landscape) frames pass through untouched. Mirrors
# the same helper in api_server.py — same env var controls both.
import numpy as _np
_MOBILE_PORTRAIT_BOTTOM_MASK_FRAC = float(
    os.environ.get("MOBILE_PORTRAIT_BOTTOM_MASK_FRAC", "0.30")
)


def _maybe_mask_portrait_bottom(img: "_np.ndarray") -> "_np.ndarray":
    if _MOBILE_PORTRAIT_BOTTOM_MASK_FRAC <= 0:
        return img
    h, w = img.shape[:2]
    if h <= w:
        return img
    keep_h = int(h * (1.0 - _MOBILE_PORTRAIT_BOTTOM_MASK_FRAC))
    if 0 < keep_h < h:
        img[keep_h:, :, :] = 0
    return img
import numpy as np
import pandas as pd

# CPU-pool cap — same fix as backend/api_server.py. Without it, torch 2.10
# fans out across every physical core for each MediaPipe + MLP forward call,
# pegging the machine at ~30% per core × N cores during a letter session.
# Diagnosed apr29 in api_server.py; the letter pipeline now runs torch too
# (LetterMLP replaced the cheap LogisticRegression) so the same cap applies.
import torch
torch.set_num_threads(1)
# See the matching note in backend/api_server.py: set_num_interop_threads() is
# once-per-process and raises RuntimeError on a second call, so importing both
# services into one process (or reloading this one) would otherwise fail at
# import time. The first call wins; the single-thread cap still applies.
try:
    torch.set_num_interop_threads(1)
except RuntimeError:
    pass
cv2.setNumThreads(1)

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

try:
    from backend.browser_origin import build_cors_settings, configured_browser_origin
    from backend.runtime_config import load_runtime_config
    from backend.auth_supabase import require_user
except Exception:
    from browser_origin import build_cors_settings, configured_browser_origin
    from runtime_config import load_runtime_config
    from auth_supabase import require_user

_require_user_http = require_user

warnings.filterwarnings("ignore")

logger = logging.getLogger("letter_server")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

DATA_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_CSV_PATH = DATA_DIR / "hand_signals.csv"
RUNTIME_CONFIG = load_runtime_config()
FRONTEND_PORT = RUNTIME_CONFIG.frontend_port
_CORS_FALLBACK_ORIGIN = configured_browser_origin(
    frontend_public_url=RUNTIME_CONFIG.frontend_public_url,
    frontend_public_url_explicit=RUNTIME_CONFIG.frontend_public_url_explicit,
    public_host=RUNTIME_CONFIG.public_host,
    public_scheme=RUNTIME_CONFIG.public_scheme,
    public_host_explicit=RUNTIME_CONFIG.public_host_explicit,
    same_origin_base_url=RUNTIME_CONFIG.letter_api_base_url,
)


def _build_cors_settings(cors_env: Optional[str] = None) -> Dict[str, Any]:
    raw = cors_env if cors_env is not None else os.environ.get("CORS_ORIGINS", "")
    return build_cors_settings(
        explicit_origins=raw,
        frontend_port=FRONTEND_PORT,
        allow_credentials=True,
        fallback_origin=_CORS_FALLBACK_ORIGIN,
        log_label="Letter CORS",
    )


# ─── Event Bus (same pattern as api_server.py) ────────────────────────────
class EventBus:
    """Thread-safe pub/sub for SSE clients."""

    def __init__(self, max_recent: int = 20):
        self._lock = threading.Lock()
        self._subscribers: List[queue.Queue] = []
        self._recent: List[Dict[str, Any]] = []
        self._max_recent = max_recent

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=256)
        with self._lock:
            self._subscribers.append(q)
            for item in self._recent:
                try:
                    q.put_nowait(item)
                except queue.Full:
                    break
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def publish(self, event_type: str, data: Any = None) -> None:
        msg = {"event": event_type, "data": data or {}}
        with self._lock:
            self._recent.append(msg)
            if len(self._recent) > self._max_recent:
                self._recent = self._recent[-self._max_recent:]
            dead: List[queue.Queue] = []
            for q in self._subscribers:
                try:
                    q.put_nowait(msg)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                try:
                    self._subscribers.remove(q)
                except ValueError:
                    pass


event_bus = EventBus()


# ─── Hand Detector (extracted from vendor hand_detector2.py) ───────────────
class HandDetector:
    """MediaPipe Hands wrapper — returns landmark positions from a BGR frame."""

    def __init__(
        self,
        static_image_mode: bool = False,
        max_num_hands: int = 1,
        min_detection_confidence: float = 0.5,
        min_tracking_confidence: float = 0.5,
    ):
        import mediapipe as mp

        self._mp_hands = mp.solutions.hands
        self._hands = self._mp_hands.Hands(
            static_image_mode=static_image_mode,
            max_num_hands=max_num_hands,
            min_detection_confidence=min_detection_confidence,
            min_tracking_confidence=min_tracking_confidence,
        )

    # Friend's hand_signals.csv was captured at roughly 1280x720 — pixel-
    # coord mean is (649, 347), max (1410, 775), with hand sometimes
    # spilling past frame edges. The MLP_256x3 + StandardScaler we trained
    # on that data assumes pixel coords in this range. Clients sending
    # JPEGs at any other resolution (mobile expo-camera defaults can be
    # 480x640 or even smaller, web canvas at 1280 already matches) would
    # produce landmarks at the wrong absolute scale → StandardScaler sees
    # a shifted distribution → model gets the broad strokes right but
    # fails on subtle distinctions like the thumb-across-fist (S) or
    # thumb-tucked-between-fingers (T). Locking the coord scaling here
    # makes the model truly resolution-invariant.
    _REF_W = 1280
    _REF_H = 720

    def detect(self, bgr_frame: np.ndarray) -> Optional[np.ndarray]:
        """Run hand detection on a BGR frame.

        Returns a (42,) feature vector [x0, y0, x1, y1, ..., x20, y20]
        with pixel coordinates for 21 landmarks scaled to the training
        reference resolution (_REF_W x _REF_H), or None if no hand found.

        Side effect: stores the normalized (21, 2) landmarks of the last
        detection on `self.last_landmarks_norm` for callers that want to
        render/publish the skeleton.
        """
        self.last_landmarks_norm: Optional[np.ndarray] = None
        rgb = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
        results = self._hands.process(rgb)

        if not results.multi_hand_landmarks:
            return None

        hand = results.multi_hand_landmarks[0]
        coords = []
        norm = np.zeros((21, 2), dtype=np.float32)
        for i, lm in enumerate(hand.landmark):
            # Scale to the training reference resolution, NOT the input
            # frame size — see _REF_W/_REF_H comment above.
            coords.append(int(lm.x * self._REF_W))
            coords.append(int(lm.y * self._REF_H))
            norm[i, 0] = float(lm.x)
            norm[i, 1] = float(lm.y)
        self.last_landmarks_norm = norm
        return np.array(coords, dtype=np.float64)

    def close(self) -> None:
        self._hands.close()


# ─── Letter Pipeline ──────────────────────────────────────────────────────
class LetterPipeline:
    """Manages model, detector, and frame-by-frame inference state."""

    def __init__(self, csv_path: Path = DEFAULT_CSV_PATH):
        self._csv_path = csv_path
        self._model = None
        self._detector: Optional[HandDetector] = None
        self._running = False
        self._lock = threading.Lock()

        # Letter accumulation
        self._current_candidate: Optional[str] = None
        self._current_candidate_count = 0
        self._current_candidate_started_at: Optional[float] = None
        self._switch_candidate: Optional[str] = None
        self._switch_candidate_count = 0
        self._switch_candidate_started_at: Optional[float] = None
        self._candidate_switch_threshold = 2
        self._accept_min_frames = 2
        self._accept_stable_seconds = 0.3
        self._confidence_threshold = 0.7
        # Fast-accept: a SINGLE prediction at this conf or higher bypasses
        # the 2-frame stability gate. Designed for low-fps clients (mobile
        # via expo-camera/takePictureAsync runs ~1 fps), where waiting for
        # two consecutive same-letter frames at 0.3s elapsed effectively
        # means the user must hold motionless for ≥1 second AND the model
        # must agree on both samples. With MLP_256x3 at 99.5% test acc,
        # >=0.85 single-frame predictions are essentially never wrong, so
        # bypassing the gate at that confidence is safe even on the web's
        # 15-30 fps stream. Dup-lock still prevents repeats.
        self._fast_accept_threshold = 0.85
        self._current_word = ""
        self._last_detection_time = 0.0
        self._idle_timeout = 2.0  # seconds of no hand → word boundary

        # Duplicate-letter lock: after accepting a letter, block re-acceptance
        # of the SAME letter until a release condition is met (hand-off, low-
        # confidence streak, or a different letter becoming stable).
        self._last_accepted_letter: Optional[str] = None
        self._weak_frame_count = 0          # consecutive no-hand / low-conf frames
        _WEAK_FRAMES_TO_UNLOCK = 3          # weak frames needed to release lock
        self._weak_unlock_threshold = _WEAK_FRAMES_TO_UNLOCK
        _STABLE_DIFF_TO_UNLOCK = 3          # frames of a different letter to release lock
        self._stable_diff_threshold = _STABLE_DIFF_TO_UNLOCK

        # Stats
        self._frames_processed = 0
        self._start_time: Optional[float] = None

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> Dict[str, Any]:
        """Load model and detector. Returns status dict."""
        with self._lock:
            if self._running:
                return {"status": "already_running"}

            logger.info("Loading letter recognition model...")
            t0 = time.time()

            # Pretrained MLP from backend/checkpoint/letter_mlp/ (~150K params,
            # 99.56% test acc on the same 80/20 split this used to train LR on).
            # See backend/checkpoint/letter_mlp/EVAL_NOTES.md for details.
            if self._model is None:
                try:
                    from backend.letter_mlp import load_letter_model
                except Exception:
                    from letter_mlp import load_letter_model
                self._model = load_letter_model()
                self._n_samples = 9966  # train portion of 12,458-row CSV (matches old metric)

            # Initialize MediaPipe detector
            if self._detector is None:
                self._detector = HandDetector(
                    static_image_mode=False,
                    max_num_hands=1,
                    min_detection_confidence=0.5,
                    min_tracking_confidence=0.5,
                )

            self._running = True
            self._frames_processed = 0
            self._start_time = time.time()
            self._last_detection_time = 0.0
            self._reset_candidate_tracking()
            self._current_word = ""
            self._last_accepted_letter = None
            self._weak_frame_count = 0

            elapsed = time.time() - t0
            logger.info(f"Letter pipeline ready in {elapsed:.2f}s")

            event_bus.publish("pipeline_started", {
                "model": "LetterMLP_256x3",
                "training_samples": getattr(self, "_n_samples", 0),
            })
            return {"status": "started", "load_time_sec": round(elapsed, 2)}

    def stop(self) -> Dict[str, Any]:
        """Stop accepting frames. Keep model cached for fast restart."""
        with self._lock:
            if not self._running:
                return {"status": "already_stopped"}
            self._running = False

            # Flush any accumulated word
            if self._current_word:
                event_bus.publish("word_completed", {"word": self._current_word})
                self._current_word = ""

            self._last_detection_time = 0.0
            self._reset_candidate_tracking()
            self._last_accepted_letter = None
            self._weak_frame_count = 0
            event_bus.publish("pipeline_stopped", {})
            logger.info("Letter pipeline stopped (model cached)")
            return {"status": "stopped"}

    def process_frame(self, jpeg_bytes: bytes) -> None:
        """Decode a JPEG frame, run detection + classification, publish events."""
        if not self._running or self._model is None or self._detector is None:
            return

        # Decode JPEG
        arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            return

        # Flip horizontally (mirror) to match vendor behavior
        frame = cv2.flip(frame, 1)

        # Mask out the bottom of portrait (mobile) frames — see
        # _MOBILE_PORTRAIT_BOTTOM_MASK_FRAC docstring at module top.
        frame = _maybe_mask_portrait_bottom(frame)

        self._frames_processed += 1

        # Detect hand landmarks
        features = self._detector.detect(frame)

        # Publish skeleton overlay for the frontend (mp86 layout: hand
        # populated at indices 65..86 = right-hand slot). Always emit so
        # the canvas clears when no hand is in view.
        kps = np.zeros((86, 2), dtype=np.float32)
        scores = np.zeros((86,), dtype=np.float32)
        has_person = False
        if self._detector.last_landmarks_norm is not None:
            # MediaPipe ran on the horizontally-flipped frame (so the LR
            # baseline sees mirror-view), but the browser <video> is NOT
            # mirrored — un-flip x so the dots line up with the user's hand.
            hand_norm = self._detector.last_landmarks_norm.copy()
            hand_norm[:, 0] = 1.0 - hand_norm[:, 0]
            kps[65:86, :] = hand_norm
            scores[65:86] = 1.0
            has_person = True
        event_bus.publish("keypoints", {
            "layout": "mp86",
            "has_person": has_person,
            "kps": kps.tolist(),
            "scores": scores.tolist(),
        })

        now = time.monotonic()

        if features is None:
            # No hand — count toward releasing duplicate lock
            self._weak_frame_count += 1
            if self._weak_frame_count >= self._weak_unlock_threshold:
                self._weak_frame_count = 0
                self._last_accepted_letter = None
                self._reset_candidate_tracking()

            # Check idle timeout for word boundary
            idle_time = now - self._last_detection_time if self._last_detection_time else 0
            if (
                idle_time >= self._idle_timeout
                and self._current_word
                and not self._current_word.endswith(" ")
            ):
                self._last_accepted_letter = None
                self._weak_frame_count = 0
                self._reset_candidate_tracking()
                event_bus.publish("word_completed", {"word": self._current_word})
                self._current_word = ""
            return

        self._last_detection_time = now

        # Classify
        location_vector = features.reshape(1, -1)
        probabilities = self._model.predict_proba(location_vector)
        max_prob = float(np.max(probabilities))
        predicted_letter = self._model.predict(location_vector)[0]

        if max_prob < self._confidence_threshold:
            # Low confidence — count toward releasing duplicate lock
            self._weak_frame_count += 1
            if self._weak_frame_count >= self._weak_unlock_threshold:
                self._weak_frame_count = 0
                self._last_accepted_letter = None
                self._reset_candidate_tracking()
            event_bus.publish("letter_rejected", {
                "letter": predicted_letter,
                "confidence": round(max_prob, 3),
            })
            return

        # Good detection — reset weak-frame streak
        self._weak_frame_count = 0

        # Publish raw detection
        event_bus.publish("letter_detected", {
            "letter": predicted_letter,
            "confidence": round(max_prob, 3),
        })

        # Duplicate lock: skip if same letter as just-accepted
        if predicted_letter == self._last_accepted_letter:
            return

        # Keep a short switching buffer so one noisy frame does not wipe
        # progress toward the next intended letter.
        self._accumulate_candidate(predicted_letter, now)

        # Release lock when a different letter is stable (3+ frames)
        if (
            self._last_accepted_letter is not None
            and self._current_candidate is not None
            and self._current_candidate != self._last_accepted_letter
            and self._current_candidate_count >= self._stable_diff_threshold
        ):
            self._last_accepted_letter = None

        # Accept letter after N consecutive frames OR on a single very
        # high-confidence prediction (low-fps client safety net — see
        # _fast_accept_threshold comment in __init__).
        stable_accept = (
            self._current_candidate is not None
            and self._current_candidate_count >= self._accept_min_frames
            and self._current_candidate_started_at is not None
            and (now - self._current_candidate_started_at) >= self._accept_stable_seconds
        )
        fast_accept = (
            self._current_candidate is not None
            and self._current_candidate == predicted_letter
            and max_prob >= self._fast_accept_threshold
        )
        if stable_accept or fast_accept:
            accepted_letter = self._current_candidate
            self._current_word += accepted_letter
            self._last_accepted_letter = accepted_letter
            self._reset_candidate_tracking()

            event_bus.publish("letter_accepted", {
                "letter": accepted_letter,
                "confidence": round(max_prob, 3),
                "word_so_far": self._current_word,
            })
            logger.debug(f"Letter accepted: {predicted_letter} → word: {self._current_word}")

    def get_status(self) -> Dict[str, Any]:
        """Return a JSON-serializable status snapshot."""
        elapsed = (time.time() - self._start_time) if self._start_time else 0
        fps = self._frames_processed / elapsed if elapsed > 0 else 0
        return {
            "running": self._running,
            "frames_processed": self._frames_processed,
            "fps": round(fps, 1),
            "current_word": self._current_word,
            "confidence_threshold": self._confidence_threshold,
            "accept_frames": self._accept_min_frames,
        }

    def _reset_candidate_tracking(self) -> None:
        self._current_candidate = None
        self._current_candidate_count = 0
        self._current_candidate_started_at = None
        self._switch_candidate = None
        self._switch_candidate_count = 0
        self._switch_candidate_started_at = None

    def _accumulate_candidate(self, predicted_letter: str, now: float) -> None:
        if self._current_candidate is None:
            self._current_candidate = predicted_letter
            self._current_candidate_count = 1
            self._current_candidate_started_at = now
            self._switch_candidate = None
            self._switch_candidate_count = 0
            self._switch_candidate_started_at = None
            return

        if predicted_letter == self._current_candidate:
            self._current_candidate_count += 1
            self._switch_candidate = None
            self._switch_candidate_count = 0
            self._switch_candidate_started_at = None
            return

        if predicted_letter == self._switch_candidate:
            self._switch_candidate_count += 1
        else:
            self._switch_candidate = predicted_letter
            self._switch_candidate_count = 1
            self._switch_candidate_started_at = now

        if self._switch_candidate_count >= self._candidate_switch_threshold:
            self._current_candidate = predicted_letter
            self._current_candidate_count = self._switch_candidate_count
            self._current_candidate_started_at = self._switch_candidate_started_at or now
            self._switch_candidate = None
            self._switch_candidate_count = 0
            self._switch_candidate_started_at = None

    def _train_model(self):
        """Train Logistic Regression from CSV. Fast (~0.5s)."""
        from sklearn.linear_model import LogisticRegression
        from sklearn.model_selection import train_test_split

        csv_path = self._csv_path
        if not csv_path.exists():
            raise FileNotFoundError(f"Training data not found: {csv_path}")

        logger.info(f"Training from {csv_path}")
        data = pd.read_csv(csv_path)
        data = data.loc[:, ~data.columns.str.contains("^Unnamed")]

        X = data.drop("letter", axis=1)
        y = data["letter"]
        self._n_samples = len(X)

        X_train, _, y_train, _ = train_test_split(
            X, y, test_size=0.2, random_state=42
        )

        model = LogisticRegression(max_iter=200)
        model.fit(X_train, y_train)
        logger.info(f"Model trained on {len(X_train)} samples, {len(model.classes_)} classes")
        return model

    def release(self) -> None:
        """Full cleanup — release detector and model."""
        self._running = False
        if self._detector is not None:
            self._detector.close()
            self._detector = None
        self._model = None
        logger.info("Letter pipeline fully released")


# ─── Global pipeline instance ─────────────────────────────────────────────
pipeline = LetterPipeline()


# ─── FastAPI app ──────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    pipeline.release()
    logger.info("Letter server shutdown complete")


app = FastAPI(
    title="ASL Letter Recognition API",
    version="1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    **_build_cors_settings(),
)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "letter"}


@app.get("/")
async def root():
    return {
        "service": "ASL Letter Recognition",
        "version": "1.0",
        "endpoints": [
            "POST /start",
            "POST /stop",
            "GET  /status",
            "GET  /stream",
            "POST /upload_frame",
            "GET  /health",
        ],
    }


@app.post("/start")
async def start_pipeline(request: Request):
    _require_user_http(request)
    try:
        result = pipeline.start()
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"Failed to start: {e}", exc_info=True)
        event_bus.publish("error", {"message": str(e)})
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@app.post("/stop")
async def stop_pipeline(request: Request):
    _require_user_http(request)
    result = pipeline.stop()
    return JSONResponse(result)


@app.get("/status")
async def get_status(request: Request):
    _require_user_http(request)
    return JSONResponse(pipeline.get_status())


@app.post("/upload_frame")
async def upload_frame(request: Request):
    """Receive a JPEG frame from the browser camera."""
    _require_user_http(request)
    body = await request.body()
    if not body:
        return Response(status_code=204)
    pipeline.process_frame(body)
    return Response(status_code=204)


@app.get("/stream")
async def stream_sse(request: Request):
    """Server-Sent Events endpoint for real-time letter detection events."""
    _require_user_http(request)
    sub = event_bus.subscribe()

    async def generate():
        try:
            while True:
                try:
                    msg = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: sub.get(timeout=15.0)
                    )
                    yield f"data: {json.dumps(msg)}\n\n"
                except queue.Empty:
                    yield ": heartbeat\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            event_bus.unsubscribe(sub)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ─── CLI entry point ─────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="ASL Letter Recognition Server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host")
    parser.add_argument("--port", type=int, default=8002, help="Bind port")
    args = parser.parse_args()

    logger.info(f"Starting Letter server on {args.host}:{args.port}")
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()

