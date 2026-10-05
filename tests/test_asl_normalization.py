"""Unit tests for AslOutputValidator and AslFallbackNormalizer.

`new_Inference` imports the heavy ML stack (torch, numpy, cv2, rtmlib) at module
scope, but the two classes under test here are pure text logic. So that these
tests still run in a minimal environment, any of those packages that is genuinely
not installed is replaced by a small stub.

Isolation contract
------------------
Stubs are installed by an autouse fixture and removed again on teardown, even if
an assertion fails. Specifically:

* The real package is imported first; a stub is only ever used when the real
  import raises ImportError. In the pinned CI environment all four packages are
  installed, so no stub is created at all.
* Every `sys.modules` entry this module creates is recorded and restored to its
  exact previous value (including "was absent") afterwards.
* Any backend module imported while a stub was active is evicted, so a later
  test re-imports it against the real packages rather than reusing a module
  object bound to stub types.

This matters because `backend/api_server.py` and `backend/letter_server.py` call
`torch.set_num_threads(1)` at import time. A leaked torch stub without that
attribute previously broke every module that imported a service afterwards — 59
failures across 8 modules, purely as an artefact of test ordering.
"""
from __future__ import annotations

import importlib
import sys
import types
from unittest.mock import MagicMock

import pytest

# sys.modules keys this module may create, in deletion-safe order.
_STUBBED_NAMES = ("torch.nn.functional", "torch.nn", "torch", "numpy", "rtmlib", "cv2")

# Module prefixes that bind to the heavy packages at import time and must be
# dropped if they were imported while a stub was active.
_BACKEND_PREFIXES = ("new_Inference", "api_server", "letter_server", "backend")


def _build_torch_stub():
    """Minimal torch surface needed to import `new_Inference`."""

    class _FakeModule:
        def __init__(self, *a, **kw): pass
        def __init_subclass__(cls, **kw): pass

    nn_mod = types.ModuleType("torch.nn")
    nn_mod.Module = _FakeModule
    nn_mod.Linear = _FakeModule
    nn_mod.GELU = _FakeModule
    nn_mod.LayerNorm = _FakeModule
    nn_mod.Dropout = _FakeModule
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
        __enter__=lambda s, *a: None, __exit__=lambda s, *a: None
    )
    # The services configure threading at import time. Include these so a stub
    # can never reproduce the historical AttributeError cascade.
    mock_torch.set_num_threads = lambda _n: None
    mock_torch.set_num_interop_threads = lambda _n: None

    return {"torch": mock_torch, "torch.nn": nn_mod, "torch.nn.functional": nn_func}


def _stub_if_unavailable(name, factory):
    """Return {name: module} stubs only when the real package cannot be imported."""
    if name in sys.modules:
        return {}
    try:
        importlib.import_module(name)
    except ImportError:
        return factory()
    return {}


@pytest.fixture(scope="module", autouse=True)
def _heavy_import_stubs():
    """Install stubs for missing ML packages, then restore process state exactly."""
    previous = {name: sys.modules.get(name) for name in _STUBBED_NAMES}
    was_present = {name: name in sys.modules for name in _STUBBED_NAMES}
    modules_before = set(sys.modules)

    installed: list[str] = []
    try:
        stubs = {}
        stubs.update(_stub_if_unavailable("torch", _build_torch_stub))
        stubs.update(_stub_if_unavailable(
            "numpy", lambda: {"numpy": _numpy_stub()}))
        for pkg in ("rtmlib", "cv2"):
            stubs.update(_stub_if_unavailable(pkg, lambda p=pkg: {p: MagicMock()}))

        for name, module in stubs.items():
            sys.modules[name] = module
            installed.append(name)

        yield
    finally:
        if installed:
            # Drop anything imported against the stubs so later tests re-import
            # cleanly against the real packages.
            for name in set(sys.modules) - modules_before:
                if name.split(".")[0] in _BACKEND_PREFIXES:
                    sys.modules.pop(name, None)

            for name in _STUBBED_NAMES:
                if name not in installed:
                    continue
                if was_present[name]:
                    sys.modules[name] = previous[name]
                else:
                    sys.modules.pop(name, None)

            importlib.invalidate_caches()


def _numpy_stub():
    mock_np = MagicMock()
    mock_np.ndarray = list
    mock_np.float32 = float
    return mock_np


def test_validator_rejects_overly_long_output(load_module):
    ni = load_module("new_Inference")
    validator = ni.AslOutputValidator(ni.NORM_CONFIG, ni.VALID_WORDS)

    # 2-token input → max allowed = 2 + 4 = 6 output words (hard cap 12)
    long_output = "The cat is very very very tall and happy and dancing."
    is_valid, reason = validator.validate("CAT TALL", long_output)
    assert not is_valid, f"Expected rejection but passed; reason would be: {reason}"


def test_validator_rejects_invented_content(load_module):
    ni = load_module("new_Inference")
    validator = ni.AslOutputValidator(ni.NORM_CONFIG, ni.VALID_WORDS)

    # "dancing" and "rooftop" are not in input tokens "CAT TALL"
    invented = "The cat is tall and dancing on the rooftop."
    is_valid, reason = validator.validate("CAT TALL", invented)
    assert not is_valid, f"Expected rejection for invented content; reason: {reason}"

    # Good output should pass
    is_valid, reason = validator.validate("CAT TALL", "The cat is tall.")
    assert is_valid, f"Expected valid output to pass; reason: {reason}"


def test_validator_preserves_question_form(load_module):
    ni = load_module("new_Inference")
    validator = ni.AslOutputValidator(ni.NORM_CONFIG, ni.VALID_WORDS)

    # Question sign present but output is not a question → rejected
    non_question = "How are you doing today."
    is_valid, reason = validator.validate("HOW YOU", non_question)
    assert not is_valid, f"Expected rejection for missing '?'; reason: {reason}"

    # Correct question form → accepted
    is_valid, reason = validator.validate("HOW YOU", "How are you?")
    assert is_valid, f"Expected 'How are you?' to pass; reason: {reason}"


def test_fallback_returns_short_safe_text(load_module):
    ni = load_module("new_Inference")
    fallback = ni.AslFallbackNormalizer()

    result = fallback.normalize(["ME", "HUNGRY"])
    assert result.startswith("I"), f"Expected ME→I mapping; got: {result!r}"
    assert result.endswith("."), f"Expected period at end; got: {result!r}"
    assert len(result) <= 20, f"Expected short output (≤20 chars); got: {result!r}"

    result_q = fallback.normalize(["HOW", "YOU"])
    assert result_q.endswith("?"), f"Expected question mark for HOW input; got: {result_q!r}"
