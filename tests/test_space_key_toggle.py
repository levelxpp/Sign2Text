"""
Static structural checks for the Space-key NO-OP contract in frontend/web/app.js.

Space must be a true no-op in the live app: no pipeline control, no mode
switching, and no native focused-button activation. These are source-pattern
assertions, not runtime behavior tests. No JS test infrastructure exists in
this repo, so this is the smallest realistic automated validation available.
Manual browser testing is required for full runtime verification.

Path note: the web frontend lives at ``frontend/web/``. This module previously
pointed at ``frontend/app.js``, a location that predates that move, and so
failed at collection with FileNotFoundError rather than actually checking
anything. The assertions below are unchanged — only the path was corrected.
"""

import re
from pathlib import Path

APP_JS = Path(__file__).parent.parent / "frontend" / "web" / "app.js"


def _keydown_block(src: str) -> str:
    """Return the keydown listener body (from its opening brace to the matching close)."""
    marker = "document.addEventListener('keydown'"
    alt_marker = 'document.addEventListener("keydown"'
    idx = src.find(marker)
    if idx == -1:
        idx = src.find(alt_marker)
    if idx == -1:
        return ""
    # find the opening { of the arrow function body
    open_idx = src.index('{', idx)
    depth = 0
    for i in range(open_idx, len(src)):
        if src[i] == '{':
            depth += 1
        elif src[i] == '}':
            depth -= 1
            if depth == 0:
                return src[open_idx:i + 1]
    return ""


def _space_block(keydown: str) -> str:
    """Return the body of the if-block that checks for Space key."""
    pattern = re.compile(
        r"if\s*\(\s*ev\.code\s*===\s*['\"]Space['\"]",
    )
    m = pattern.search(keydown)
    if not m:
        return ""
    start = keydown.index('{', m.end())
    depth = 0
    for i in range(start, len(keydown)):
        if keydown[i] == '{':
            depth += 1
        elif keydown[i] == '}':
            depth -= 1
            if depth == 0:
                return keydown[start:i + 1]
    return ""


class TestSpaceKeyToggleStructure:
    src: str = ""
    keydown: str = ""
    space: str = ""

    @classmethod
    def setup_class(cls):
        cls.src = APP_JS.read_text(encoding="utf-8")
        cls.keydown = _keydown_block(cls.src)
        cls.space = _space_block(cls.keydown)

    # --- guards in the keydown listener ---

    def test_iscontenteditable_guard_present(self):
        assert "isContentEditable" in self.keydown, (
            "keydown listener must guard against contenteditable elements"
        )

    # --- Space block extracted ---

    def test_space_block_found(self):
        assert self.space, "Could not locate the Space key if-block in the keydown listener"

    # --- Space block: no mode switching (Space must be a NO-OP) ---

    def test_switch_mode_absent_from_space_block(self):
        assert "switchMode" not in self.space, (
            "Space block must NOT call switchMode — Space is a NO-OP in this task"
        )

    # --- Space block: removed pipeline controls ---

    def test_start_pipeline_absent_from_space_block(self):
        assert "startPipeline" not in self.space, (
            "startPipeline must NOT appear in the Space block"
        )

    def test_resume_pipeline_absent_from_space_block(self):
        assert "resumePipeline" not in self.space, (
            "resumePipeline must NOT appear in the Space block"
        )

    def test_handle_stop_absent_from_space_block(self):
        assert "handleStopRequested" not in self.space, (
            "handleStopRequested must NOT appear in the Space block"
        )

    # --- Space block: no button-focus early-return (regression guard) ---
    # If focus is on a <button> and the Space block returns BEFORE preventDefault,
    # the browser activates the focused button (e.g. Stop) — which silently stops
    # the pipeline. The Space block must NOT contain a button-focus early-return.

    def test_no_button_focus_early_return(self):
        assert "getAttribute('role')" not in self.space and 'getAttribute("role")' not in self.space, (
            "Space block must NOT inspect role attribute — caused 'Space stops pipeline' bug"
        )
        assert "tag === 'button'" not in self.space and 'tag === "button"' not in self.space, (
            "Space block must NOT early-return on button focus — caused 'Space stops pipeline' bug"
        )

    def test_prevent_default_present_in_space_block(self):
        # preventDefault must be present so the browser never activates a focused
        # button and never scrolls the page when Space is pressed.
        assert "preventDefault" in self.space, (
            "Space block must call ev.preventDefault() to neutralize native "
            "focused-button activation and default scroll behavior"
        )


class TestSwitchModeConcurrencyGuard:
    """Structural checks for the switchMode concurrency guard added in Task 2."""

    src: str = ""
    switch_fn: str = ""

    @classmethod
    def setup_class(cls):
        cls.src = APP_JS.read_text(encoding="utf-8")
        cls.switch_fn = _extract_switch_mode(cls.src)

    def test_switch_mode_found(self):
        assert self.switch_fn, "Could not locate switchMode function body"

    def test_switching_flag_in_appstate(self):
        assert "switching:" in cls_src() or "switching :" in cls_src(), (
            "appState must declare a 'switching' field"
        )

    def test_switching_guard_at_top(self):
        assert "appState.switching" in self.switch_fn, (
            "switchMode must check appState.switching to prevent concurrent calls"
        )

    def test_busy_guard_in_switch_mode(self):
        assert "appState.busy" in self.switch_fn, (
            "switchMode must also check appState.busy to block switches during startPipeline"
        )

    def test_switching_set_true(self):
        assert "appState.switching = true" in self.switch_fn or "appState.switching=true" in self.switch_fn, (
            "switchMode must set appState.switching = true to lock concurrent calls"
        )

    def test_switching_cleared_in_finally(self):
        assert "finally" in self.switch_fn and "appState.switching = false" in self.switch_fn, (
            "switchMode must clear appState.switching in a finally block"
        )


def cls_src() -> str:
    return APP_JS.read_text(encoding="utf-8")


def _extract_switch_mode(src: str) -> str:
    """Return the body of the switchMode function."""
    marker = "async function switchMode("
    idx = src.find(marker)
    if idx == -1:
        return ""
    start = src.index('{', idx)
    depth = 0
    for i in range(start, len(src)):
        if src[i] == '{':
            depth += 1
        elif src[i] == '}':
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    return ""
