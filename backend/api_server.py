#!/usr/bin/env python3
"""
api_server.py — FastAPI wrapper for ASL Real-Time Translation (Website Edition)

v3.3 — RESTART-SAFE with MODEL CACHING + CTR-GCN + REMOTE CAMERA SUPPORT

Continuous sign detection → classification → translation with zero interruption.
No review mode, no data collection — pure inference for the user-facing product.

RESTART ARCHITECTURE:
    First start:  Load model + RTMW (slow, ~10s) → open camera → run loop
    Stop:         Kill loop → release camera → KEEP model + RTMW cached
    Second start: Reuse cached model + RTMW (instant) → open camera → run loop

    Model + RTMW are only released on server shutdown (lifespan exit),
    NOT on stop(). This makes stop→start nearly instant.

ARCHITECTURE:
    ┌─────────────────────────────────────────────────┐
    │  FastAPI (main thread / uvicorn)                │
    │                                                 │
    │  GET  /video_feed  → MJPEG stream               │
    │  GET  /stream      → SSE (sign/translate/state) │
    │  GET  /status      → JSON snapshot              │
    │  WS   /ws/frames   → receive browser cam frames │
    │  POST /start       → launch pipeline            │
    │  POST /stop        → stop pipeline              │
    │  POST /manual_sign → toggle [S] recording       │
    │  POST /clear       → clear translations         │
    │  POST /reset       → full reset                 │
    │  POST /threshold   → adjust confidence          │
    └─────────┬───────────────────────────┬───────────┘
              │                           │
              ▼                           ▼
    ┌──────────────────┐     ┌─────────────────────┐
    │ InferencePipeline│     │  SSE Event Bus       │
    │ (background      │────▶│  (thread → async)    │
    │  thread)         │     │                      │
    │                  │     │  Subscribers get      │
    │  Camera    [new] │     │  JSON events via      │
    │  RTMW    [cached]│     │  queue.Queue          │
    │  Model   [cached]│     └─────────────────────┘
    │  PersonTracker   │
    │  FeatureExtractor│
    │  SignSpotter     │
    │  GlossBuffer     │
    │  SentenceDetector│
    │  OllamaTranslator│
    └──────────────────┘

USAGE:
    python api_server.py
    python api_server.py --port 8000 --host 0.0.0.0 --checkpoint "path/to/best_model.pt"
    python api_server.py --auto_start

REQUIRES:
    pip install fastapi uvicorn python-multipart
    (plus all dependencies from new_Inference.py)
"""

import argparse
import asyncio
import json
import logging
import os
import queue
import threading
import time
import traceback
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

import cv2
import numpy as np
import torch

# Cap torch's CPU thread pool to 1. The classifier runs on CUDA so the only
# CPU-side torch work is the per-frame preprocessing chain (PoseNormalizer →
# KinematicsBlock → BoneFeatureBlock) on a single (1,T,86,7) tensor — there
# is no parallelism worth extracting. Without this cap, torch 2.10 fans out
# across every physical core for each preprocess call (12 threads × ~30%
# each at 30fps). Diagnosed with scripts/repro_cpu_spike.py: same Python
# code on torch 2.6 = 1.3% machine; on torch 2.10 = 29.5% machine.
torch.set_num_threads(1)
# set_num_interop_threads() may only be called once per process, and only
# before any parallel work has started; a second call raises RuntimeError.
# set_num_threads() carries no such restriction. Importing this module in the
# same process as backend/letter_server.py (which applies the same cap), or
# reloading it, would otherwise abort at import time. The first call wins, so
# tolerating the error leaves the intended single-thread cap in place.
try:
    torch.set_num_interop_threads(1)
except RuntimeError:
    pass
# OpenCV is also a noticeable thread-pool offender for cvtColor/resize on
# 640x480 frames. Bound to a single thread for the same reason — frames are
# small and per-call overhead of orchestrating a parallel job exceeds the
# benefit.
cv2.setNumThreads(1)

from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

# ─────────────────────────────────────────────────────────────────────
# Import everything from the inference module — no logic duplication
# ─────────────────────────────────────────────────────────────────────
try:
    from backend.browser_origin import build_cors_settings, configured_browser_origin
    from backend import new_Inference as inf
    from backend.hardware import log_hardware_report
    from backend.runtime_config import load_runtime_config
    from backend.auth_supabase import require_user, get_user_from_ws
    from backend.our_pipeline.adapter import OursPipelineAdapter
except ImportError:
    from browser_origin import build_cors_settings, configured_browser_origin
    import new_Inference as inf
    from hardware import log_hardware_report
    from runtime_config import load_runtime_config
    from auth_supabase import require_user, get_user_from_ws
    from our_pipeline.adapter import OursPipelineAdapter

_require_user_http = require_user
_get_user_ws = get_user_from_ws

logger = logging.getLogger("asl_api")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
RUNTIME_CONFIG = load_runtime_config()
log_hardware_report(RUNTIME_CONFIG.hardware_report)
FRONTEND_PORT = RUNTIME_CONFIG.frontend_port

_CORS_FALLBACK_ORIGIN = configured_browser_origin(
    frontend_public_url=RUNTIME_CONFIG.frontend_public_url,
    frontend_public_url_explicit=RUNTIME_CONFIG.frontend_public_url_explicit,
    public_host=RUNTIME_CONFIG.public_host,
    public_scheme=RUNTIME_CONFIG.public_scheme,
    public_host_explicit=RUNTIME_CONFIG.public_host_explicit,
    same_origin_base_url=RUNTIME_CONFIG.asl_api_base_url,
)


def _build_cors_settings(cors_env: Optional[str] = None) -> Dict[str, Any]:
    raw = (
        cors_env
        if cors_env is not None
        else (os.environ.get("ASL_CORS_ORIGINS") or os.environ.get("CORS_ORIGINS") or "")
    )
    return build_cors_settings(
        explicit_origins=raw,
        frontend_port=FRONTEND_PORT,
        allow_credentials=True,
        fallback_origin=_CORS_FALLBACK_ORIGIN,
        log_label="ASL CORS",
    )


# ═════════════════════════════════════════════════════════════════════
# REQUEST / RESPONSE MODELS
# ═════════════════════════════════════════════════════════════════════
class StartRequest(BaseModel):
    checkpoint: Optional[str] = None
    camera_id: Optional[int] = None
    camera_source: Optional[str] = None  # "local", "remote", or None (auto)
    conf_threshold: Optional[float] = None
    disable_translation: Optional[bool] = None
    skip_preview: Optional[bool] = None  # remote: skip MJPEG annotate/encode
    mode: Optional[str] = None    # "asl" | "voice" — sent by mobile client
    device: Optional[str] = None  # "mobile" | "web" — informational


class OfferRequest(BaseModel):
    sdp: str
    type: str


class ThresholdRequest(BaseModel):
    value: float


# ═════════════════════════════════════════════════════════════════════
# SSE EVENT BUS — bridges inference thread → async FastAPI
# ═════════════════════════════════════════════════════════════════════
class EventBus:
    """
    Thread-safe pub/sub for Server-Sent Events.

    The inference thread calls `publish(event_type, data)`.
    Each connected SSE client gets its own queue.Queue and
    receives events as they arrive.
    """

    # Events that are SAFE to replay to a freshly-subscribed client. These
    # are state-snapshot events ("here's the current pipeline state"), not
    # APPEND-style events. Without this allowlist, switching the web app
    # asl → letter → asl quickly would re-open the SSE stream, replay the
    # last 20 events including the most recent `caption.final`, and the
    # frontend would append the previously-signed word (e.g. "NAME") to the
    # transcript a second time. Waiting long enough for keypoints / other
    # high-rate events to push caption.final out of the 20-slot ring buffer
    # masked the bug — that's why the user observed it only on FAST switches.
    _REPLAYABLE_EVENT_TYPES = frozenset({
        "pipeline_started",
        "pipeline_stopped",
        "status",
        "model_loaded",
    })

    def __init__(self, maxsize: int = 100):
        self._lock = threading.Lock()
        self._subscribers: Set[queue.Queue] = set()
        self._maxsize = maxsize
        self._recent: deque = deque(maxlen=20)

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=self._maxsize)
        with self._lock:
            self._subscribers.add(q)
            for event in self._recent:
                try:
                    q.put_nowait(event)
                except queue.Full:
                    pass
        return q

    def unsubscribe(self, q: queue.Queue):
        with self._lock:
            self._subscribers.discard(q)

    def publish(self, event_type: str, data: dict):
        """Called from ANY thread. Thread-safe."""
        event = {
            "event": event_type,
            "data": data,
            "timestamp": datetime.now().isoformat(),
        }
        with self._lock:
            # Only store state-snapshot events for catch-up replay. Append-
            # style events (caption.final, translation, sign_accepted, ...)
            # MUST NOT be replayed — see _REPLAYABLE_EVENT_TYPES comment.
            if event_type in self._REPLAYABLE_EVENT_TYPES:
                self._recent.append(event)
            dead = []
            for q in self._subscribers:
                try:
                    q.put_nowait(event)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                self._subscribers.discard(q)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)


