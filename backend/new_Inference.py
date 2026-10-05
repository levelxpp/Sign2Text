#!/usr/bin/env python3
"""
new_Inference.py — ASL Real-Time Translation (Website Edition) v2

Continuous sign detection → classification → gloss accumulation → Ollama translation.
No review mode, no data collection, no frame buffering — pure inference pipeline.

FLOW:
  Camera frame → RTMW pose → feature extraction → SignSpotter detects sign boundary
  → classify segment → auto-accept if confidence >= threshold, else auto-reject
  → sentence boundary detected (hands at rest ~1.5s) → flush gloss to Ollama
  → Ollama returns English → display via SSE to frontend
  → immediately continue detecting next sign (zero interruption)

FEATURES:
  - Real-time ASL sign detection and classification
  - CTR-GCN + Transformer model support (v9 checkpoints) — 91.25% accuracy
  - Backward compatible: loads v7 (no GCN), v8 (GCN), and v9 (CTR-GCN) checkpoints
  - Gloss buffer with consecutive deduplication
  - Sentence boundary detection (hands-at-rest timeout)
  - Ollama gloss-to-English translation (async, non-blocking)
  - Person tracking for multi-person scenes
  - Confidence-aware feature extraction with EMA smoothing
  - Skeleton clears when hands leave frame (no floating lines)
  - Responsive sign detection (lower thresholds, faster cooldown)
"""

import argparse
import json
import math
import os
import re
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional, Tuple

import cv2
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from rtmlib import Wholebody
try:
    from backend.hardware import detect_runtime_profile
    from backend.runtime_config import load_runtime_config
except ImportError:
    from hardware import detect_runtime_profile
    from runtime_config import load_runtime_config

# Optional: requests for Ollama API calls
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    print("[WARN] 'requests' not installed — Ollama translation disabled.")
    print("       Install with: pip install requests")


# ═══════════════════════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

RUNTIME_CONFIG = load_runtime_config()
DEFAULT_CHECKPOINT = RUNTIME_CONFIG.asl_checkpoint_path
DEFAULT_OLLAMA_URL = RUNTIME_CONFIG.ollama_url
DEFAULT_OLLAMA_MODEL = RUNTIME_CONFIG.ollama_model
DEFAULT_OLLAMA_TIMEOUT = RUNTIME_CONFIG.ollama_timeout_sec
DEFAULT_OLLAMA_AUTH_TOKEN = RUNTIME_CONFIG.ollama_auth_token

# ── ASL normalisation config + vocabulary ────────────────────────────────────
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
_NORM_CONFIG_PATH = os.path.join(_BACKEND_DIR, "normalization_config.json")
_VALID_WORDS_PATH = os.path.join(os.path.dirname(_BACKEND_DIR), "valid_words.txt")


def _load_norm_config() -> dict:
    try:
        with open(_NORM_CONFIG_PATH, "r", encoding="utf-8") as _f:
            return json.load(_f)
    except Exception as _e:
        print(f"[NORM] Could not load normalization_config.json: {_e}", flush=True)
        return {}


def _load_valid_words() -> frozenset:
    try:
        with open(_VALID_WORDS_PATH, "r", encoding="utf-8") as _f:
            words = frozenset(line.strip().upper() for line in _f if line.strip())
        print(f"[NORM] Loaded {len(words)} valid words from valid_words.txt", flush=True)
        return words
    except Exception as _e:
        print(f"[NORM] Could not load valid_words.txt: {_e}", flush=True)
        return frozenset()


NORM_CONFIG: dict = _load_norm_config()
VALID_WORDS: frozenset = _load_valid_words()


# ═══════════════════════════════════════════════════════════════════════════════
#  CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

NUM_KPTS = 133
FEAT_DIM = 7
FLAT_DIM = NUM_KPTS * FEAT_DIM
EMA_ALPHA = 0.25
CONF_THRESH = 0.2
WRIST_CONF_THRESH = 0.4   # Higher confidence for wrist-based signing decisions
HAND_CONF_THRESH = 0.3    # Per-joint confidence bar for hand keypoints (moderate — the
                          # main validity check is now spatial spread, not confidence).

# When RTMPose loses sight of the hands, the 21 hand joints scatter across the frame
# at low confidences. A real hand forms a tight cluster (typically < 25% of frame
# width/height). If the bounding box of confident hand joints exceeds the limits
# below, the detection is treated as noise and ignored.
HAND_MAX_SPREAD_X = 0.35
HAND_MAX_SPREAD_Y = 0.35
CENTER_WEIGHT = 0.35

HAND_L = list(range(91, 112))
HAND_R = list(range(112, 133))
WRIST_L, WRIST_R = 9, 10
SHOULDER_L, SHOULDER_R = 5, 6
HIP_L, HIP_R = 11, 12
LOWER_BODY = [13, 14, 15, 16, 17, 18, 19, 20, 21, 22]

HAND_BONES = [
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (0, 9), (9, 10), (10, 11), (11, 12),
    (0, 13), (13, 14), (14, 15), (15, 16),
    (0, 17), (17, 18), (18, 19), (19, 20),
    (5, 9), (9, 13), (13, 17),
]

# Sentence boundary: frames of rest required after active signing (~1.5-2s at 30fps)
SENTENCE_REST_FRAMES = 50   # ~1.7s at 30fps
SENTENCE_MIN_SIGNS = 1      # minimum signs in buffer before triggering translation


# ═══════════════════════════════════════════════════════════════════════════════
#  SKELETON ADJACENCY — Shared helpers for GCN and CTR-GCN
# ═══════════════════════════════════════════════════════════════════════════════

def _get_skeleton_edges_133() -> list:
    """Return the raw edge list for 133 COCO-WholeBody keypoints."""
    edges = []

    # Body skeleton (0-16)
    edges.extend([
        (0, 1), (0, 2), (1, 3), (2, 4),
        (0, 5), (0, 6), (5, 6),
        (5, 7), (7, 9), (6, 8), (8, 10),
        (5, 11), (6, 12), (11, 12),
        (11, 13), (13, 15), (12, 14), (14, 16),
    ])

    # Feet (17-22)
    edges.extend([
        (15, 17), (15, 18), (15, 19),
        (16, 20), (16, 21), (16, 22),
    ])

    # Face (23-90)
    for i in range(23, 38): edges.append((i, i + 1))
    for i in range(39, 43): edges.append((i, i + 1))
    for i in range(44, 48): edges.append((i, i + 1))
    for i in range(49, 53): edges.append((i, i + 1))
    for i in range(53, 57): edges.append((i, i + 1))
    for i in range(59, 64): edges.append((i, i + 1))
    edges.append((64, 59))
    for i in range(65, 70): edges.append((i, i + 1))
    edges.append((70, 65))
    for i in range(71, 82): edges.append((i, i + 1))
    edges.append((82, 71))
    for i in range(83, 90): edges.append((i, i + 1))
    edges.append((90, 83))
    edges.append((0, 49))

    # Left hand (91-111)
    lh = 91
    hand_bones_topo = [
        (0, 1), (1, 2), (2, 3), (3, 4),
        (0, 5), (5, 6), (6, 7), (7, 8),
        (0, 9), (9, 10), (10, 11), (11, 12),
        (0, 13), (13, 14), (14, 15), (15, 16),
        (0, 17), (17, 18), (18, 19), (19, 20),
        (5, 9), (9, 13), (13, 17),
    ]
    for a, b in hand_bones_topo:
        edges.append((lh + a, lh + b))
    edges.append((9, 91))

    # Right hand (112-132)
    rh = 112
    for a, b in hand_bones_topo:
        edges.append((rh + a, rh + b))
    edges.append((10, 112))

    return edges


def build_adjacency_133() -> np.ndarray:
    """Build normalized physical adjacency for 133 COCO-WholeBody keypoints."""
    edges = _get_skeleton_edges_133()
    A = np.zeros((NUM_KPTS, NUM_KPTS), dtype=np.float32)
    for i, j in edges:
        if 0 <= i < NUM_KPTS and 0 <= j < NUM_KPTS:
            A[i, j] = 1.0
            A[j, i] = 1.0
    np.fill_diagonal(A, 1.0)

    D = A.sum(axis=1)
    D_inv_sqrt = np.where(D > 0, 1.0 / np.sqrt(D), 0.0)
    D_mat = np.diag(D_inv_sqrt)
    return (D_mat @ A @ D_mat).astype(np.float32)


