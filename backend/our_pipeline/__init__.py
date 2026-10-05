"""backend.our_pipeline — ASL recognition pipeline (not published).

PUBLIC-REPOSITORY NOTE
----------------------
The real implementation of this package (~13,800 lines: MediaPipe keypoint
extraction, sign-boundary segmentation, sliding-window inference, the CTR-GCN
classifier wrapper, and a vendored ML training framework) was authored by
Stepan Karapetyan and is not mine to publish.

This package exists so that the services in this repository remain importable
and the test suite remains collectable. See the "What is and isn't in this
repository" section of the README.
"""

__all__ = ["adapter"]