def _remote_dead_camera_reason(
    now: float,
    last_frame_ts: float,
    loop_start_ts: float,
    no_ingress_timeout_sec: float = 30.0,
    connect_grace_sec: float = 60.0,
) -> Optional[str]:
    """Return a reason string if the remote source should be declared
    dead, else None. Based purely on ingress time — repeated stale
    reads are not evidence of a dead browser camera.
    """
    if last_frame_ts > 0:
        idle = now - last_frame_ts
        if idle > no_ingress_timeout_sec:
            return f"no browser frame for {idle:.1f}s"
        return None
    if (now - loop_start_ts) > connect_grace_sec:
        return (
            "no browser frame received within connect grace "
            f"({connect_grace_sec:.0f}s)"
        )
    return None


# ═════════════════════════════════════════════════════════════════════
# REMOTE FRAME SOURCE — browser camera via WebSocket
# ═════════════════════════════════════════════════════════════════════
class RemoteFrameSource:
    """
    Drop-in replacement for CameraThread when frames come from the
    browser camera over WebSocket instead of a local cv2.VideoCapture.

    Same interface:  .read() → frame | None,  .release(),  .W, .H
    """

    def __init__(self, default_w: int = 1280, default_h: int = 720):
        self._lock = threading.Lock()
        self._frame: Optional[np.ndarray] = None
        self._active = True
        self.W = default_w
        self.H = default_h
        self._frame_count = 0
        self._frame_gen = 0
        self._last_read_gen = 0
        logger.info("[RemoteFrameSource] Waiting for browser frames...")

    def push(self, jpeg_bytes: bytes) -> bool:
        """Decode JPEG bytes from the WebSocket and store as latest frame."""
        arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            return False
        h, w = frame.shape[:2]
        with self._lock:
            self._frame = frame
            self._frame_count += 1
            self._frame_gen += 1
            if self._frame_count == 1:
                self.W = w
                self.H = h
                logger.info(
                    f"[RemoteFrameSource] First frame received: {w}x{h}"
                )
        return True

    def read(self) -> Optional[np.ndarray]:
        with self._lock:
            if self._frame is None:
                return None
            if self._frame_gen == self._last_read_gen:
                return None
            self._last_read_gen = self._frame_gen
            return self._frame.copy()

    def release(self):
        self._active = False
        with self._lock:
            self._frame = None
        logger.info("[RemoteFrameSource] Released")

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def frame_count(self) -> int:
        with self._lock:
            return self._frame_count