def build_adjacency_subsets_133(num_subsets: int = 3) -> np.ndarray:
    """Build multi-subset adjacency for CTR-GCN.

    Subset 0: Identity (self-connections)
    Subset 1: Centripetal (toward root)
    Subset 2: Centrifugal (away from root)
    """
    from collections import deque as _deque

    if num_subsets == 1:
        return build_adjacency_133()[np.newaxis, ...]

    edges = _get_skeleton_edges_133()

    adj_list = {i: set() for i in range(NUM_KPTS)}
    for i, j in edges:
        if 0 <= i < NUM_KPTS and 0 <= j < NUM_KPTS:
            adj_list[i].add(j)
            adj_list[j].add(i)

    # BFS from root=0
    root = 0
    depth = [-1] * NUM_KPTS
    depth[root] = 0
    queue = _deque([root])
    while queue:
        node = queue.popleft()
        for neighbor in adj_list[node]:
            if depth[neighbor] == -1:
                depth[neighbor] = depth[node] + 1
                queue.append(neighbor)

    for i in range(NUM_KPTS):
        if depth[i] == -1:
            depth[i] = 0

    A_id = np.eye(NUM_KPTS, dtype=np.float32)
    A_centripetal = np.zeros((NUM_KPTS, NUM_KPTS), dtype=np.float32)
    A_centrifugal = np.zeros((NUM_KPTS, NUM_KPTS), dtype=np.float32)

    for i, j in edges:
        if not (0 <= i < NUM_KPTS and 0 <= j < NUM_KPTS):
            continue
        di, dj = depth[i], depth[j]
        if di > dj:
            A_centripetal[i, j] = 1.0
            A_centrifugal[j, i] = 1.0
        elif dj > di:
            A_centripetal[j, i] = 1.0
            A_centrifugal[i, j] = 1.0
        else:
            A_centripetal[i, j] = 1.0
            A_centripetal[j, i] = 1.0

    subsets = [A_id, A_centripetal, A_centrifugal]
    normed = []
    for A in subsets:
        D = A.sum(axis=1)
        D_inv_sqrt = np.where(D > 0, 1.0 / np.sqrt(D), 0.0)
        D_mat = np.diag(D_inv_sqrt)
        normed.append((D_mat @ A @ D_mat).astype(np.float32))

    return np.stack(normed, axis=0)


# ═══════════════════════════════════════════════════════════════════════════════
#  MODEL — Supports v7 (no GCN), v8 (GCN), and v9 (CTR-GCN) checkpoints
# ═══════════════════════════════════════════════════════════════════════════════

class PositionalEncoding(nn.Module):
    def __init__(self, d_model, max_len=5000):
        super().__init__()
        pe = torch.zeros(max_len, d_model)
        pos = torch.arange(0, max_len, dtype=torch.float32).unsqueeze(1)
        div = torch.exp(
            torch.arange(0, d_model, 2, dtype=torch.float32)
            * (-math.log(10000.0) / d_model)
        )
        pe[:, 0::2] = torch.sin(pos * div)
        pe[:, 1::2] = torch.cos(pos * div)
        self.register_buffer("pe", pe.unsqueeze(0))

    def forward(self, x):
        T = x.size(1)
        if T > self.pe.size(1):
            x = x[:, :self.pe.size(1)]
            T = self.pe.size(1)
        return x + self.pe[:, :T]


# ── Legacy GCN (v8 checkpoints) ──

class GraphConvolution(nn.Module):
    """Single graph convolution layer: H' = A * H * W + bias."""
    def __init__(self, in_features: int, out_features: int):
        super().__init__()
        self.weight = nn.Linear(in_features, out_features, bias=True)

    def forward(self, x: torch.Tensor, A: torch.Tensor) -> torch.Tensor:
        support = torch.matmul(A, x)
        return self.weight(support)


