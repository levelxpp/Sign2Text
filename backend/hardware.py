"""
Hardware runtime profile detection for Sign2Text.

Determines whether to use CUDA or CPU for PyTorch and ONNX Runtime,
based on available hardware and optional environment override.

Usage:
    from backend.hardware import detect_runtime_profile, log_hardware_report

    report = detect_runtime_profile()
    log_hardware_report(report)
    device_str = report.profile  # "cuda" or "cpu"
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

VALID_PROFILES = ("auto", "cuda", "cpu")
ENV_KEY = "ASL_RUNTIME_PROFILE"


@dataclass(frozen=True)
class HardwareReport:
    """Snapshot of detected hardware capabilities and chosen profile."""

    profile: str  # "cuda" or "cpu"
    source: str  # "env" or "auto"
    torch_cuda: bool
    onnx_cuda: bool
    torch_version: str
    onnx_version: str
    gpu_name: Optional[str]
    onnx_providers: Tuple[str, ...]  # all available ONNX Runtime providers
    directml_available: bool  # informational — RTMLib cannot use DirectML


def _check_torch_cuda() -> tuple[bool, str, Optional[str]]:
    """Return (cuda_available, torch_version, gpu_name_or_None)."""
    try:
        import torch

        available = torch.cuda.is_available()
        version = torch.__version__
        gpu_name = None
        if available:
            try:
                gpu_name = torch.cuda.get_device_name(0)
            except Exception:
                gpu_name = "unknown"
        return available, version, gpu_name
    except ImportError:
        return False, "not installed", None


def _check_onnx_providers() -> tuple[bool, str, tuple[str, ...]]:
    """Return (cuda_provider_available, ort_version, all_providers)."""
    try:
        import onnxruntime as ort

        providers = tuple(ort.get_available_providers())
        has_cuda = "CUDAExecutionProvider" in providers
        return has_cuda, ort.__version__, providers
    except ImportError:
        return False, "not installed", ()


def detect_runtime_profile() -> HardwareReport:
    """Detect the best runtime profile for the current machine.

    Checks the ``ASL_RUNTIME_PROFILE`` environment variable first
    (``auto`` | ``cuda`` | ``cpu``).  When set to ``auto`` or unset,
    probes PyTorch and ONNX Runtime for CUDA support and selects the
    best available profile.

    Returns a frozen :class:`HardwareReport` describing the decision.
    """
    raw = os.environ.get(ENV_KEY, "auto").strip().lower()
    if raw not in VALID_PROFILES:
        logger.warning(
            "%s=%r is not valid (expected %s); falling back to 'auto'",
            ENV_KEY,
            raw,
            "/".join(VALID_PROFILES),
        )
        raw = "auto"

    torch_cuda, torch_ver, gpu_name = _check_torch_cuda()
    onnx_cuda, onnx_ver, onnx_providers = _check_onnx_providers()
    directml_available = "DmlExecutionProvider" in onnx_providers

    if raw == "cuda":
        source = "env"
        if torch_cuda and onnx_cuda:
            profile = "cuda"
        else:
            missing = []
            if not torch_cuda:
                missing.append("PyTorch CUDA")
            if not onnx_cuda:
                missing.append("ONNX CUDAExecutionProvider")
            logger.warning(
                "%s=cuda but %s unavailable — falling back to cpu",
                ENV_KEY,
                " and ".join(missing),
            )
            profile = "cpu"
    elif raw == "cpu":
        source = "env"
        profile = "cpu"
    else:
        # auto
        source = "auto"
        if torch_cuda and onnx_cuda:
            profile = "cuda"
        else:
            profile = "cpu"
            if torch_cuda != onnx_cuda:
                has = "PyTorch" if torch_cuda else "ONNX Runtime"
                lacks = "ONNX Runtime" if torch_cuda else "PyTorch"
                logger.warning(
                    "%s has CUDA but %s does not — using cpu profile "
                    "for consistency",
                    has,
                    lacks,
                )

    return HardwareReport(
        profile=profile,
        source=source,
        torch_cuda=torch_cuda,
        onnx_cuda=onnx_cuda,
        torch_version=torch_ver,
        onnx_version=onnx_ver,
        gpu_name=gpu_name,
        onnx_providers=onnx_providers,
        directml_available=directml_available,
    )


def log_hardware_report(report: HardwareReport) -> None:
    """Print a human-readable startup diagnostic banner."""
    # Active device description
    if report.profile == "cuda":
        active_device = report.gpu_name or "CUDA GPU"
        active_onnx = "CUDAExecutionProvider"
    else:
        active_device = "CPU"
        active_onnx = "CPUExecutionProvider"

    # Available capabilities
    cuda_torch_str = "not available"
    if report.torch_cuda:
        cuda_torch_str = f"yes ({report.gpu_name})" if report.gpu_name else "yes"

    cuda_onnx_str = "yes" if report.onnx_cuda else "not available"

    if report.directml_available:
        dml_str = "detected (not usable -- RTMLib limitation)"
    else:
        dml_str = "not detected"

    providers_str = ", ".join(report.onnx_providers) if report.onnx_providers else "none"

    sep = "=" * 58
    banner = (
        f"\n{sep}\n"
        f"  Runtime Profile    : {report.profile.upper()}  (source: {report.source})\n"
        f"  Active device      : {active_device}\n"
        f"  Active ONNX prov.  : {active_onnx}\n"
        f"  -- Available --\n"
        f"  PyTorch CUDA       : {cuda_torch_str}\n"
        f"  ONNX CUDA          : {cuda_onnx_str}\n"
        f"  DirectML           : {dml_str}\n"
        f"  ONNX providers     : {providers_str}\n"
        f"  -- Versions --\n"
        f"  PyTorch            : {report.torch_version}\n"
        f"  ONNX Runtime       : {report.onnx_version}\n"
        f"{sep}"
    )
    print(banner, flush=True)
    logger.info("Runtime profile: %s (source: %s)", report.profile, report.source)