# ═════════════════════════════════════════════════════════════════════
# INFERENCE PIPELINE — runs in a background thread
# ═════════════════════════════════════════════════════════════════════
class InferencePipeline:
    """
    Wraps the full inference stack from new_Inference.py.

    Website edition: continuous auto-accept/reject flow.
    No review mode, no data collection, no pausing.

    RESTART-SAFE with MODEL CACHING:
        - Model + RTMW are loaded once and cached across restarts.
        - stop() releases camera + pipeline state but KEEPS GPU models.
        - start() reuses cached models, only reinitializes camera + state.
        - Full GPU release only happens via release_gpu() or server shutdown.
    """

    def __init__(self, event_bus: EventBus):
        self._bus = event_bus
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # ── GPU-resident models (CACHED across restarts) ────────
        self._model = None
        self._label2idx: Dict[str, int] = {}
        self._idx2label: Dict[int, str] = {}
        self._cfg: Dict[str, Any] = {}
        self._device = None
        self._wholebody = None
        self._gpu_initialized: bool = False
        # Our pipeline adapter (MediaPipe-86 + curated604). Lazily built on
        # first start when RUNTIME_CONFIG.asl_pipeline == "ours". Cached
        # across restarts (model load is expensive).
        self._our_adapter: Optional[OursPipelineAdapter] = None

        # ── Per-run components (recreated on each start) ────────
        self._camera = None  # CameraThread or RemoteFrameSource
        self._camera_source: str = "local"  # "local" or "remote"
        self._tracker: Optional[inf.PersonTracker] = None
        self._feature_ext: Optional[inf.FrameFeatureExtractor] = None
        self._spotter: Optional[inf.SignSpotter] = None
        self._gloss_buf: Optional[inf.GlossBuffer] = None
        self._sentence_det: Optional[inf.SentenceBoundaryDetector] = None
        self._translator: Optional[inf.OllamaTranslator] = None

        # ── State ───────────────────────────────────────────────
        self._conf_threshold: float = 0.25
        self._disable_translation: bool = False
        self._skip_preview: bool = False
        self._latest_frame: Optional[bytes] = None
        self._frame_lock = threading.Lock()

        # ── Remote transport observability ──────────────────────
        self._ws_frames_received: int = 0
        self._http_frames_received: int = 0
        self._last_frame_ts: float = 0.0
        self._remote_transport: Optional[str] = None  # "ws" | "http" | None

        self._current_feat = None
        self._confirmed_label: Optional[str] = None
        self._confirmed_conf: float = 0.0
        self._raw_label: Optional[str] = None
        self._raw_conf: float = 0.0
        self._raw_top5: List = []
        self._history: List[str] = []
        self._segment_count: int = 0
        self._fps: float = 0.0

        self._pending_manual_toggle = False

        self._init_config: Dict[str, Any] = {}
        self._init_error: Optional[str] = None
        self._init_done = threading.Event()

    # ── Properties ──────────────────────────────────────────────
    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_truly_running(self) -> bool:
        return (
            self._running
            and self._thread is not None
            and self._thread.is_alive()
        )

    @property
    def latest_frame_jpeg(self) -> Optional[bytes]:
        with self._frame_lock:
            return self._latest_frame

    @property
    def gpu_cached(self) -> bool:
        return self._gpu_initialized

    # ── Start ───────────────────────────────────────────────────
    def start(
        self,
        checkpoint: str,
        camera_id: int = 0,
        camera_source: str = "auto",
        conf_threshold: float = 0.25,
        rtmw_mode: str = "performance",
        rtmw_backend: str = "onnxruntime",
        rtmw_device: str = "cuda",
        ollama_url: str = inf.DEFAULT_OLLAMA_URL,
        ollama_model: str = inf.DEFAULT_OLLAMA_MODEL,
        ollama_timeout: float = inf.DEFAULT_OLLAMA_TIMEOUT,
        disable_translation: bool = False,
        sentence_rest_frames: int = inf.SENTENCE_REST_FRAMES,
        skip_preview: bool = False,
    ):
        if self._thread is not None and self._thread.is_alive():
            logger.warning(
                "Old inference thread still alive — forcing stop first"
            )
            self.stop()
        elif self._running:
            logger.warning("_running flag was stale True — forcing stop")
            self.stop()
        elif self._camera is not None:
            logger.warning("Camera was still held — cleaning up")
            self._cleanup_pipeline()

        self._conf_threshold = conf_threshold
        self._disable_translation = disable_translation
        self._skip_preview = bool(skip_preview)

        # Reset transport observability for this run
        self._ws_frames_received = 0
        self._http_frames_received = 0
        self._last_frame_ts = 0.0
        self._remote_transport = None

        self._init_config = {
            "checkpoint": checkpoint,
            "camera_id": camera_id,
            "camera_source": camera_source,
            "rtmw_mode": rtmw_mode,
            "rtmw_backend": rtmw_backend,
            "rtmw_device": rtmw_device,
            "ollama_url": ollama_url,
            "ollama_model": ollama_model,
            "ollama_timeout": ollama_timeout,
            "disable_translation": disable_translation,
            "sentence_rest_frames": sentence_rest_frames,
        }

        # ── Resolve camera source ──
        resolved_source = camera_source
        if resolved_source == "auto":
            # Try local camera first; fall back to remote
            try:
                test_cam = inf.CameraThread(camera_id)
                test_cam.release()
                resolved_source = "local"
            except Exception:
                logger.info(
                    "No local camera available — using remote "
                    "(browser) frame source"
                )
                resolved_source = "remote"

        self._camera_source = resolved_source

        if resolved_source == "remote":
            logger.info("Using remote (browser) camera source")
            self._camera = RemoteFrameSource()
        else:
            logger.info(f"Opening local camera {camera_id}...")
            try:
                self._camera = inf.CameraThread(camera_id)
            except Exception as e:
                logger.error(
                    f"Failed to open camera {camera_id}: {e}"
                )
                raise RuntimeError(
                    f"Camera {camera_id} failed: {e}"
                )

        logger.info(
            f"Camera ready ({resolved_source}): "
            f"{self._camera.W}x{self._camera.H}"
        )

        self._tracker = inf.PersonTracker()
        self._feature_ext = inf.FrameFeatureExtractor()
        self._spotter = inf.SignSpotter(
            start_threshold=0.008,
            stop_threshold=0.003,
            stop_patience=5,
            min_sign_frames=8,
            max_sign_frames=70,
            cooldown_frames=12,
        )
        self._gloss_buf = inf.GlossBuffer()
        self._sentence_det = inf.SentenceBoundaryDetector(
            rest_frames_threshold=sentence_rest_frames,
            min_signs=inf.SENTENCE_MIN_SIGNS,
        )
        self._translator = inf.OllamaTranslator(
            url=ollama_url,
            model=ollama_model,
            timeout=ollama_timeout,
            auth_token=inf.DEFAULT_OLLAMA_AUTH_TOKEN,
        )

        if not disable_translation:
            logger.info("Checking Ollama connection...")
            self._translator.check_availability()
            if self._translator.is_available:
                logger.info(f"Ollama ready: {ollama_model}")
            else:
                logger.warning(
                    "Ollama not available — translation disabled"
                )
        else:
            logger.info("Translation disabled by config")

        self._history.clear()
        self._confirmed_label = None
        self._confirmed_conf = 0.0
        self._raw_label = None
        self._raw_conf = 0.0
        self._raw_top5 = []
        self._segment_count = 0
        self._current_feat = None
        self._fps = 0.0
        self._pending_manual_toggle = False
        with self._frame_lock:
            self._latest_frame = None

        self._init_error = None
        self._init_done = threading.Event()
        self._running = True
        self._thread = threading.Thread(
            target=self._inference_loop, daemon=True, name="inference"
        )
        self._thread.start()

        timeout = 10 if self._gpu_initialized else 60
        logger.info(
            f"Waiting for inference thread init "
            f"(cached={self._gpu_initialized}, timeout={timeout}s)..."
        )
        if not self._init_done.wait(timeout=timeout):
            self._running = False
            self._cleanup_pipeline()
            raise RuntimeError(
                "Inference thread timed out during initialization"
            )

        if self._init_error:
            self._running = False
            self._cleanup_pipeline()
            raise RuntimeError(
                f"Inference thread init failed: {self._init_error}"
            )

        self._bus.publish("pipeline_started", {
            "classes": len(self._label2idx),
            "model_config": {
                "d_model": self._cfg.get("d_model"),
                "num_layers": self._cfg.get("num_layers"),
                "use_gcn": self._cfg.get("use_gcn", False),
                "use_ctr_gcn": self._cfg.get("use_ctr_gcn", False),
            },
            "camera": {
                "width": self._camera.W if self._camera else 0,
                "height": self._camera.H if self._camera else 0,
                "source": self._camera_source,
            },
            "ollama_available": (
                self._translator.is_available
                if self._translator
                else False
            ),
            "conf_threshold": self._conf_threshold,
            "gpu_cached": self._gpu_initialized,
        })
        logger.info("Pipeline started successfully")

    # ── Stop ────────────────────────────────────────────────────
    def stop(self):
        if not self._running and self._thread is None:
            return

        logger.info("Stopping pipeline...")
        self._running = False

        if self._thread is not None:
            logger.info("Waiting for inference thread to exit...")
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                logger.warning(
                    "Inference thread did not exit after 5s — "
                    "waiting 5 more..."
                )
                self._thread.join(timeout=5)
                if self._thread.is_alive():
                    logger.error(
                        "Inference thread STILL alive after 10s! "
                        "Proceeding with cleanup anyway."
                    )
            else:
                logger.info("Inference thread exited cleanly")
            self._thread = None

        self._cleanup_pipeline()
        time.sleep(0.5)

        self._bus.publish("pipeline_stopped", {})
        logger.info(
            "Pipeline stopped — camera released, model cached "
            f"(gpu_cached={self._gpu_initialized})"
        )

    def _cleanup_pipeline(self):
        if self._camera is not None:
            logger.info("Releasing camera...")
            try:
                self._camera.release()
            except Exception as e:
                logger.warning(f"Camera release error (non-fatal): {e}")
            self._camera = None
            logger.info("Camera released")

        self._tracker = None
        self._feature_ext = None
        self._spotter = None
        self._gloss_buf = None
        self._sentence_det = None
        self._translator = None

        # Soft-reset our adapter (keeps classifier + MediaPipe graph cached
        # so restart is fast). Full release happens in release_gpu().
        if self._our_adapter is not None:
            try:
                self._our_adapter.stop()
            except Exception as exc:
                logger.warning(f"OurAdapter stop error (non-fatal): {exc}")

        with self._frame_lock:
            self._latest_frame = None

    def release_gpu(self):
        logger.info("Releasing GPU resources...")
        self._model = None
        self._wholebody = None
        self._device = None
        self._label2idx = {}
        self._idx2label = {}
        self._cfg = {}
        self._gpu_initialized = False
        if self._our_adapter is not None:
            try:
                self._our_adapter.release()
            except Exception as exc:
                logger.warning(f"OurAdapter release error (non-fatal): {exc}")
            self._our_adapter = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        logger.info("GPU resources released")

    # ── Actions (called from API endpoints) ─────────────────────
    def manual_sign_toggle(self):
        self._pending_manual_toggle = True

    def set_threshold(self, value: float):
        self._conf_threshold = max(0.05, min(0.95, value))
        self._bus.publish("threshold_changed", {
            "value": self._conf_threshold,
        })

    def clear_history(self):
        self._history.clear()
        if self._gloss_buf:
            self._gloss_buf.clear()
        if self._sentence_det:
            self._sentence_det.reset()
        self._bus.publish("history_cleared", {})

    def full_reset(self):
        if self._tracker:
            self._tracker.reset()
        if self._feature_ext:
            self._feature_ext.reset()
        if self._spotter:
            self._spotter.reset()
        self._history.clear()
        self._confirmed_label = None
        self._confirmed_conf = 0.0
        self._raw_label = None
        self._raw_conf = 0.0
        self._raw_top5 = []
        self._current_feat = None
        self._segment_count = 0
        if self._gloss_buf:
            self._gloss_buf.clear()
        if self._sentence_det:
            self._sentence_det.reset()
        self._bus.publish("full_reset", {})

    # ── Status snapshot ─────────────────────────────────────────
    def get_status(self) -> dict:
        spotter_state = (
            self._spotter.state if self._spotter else "stopped"
        )
        return {
            "running": self._running,
            "state": spotter_state,
            "camera_source": self._camera_source,
            "fps": round(self._fps, 1),
            "segment_count": self._segment_count,
            "conf_threshold": self._conf_threshold,
            "confirmed_label": self._confirmed_label,
            "confirmed_confidence": round(self._confirmed_conf, 4),
            "raw_label": self._raw_label,
            "raw_confidence": round(self._raw_conf, 4),
            "raw_top5": [
                {"label": l, "confidence": round(c, 4)}
                for l, c in self._raw_top5
            ],
            "history": list(self._history[-20:]),
            "gloss_buffer": (
                self._gloss_buf.current_gloss
                if self._gloss_buf
                else []
            ),
            "gloss_str": (
                self._gloss_buf.current_gloss_str
                if self._gloss_buf
                else ""
            ),
            "translations": (
                self._gloss_buf.translations[-5:]
                if self._gloss_buf
                else []
            ),
            "last_translation": (
                self._gloss_buf.last_translation
                if self._gloss_buf
                else None
            ),
            "ollama_available": (
                self._translator.is_available
                if self._translator
                else False
            ),
            "ollama_translating": (
                self._translator.is_translating
                if self._translator
                else False
            ),
            "gpu_cached": self._gpu_initialized,
            "motion": round(
                float(
                    self._spotter.current_motion
                    if self._spotter
                    else 0.0
                ),
                6,
            ),
            "hands_raised": bool(
                self._spotter.dbg_hands_raised
                if self._spotter
                else False
            ),
            "hands_visible": bool(
                self._spotter.dbg_hands_visible
                if self._spotter
                else False
            ),
            "buffer_frames": (
                self._spotter.buffer_len if self._spotter else 0
            ),
            "skip_preview": self._skip_preview,
            "preview_mode": (
                "browser-native" if self._skip_preview else "mjpeg"
            ),
            "remote_transport": self._remote_transport,
            "ws_frames_received": self._ws_frames_received,
            "http_frames_received": self._http_frames_received,
            "last_frame_age_sec": (
                round(time.time() - self._last_frame_ts, 3)
                if self._last_frame_ts else None
            ),
        }

    # ── Internal: classification ────────────────────────────────
    def _do_classify(self, seg):
        self._segment_count += 1
        sf = seg.shape[0]
        logger.info(f"Segment #{self._segment_count}: {sf} frames")

        if RUNTIME_CONFIG.asl_pipeline == "ours" and self._our_adapter is not None:
            # ── OURS path: curated604 + synfold (and optional async F-refine) ──
            result = self._our_adapter.classify(seg)
            self._raw_conf = float(result.top1_confidence)
            self._raw_label = result.top1_label
            self._raw_top5 = list(result.top5)
            # F-refinement: re-classify halves async; on better split, log only
            # for now (replacing already-published gloss_buf/history entries
            # would need deeper integration with friend's gloss buffer).
            try:
                self._our_adapter.maybe_refine_async(
                    seg, result,
                    on_better=lambda l, r: logger.info(
                        f"  F-refine suggests split: "
                        f"{l.top1_label}({l.top1_confidence:.2f}) + "
                        f"{r.top1_label}({r.top1_confidence:.2f})"
                    ),
                )
            except Exception:
                pass
        else:
            mf = int(self._cfg.get("max_frames", 128))
            nc = len(self._label2idx)
            vnc = int(self._cfg.get("val_num_clips", 3))
            vmc = int(self._cfg.get("val_max_frames_cap", 384))

            probs = inf.classify_segment(
                self._model, seg, mf, self._device, nc, vmc, vnc
            )
            tc, ti = probs.max(0)
            self._raw_conf = float(tc.item())
            self._raw_label = self._idx2label.get(int(ti.item()), "???")

            k = min(5, nc)
            tv, tii = probs.topk(k)
            self._raw_top5 = [
                (self._idx2label.get(int(i.item()), "?"), float(v.item()))
                for v, i in zip(tv, tii)
            ]

        self._bus.publish("sign_detected", {
            "label": self._raw_label,
            "confidence": round(self._raw_conf, 4),
            "top5": [
                {"label": l, "confidence": round(c, 4)}
                for l, c in self._raw_top5
            ],
            "frames": sf,
            "segment": self._segment_count,
        })
        # Mobile-compatible alias
        self._bus.publish("caption.partial", {"text": self._raw_label})

        if self._raw_conf >= self._conf_threshold:
            self._confirmed_label = self._raw_label
            self._confirmed_conf = self._raw_conf
            if (
                not self._history
                or self._history[-1] != self._confirmed_label
            ):
                self._history.append(self._confirmed_label)

            logger.info(
                f"  ACCEPTED: {self._confirmed_label} "
                f"({self._confirmed_conf * 100:.1f}%)"
            )

            added = self._gloss_buf.add(
                self._confirmed_label, self._confirmed_conf
            )
            if added:
                self._sentence_det.notify_sign_added()
                self._bus.publish("gloss_updated", {
                    "tokens": self._gloss_buf.current_gloss,
                    "gloss_str": self._gloss_buf.current_gloss_str,
                    "latest_sign": self._confirmed_label,
                })
                if self._disable_translation:
                    self._on_translation_done(self._confirmed_label, suppress_tts=True)

            self._bus.publish("sign_accepted", {
                "label": self._confirmed_label,
                "confidence": round(self._confirmed_conf, 4),
            })
            # Mobile-compatible alias
            self._bus.publish("caption.final", {"text": self._confirmed_label})
        else:
            logger.info(
                f"  REJECTED: {self._raw_label} "
                f"({self._raw_conf * 100:.1f}%) "
                f"< {self._conf_threshold * 100:.0f}%"
            )
            self._bus.publish("sign_rejected", {
                "label": self._raw_label,
                "confidence": round(self._raw_conf, 4),
                "threshold": self._conf_threshold,
            })

    # ── Sliding-cover v3 emit publisher ─────────────────────────
    def _do_classify_sliding(self, label: str, conf: float, end_idx: int) -> None:
        """Per-sign emit from SlidingLiveEmitter — no per-segment classify.

        Bypasses GlossBuffer + SentenceBoundaryDetector + Ollama. Each emit
        becomes its own caption line (matches our /dev demo behaviour). The
        future agentic-Ollama pass will reintroduce smarter grouping.
        """
        self._segment_count += 1
        self._raw_label = label
        self._raw_conf = float(conf)
        self._raw_top5 = [(label, float(conf))]
        self._bus.publish("sign_detected", {
            "label": label,
            "confidence": round(float(conf), 4),
            "top5": [{"label": label, "confidence": round(float(conf), 4)}],
            "frames": 0,
            "segment": self._segment_count,
        })
        self._bus.publish("caption.partial", {"text": label})
        if float(conf) >= self._conf_threshold:
            self._confirmed_label = label
            self._confirmed_conf = float(conf)
            if not self._history or self._history[-1] != label:
                self._history.append(label)
            self._bus.publish("sign_accepted", {
                "label": label,
                "confidence": round(float(conf), 4),
            })
            self._bus.publish("caption.final", {"text": label})
            # Web frontend (frontend/web/app.js) appends to its transcript on
            # the "translation" event — sliding mode bypasses GlossBuffer +
            # Ollama so we publish a passthrough translation here so the web
            # UI populates. Mobile uses caption.final directly and ignores this.
            self._bus.publish("translation", {
                "english": label,
                "all_translations": [],
                "tts_suppress": self._disable_translation,
            })
            logger.info(
                f"  SLIDING ACCEPTED: {label} ({float(conf) * 100:.1f}%) "
                f"end_idx={end_idx}"
            )

    # ── Translation callback ────────────────────────────────────
    def _on_translation_done(self, english: str, suppress_tts: bool = False):
        if self._gloss_buf:
            self._gloss_buf.add_translation(english)
        payload: dict = {
            "english": english,
            "all_translations": (
                self._gloss_buf.translations[-5:]
                if self._gloss_buf
                else []
            ),
        }
        if suppress_tts:
            payload["tts_suppress"] = True
        self._bus.publish("translation", payload)
        logger.info(f'  Translation: "{english}"')

    # ── Annotate frame for MJPEG ────────────────────────────────
    def _annotate_frame(self, frame, feat):
        """Draw skeleton overlay and minimal status on the frame.
        Only draws skeleton when hands are visible — prevents floating lines."""
        disp = frame.copy()
        H, W = disp.shape[:2]

        # Skeleton overlay: friend's draw_skel expects RTMPose-133 layout. In
        # ours-mode feat is (86, 7) MediaPipe — skip the skeleton and let the
        # frontend canvas (driven by the SSE "keypoints" event with layout=mp86)
        # handle it instead.
        if (
            RUNTIME_CONFIG.asl_pipeline != "ours"
            and feat is not None
            and self._spotter
            and self._spotter.dbg_hands_visible
        ):
            inf.draw_skel(
                disp, feat, W, H,
                inf.HAND_L, (200, 255, 100), (150, 200, 80),
            )
            inf.draw_skel(
                disp, feat, W, H,
                inf.HAND_R, (100, 200, 255), (80, 160, 200),
            )
            for idx, col in [
                (inf.WRIST_L, (50, 255, 50)),
                (inf.WRIST_R, (255, 100, 50)),
            ]:
                if feat[idx, 2] >= inf.CONF_THRESH:
                    cx = int(feat[idx, 0] * W)
                    cy = int(feat[idx, 1] * H)
                    cv2.circle(disp, (cx, cy), 6, col, 2, cv2.LINE_AA)

        # State indicator border
        state = self._spotter.state if self._spotter else "idle"
        if state == "signing":
            cv2.rectangle(
                disp, (0, 0), (W - 1, H - 1), (0, 200, 255), 3
            )
        elif state == "manual":
            cv2.rectangle(
                disp, (0, 0), (W - 1, H - 1), (200, 50, 200), 4
            )

        return disp

    # ── Main inference loop (runs in background thread) ─────────
    def _inference_loop(self):
        logger.info("Inference thread started")
        print(
            f"[INFERENCE THREAD] Started "
            f"(gpu_cached={self._gpu_initialized})",
            flush=True,
        )

        # ── Phase 1: GPU initialization ─────────────────────────
        try:
            cfg = self._init_config

            # ── OURS pipeline init (skip RTMW + friend model load) ──
            if RUNTIME_CONFIG.asl_pipeline == "ours":
                if self._our_adapter is None:
                    logger.info(
                        "[ASL_PIPELINE=ours] Building MediaPipe-86 + "
                        "curated604 + SignSpotter adapter..."
                    )
                    self._our_adapter = OursPipelineAdapter(
                        checkpoint_path=RUNTIME_CONFIG.our_model_checkpoint,
                        config_path=RUNTIME_CONFIG.our_model_config,
                        vocab_path=RUNTIME_CONFIG.our_model_vocab,
                        preset_name=RUNTIME_CONFIG.our_seg_preset,
                        synfold=RUNTIME_CONFIG.our_synfold,
                        refine_conf=RUNTIME_CONFIG.our_refine_conf,
                        device=cfg.get(
                            "rtmw_device", RUNTIME_CONFIG.hardware_profile
                        ),
                        blocked_labels=list(RUNTIME_CONFIG.our_blocked_labels),
                        sliding_mode=RUNTIME_CONFIG.our_sliding_mode,
                    )
                    self._our_adapter.start()
                else:
                    # Restart — rebuild MediaPipe graph (cheap), keep model cached.
                    self._our_adapter.start()
                # Expose our spotter as the "spotter" the rest of the loop
                # queries for current_motion / dbg_hands_raised / state /
                # buffer_len / manual_toggle. SignSpotter's API is identical.
                self._spotter = self._our_adapter.spotter
                self._device = (
                    self._our_adapter.classifier._device
                    if self._our_adapter.classifier is not None
                    else None
                )
                self._gpu_initialized = True
                self._init_done.set()
                # Skip friend's RTMW/model init below
            elif not self._gpu_initialized:
                device_str = cfg.get(
                    "rtmw_device",
                    RUNTIME_CONFIG.hardware_profile,
                )
                self._device = torch.device(device_str)
                device_label = (
                    "CUDA GPU" if self._device.type == "cuda" else "CPU"
                )

                logger.info("First start — loading model + RTMW...")
                print(
                    f"[INFERENCE THREAD] First start — {device_label} init...",
                    flush=True,
                )
                logger.info(f"Device: {self._device}")

                if self._device.type == "cuda":
                    torch.backends.cuda.matmul.allow_tf32 = True
                    torch.backends.cudnn.allow_tf32 = True

                checkpoint = cfg["checkpoint"]
                logger.info(f"Loading checkpoint: {checkpoint}")
                print(
                    f"[INFERENCE THREAD] Loading: {checkpoint}",
                    flush=True,
                )
                self._model, self._label2idx, self._cfg = inf.load_ckpt(
                    checkpoint, self._device
                )
                self._idx2label = {
                    v: k for k, v in self._label2idx.items()
                }
                mf = int(self._cfg.get("max_frames", 128))
                logger.info(
                    f"Model loaded: {len(self._label2idx)} classes"
                )

                if self._device.type == "cuda":
                    logger.info("GPU warmup (3 forward passes)...")
                    d = torch.randn(
                        1, mf, inf.FLAT_DIM, device=self._device
                    )
                    m = torch.ones(
                        1, mf, dtype=torch.bool, device=self._device
                    )
                    with torch.no_grad(), torch.amp.autocast("cuda"):
                        for _ in range(3):
                            self._model(d, m)
                    del d, m
                    torch.cuda.empty_cache()
                    logger.info("GPU warmup complete")

                logger.info("Initializing RTMW wholebody detector...")
                print(
                    "[INFERENCE THREAD] Loading RTMW (ONNX)...",
                    flush=True,
                )
                from rtmlib import Wholebody

                self._wholebody = Wholebody(
                    mode=cfg["rtmw_mode"],
                    backend=cfg["rtmw_backend"],
                    device=cfg["rtmw_device"],
                )
                logger.info("RTMW ready")

                logger.info("Testing RTMW with dummy frame...")
                test_frame = np.zeros((480, 640, 3), dtype=np.uint8)
                _test_kp, _test_sc = self._wholebody(test_frame)
                logger.info(
                    f"RTMW test OK (kp type: {type(_test_kp)})"
                )
                del test_frame, _test_kp, _test_sc

                self._gpu_initialized = True
                logger.info(
                    f"{device_label} init complete — models now cached"
                )
                print(
                    f"[INFERENCE THREAD] {device_label} init done "
                    f"— cached for future restarts",
                    flush=True,
                )

            else:
                device_label = (
                    "CUDA GPU" if self._device.type == "cuda" else "CPU"
                )
                logger.info(
                    "Restart — reusing cached model + RTMW "
                    f"({len(self._label2idx)} classes)"
                )
                print(
                    f"[INFERENCE THREAD] Using cached {device_label} "
                    f"models (instant restart)",
                    flush=True,
                )

            self._init_done.set()

        except Exception as e:
            err_msg = (
                f"GPU init failed: {e}\n{traceback.format_exc()}"
            )
            logger.error(err_msg)
            print(f"[INFERENCE THREAD ERROR] {err_msg}", flush=True)
            self._init_error = str(e)
            self._init_done.set()
            self._running = False
            return

        # ── Phase 2: Main frame loop ────────────────────────────
        fpsd = deque(maxlen=30)
        prev_state = "idle"
        consecutive_errors = 0
        consecutive_none_frames = 0
        MAX_CONSECUTIVE_ERRORS = 50
        # Local cameras: consecutive-None reads indicate a dead device.
        # Remote (browser) sources: None often just means "no fresh
        # frame yet" thanks to the freshness gate, so we watchdog on
        # ingress time (_last_frame_ts) instead of None-read count.
        MAX_NONE_FRAMES = 150
        REMOTE_NO_INGRESS_TIMEOUT_SEC = 30.0
        REMOTE_CONNECT_GRACE_SEC = 60.0
        is_remote = self._camera_source == "remote"
        loop_start_ts = time.time()

        logger.info(
            f"Entering main frame loop "
            f"(self._running={self._running}, "
            f"camera={self._camera is not None})"
        )

        if self._camera is None:
            logger.error("Camera is None — cannot enter frame loop!")
            self._running = False
            return

        loop_iteration = 0

        while self._running:
            try:
                t0 = time.perf_counter()
                loop_iteration += 1

                frame = self._camera.read() if self._camera else None
                if frame is None:
                    consecutive_none_frames += 1
                    if is_remote:
                        reason = _remote_dead_camera_reason(
                            now=time.time(),
                            last_frame_ts=self._last_frame_ts,
                            loop_start_ts=loop_start_ts,
                            no_ingress_timeout_sec=REMOTE_NO_INGRESS_TIMEOUT_SEC,
                            connect_grace_sec=REMOTE_CONNECT_GRACE_SEC,
                        )
                        if reason is not None:
                            logger.error(f"Remote source dead: {reason}")
                            self._bus.publish("error", {
                                "message": (
                                    "Browser stopped sending frames"
                                ),
                            })
                            break
                        time.sleep(0.01)
                        continue

                    if consecutive_none_frames == 1:
                        logger.warning(
                            "Camera returned None frame "
                            f"(iteration {loop_iteration})"
                        )
                    elif consecutive_none_frames % 30 == 0:
                        logger.warning(
                            f"Camera: {consecutive_none_frames} "
                            f"consecutive None frames "
                            f"(camera obj: {self._camera is not None})"
                        )
                    if consecutive_none_frames >= MAX_NONE_FRAMES:
                        logger.error(
                            f"Camera produced {MAX_NONE_FRAMES} "
                            "consecutive None frames — camera is "
                            "dead. Exiting loop."
                        )
                        self._bus.publish("error", {
                            "message": (
                                "Camera stopped producing frames"
                            ),
                        })
                        break
                    time.sleep(0.01)
                    continue

                consecutive_none_frames = 0
                H, W = frame.shape[:2]
                feat = None

                # ────────────────────────────────────────────────────────────
                # OURS pipeline: MediaPipe-86 → SignSpotter(mp86) → curated604
                # ────────────────────────────────────────────────────────────
                if RUNTIME_CONFIG.asl_pipeline == "ours":
                    try:
                        seg_emit, feat = self._our_adapter.process_frame(frame)
                    except Exception as e:
                        logger.error(f"MediaPipe extract failed: {e}")
                        logger.error(traceback.format_exc())
                        consecutive_errors += 1
                        if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                            break
                        time.sleep(0.01)
                        continue
                    self._current_feat = feat

                    if (
                        RUNTIME_CONFIG.keypoints_stream_enabled
                        and loop_iteration % 2 == 0
                    ):
                        try:
                            self._bus.publish(
                                "keypoints",
                                self._our_adapter.build_keypoints_payload(feat),
                            )
                        except Exception:
                            pass

                    if seg_emit is not None:
                        seg_feat, _seg_frames = seg_emit
                        try:
                            self._do_classify(seg_feat)
                        except Exception as e:
                            logger.error(f"Classification failed: {e}")
                            logger.error(traceback.format_exc())
                            print(f"[CLASSIFY ERROR] {e}", flush=True)

                    # ── Sliding-cover v3 emit drain ──
                    # Classifications happen inside SlidingLiveEmitter.feed();
                    # drain any (label, conf, end_idx) and publish per-sign
                    # captions, bypassing GlossBuffer + Ollama (matches /dev
                    # demo: each sign streams as its own caption line).
                    if RUNTIME_CONFIG.our_sliding_mode:
                        emit = self._our_adapter.pop_sliding_emit()
                        if emit is not None:
                            try:
                                self._do_classify_sliding(*emit)
                            except Exception as e:
                                logger.error(f"Sliding emit publish failed: {e}")
                                logger.error(traceback.format_exc())
                else:
                    # ── Friend's pose estimation (RTMPose-133) ──
                    try:
                        kp, sc = self._wholebody(frame)
                    except Exception as e:
                        logger.error(f"RTMW inference failed: {e}")
                        print(f"[RTMW ERROR] {e}", flush=True)
                        logger.error(traceback.format_exc())
                        consecutive_errors += 1
                        if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                            logger.error(
                                f"Too many RTMW errors "
                                f"({consecutive_errors}), stopping"
                            )
                            break
                        time.sleep(0.01)
                        continue

                    pk, ps = self._tracker.select(kp, sc)
                    feat = self._feature_ext.process(pk, ps, W, H)
                    self._current_feat = feat

                    # ── Keypoints streaming (env: KEYPOINTS_STREAM_ENABLED) ──
                    if RUNTIME_CONFIG.keypoints_stream_enabled and loop_iteration % 2 == 0:
                        try:
                            if pk is not None and len(pk) > 0:
                                kps_norm = (pk[:, :2] / np.array([W, H], dtype=np.float32)).clip(0.0, 1.0).tolist()
                                self._bus.publish("keypoints", {
                                    "kps": kps_norm,
                                    "scores": ps.tolist() if ps is not None else [],
                                    "has_person": True,
                                })
                            else:
                                self._bus.publish("keypoints", {"has_person": False, "kps": [], "scores": []})
                        except Exception:
                            pass  # never let keypoints crash the inference loop

                    # ── Sign spotting (auto-detect) ──
                    segment = self._spotter.update(feat)
                    if segment is not None:
                        try:
                            self._do_classify(segment)
                        except Exception as e:
                            logger.error(f"Classification failed: {e}")
                            logger.error(traceback.format_exc())
                            print(
                                f"[CLASSIFY ERROR] {e}", flush=True
                            )

                # ── Manual sign toggle (from API) ──
                if self._pending_manual_toggle:
                    self._pending_manual_toggle = False
                    manual_seg = self._spotter.manual_toggle()
                    if manual_seg is not None:
                        # Our SignSpotter returns (seg, frames) tuple; friend's
                        # returns a plain ndarray. Normalize.
                        seg_arr = (
                            manual_seg[0]
                            if isinstance(manual_seg, tuple)
                            else manual_seg
                        )
                        try:
                            self._do_classify(seg_arr)
                        except Exception as e:
                            logger.error(
                                f"Manual classify failed: {e}"
                            )
                            logger.error(traceback.format_exc())
                    else:
                        cur = self._spotter.state
                        self._bus.publish(
                            "state_change", {"state": cur}
                        )

                # ── State change events ──
                cur_state = self._spotter.state
                if cur_state != prev_state:
                    self._bus.publish("state_change", {
                        "state": cur_state,
                        "prev_state": prev_state,
                        "motion": round(
                            self._spotter.current_motion, 6
                        ),
                        "buffer_frames": self._spotter.buffer_len,
                    })
                    prev_state = cur_state

                # ── Sentence boundary detection ──
                if (
                    self._gloss_buf
                    and self._gloss_buf.is_active
                ):
                    boundary = self._sentence_det.update(
                        hands_raised=(
                            self._spotter.dbg_hands_raised
                        ),
                        motion=self._spotter.current_motion,
                        gloss_size=self._gloss_buf.size,
                    )
                    if boundary:
                        tokens = self._gloss_buf.flush()
                        if tokens:
                            gloss_str = " ".join(tokens)
                            logger.info(
                                f"Sentence boundary! "
                                f'Gloss: "{gloss_str}"'
                            )
                            self._bus.publish(
                                "sentence_boundary", {
                                    "gloss_tokens": tokens,
                                    "gloss_str": gloss_str,
                                },
                            )
                            if self._disable_translation:
                                pass
                            elif (
                                self._translator is None
                                or not self._translator.is_available
                            ):
                                self._on_translation_done(gloss_str)
                            else:
                                self._translator.translate_async(
                                    tokens,
                                    callback=(
                                        self._on_translation_done
                                    ),
                                )
                            self._sentence_det.reset()

                # ── Annotate frame and encode JPEG (for MJPEG preview) ──
                # Skipped when skip_preview is True (remote/proxy browser-native
                # preview path); saves CPU/GPU by avoiding unnecessary work.
                if not self._skip_preview:
                    try:
                        annotated = self._annotate_frame(frame, feat)
                        ok, jpeg = cv2.imencode(
                            ".jpg", annotated,
                            [cv2.IMWRITE_JPEG_QUALITY, 75],
                        )
                        if ok:
                            with self._frame_lock:
                                self._latest_frame = jpeg.tobytes()
                    except Exception as e:
                        logger.error(
                            f"Frame annotation/encode failed: {e}"
                        )

                # ── FPS tracking ──
                dt = time.perf_counter() - t0
                fpsd.append(1.0 / max(dt, 1e-6))
                self._fps = (
                    float(np.mean(fpsd)) if fpsd else 0.0
                )

                consecutive_errors = 0

            except Exception as e:
                consecutive_errors += 1
                logger.error(
                    f"Frame error "
                    f"({consecutive_errors}/"
                    f"{MAX_CONSECUTIVE_ERRORS}): {e}"
                )
                logger.error(traceback.format_exc())
                print(
                    f"[FRAME ERROR {consecutive_errors}] {e}\n"
                    f"{traceback.format_exc()}",
                    flush=True,
                )
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    logger.error(
                        "Too many errors — stopping loop"
                    )
                    self._bus.publish("error", {
                        "message": (
                            f"Loop stopped after "
                            f"{consecutive_errors} errors: {e}"
                        ),
                    })
                    break
                time.sleep(0.01)

        logger.info(
            f"Inference loop exited: "
            f"_running={self._running}, "
            f"iterations={loop_iteration}, "
            f"none_frames={consecutive_none_frames}, "
            f"errors={consecutive_errors}"
        )
        self._running = False
        print("[INFERENCE THREAD] Loop ended", flush=True)