class SpatialGCN(nn.Module):
    """Multi-layer spatial GCN with physical + learnable adjacency (legacy v8)."""

    def __init__(self, in_channels: int = 7, hidden_channels: int = 64,
                 out_channels: int = 7, num_layers: int = 2,
                 num_joints: int = 133, dropout: float = 0.1):
        super().__init__()
        self.num_joints = num_joints
        self.num_layers = num_layers

        A_phys = build_adjacency_133()
        self.register_buffer("A_phys", torch.from_numpy(A_phys))
        self.A_learn = nn.Parameter(torch.randn(num_joints, num_joints) * 0.01)

        self.layers = nn.ModuleList()
        self.bns = nn.ModuleList()

        self.layers.append(GraphConvolution(in_channels, hidden_channels))
        self.bns.append(nn.BatchNorm1d(num_joints))

        for _ in range(num_layers - 2):
            self.layers.append(GraphConvolution(hidden_channels, hidden_channels))
            self.bns.append(nn.BatchNorm1d(num_joints))

        if num_layers >= 2:
            self.layers.append(GraphConvolution(hidden_channels, out_channels))
            self.bns.append(nn.BatchNorm1d(num_joints))

        self.dropout = nn.Dropout(dropout)
        self.residual = (
            nn.Linear(in_channels, out_channels)
            if in_channels != out_channels
            else nn.Identity()
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        A = self.A_phys + torch.softmax(self.A_learn, dim=-1) * 0.1
        D = A.sum(dim=-1, keepdim=True).clamp(min=1e-6)
        A_norm = A / D

        residual = self.residual(x)
        h = x
        for i, (gcn, bn) in enumerate(zip(self.layers, self.bns)):
            h = gcn(h, A_norm)
            h = bn(h)
            if i < len(self.layers) - 1:
                h = F.relu(h)
                h = self.dropout(h)
        return F.relu(h + residual)


# ── CTR-GCN (v9 checkpoints) ──

class CTRGC(nn.Module):
    """Channel-wise Topology Refinement Graph Convolution (ICCV 2021).

    For each output channel c, learns a unique adjacency refinement:
        A_c = A_shared + alpha * Q_c(x)
    Alpha initialized to 0 → starts from physical topology.
    """

    def __init__(self, in_channels: int, out_channels: int, rel_reduction: int = 8):
        super().__init__()
        self.in_channels = in_channels
        self.out_channels = out_channels
        self.rel_channels = max(1, in_channels // rel_reduction)

        self.fc_q = nn.Linear(in_channels, self.rel_channels, bias=False)
        self.fc_k = nn.Linear(in_channels, self.rel_channels, bias=False)
        self.fc_v = nn.Linear(in_channels, out_channels, bias=True)
        self.fc_rel = nn.Linear(self.rel_channels, out_channels, bias=False)
        self.alpha = nn.Parameter(torch.zeros(1))

        nn.init.normal_(self.fc_q.weight, std=0.01)
        nn.init.normal_(self.fc_k.weight, std=0.01)
        nn.init.normal_(self.fc_rel.weight, std=0.01)

    def forward(self, x: torch.Tensor, A: torch.Tensor,
                B: int = None, T: int = None,
                mask: torch.Tensor = None) -> torch.Tensor:
        N, K, C = x.shape

        if B is not None and T is not None and T > 1:
            x_bt = x.view(B, T, K, C)
            if mask is not None:
                m = mask.unsqueeze(-1).unsqueeze(-1).float()
                x_avg = (x_bt * m).sum(dim=1) / m.sum(dim=1).clamp(min=1.0)
            else:
                x_avg = x_bt.mean(dim=1)
        else:
            x_avg = x
            B = N
            T = 1

        q = torch.tanh(self.fc_q(x_avg))
        k = torch.tanh(self.fc_k(x_avg))
        corr = torch.tanh(q.unsqueeze(2) - k.unsqueeze(1))
        Q = self.fc_rel(corr)
        A_refined = A.unsqueeze(0).unsqueeze(-1) + self.alpha * Q
        v = self.fc_v(x)

        if T > 1:
            v_bt = v.view(B, T, K, self.out_channels)
            out = torch.einsum('bijc,btjc->btic', A_refined, v_bt)
            return out.reshape(N, K, self.out_channels)
        else:
            out = torch.einsum('bijc,bjc->bic', A_refined, v)
            return out


class CTRSpatialGCN(nn.Module):
    """Multi-layer CTR-GCN with multi-subset adjacency decomposition."""

    def __init__(self, in_channels: int = 7,
                 channels: List[int] = None,
                 rel_reduction: int = 8,
                 num_subsets: int = 3,
                 num_joints: int = 133,
                 dropout: float = 0.1):
        super().__init__()

        if channels is None:
            channels = [7, 64, 64, 7]

        self.num_joints = num_joints
        self.num_layers = len(channels) - 1
        self.channels = channels

        A_subsets = build_adjacency_subsets_133(num_subsets)
        self.register_buffer("A", torch.from_numpy(A_subsets))
        self.num_subsets = A_subsets.shape[0]

        self.layers = nn.ModuleList()
        self.bns = nn.ModuleList()
        self.residuals = nn.ModuleList()

        for i in range(self.num_layers):
            c_in = channels[i]
            c_out = channels[i + 1]

            subset_ctrgcs = nn.ModuleList([
                CTRGC(c_in, c_out, rel_reduction)
                for _ in range(self.num_subsets)
            ])
            self.layers.append(subset_ctrgcs)
            self.bns.append(nn.BatchNorm1d(num_joints))

            if c_in != c_out:
                self.residuals.append(nn.Sequential(
                    nn.Linear(c_in, c_out, bias=False),
                    nn.BatchNorm1d(num_joints),
                ))
            else:
                self.residuals.append(nn.Identity())

        self.dropout = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor,
                B: int = None, T: int = None,
                mask: torch.Tensor = None) -> torch.Tensor:
        for i, (ctrgc_list, bn, res) in enumerate(
            zip(self.layers, self.bns, self.residuals)
        ):
            residual = res(x)
            y = None
            for s, ctrgc in enumerate(ctrgc_list):
                ys = ctrgc(x, self.A[s], B=B, T=T, mask=mask)
                y = ys if y is None else y + ys

            y = bn(y)

            if i < self.num_layers - 1:
                y = F.relu(y + residual)
                y = self.dropout(y)
            else:
                y = F.relu(y + residual)

            x = y
        return x


# ── Main model: supports all checkpoint versions ──

class SignGCNTransformer(nn.Module):
    """
    GCN-Transformer Hybrid — supports v7 (no GCN), v8 (GCN), v9 (CTR-GCN):
      1. Reshape flat input → (B, T, 133, 7)
      2. Spatial GCN / CTR-GCN per frame
      3. Flatten → (B, T, 931)
      4. Conv1d temporal stem
      5. Transformer encoder with CLS pooling
      6. Classification head
    """

    def __init__(self, input_dim, num_classes, d_model=256, n_heads=8,
                 num_layers=4, dim_feedforward=768, dropout=0.30,
                 input_dropout=0.20, max_len=128, pooling="cls",
                 use_gcn=True, gcn_hidden=64, gcn_layers=2, gcn_dropout=0.1,
                 # CTR-GCN parameters (v9)
                 use_ctr_gcn=False,
                 ctr_gcn_channels=None,
                 ctr_rel_reduction=8,
                 ctr_num_subsets=3):
        super().__init__()
        self.pooling = pooling
        self.use_gcn = use_gcn
        self.use_ctr_gcn = use_ctr_gcn and use_gcn
        self.num_kpts = NUM_KPTS
        self.feat_dim = FEAT_DIM

        if use_gcn:
            if self.use_ctr_gcn:
                if ctr_gcn_channels is None:
                    ctr_gcn_channels = [FEAT_DIM, gcn_hidden, gcn_hidden, FEAT_DIM]
                self.gcn = CTRSpatialGCN(
                    in_channels=FEAT_DIM,
                    channels=ctr_gcn_channels,
                    rel_reduction=ctr_rel_reduction,
                    num_subsets=ctr_num_subsets,
                    num_joints=NUM_KPTS,
                    dropout=gcn_dropout,
                )
            else:
                self.gcn = SpatialGCN(
                    in_channels=FEAT_DIM,
                    hidden_channels=gcn_hidden,
                    out_channels=FEAT_DIM,
                    num_layers=gcn_layers,
                    num_joints=NUM_KPTS,
                    dropout=gcn_dropout,
                )

        self.stem = nn.Sequential(
            nn.Conv1d(input_dim, d_model, kernel_size=5, padding=2),
            nn.BatchNorm1d(d_model), nn.ReLU(), nn.Dropout(input_dropout),
            nn.Conv1d(d_model, d_model, kernel_size=3, padding=1),
            nn.BatchNorm1d(d_model), nn.ReLU(), nn.Dropout(input_dropout),
        )
        self.pos = PositionalEncoding(d_model, max_len + 1)
        self.cls = (
            nn.Parameter(torch.zeros(1, 1, d_model)) if pooling == "cls" else None
        )

        enc = nn.TransformerEncoderLayer(
            d_model=d_model, nhead=n_heads, dim_feedforward=dim_feedforward,
            dropout=dropout, batch_first=True, activation="gelu", norm_first=True,
        )
        self.encoder = nn.TransformerEncoder(enc, num_layers=num_layers)
        self.drop = nn.Dropout(dropout)
        self.head = nn.Linear(d_model, num_classes)

        if self.cls is not None:
            nn.init.trunc_normal_(self.cls, std=0.02)

    def forward(self, x, mask):
        B, T, D = x.shape

        if self.use_gcn and D == self.num_kpts * self.feat_dim:
            x_3d = x.view(B, T, self.num_kpts, self.feat_dim)
            x_flat = x_3d.reshape(B * T, self.num_kpts, self.feat_dim)

            if self.use_ctr_gcn:
                x_gcn = self.gcn(x_flat, B=B, T=T, mask=mask)
            else:
                x_gcn = self.gcn(x_flat)

            x = x_gcn.reshape(B, T, D)

        # Temporal stem
        x = self.stem(x.permute(0, 2, 1)).permute(0, 2, 1)

        # CLS token
        if self.pooling == "cls":
            x = torch.cat([self.cls.expand(B, 1, -1), x], dim=1)
            mask = torch.cat(
                [torch.ones(B, 1, device=mask.device, dtype=torch.bool), mask],
                dim=1,
            )

        x = self.pos(x)
        h = self.encoder(x, src_key_padding_mask=~mask)

        if self.pooling == "cls":
            pooled = h[:, 0]
        else:
            m = mask.unsqueeze(-1).float()
            pooled = (h * m).sum(1) / m.sum(1).clamp(1)

        return self.head(self.drop(pooled))


# Backward compat alias
SignTransformer = SignGCNTransformer


# ═══════════════════════════════════════════════════════════════════════════════
#  PERSON TRACKER
# ═══════════════════════════════════════════════════════════════════════════════

class PersonTracker:
    """Maintains consistent person selection across frames."""

    def __init__(self):
        self.have_prev = False
        self.prev_center = np.array([0.5, 0.5], dtype=np.float32)

    def reset(self):
        self.have_prev = False
        self.prev_center[:] = 0.5

    def select(self, keypoints, scores):
        if keypoints is None:
            return None, None
        k = np.asarray(keypoints)
        if k.size == 0:
            return None, None

        if k.ndim == 2:
            s = np.asarray(scores) if scores is not None else None
            if s is not None and s.ndim == 1 and s.shape[0] == k.shape[0]:
                return k.astype(np.float32), s.astype(np.float32)
            return k.astype(np.float32), None

        if k.ndim == 3:
            s = np.asarray(scores) if scores is not None else None
            P = k.shape[0]
            if s is None or s.ndim != 2 or s.shape[0] != P:
                return k[0].astype(np.float32), None
            ms = s.mean(axis=1)
            centers = []
            for p in range(P):
                xy = k[p, :, 0:2].astype(np.float32)
                g = s[p] >= CONF_THRESH
                centers.append(xy[g].mean(0) if g.any() else xy.mean(0))
            centers = np.stack(centers).astype(np.float32)
            d = (
                np.linalg.norm(centers - self.prev_center[None], axis=1)
                if self.have_prev
                else np.zeros(P)
            )
            best = int(np.argmin(-ms + CENTER_WEIGHT * d))
            self.prev_center = centers[best]
            self.have_prev = True
            return k[best].astype(np.float32), s[best].astype(np.float32)

        return None, None


# ═══════════════════════════════════════════════════════════════════════════════
#  FEATURE EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════════

class FrameFeatureExtractor:
    """Per-frame keypoint feature extraction with EMA smoothing and derivatives."""

    def __init__(self):
        self.have_prev = False
        self.prev_xy = np.zeros((NUM_KPTS, 2), dtype=np.float32)
        self.prev_v = np.zeros((NUM_KPTS, 2), dtype=np.float32)
        self.prev_vis = np.zeros(NUM_KPTS, dtype=bool)
        self.sm_xy = np.zeros((NUM_KPTS, 2), dtype=np.float32)

    def reset(self):
        self.have_prev = False
        self.prev_xy[:] = 0
        self.prev_v[:] = 0
        self.prev_vis[:] = False
        self.sm_xy[:] = 0

    def process(self, p_kpts, p_scores, fw, fh):
        feat = np.zeros((NUM_KPTS, FEAT_DIM), dtype=np.float32)
        if p_kpts is not None:
            kp = np.asarray(p_kpts, dtype=np.float32)
            sc = (
                np.asarray(p_scores, dtype=np.float32)
                if p_scores is not None
                else np.zeros(kp.shape[0], dtype=np.float32)
            )
            xy = kp[:, 0:2].copy()
            if fw > 0 and fh > 0:
                xy[:, 0] /= float(fw)
                xy[:, 1] /= float(fh)
            K = min(NUM_KPTS, xy.shape[0])
            feat[:K, 0:2] = xy[:K]
            feat[:K, 2] = sc[:K]

        feat[LOWER_BODY, :] = 0.0
        xy = feat[:, 0:2]
        sc = feat[:, 2]
        vis = sc >= CONF_THRESH

        # EMA smoothing
        if EMA_ALPHA > 0:
            if not self.have_prev:
                self.sm_xy = xy.copy()
            else:
                self.sm_xy[vis] = (
                    (1.0 - EMA_ALPHA) * self.sm_xy[vis] + EMA_ALPHA * xy[vis]
                )
            xy_use = self.sm_xy
        else:
            xy_use = xy

        # Confidence-aware derivatives
        v = np.zeros((NUM_KPTS, 2), dtype=np.float32)
        a = np.zeros((NUM_KPTS, 2), dtype=np.float32)
        if self.have_prev:
            ok = vis & self.prev_vis
            if ok.any():
                v[ok] = xy_use[ok] - self.prev_xy[ok]
                a[ok] = v[ok] - self.prev_v[ok]

        feat[:, 3:5] = v
        feat[:, 5:7] = a
        self.prev_xy = xy_use.copy()
        self.prev_v = v.copy()
        self.prev_vis = vis.copy()
        self.have_prev = True
        return feat


# ═══════════════════════════════════════════════════════════════════════════════
#  PREPROCESSING + CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════════════════

def preprocess_segment(seg, max_frames, cap=384):
    """Normalize a keypoint segment for model input."""
    arr = seg.copy().astype(np.float32)
    T = arr.shape[0]
    mx = max(max_frames, cap)
    if T > mx:
        arr = arr[np.linspace(0, T - 1, mx).astype(np.int64)]

    arr[:, LOWER_BODY, :] = 0.0

    if arr.shape[1] == 133:
        c = (arr[:, SHOULDER_L, :2] + arr[:, SHOULDER_R, :2]) / 2.0
        arr[..., 0] -= c[:, 0:1]
        arr[..., 1] -= c[:, 1:2]

    x2 = arr.reshape(arr.shape[0], -1).astype(np.float32)
    mu = x2.mean(0, keepdims=True)
    sd = x2.std(0, keepdims=True) + 1e-6
    return (x2 - mu) / sd


def classify_segment(model, seg, mf, dev, nc, cap=384, clips=3, debug=False):
    """Classify a keypoint segment using multi-clip averaging."""
    T0 = seg.shape[0]
    x2 = preprocess_segment(seg, mf, cap)
    T, D = x2.shape

    if debug:
        print(f"    [DEBUG] {T0}->{T}f, dim={D}")

    if T <= mf:
        xt = torch.from_numpy(x2).unsqueeze(0).to(dev)
        mt = torch.ones(1, T, dtype=torch.bool, device=dev)
        with torch.no_grad(), torch.amp.autocast(
            device_type=dev.type, enabled=dev.type == "cuda"
        ):
            return torch.softmax(model(xt, mt), -1)[0]

    ms = T - mf
    starts = (
        np.linspace(0, ms, clips).astype(int).tolist()
        if clips > 2
        else ([ms // 2] if clips == 1 else [0, ms])
    )
    cx, cm = [], []
    for s in starts:
        c = x2[s : s + mf]
        cl = c.shape[0]
        if cl < mf:
            c = np.concatenate([c, np.zeros((mf - cl, D), np.float32)])
            m = np.zeros(mf, bool)
            m[:cl] = True
        else:
            m = np.ones(mf, bool)
        cx.append(c)
        cm.append(m)

    xb = torch.from_numpy(np.stack(cx)).to(dev)
    mb = torch.from_numpy(np.stack(cm)).to(dev)
    with torch.no_grad(), torch.amp.autocast(
        device_type=dev.type, enabled=dev.type == "cuda"
    ):
        return torch.softmax(model(xb, mb), -1).mean(0)


# ═══════════════════════════════════════════════════════════════════════════════
#  SIGN SPOTTER — posture-gated auto-detection + manual trigger
#  (Website edition: no raw frame buffering, keypoints only)
# ═══════════════════════════════════════════════════════════════════════════════

class SignSpotter:
    """
    Detects sign boundaries using wrist motion + posture gating.
    Website edition: does NOT buffer raw BGR frames (saves memory).
    """

    IDLE = "idle"
    SIGNING = "signing"
    COOLDOWN = "cooldown"
    MANUAL = "manual"

    def __init__(self, start_threshold=0.005, stop_threshold=0.003,
                 stop_patience=3, min_sign_frames=6, max_sign_frames=70,
                 pre_frames=4, cooldown_frames=6, disp_window=5, min_hand_kpts=8):
        self.start_threshold = float(start_threshold)
        self.stop_threshold = float(stop_threshold)
        self.stop_patience = int(stop_patience)
        self.min_sign_frames = int(min_sign_frames)
        self.max_sign_frames = int(max_sign_frames)
        self.pre_frames = int(pre_frames)
        self.cooldown_frames = int(cooldown_frames)
        self.disp_window = int(disp_window)
        self.min_hand_kpts = int(min_hand_kpts)

        self.state = self.IDLE
        self._feat_buf: List[np.ndarray] = []
        self._low_count = 0
        self._cd_count = 0
        self._recent_feat: deque = deque(maxlen=pre_frames + 1)
        self._prev_wrist = None
        self._disp_hist: deque = deque(maxlen=disp_window)

        # Debug state (readable by external code)
        self.dbg_motion = 0.0
        self.dbg_hands_raised = False
        self.dbg_hands_visible = False

    def reset(self):
        self.state = self.IDLE
        self._feat_buf.clear()
        self._low_count = 0
        self._cd_count = 0
        self._recent_feat.clear()
        self._prev_wrist = None
        self._disp_hist.clear()

    def _wrist_xy(self, feat):
        return np.array(
            [feat[WRIST_L, 0], feat[WRIST_L, 1],
             feat[WRIST_R, 0], feat[WRIST_R, 1]],
            np.float32,
        )

    def _motion(self):
        return (
            float(np.mean(list(self._disp_hist)))
            if len(self._disp_hist) >= 2
            else 0.0
        )

    def _hands_raised(self, feat):
        sh_y = (feat[SHOULDER_L, 1] + feat[SHOULDER_R, 1]) / 2.0
        hp_y = (feat[HIP_L, 1] + feat[HIP_R, 1]) / 2.0
        mid = (
            (sh_y + hp_y) / 2.0
            if (feat[HIP_L, 2] >= CONF_THRESH or feat[HIP_R, 2] >= CONF_THRESH)
            else sh_y + 0.15
        )
        return (feat[WRIST_L, 2] >= WRIST_CONF_THRESH and feat[WRIST_L, 1] < mid) or (
            feat[WRIST_R, 2] >= WRIST_CONF_THRESH and feat[WRIST_R, 1] < mid
        )

    def _hand_is_real(self, feat, hand_idx):
        """A hand is 'real' when enough joints are confident AND they cluster
        tightly. Phantom detections (when the real hand isn't in frame) scatter
        the 21 joints all over the image — that spread is the strongest signal
        the hand isn't actually there."""
        xs = []
        ys = []
        for i in hand_idx:
            if feat[i, 2] >= HAND_CONF_THRESH:
                xs.append(float(feat[i, 0]))
                ys.append(float(feat[i, 1]))
        if len(xs) < self.min_hand_kpts:
            return False
        spread_x = max(xs) - min(xs)
        spread_y = max(ys) - min(ys)
        if spread_x > HAND_MAX_SPREAD_X or spread_y > HAND_MAX_SPREAD_Y:
            return False
        return True

    def _hands_visible(self, feat):
        return self._hand_is_real(feat, HAND_L) or self._hand_is_real(feat, HAND_R)

    def _posture_ok(self, feat):
        r = self._hands_raised(feat)
        v = self._hands_visible(feat)
        self.dbg_hands_raised = r
        self.dbg_hands_visible = v
        return r and v

    def manual_toggle(self):
        """Toggle manual recording. Returns segment or None."""
        if self.state == self.MANUAL:
            return self._finalize()
        self._feat_buf = list(self._recent_feat)
        self.state = self.MANUAL
        return None

    def update(self, feat):
        """Feed one frame of keypoint features. Returns segment (np.ndarray) or None."""
        w = self._wrist_xy(feat)
        self._recent_feat.append(feat.copy())

        if self._prev_wrist is not None:
            # Only count motion if at least one wrist is confidently detected
            lw_conf = feat[WRIST_L, 2]
            rw_conf = feat[WRIST_R, 2]
            if lw_conf >= WRIST_CONF_THRESH or rw_conf >= WRIST_CONF_THRESH:
                d = np.sqrt(
                    ((w - self._prev_wrist) ** 2).reshape(2, 2).sum(axis=1)
                ).mean()
                self._disp_hist.append(float(d))
            else:
                # Wrists not confidently detected — inject zero motion
                self._disp_hist.append(0.0)
        self._prev_wrist = w.copy()

        motion = self._motion()
        self.dbg_motion = motion

        # Always update posture flags (hands_raised, hands_visible)
        # so sentence boundary detector has fresh data in ALL states
        # (not just IDLE/SIGNING where _posture_ok was previously called)
        self._posture_ok(feat)

        if self.state == self.MANUAL:
            self._feat_buf.append(feat.copy())
            if len(self._feat_buf) >= 300:
                return self._finalize()
            return None

        if self.state == self.COOLDOWN:
            self._cd_count -= 1
            if self._cd_count <= 0:
                self.state = self.IDLE
            return None

        if self.state == self.IDLE:
            if motion >= self.start_threshold and self._posture_ok(feat):
                self._feat_buf = list(self._recent_feat)
                self._low_count = 0
                self.state = self.SIGNING
            return None

        if self.state == self.SIGNING:
            self._feat_buf.append(feat.copy())
            if len(self._feat_buf) >= self.max_sign_frames:
                return self._finalize()
            if motion < self.stop_threshold or not self._posture_ok(feat):
                self._low_count += 1
                if self._low_count >= self.stop_patience:
                    return self._finalize()
            else:
                self._low_count = 0
            return None

        return None

    def _finalize(self):
        """End current recording, return segment if long enough."""
        seg = np.stack(self._feat_buf, axis=0) if self._feat_buf else None
        self._feat_buf.clear()
        self._low_count = 0
        self._cd_count = self.cooldown_frames
        self.state = self.COOLDOWN
        if seg is not None and seg.shape[0] >= self.min_sign_frames:
            return seg
        return None

    @property
    def current_motion(self):
        return self._motion()

    @property
    def buffer_len(self):
        return len(self._feat_buf)


# ═══════════════════════════════════════════════════════════════════════════════
#  GLOSS BUFFER — accumulates recognized signs with deduplication
# ═══════════════════════════════════════════════════════════════════════════════

class GlossBuffer:
    """
    Accumulates recognized sign labels (gloss) over time.
    Deduplicates consecutive identical signs: [HELLO, HELLO] → [HELLO].
    """

    def __init__(self):
        self._buffer: List[str] = []
        self._all_sentences: List[str] = []
        self._last_add_time: float = 0.0
        self._active: bool = False

    def add(self, label: str, confidence: float) -> bool:
        label_upper = label.strip().upper()
        if not label_upper:
            return False
        if self._buffer and self._buffer[-1] == label_upper:
            return False
        self._buffer.append(label_upper)
        self._last_add_time = time.time()
        self._active = True
        return True

    @property
    def current_gloss(self) -> List[str]:
        return list(self._buffer)

    @property
    def current_gloss_str(self) -> str:
        return " ".join(self._buffer)

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def size(self) -> int:
        return len(self._buffer)

    def flush(self) -> List[str]:
        tokens = list(self._buffer)
        self._buffer.clear()
        self._active = False
        return tokens

    def add_translation(self, english: str):
        self._all_sentences.append(english)

    @property
    def translations(self) -> List[str]:
        return list(self._all_sentences)

    @property
    def last_translation(self) -> Optional[str]:
        return self._all_sentences[-1] if self._all_sentences else None

    def clear(self):
        self._buffer.clear()
        self._all_sentences.clear()
        self._active = False
        self._last_add_time = 0.0


# ═══════════════════════════════════════════════════════════════════════════════
#  SENTENCE BOUNDARY DETECTOR
# ═══════════════════════════════════════════════════════════════════════════════

class SentenceBoundaryDetector:
    """Fires a sentence boundary after hands rest for ~1.5s following active signing."""

    def __init__(self, rest_frames_threshold: int = SENTENCE_REST_FRAMES,
                 min_signs: int = SENTENCE_MIN_SIGNS,
                 motion_threshold: float = 0.004):
        self.rest_frames_threshold = int(rest_frames_threshold)
        self.min_signs = int(min_signs)
        self.motion_threshold = float(motion_threshold)
        self._rest_count: int = 0
        self._signing_detected: bool = False

    def reset(self):
        self._rest_count = 0
        self._signing_detected = False

    def notify_sign_added(self):
        self._signing_detected = True
        self._rest_count = 0

    def update(self, hands_raised: bool, motion: float, gloss_size: int) -> bool:
        if not self._signing_detected or gloss_size < self.min_signs:
            return False
        at_rest = (not hands_raised) or (motion < self.motion_threshold)
        if at_rest:
            self._rest_count += 1
        else:
            self._rest_count = 0
        if self._rest_count >= self.rest_frames_threshold:
            self._rest_count = 0
            self._signing_detected = False
            return True
        return False


# ═══════════════════════════════════════════════════════════════════════════════
#  ASL OUTPUT VALIDATOR — rejects hallucinated / over-expanded Ollama output
# ═══════════════════════════════════════════════════════════════════════════════

class AslOutputValidator:
    """Validates Ollama ASL normalisation output to prevent hallucinations.

    Checks (in order):
      1. Absolute word count (hard ceiling)
      2. Relative word count vs. input gloss length
      3. Sentence count (no run-on multi-sentence responses)
      4. Question preservation (HOW/WHAT/WHERE/WHO/WHY/WHEN → must end with ?)
      5. Invented content (output content words must trace back to input tokens)

    valid_words.txt is used ONLY as a diagnostic label in rejection log messages;
    it does NOT grant permission for a word to appear in the output.
    """

    _QUESTION_SIGNS: ClassVar[frozenset] = frozenset({"HOW", "WHAT", "WHERE", "WHO", "WHY", "WHEN", "WHATFOR"})

    # Expand English contractions before validation so "i'm" → ["i", "am"],
    # both of which are legitimate helper words and won't be flagged as invented.
    _CONTRACTIONS: ClassVar[Dict[str, List[str]]] = {
        "i'm": ["i", "am"], "i've": ["i", "have"], "i'll": ["i", "will"],
        "i'd": ["i", "would"], "you're": ["you", "are"], "you've": ["you", "have"],
        "you'll": ["you", "will"], "he's": ["he", "is"], "she's": ["she", "is"],
        "it's": ["it", "is"], "we're": ["we", "are"], "we've": ["we", "have"],
        "we'll": ["we", "will"], "they're": ["they", "are"], "they've": ["they", "have"],
        "they'll": ["they", "will"], "that's": ["that", "is"], "can't": ["can", "not"],
        "won't": ["will", "not"], "don't": ["do", "not"], "doesn't": ["does", "not"],
        "didn't": ["did", "not"], "isn't": ["is", "not"], "aren't": ["are", "not"],
        "wasn't": ["was", "not"], "weren't": ["were", "not"], "haven't": ["have", "not"],
        "hasn't": ["has", "not"], "hadn't": ["had", "not"],
    }

    def __init__(self, config: dict, valid_words: frozenset):
        self._valid_words = valid_words
        self._helper_words = frozenset(
            w.lower() for w in config.get("allowed_helper_words", [
                "is", "are", "am", "was", "were",
                "the", "a", "an",
                "do", "does", "did",
                "what", "where", "how", "who", "why", "when",
                "to", "of", "and", "in", "on", "at", "for", "not",
                "have", "has", "had",
                "i", "you", "he", "she", "it", "we", "they",
                "my", "your", "his", "her", "its", "our", "their",
                "me", "him", "us", "them", "that", "this", "with",
            ])
        )
        self._max_sentences = int(config.get("max_sentences", 1))
        self._max_extra_words = int(config.get("max_extra_words", 4))
        self._max_abs_words = int(config.get("max_absolute_output_words", 12))

    def validate(self, gloss_str: str, output_text: str) -> tuple:
        """Return (is_valid: bool, reason: str)."""
        if not output_text or not output_text.strip():
            return False, "empty output"

        gloss_tokens = [t.upper() for t in gloss_str.split() if t.strip()]
        output_words = output_text.strip().split()

        # 1. Absolute word count
        if len(output_words) > self._max_abs_words:
            return False, (
                f"too long: {len(output_words)} words (max {self._max_abs_words})"
            )

        # 2. Relative word count
        max_rel = len(gloss_tokens) + self._max_extra_words
        if len(output_words) > max_rel:
            return False, (
                f"output too expanded: {len(output_words)} words "
                f"for {len(gloss_tokens)} input tokens (max {max_rel})"
            )

        # 3. Sentence count — count sentence-ending punctuation marks
        punct_count = len(re.findall(r"[.!?]", output_text))
        # Treat no punctuation as one sentence (acceptable)
        if punct_count > self._max_sentences:
            return False, f"too many sentences: {punct_count}"

        # 4. Question preservation
        has_q = any(t in self._QUESTION_SIGNS for t in gloss_tokens)
        if has_q and not output_text.strip().endswith("?"):
            return False, "question sign in input but output is not a question"

        # 5. Invented content detection
        # Build normalised input lookup: "THANK_YOU" → {"thank", "you"}, "CAT" → {"cat"}
        input_lookup: set = set()
        for tok in gloss_tokens:
            for part in tok.lower().replace("_", " ").split():
                input_lookup.add(part)

        for raw_word in output_words:
            base = re.sub(r"[^a-z']+", "", raw_word.lower())
            # Expand contractions into component words ("i'm" → ["i", "am"])
            # so each component is checked individually against helper_words / input.
            for word in self._CONTRACTIONS.get(base, [base]):
                if not word or word in self._helper_words:
                    continue
                # Direct match against any input token part
                if word in input_lookup:
                    continue
                # Prefix match handles inflections (hungry ↔ HUNGRY, eating ↔ EAT)
                pfx = word[:5] if len(word) >= 5 else word
                if any(tok == pfx or tok.startswith(pfx) or pfx in tok
                       for tok in input_lookup if len(tok) >= 3):
                    continue
                # Rejected — build informative log label using valid_words.txt
                diag = ("supported-vocab" if word.upper() in self._valid_words
                        else "unknown")
                return False, f"invented {diag} word '{word}' not in input"

        return True, "ok"


# ═══════════════════════════════════════════════════════════════════════════════
#  ASL FALLBACK NORMALIZER — rule-based minimal conversion when Ollama fails
# ═══════════════════════════════════════════════════════════════════════════════

class AslFallbackNormalizer:
    """Minimal rule-based ASL gloss → English normaliser.

    Used when Ollama is unavailable, times out, or its output fails validation.
    Produces short, faithful sentences with no invented content.
    """

    _PRONOUN_MAP = {
        "ME": "I", "MY": "my", "MINE": "mine",
        "YOU": "you", "YOUR": "your",
        "HE": "he", "HIS": "his", "HIM": "him",
        "SHE": "she", "HER": "her",
        "WE": "we", "OUR": "our", "US": "us",
        "THEY": "they", "THEIR": "their", "THEM": "them",
        "IT": "it", "ITS": "its",
    }
    _GLOSS_MAP = {
        "THANK_YOU": "thank you",
        "HELLO": "hello",
        "GOODBYE": "goodbye",
        "WANT": "want",
        "EAT": "eat",
        "DRINK": "drink",
        "HELP": "help",
        "KNOW": "know",
        "LIKE": "like",
        "LOVE": "love",
        "UNDERSTAND": "understand",
        "BATHROOM": "bathroom",
        "NAME": "name",
        "FINE": "fine",
        "SORRY": "sorry",
        "PLEASE": "please",
    }
    _QUESTION_WORDS = frozenset(
        {"WHAT", "WHERE", "WHO", "WHY", "WHEN", "HOW", "WHATFOR"}
    )

    def normalize(self, gloss_tokens: List[str]) -> str:
        if not gloss_tokens:
            return ""
        tokens_upper = [t.strip().upper() for t in gloss_tokens if t.strip()]
        is_question = any(t in self._QUESTION_WORDS for t in tokens_upper)

        parts: List[str] = []
        for tok in tokens_upper:
            if tok in self._PRONOUN_MAP:
                parts.append(self._PRONOUN_MAP[tok])
            elif tok in self._GLOSS_MAP:
                parts.append(self._GLOSS_MAP[tok])
            else:
                parts.append(tok.lower().replace("_", " "))

        sentence = " ".join(parts)
        if sentence:
            sentence = sentence[0].upper() + sentence[1:]

        if is_question:
            sentence = sentence.rstrip(".!") + "?"
        elif not sentence.endswith((".", "!", "?")):
            sentence += "."

        return sentence


# ═══════════════════════════════════════════════════════════════════════════════
#  OLLAMA TRANSLATOR — async gloss-to-English via local LLM
# ═══════════════════════════════════════════════════════════════════════════════

OLLAMA_SYSTEM_PROMPT = """You are a strict ASL gloss-to-English sentence normalizer. Do not act as a chatbot.

INPUT: Uppercase ASL gloss tokens representing individual signs (e.g. "ME HUNGRY" or "HOW YOU").
OUTPUT: A single short, natural English sentence — nothing more.

MANDATORY RULES:
1. Respond with ONLY this JSON object and nothing else: {"normalized_text": "Your sentence here."}
2. Use ONLY the meaning of the words given. Do NOT add new facts, new actions, emotions, or context.
3. One sentence maximum. Do not continue the thought or add related ideas.
4. If the input contains HOW, WHAT, WHERE, WHO, WHY, or WHEN — the output MUST end with "?".
5. You MAY use small grammar helpers ONLY: is, are, am, the, a, an, do, I, you, my, your.
6. Do NOT explain. Do NOT apologize. Do NOT write any text outside the JSON object.
7. When uncertain, stay as close to the original words as possible.

EXAMPLES — follow this pattern exactly:
Input: ME HUNGRY
Output: {"normalized_text": "I am hungry."}

Input: HOW YOU
Output: {"normalized_text": "How are you?"}

Input: CAT TALL
Output: {"normalized_text": "The cat is tall."}

Input: YOUR NAME WHAT
Output: {"normalized_text": "What is your name?"}

Input: WHERE BATHROOM
Output: {"normalized_text": "Where is the bathroom?"}

Input: FINE THANK_YOU
Output: {"normalized_text": "I am fine, thank you."}"""


class OllamaTranslator:
    """Non-blocking gloss-to-English translation via Ollama API."""

    def __init__(self, url: str = DEFAULT_OLLAMA_URL,
                 model: str = DEFAULT_OLLAMA_MODEL,
                 timeout: float = 30.0,
                 auth_token: Optional[str] = None):
        self.url = url
        self.model = model
        self.timeout = timeout
        self._headers: Dict[str, str] = (
            {"Authorization": f"Bearer {auth_token}"}
            if auth_token
            else {}
        )
        self._lock = threading.Lock()
        self._translating = False
        self._last_result: Optional[str] = None
        self._last_gloss: Optional[str] = None
        self._error: Optional[str] = None
        self._available: Optional[bool] = None
        self._validator = AslOutputValidator(NORM_CONFIG, VALID_WORDS)
        self._fallback = AslFallbackNormalizer()

    def check_availability(self) -> bool:
        if not HAS_REQUESTS:
            self._available = False
            return False
        try:
            resp = requests.post(
                self.url,
                json={"model": self.model, "messages": [{"role": "user", "content": "hi"}], "stream": False},
                timeout=10.0,
                headers=self._headers or None,
            )
            self._available = resp.status_code == 200
            if self._available:
                print(f"[OLLAMA] Connected to {self.url} with model '{self.model}'")
            else:
                print(f"[OLLAMA] Server returned status {resp.status_code}")
            return self._available
        except Exception as e:
            print(f"[OLLAMA] Not available: {e}")
            self._available = False
            return False

    @property
    def is_available(self) -> bool:
        return bool(self._available)

    @property
    def is_translating(self) -> bool:
        with self._lock:
            return self._translating

    @property
    def last_result(self) -> Optional[str]:
        with self._lock:
            return self._last_result

    @property
    def last_error(self) -> Optional[str]:
        with self._lock:
            return self._error

    def translate_async(self, gloss_tokens: List[str], callback: Optional[callable] = None):
        if not self._available or not HAS_REQUESTS:
            print(f"  [OLLAMA] Skip — available={self._available}, requests={HAS_REQUESTS}", flush=True)
            return
        if not gloss_tokens:
            return
        with self._lock:
            if self._translating:
                print("  [OLLAMA] Skip — already translating", flush=True)
                return
            self._translating = True
            self._error = None

        gloss_str = " ".join(gloss_tokens)
        self._last_gloss = gloss_str
        print(f"  [OLLAMA] Sending: \"{gloss_str}\"...", flush=True)
        thread = threading.Thread(
            target=self._do_translate, args=(gloss_str, callback), daemon=True,
        )
        thread.start()

    def _parse_ollama_response(self, raw_content: str, gloss_str: str) -> str:
        """Extract normalised text from Ollama's raw response.

        Tries JSON parse first, then regex extraction, then plain-text fallback.
        """
        if not raw_content:
            return self._fallback.normalize(gloss_str.split())
        # 1. Full JSON parse
        try:
            parsed = json.loads(raw_content)
            if isinstance(parsed, dict) and "normalized_text" in parsed:
                return str(parsed["normalized_text"]).strip()
        except (json.JSONDecodeError, ValueError):
            pass
        # 2. Regex extraction (handles extra whitespace / surrounding text)
        m = re.search(r'\{"normalized_text"\s*:\s*"([^"]+)"\}', raw_content)
        if m:
            return m.group(1).strip()
        # 3. Short plain-text response (model ignored JSON instruction)
        if len(raw_content) < 120 and not raw_content.lstrip().startswith("{"):
            return raw_content.strip()
        # 4. Rule-based fallback
        return self._fallback.normalize(gloss_str.split())

    def _do_translate(self, gloss_str: str, callback: Optional[callable]):
        def _emit(text: str) -> None:
            with self._lock:
                self._last_result = text
                self._translating = False
            if callback:
                callback(text)

        try:
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": OLLAMA_SYSTEM_PROMPT},
                    {"role": "user", "content": gloss_str},
                ],
                "stream": False,
            }
            resp = requests.post(self.url, json=payload, timeout=self.timeout, headers=self._headers or None)
            if resp.status_code == 200:
                data = resp.json()
                raw_content = data.get("message", {}).get("content", "").strip()
                english = self._parse_ollama_response(raw_content, gloss_str)
                is_valid, reason = self._validator.validate(gloss_str, english)
                if not is_valid:
                    print(f"  [NORM] Rejected: {reason}. Using fallback.", flush=True)
                    english = self._fallback.normalize(gloss_str.split())
                print(f'  [OLLAMA] "{gloss_str}" -> "{english}"', flush=True)
                _emit(english)
            else:
                fallback = self._fallback.normalize(gloss_str.split())
                with self._lock:
                    self._error = f"HTTP {resp.status_code}"
                    self._translating = False
                print(
                    f"  [OLLAMA ERROR] HTTP {resp.status_code} — fallback: \"{fallback}\"",
                    flush=True,
                )
                if callback:
                    callback(fallback)
        except requests.exceptions.Timeout:
            fallback = self._fallback.normalize(gloss_str.split())
            with self._lock:
                self._error = "Timeout"
                self._translating = False
            print(
                f"  [OLLAMA TIMEOUT] No response after {self.timeout}s"
                f" — fallback: \"{fallback}\"",
                flush=True,
            )
            if callback:
                callback(fallback)
        except Exception as e:
            fallback = self._fallback.normalize(gloss_str.split())
            with self._lock:
                self._error = str(e)
                self._translating = False
            print(f"  [OLLAMA ERROR] {e} — fallback: \"{fallback}\"", flush=True)
            if callback:
                callback(fallback)


# ═══════════════════════════════════════════════════════════════════════════════
#  CAMERA
# ═══════════════════════════════════════════════════════════════════════════════

class CameraThread:
    """Threaded camera capture for non-blocking frame reads."""

    def __init__(self, src=0, w=1280, h=720):
        self.cap = cv2.VideoCapture(src, cv2.CAP_DSHOW)
        if not self.cap.isOpened():
            self.cap = cv2.VideoCapture(src)
        if not self.cap.isOpened():
            raise RuntimeError(f"Cannot open camera {src}")
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        self.cap.set(cv2.CAP_PROP_FPS, 30)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        self._lock = threading.Lock()
        self._frame = None
        self._run = True
        self._t = threading.Thread(target=self._read, daemon=True)
        self._t.start()

        self.W = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.H = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        print(f"[Camera] {self.W}x{self.H}")

    def _read(self):
        while self._run:
            ok, f = self.cap.read()
            if not ok:
                time.sleep(0.001)
                continue
            with self._lock:
                self._frame = f

    def read(self):
        with self._lock:
            return self._frame.copy() if self._frame is not None else None

    def release(self):
        self._run = False
        self._t.join(timeout=2)
        try:
            self.cap.release()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
#  SKELETON DRAWING — used by api_server.py for MJPEG overlay
# ═══════════════════════════════════════════════════════════════════════════════

def draw_skel(img, feat, fw, fh, idx, dc, lc):
    """Draw hand skeleton on image. Skips drawing entirely when the confident
    hand joints don't cluster into a hand-sized bounding box — this avoids
    the scattered-noise look when the real hand is out of frame but RTMPose
    still emits phantom keypoints spread across the image."""
    pts = {}
    xs_norm = []
    ys_norm = []
    for li, gi in enumerate(idx):
        if gi < feat.shape[0] and feat[gi, 2] >= HAND_CONF_THRESH:
            pts[li] = (int(feat[gi, 0] * fw), int(feat[gi, 1] * fh))
            xs_norm.append(float(feat[gi, 0]))
            ys_norm.append(float(feat[gi, 1]))
    if len(pts) < 6:
        return
    if (max(xs_norm) - min(xs_norm)) > HAND_MAX_SPREAD_X:
        return
    if (max(ys_norm) - min(ys_norm)) > HAND_MAX_SPREAD_Y:
        return
    for a, b in HAND_BONES:
        if a in pts and b in pts:
            cv2.line(img, pts[a], pts[b], lc, 1, cv2.LINE_AA)
    for _, (px, py) in pts.items():
        cv2.circle(img, (px, py), 3, dc, -1, cv2.LINE_AA)


# ═══════════════════════════════════════════════════════════════════════════════
#  CHECKPOINT LOADER — supports v7, v8 (GCN), and v9 (CTR-GCN) checkpoints
# ═══════════════════════════════════════════════════════════════════════════════

def load_ckpt(path, dev, ema=True):
    """Load model checkpoint and return (model, label2idx, config)."""
    ck = torch.load(path, map_location="cpu", weights_only=False)
    cfg = ck["config"]
    l2i = ck["label2idx"]

    # Standard GCN config
    use_gcn = bool(cfg.get("use_gcn", False))
    gcn_hidden = int(cfg.get("gcn_hidden", 64))
    gcn_layers = int(cfg.get("gcn_layers", 2))
    gcn_dropout = float(cfg.get("gcn_dropout", 0.1))

    # CTR-GCN config (v9+)
    use_ctr_gcn = bool(cfg.get("use_ctr_gcn", False))
    ctr_gcn_channels = cfg.get("ctr_gcn_channels", [7, 64, 64, 7])
    if isinstance(ctr_gcn_channels, str):
        ctr_gcn_channels = [int(x.strip()) for x in ctr_gcn_channels.split(",")]
    ctr_rel_reduction = int(cfg.get("ctr_rel_reduction", 8))
    ctr_num_subsets = int(cfg.get("ctr_num_subsets", 3))

    m = SignGCNTransformer(
        FLAT_DIM, len(l2i), int(cfg["d_model"]), int(cfg["n_heads"]),
        int(cfg["num_layers"]), int(cfg["dim_feedforward"]),
        float(cfg["dropout"]), float(cfg["input_dropout"]),
        int(cfg["max_frames"]), cfg.get("pooling", "cls"),
        use_gcn=use_gcn, gcn_hidden=gcn_hidden,
        gcn_layers=gcn_layers, gcn_dropout=gcn_dropout,
        use_ctr_gcn=use_ctr_gcn,
        ctr_gcn_channels=ctr_gcn_channels,
        ctr_rel_reduction=ctr_rel_reduction,
        ctr_num_subsets=ctr_num_subsets,
    ).to(dev).eval()

    st = ck["ema_state"] if (ema and "ema_state" in ck) else ck["model_state"]
    m.load_state_dict(st, strict=True)

    # Info log
    gcn_str = ""
    if use_ctr_gcn:
        gcn_str = f" | CTR-GCN={ctr_gcn_channels} subsets={ctr_num_subsets}"
    elif use_gcn:
        gcn_str = f" | GCN=legacy h={gcn_hidden}"
    print(
        f"[INFO] {len(l2i)} classes | d={cfg['d_model']}{gcn_str} "
        f"| best={ck.get('best_val_acc', '?')}"
    )
    return m, l2i, cfg


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN — standalone mode (for testing without the web server)
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    """Standalone test mode with OpenCV window. Run api_server.py for production."""
    _hw = detect_runtime_profile()
    p = argparse.ArgumentParser(
        description="ASL Translation — Website Inference v2 (standalone test)"
    )
    p.add_argument("--checkpoint", default=DEFAULT_CHECKPOINT)
    p.add_argument("--device", default=_hw.profile, choices=["cuda", "cpu", "mps"])
    p.add_argument("--camera", type=int, default=0)
    p.add_argument("--conf_threshold", type=float, default=0.25)
    p.add_argument("--start_threshold", type=float, default=0.005)
    p.add_argument("--stop_threshold", type=float, default=0.003)
    p.add_argument("--max_sign_frames", type=int, default=70)
    p.add_argument("--debug", type=int, default=0)
    p.add_argument("--rtmw_mode", default="performance")
    p.add_argument("--rtmw_backend", default="onnxruntime")
    p.add_argument("--rtmw_device", default=_hw.profile)
    p.add_argument("--ollama_url", default=DEFAULT_OLLAMA_URL)
    p.add_argument("--ollama_model", default=DEFAULT_OLLAMA_MODEL)
    p.add_argument("--ollama_timeout", type=float, default=DEFAULT_OLLAMA_TIMEOUT)
    p.add_argument("--sentence_rest_frames", type=int, default=SENTENCE_REST_FRAMES)
    p.add_argument("--disable_translation", type=int, default=0)
    args = p.parse_args()

    if not os.path.isfile(args.checkpoint):
        print(f"[ERROR] Not found: {args.checkpoint}")
        return
    if args.device == "cuda" and not torch.cuda.is_available():
        args.device = "cpu"
    dev = torch.device(args.device)
    if dev.type == "cuda":
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True

    model, l2i, cfg = load_ckpt(args.checkpoint, dev)
    i2l = {v: k for k, v in l2i.items()}
    mf = int(cfg.get("max_frames", 128))
    nc = len(l2i)
    vnc = int(cfg.get("val_num_clips", 3))
    vmc = int(cfg.get("val_max_frames_cap", 384))

    # GPU warmup
    if dev.type == "cuda":
        d = torch.randn(1, mf, FLAT_DIM, device=dev)
        m = torch.ones(1, mf, dtype=torch.bool, device=dev)
        with torch.no_grad(), torch.amp.autocast("cuda"):
            for _ in range(3):
                model(d, m)

    wb = Wholebody(
        mode=args.rtmw_mode, backend=args.rtmw_backend, device=args.rtmw_device
    )
    tk = PersonTracker()
    fe = FrameFeatureExtractor()
    sp = SignSpotter(
        start_threshold=args.start_threshold,
        stop_threshold=args.stop_threshold,
        max_sign_frames=args.max_sign_frames,
    )
    cam = CameraThread(args.camera)

    gloss_buf = GlossBuffer()
    sentence_det = SentenceBoundaryDetector(
        rest_frames_threshold=args.sentence_rest_frames,
        min_signs=SENTENCE_MIN_SIGNS,
    )
    translator = OllamaTranslator(
        url=args.ollama_url, model=args.ollama_model, timeout=args.ollama_timeout,
        auth_token=DEFAULT_OLLAMA_AUTH_TOKEN,
    )

    if not args.disable_translation:
        print("[OLLAMA] Checking connection...")
        translator.check_availability()
        if not translator.is_available:
            print("[OLLAMA] Translation unavailable — signs will still be recognized.")
    else:
        print("[OLLAMA] Translation disabled")

    # State
    hist = []
    dbg = bool(args.debug)
    fpsd = deque(maxlen=30)
    cl, cc = None, 0.0
    rl, rc, r5 = None, 0.0, []
    cf, cm2 = None, 0.0
    ct = args.conf_threshold
    sn = 0

    print("\n" + "=" * 60)
    print("  ASL TRANSLATION v2 — CTR-GCN + STANDALONE TEST")
    print(f"  {nc} signs | thresh {ct * 100:.0f}%")
    print(f"  Ollama: {'ON' if translator.is_available else 'OFF'}")
    print(f"  [S] manual | [Q] quit | [R] reset | [C] clear")
    print("=" * 60 + "\n")

    def on_translation_done(english: str):
        gloss_buf.add_translation(english)

    win = "ASL Translation"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)

    try:
        while True:
            t0 = time.perf_counter()
            fr = cam.read()
            if fr is None:
                time.sleep(0.003)
                continue

            H, W = fr.shape[:2]
            disp = fr.copy()

            # ── Pose → Feature → Detect ──
            kp, sc = wb(fr)
            pk, ps = tk.select(kp, sc)
            feat = fe.process(pk, ps, W, H)

            # Only show skeleton when hands are visible in frame
            if pk is not None and sp._hands_visible(feat):
                cf = feat
            else:
                cf = None  # clear skeleton — no hands in frame

            cm2 = sp.current_motion

            segment = sp.update(feat)
            if segment is not None:
                sn += 1
                sf = segment.shape[0]
                if dbg:
                    print(f"\n  [SEG #{sn}] {sf}f")

                probs = classify_segment(model, segment, mf, dev, nc, vmc, vnc, dbg)
                tc, ti = probs.max(0)
                rc = float(tc.item())
                rl = i2l.get(int(ti.item()), "???")
                k = min(5, nc)
                tv, tii = probs.topk(k)
                r5 = [
                    (i2l.get(int(i.item()), "?"), float(v.item()))
                    for v, i in zip(tv, tii)
                ]

                if rc >= ct:
                    cl = rl
                    cc = rc
                    if not hist or hist[-1] != cl:
                        hist.append(cl)
                    print(f"  >>> {cl} ({cc * 100:.1f}%) | {sf}f")
                    added = gloss_buf.add(cl, cc)
                    if added:
                        sentence_det.notify_sign_added()
                else:
                    print(f"  [skip] {rl} ({rc * 100:.1f}%) < {ct * 100:.0f}%")

            # ── Sentence boundary ──
            if gloss_buf.is_active:
                boundary = sentence_det.update(
                    hands_raised=sp.dbg_hands_raised,
                    motion=cm2,
                    gloss_size=gloss_buf.size,
                )
                if boundary:
                    tokens = gloss_buf.flush()
                    if tokens:
                        gloss_str = " ".join(tokens)
                        print(f'\n  [SENTENCE] "{gloss_str}"')
                        translator.translate_async(
                            tokens, callback=on_translation_done
                        )
                    sentence_det.reset()

            # ── Minimal display ──
            if cf is not None:
                draw_skel(disp, cf, W, H, HAND_L, (200, 255, 100), (150, 200, 80))
                draw_skel(disp, cf, W, H, HAND_R, (100, 200, 255), (80, 160, 200))

            # Status bar
            fps = float(np.mean(fpsd)) if fpsd else 0.0
            txt = f"{sp.state.upper()} | {fps:.0f} FPS | #{sn}"
            if cl:
                txt += f" | {cl}"
            gloss_str = gloss_buf.current_gloss_str
            if gloss_str:
                txt += f" | [{gloss_str}]"
            trans = translator.last_result
            if trans:
                txt += f' | "{trans}"'
            cv2.putText(
                disp, txt, (10, 28),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (80, 220, 100), 1, cv2.LINE_AA,
            )

            cv2.imshow(win, disp)
            fpsd.append(1.0 / max(time.perf_counter() - t0, 1e-6))

            key = cv2.waitKey(1) & 0xFF
            if key == 255:
                continue
            if key in (ord("q"), ord("Q"), 27):
                break
            elif key in (ord("s"), ord("S")):
                seg = sp.manual_toggle()
                if seg is not None:
                    probs = classify_segment(model, seg, mf, dev, nc, vmc, vnc, dbg)
                    tc, ti = probs.max(0)
                    rc = float(tc.item())
                    rl = i2l.get(int(ti.item()), "???")
                    if rc >= ct:
                        cl = rl
                        cc = rc
                        gloss_buf.add(cl, cc)
                        sentence_det.notify_sign_added()
                        print(f"  >>> {cl} ({cc * 100:.1f}%)")
            elif key in (ord("r"), ord("R")):
                tk.reset()
                fe.reset()
                sp.reset()
                hist.clear()
                gloss_buf.clear()
                sentence_det.reset()
                cl = None
                rl = None
                r5 = []
                sn = 0
            elif key in (ord("c"), ord("C")):
                hist.clear()
                gloss_buf.clear()
                sentence_det.reset()

    except KeyboardInterrupt:
        pass
    finally:
        cam.release()
        cv2.destroyAllWindows()
        if gloss_buf.translations:
            print(f"\n[INFO] Translations: {len(gloss_buf.translations)}")
        print("[INFO] Done.")


if __name__ == "__main__":
    main()