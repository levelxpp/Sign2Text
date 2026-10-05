"""backend.our_pipeline.adapter — pipeline adapter interface (implementation not published).

PUBLIC-REPOSITORY NOTE
----------------------
`OursPipelineAdapter` is the boundary between `backend/api_server.py` and the
ASL recognition pipeline. The real implementation was authored by Stepan
Karapetyan and is not published here — see the "What is and isn't in this
repository" section of the README.

This module documents the contract `api_server.py` depends on and raises a
clear error if the adapter is actually constructed, rather than failing later
with an obscure AttributeError.

Consequence: **ASL mode is not runnable from this repository.** Voice mode,
Letter mode, the admin service, the landing site, and the full test suite are
unaffected — none of them touch this adapter.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

_UNAVAILABLE_MESSAGE = (
    "The ASL recognition pipeline is not included in this public repository.\n"
    "\n"
    "backend/our_pipeline/ was authored by a teammate (Stepan Karapetyan) and "
    "is intentionally excluded — see the 'What is and isn't in this repository' "
    "section of README.md.\n"
    "\n"
    "Voice mode, Letter mode, the admin service, the landing site, and the test "
    "suite all run without it. To run ASL mode you would need to supply your own "
    "implementation of this interface plus a trained checkpoint."
)


class PipelineNotAvailableError(NotImplementedError):
    """Raised when code attempts to use the unpublished ASL pipeline."""


class ClassificationResult:
    """Result of classifying one segmented sign candidate.

    Attributes
    ----------
    label:
        Predicted gloss, or None when no class cleared the confidence gate.
    confidence:
        Top-1 softmax probability.
    topk:
        Ranked ``(label, confidence)`` pairs used for top-5 reporting and for
        the cover-after-run segmentation gate.
    """

    __slots__ = ("label", "confidence", "topk")

    def __init__(
        self,
        label: Optional[str] = None,
        confidence: float = 0.0,
        topk: Optional[Tuple[Tuple[str, float], ...]] = None,
    ) -> None:
        self.label = label
        self.confidence = confidence
        self.topk = topk or ()


class OursPipelineAdapter:
    """Adapter over the ASL recognition pipeline.

    The full implementation performs per-frame MediaPipe keypoint extraction,
    velocity-based sign-boundary segmentation, sliding-window classification
    against a CTR-GCN checkpoint, synonym folding, and asynchronous confidence
    refinement of low-confidence segments.

    Interface consumed by ``backend/api_server.py``:

    ==========================  ==========================================
    ``start()``                 Load model + extractor, begin a session.
    ``stop()``                  End the session, keep the model cached.
    ``release()``               Drop cached model and extractor.
    ``process_frame(frame)``    Feed one BGR frame; returns overlay + events.
    ``pop_sliding_emit()``      Pop a ready ``(label, confidence, frame_idx)``.
    ``classify(segment)``       Classify a keypoint segment.
    ``maybe_refine_async(...)`` Re-classify halves of a weak segment.
    ``build_keypoints_payload`` Serialize keypoints for the browser overlay.
    ``measured_fps``            Observed processing frame rate.
    ==========================  ==========================================
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise PipelineNotAvailableError(_UNAVAILABLE_MESSAGE)

    def start(self) -> None:
        raise PipelineNotAvailableError(_UNAVAILABLE_MESSAGE)

    def stop(self) -> None:
        raise PipelineNotAvailableError(_UNAVAILABLE_MESSAGE)

    def release(self) -> None:
        raise PipelineNotAvailableError(_UNAVAILABLE_MESSAGE)

    def process_frame(self, *args: Any, **kwargs: Any) -> Any:
        raise PipelineNotAvailableError(_UNAVAILABLE_MESSAGE)

    def pop_sliding_emit(self) -> Optional[Tuple[str, float, int]]:
        raise PipelineNotAvailableError(_UNAVAILABLE_MESSAGE)

    def classify(self, segment: Any) -> ClassificationResult:
        raise PipelineNotAvailableError(_UNAVAILABLE_MESSAGE)

    def maybe_refine_async(self, *args: Any, **kwargs: Any) -> Any:
        raise PipelineNotAvailableError(_UNAVAILABLE_MESSAGE)

    def build_keypoints_payload(self, feat: Any) -> Dict[str, Any]:
        raise PipelineNotAvailableError(_UNAVAILABLE_MESSAGE)

    @property
    def measured_fps(self) -> float:
        raise PipelineNotAvailableError(_UNAVAILABLE_MESSAGE)


__all__ = [
    "OursPipelineAdapter",
    "ClassificationResult",
    "PipelineNotAvailableError",
]