# ═════════════════════════════════════════════════════════════════════
# GLOBAL STATE
# ═════════════════════════════════════════════════════════════════════
event_bus = EventBus()
pipeline = InferencePipeline(event_bus)
_webrtc_pcs: Set[Any] = set()  # tracks open WebRTC peer connections


# Mobile portrait frames have a "table-edge" zone below the user's torso
# where their hands rest between signs. MediaPipe happily picks those
# resting hands up, the segmenter never sees a clean idle interval, and
# the model never accepts/finalizes a sign. Mask the bottom fraction of
# any portrait frame (h > w) to zero so MediaPipe simply doesn't see
# below the visible "active" area on the phone preview. Web (landscape)
# frames pass through untouched. Configurable via env; 0.30 default keeps
# the upper 70% of the portrait frame.
_MOBILE_PORTRAIT_BOTTOM_MASK_FRAC = float(
    os.environ.get("MOBILE_PORTRAIT_BOTTOM_MASK_FRAC", "0.30")
)


def _maybe_mask_portrait_bottom(img: np.ndarray) -> np.ndarray:
    if _MOBILE_PORTRAIT_BOTTOM_MASK_FRAC <= 0:
        return img
    h, w = img.shape[:2]
    if h <= w:
        return img  # landscape (web) — leave alone
    keep_h = int(h * (1.0 - _MOBILE_PORTRAIT_BOTTOM_MASK_FRAC))
    if 0 < keep_h < h:
        img[keep_h:, :, :] = 0
    return img

