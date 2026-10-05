from __future__ import annotations

import importlib
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Ensure runtime_config ignores any deployment-style values in the developer's
# local .env during the test session, so monkeypatch.setenv / delenv fully
# controls the environment the backend modules see on reload.
os.environ["S2T_SKIP_DOTENV"] = "1"

# Pre-import real numpy and cv2 so that _stub_heavy_imports() in
# test_asl_normalization.py sees them already in sys.modules and skips
# replacing them with MagicMock.  This prevents mock contamination of
# test_letter_server.py, which requires the genuine packages.
try:
    import numpy as _real_numpy  # noqa: F401
except ImportError:
    pass
try:
    import cv2 as _real_cv2  # noqa: F401
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Targeted ML-stub helper for api_server tests
# ---------------------------------------------------------------------------

def _do_ensure_api_server_importable(monkeypatch):
    """Stub only ``rtmlib`` and ``torch`` if they are not installed.

    ``numpy`` and ``cv2`` are **not** touched — they are expected to be
    installed and are used by other test files (e.g. test_letter_server).
    """

    # ── rtmlib ────────────────────────────────────────────────────────────
    if "rtmlib" not in sys.modules:
        try:
            __import__("rtmlib")
        except ImportError:
            monkeypatch.setitem(sys.modules, "rtmlib", MagicMock())

    # ── torch (needed by new_Inference model definitions) ─────────────────
    if "torch" not in sys.modules:
        try:
            __import__("torch")
        except ImportError:
            class _Fake:
                def __init__(self, *a, **kw):
                    pass
                def __init_subclass__(cls, **kw):
                    pass

            nn_mod = types.ModuleType("torch.nn")
            nn_mod.Module = _Fake
            nn_mod.Linear = _Fake
            nn_mod.GELU = _Fake
            nn_mod.LayerNorm = _Fake
            nn_mod.Dropout = _Fake
            nn_func = types.ModuleType("torch.nn.functional")
            nn_mod.functional = nn_func

            mock_torch = types.ModuleType("torch")
            mock_torch.__version__ = "0.0.0"
            mock_torch.cuda = types.SimpleNamespace(
                is_available=lambda: False,
                get_device_name=lambda _i: None,
            )
            mock_torch.nn = nn_mod
            mock_torch.Tensor = object
            mock_torch.zeros = lambda *a, **kw: None
            mock_torch.float32 = "float32"
            mock_torch.no_grad = lambda: MagicMock(
                __enter__=lambda s, *a: None,
                __exit__=lambda s, *a: None,
            )

            monkeypatch.setitem(sys.modules, "torch", mock_torch)
            monkeypatch.setitem(sys.modules, "torch.nn", nn_mod)
            monkeypatch.setitem(sys.modules, "torch.nn.functional", nn_func)


@pytest.fixture
def ensure_api_server_importable(monkeypatch):
    """Fixture wrapper — call the returned function before load_module("api_server")."""
    def _apply():
        _do_ensure_api_server_importable(monkeypatch)
    return _apply


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

for path in (ROOT, BACKEND):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)


@pytest.fixture
def load_module():
    def _load(name: str):
        if name in sys.modules:
            return importlib.reload(sys.modules[name])
        return importlib.import_module(name)

    return _load

