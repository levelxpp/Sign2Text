# Machine Learning Pipeline

This document provides a technical overview of the Machine Learning lifecycle for the Sign2Text project, from raw data extraction to live inference.

---

## 🏗️ Pipeline Overview

The Sign2Text ML pipeline follows a structured path that transforms raw video frames into natural language English.

1.  **Extraction**: Raw video/camera frames are processed to extract skeletal keypoints.
2.  **Preprocessing**: Keypoints are normalized, augmented with motion derivatives, and smoothed.
3.  **Classification**: A specialized neural network (CTR-GCN + Transformer) classifies sign segments into glosses.
4.  **Translation**: Recognized glosses are buffered and translated into natural English using a Large Language Model (Ollama).

---

## 📹 Extraction Stage

The system relies on high-fidelity skeletal tracking rather than raw RGB pixel analysis. This ensures privacy and reduces the computational load for the main classifier.

-   **Tooling**: The extraction layer uses **rtmlib** (WholeBody model).
-   **Skeleton**: It tracks **133 keypoints** (COCO-WholeBody format), including:
    -   Body (17 points)
    -   Hands (2 x 21 points)
    -   Face (68 points)
    -   Feet (6 points)
-   **Output**: Extraction produces spatial coordinates $(x, y)$ and confidence scores $(c)$ for every joint in every frame.

---

## 🧠 Model Architecture

The core recognition model is a hybrid architecture designed to handle the complex spatial and temporal patterns of American Sign Language.

### 1. Spatial Layer: CTR-GCN
The model uses **Channel-wise Topology Refinement Graph Convolutional Networks (CTR-GCN)**.
-   **Physical Topology**: The model starts with a graph representing the physical human skeleton.
-   **Refinement**: It learns to "refine" this topology per feature channel, discovering which joint relationships are most important for specific signs.
-   **Decomposition**: Adjacency is broken into three subsets (Identity, Centripetal, Centrifugal) to capture directional motion flow.

### 2. Temporal Layer: Transformer
After spatial features are enriched by the GCN, they are passed to a **Transformer Encoder**.
-   **Attention**: Captures long-range temporal dependencies within a sign (e.g., the relationship between the start and end of a movement).
-   **CLS Pooling**: Uses a specialized "classification token" to aggregate the entire sequence into a final representation.

---

## 👟 Training & Augmentation

The models are trained using extensive augmentations to bridge the "domain gap" between studio-quality datasets and real-world webcam usage.

-   **Spatial Augmentations**: Jitter, scaling, and rotation to handle different camera angles and distances.
-   **Temporal Augmentations**: Speed jitter ($0.7\times$ to $1.3\times$) and random frame dropping.
-   **Domain-Specific**:
    -   **Lower Body Masking**: Forces the model to ignore feet/legs, which are often not visible in real usage.
    -   **Idle Padding**: Simulates hands starting from and returning to a rest position.

---

## ⚡ Runtime Inference

The live inference engine (`new_Inference.py`) executes the pipeline in real-time with zero-interruption gloss detection.

1.  **Pose Stream**: Frames from the camera are processed by `rtmlib` sequentially.
2.  **Feature Extraction**:
    -   **Normalization**: Coordinates are centered at the shoulders and scaled.
    -   **EMA Smoothing**: Reduces jitter from the pose estimator.
    -   **Derivatives**: Velocity ($v$) and Acceleration ($a$) are calculated per joint.
3.  **SignSpotting**: The system detects when a sign starts and ends based on hand movement and confidence thresholds.
4.  **Continuous Recognition**: Validated sign segments are classified immediately. Confidence-aware thresholds auto-accept or reject predictions.
5.  **Gloss Buffering**: Recognized glosses (e.g., `HELLO`, `ABOUT`, `YOU`) are collected.
6.  **Sentence Boundary**: When a "rest state" is detected for ~1.5s, the gloss buffer is flushed to Ollama for final English translation.

---

> [!NOTE]
> The performance and vocabulary of the pipeline are highly dependent on the currently loaded checkpoint and its associated configuration. See **[Supported Signs](SUPPORTED_SIGNS.md)** for details on the current recognition scope.