_default_config = {
    "checkpoint": str(inf.DEFAULT_CHECKPOINT),
    "camera_id": 0,
    "camera_source": "auto",
    # When ASL_PIPELINE=ours, curated604 segments come in around 0.15–0.35
    # for in-vocab signs; 0.45 rejects nearly everything. 0.20 matches our
    # own backend's pipeline_runtime default. Friend's RTMPose pipeline
    # used to default 0.45 but was always overridden via the start request,
    # so this lower default is safe for both paths.
    "conf_threshold": (
        float(RUNTIME_CONFIG.env.get("ASL_CONF_THRESHOLD", "0.20"))
        if RUNTIME_CONFIG.asl_pipeline == "ours"
        else 0.45
    ),
    "rtmw_mode": "performance",
    "rtmw_backend": "onnxruntime",
    "rtmw_device": RUNTIME_CONFIG.hardware_profile,
    "ollama_url": str(inf.DEFAULT_OLLAMA_URL),
    "ollama_model": str(inf.DEFAULT_OLLAMA_MODEL),
    "ollama_timeout": float(inf.DEFAULT_OLLAMA_TIMEOUT),
    "disable_translation": (
        os.environ.get("ASL_DISABLE_TRANSLATION", "0") == "1"
        or not RUNTIME_CONFIG.ollama_asl_enabled
    ),
    "sentence_rest_frames": inf.SENTENCE_REST_FRAMES,
    "skip_preview": False,
}

_public_start_response_config_keys = (
    "checkpoint",
    "camera_id",
    "camera_source",
    "conf_threshold",
    "disable_translation",
)


def _public_start_response_config(config: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: config[key]
        for key in _public_start_response_config_keys
        if key in config
    }


# ═════════════════════════════════════════════════════════════════════
# FASTAPI APP
# ═════════════════════════════════════════════════════════════════════
@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    pipeline.stop()
    pipeline.release_gpu()


app = FastAPI(
    title="ASL Real-Time Translation API",
    version="3.3",
    description=(
        "FastAPI wrapper for ASL sign language recognition and "
        "translation. Website edition — continuous flow, "
        "restart-safe with model caching. CTR-GCN support. "
        "Supports local camera or remote browser camera via WebSocket."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    **_build_cors_settings(),
)


@app.post("/offer")
async def offer(request: Request, params: OfferRequest):
    """WebRTC signaling endpoint — used by the mobile client.

    The mobile sends an SDP offer; we return an SDP answer. Frames from
    the WebRTC track are pushed into the pipeline's RemoteFrameSource once
    the pipeline is started with camera_source='remote'.
    """
    _require_user_http(request)
    from aiortc import RTCPeerConnection, RTCSessionDescription

    sdp_offer = RTCSessionDescription(sdp=params.sdp, type=params.type)
    pc = RTCPeerConnection()
    _webrtc_pcs.add(pc)

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        logger.info(f"WebRTC state: {pc.connectionState}")
        if pc.connectionState in ("failed", "closed"):
            _webrtc_pcs.discard(pc)

    @pc.on("track")
    def on_track(track):
        if track.kind == "video":
            logger.info("WebRTC video track received")

            async def consume():
                while True:
                    try:
                        frame = await track.recv()
                        img = frame.to_ndarray(format="bgr24")
                        # Mask out the bottom portion of portrait frames so
                        # MediaPipe doesn't see the user's hands resting on
                        # the table just below the visible "active" area.
                        # Web (landscape) frames are untouched. Mobile
                        # portrait (h > w) frames get the lower fraction
                        # zeroed out — see _MOBILE_PORTRAIT_BOTTOM_MASK_FRAC.
                        img = _maybe_mask_portrait_bottom(img)
                        # Push into the pipeline's RemoteFrameSource if available
                        cam = pipeline._camera
                        if isinstance(cam, RemoteFrameSource):
                            _, jpeg = cv2.imencode(
                                ".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 75]
                            )
                            cam.push(jpeg.tobytes())
                            pipeline._last_frame_ts = time.time()
                            pipeline._remote_transport = "webrtc"
                    except Exception as e:
                        logger.warning(f"WebRTC track ended: {e}")
                        break

            asyncio.create_task(consume())

    await pc.setRemoteDescription(sdp_offer)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}


@app.post("/start")
async def start_pipeline(request: Request, req: StartRequest = StartRequest()):
    _require_user_http(request)

    # Voice mode: mobile sends mode="voice" — skip ASL pipeline entirely
    if req.mode == "voice":
        logger.info("Voice mode requested; skipping ASL pipeline.")
        event_bus.publish("status", {"text": "session_started"})
        return {"status": "started", "note": "voice_mode"}

    if pipeline.is_truly_running:
        return JSONResponse(
            status_code=409,
            content={
                "error": (
                    "Pipeline is already running. "
                    "POST /stop first."
                )
            },
        )

    config = dict(_default_config)
    if req.checkpoint:
        config["checkpoint"] = req.checkpoint
    if req.camera_id is not None:
        config["camera_id"] = req.camera_id
    if req.camera_source is not None:
        config["camera_source"] = req.camera_source
    if req.conf_threshold is not None:
        config["conf_threshold"] = req.conf_threshold
    if req.disable_translation is not None:
        config["disable_translation"] = req.disable_translation
    if not RUNTIME_CONFIG.ollama_asl_enabled:
        config["disable_translation"] = True
    if req.skip_preview is not None:
        config["skip_preview"] = bool(req.skip_preview)

    try:
        pipeline.start(
            checkpoint=config["checkpoint"],
            camera_id=config["camera_id"],
            camera_source=config["camera_source"],
            conf_threshold=config["conf_threshold"],
            rtmw_mode=config["rtmw_mode"],
            rtmw_backend=config["rtmw_backend"],
            rtmw_device=config["rtmw_device"],
            ollama_url=config["ollama_url"],
            ollama_model=config["ollama_model"],
            ollama_timeout=config["ollama_timeout"],
            disable_translation=config["disable_translation"],
            sentence_rest_frames=config["sentence_rest_frames"],
            skip_preview=config["skip_preview"],
        )
        return {
            "status": "started",
            "gpu_cached": pipeline.gpu_cached,
            "config": _public_start_response_config(config),
        }
    except Exception as e:
        logger.error(f"Failed to start pipeline: {e}")
        logger.error(traceback.format_exc())
        return JSONResponse(
            status_code=500,
            content={"error": str(e)},
        )


@app.post("/stop")
async def stop_pipeline(request: Request):
    _require_user_http(request)
    if not pipeline.is_running:
        return JSONResponse(
            status_code=409,
            content={"error": "Pipeline is not running."},
        )
    pipeline.stop()
    return {
        "status": "stopped",
        "gpu_cached": pipeline.gpu_cached,
    }


@app.get("/status")
async def get_status(request: Request):
    _require_user_http(request)
    return pipeline.get_status()


@app.get("/runtime")
async def runtime_info(request: Request):
    _require_user_http(request)
    report = RUNTIME_CONFIG.hardware_report
    return {
        "profile": report.profile,
        "source": report.source,
        "torch_cuda": report.torch_cuda,
        "onnx_cuda": report.onnx_cuda,
        "onnx_providers": list(report.onnx_providers),
        "directml_available": report.directml_available,
        "directml_supported": False,
        "gpu_name": report.gpu_name,
        "torch_version": report.torch_version,
        "onnx_version": report.onnx_version,
    }


@app.get("/stream")
async def sse_stream(request: Request):
    _require_user_http(request)
    q = event_bus.subscribe()

    async def stream_events():
        last_data_time = time.time()
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = q.get_nowait()
                    last_data_time = time.time()
                    yield f"data: {json.dumps(event)}\n\n"
                except queue.Empty:
                    now = time.time()
                    if now - last_data_time > 15.0:
                        yield ": keepalive\n\n"
                        last_data_time = now
                    await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            pass
        finally:
            event_bus.unsubscribe(q)

    return StreamingResponse(
        stream_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/video_feed")
async def video_feed(request: Request):
    _require_user_http(request)

    async def generate():
        while True:
            jpeg = pipeline.latest_frame_jpeg
            if jpeg is not None:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n"
                    + jpeg
                    + b"\r\n"
                )
            await asyncio.sleep(0.033)

    return StreamingResponse(
        generate(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache"},
    )


@app.websocket("/ws/frames")
async def ws_frames(ws: WebSocket):
    """
    Receive camera frames from the browser over WebSocket.

    The client captures frames from getUserMedia(), encodes as JPEG,
    and sends as binary WebSocket messages. Each message is one JPEG frame.

    The frames are pushed into the pipeline's RemoteFrameSource.
    """
    user = _get_user_ws(ws)
    if not user:
        await ws.close(code=4401)
        return

    await ws.accept()
    logger.info("[/ws/frames] Browser camera connected")

    if not pipeline.is_running:
        await ws.close(code=4000, reason="Pipeline not running — POST /start first")
        return

    if pipeline._camera_source != "remote":
        await ws.close(
            code=4001,
            reason="Pipeline is using local camera, not remote",
        )
        return

    remote_src = pipeline._camera
    if not isinstance(remote_src, RemoteFrameSource):
        await ws.close(code=4002, reason="Frame source is not remote")
        return

    frames_received = 0
    try:
        while pipeline.is_running and remote_src.is_active:
            data = await ws.receive_bytes()
            if remote_src.push(data):
                frames_received += 1
                pipeline._ws_frames_received += 1
                pipeline._last_frame_ts = time.time()
                pipeline._remote_transport = "ws"
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning(f"[/ws/frames] Error: {e}")
    finally:
        logger.info(
            f"[/ws/frames] Browser camera disconnected "
            f"(frames={frames_received})"
        )


@app.post("/upload_frame")
async def upload_frame(request: Request):
    """Receive a single JPEG frame via HTTP POST (HTTP/2-compatible alternative to /ws/frames)."""
    _require_user_http(request)
    if not pipeline.is_running:
        return JSONResponse(status_code=409, content={"error": "Pipeline not running"})
    if pipeline._camera_source != "remote":
        return JSONResponse(status_code=400, content={"error": "Not in remote camera mode"})
    remote_src = pipeline._camera
    if not isinstance(remote_src, RemoteFrameSource):
        return JSONResponse(status_code=400, content={"error": "Frame source is not remote"})

    jpeg_bytes = await request.body()
    if not jpeg_bytes:
        return JSONResponse(status_code=400, content={"error": "Empty body"})

    if remote_src.push(jpeg_bytes):
        pipeline._http_frames_received += 1
        pipeline._last_frame_ts = time.time()
        pipeline._remote_transport = "http"
    return Response(status_code=204)


@app.post("/manual_sign")
async def manual_sign(request: Request):
    _require_user_http(request)
    if not pipeline.is_running:
        return JSONResponse(
            status_code=409,
            content={"error": "Pipeline not running"},
        )
    pipeline.manual_sign_toggle()
    return {"status": "toggled"}


@app.post("/threshold")
async def set_threshold(request: Request, req: ThresholdRequest):
    _require_user_http(request)
    pipeline.set_threshold(req.value)
    return {
        "status": "updated",
        "value": pipeline._conf_threshold,
    }


@app.post("/clear")
async def clear_history(request: Request):
    _require_user_http(request)
    pipeline.clear_history()
    return {"status": "cleared"}


@app.post("/reset")
async def full_reset(request: Request):
    _require_user_http(request)
    pipeline.full_reset()
    return {"status": "reset"}


@app.get("/")
async def root():
    return {
        "service": "ASL Real-Time Translation API",
        "version": "3.3",
        "running": pipeline.is_running,
        "gpu_cached": pipeline.gpu_cached,
        "camera_source": pipeline._camera_source if pipeline.is_running else None,
        "endpoints": {
            "POST /start": "Launch pipeline (camera_source: local|remote|auto)",
            "POST /stop": "Stop pipeline (keeps model cached)",
            "GET /status": "Current state snapshot (JSON)",
            "GET /runtime": "Hardware profile and runtime diagnostics",
            "GET /stream": "Server-Sent Events (real-time)",
            "GET /video_feed": "MJPEG camera stream (annotated)",
            "WS /ws/frames": "Send browser camera JPEG frames (remote mode)",
            "POST /manual_sign": "Toggle manual recording [S]",
            "POST /threshold": "Set confidence {value: 0.25}",
            "POST /clear": "Clear translations/history [C]",
            "POST /reset": "Full reset [R]",
        },
    }


# ═════════════════════════════════════════════════════════════════════
# CLI + MAIN
# ═════════════════════════════════════════════════════════════════════
def parse_args():
    p = argparse.ArgumentParser(
        description="ASL Real-Time Translation — FastAPI Server"
    )
    p.add_argument("--host", default=RUNTIME_CONFIG.asl_bind_host, help="Bind host")
    p.add_argument(
        "--port", type=int, default=RUNTIME_CONFIG.asl_port, help="Bind port"
    )
    p.add_argument(
        "--checkpoint", default=None,
        help="Override default checkpoint path",
    )
    p.add_argument(
        "--camera", type=int, default=None,
        help="Override camera ID",
    )
    p.add_argument(
        "--conf_threshold", type=float, default=None,
        help="Override confidence threshold",
    )
    p.add_argument(
        "--ollama_url", default=None,
        help="Override Ollama API URL",
    )
    p.add_argument(
        "--ollama_model", default=None,
        help="Override Ollama model name",
    )
    p.add_argument(
        "--ollama_timeout", type=float, default=None,
        help="Override Ollama request timeout in seconds",
    )
    p.add_argument(
        "--disable_translation", action="store_true",
        help="Disable Ollama translation",
    )
    p.add_argument(
        "--auto_start", action="store_true",
        help="Auto-start pipeline on server launch",
    )
    return p.parse_args()


def main():
    import uvicorn

    args = parse_args()

    if args.checkpoint:
        _default_config["checkpoint"] = args.checkpoint
    if args.camera is not None:
        _default_config["camera_id"] = args.camera
    if args.conf_threshold is not None:
        _default_config["conf_threshold"] = args.conf_threshold
    if args.ollama_url:
        _default_config["ollama_url"] = args.ollama_url
    if args.ollama_model:
        _default_config["ollama_model"] = args.ollama_model
    if args.ollama_timeout is not None:
        _default_config["ollama_timeout"] = args.ollama_timeout
    if args.disable_translation:
        _default_config["disable_translation"] = True

    print("\n" + "=" * 66)
    print("  ASL REAL-TIME TRANSLATION — FastAPI Server v3.3")
    print("  Restart-safe with model caching + CTR-GCN + remote camera")
    print(f"  http://{args.host}:{args.port}")
    print(f"  Checkpoint: {_default_config['checkpoint']}")
    print(f"  Ollama:     {_default_config['ollama_model']}")
    if args.auto_start:
        print("  Auto-start: YES")
    print("=" * 66 + "\n")

    if args.auto_start:
        def _auto_start():
            time.sleep(1.5)
            try:
                pipeline.start(**{
                    k: v for k, v in _default_config.items()
                })
            except Exception as e:
                logger.error(f"Auto-start failed: {e}")

        threading.Thread(
            target=_auto_start, daemon=True
        ).start()

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
