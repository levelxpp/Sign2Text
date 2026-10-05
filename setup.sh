#!/usr/bin/env bash
# Set up the Sign2Text Python environment with the correct runtime profile.
#
# Usage:
#   ./setup.sh                  # auto-detect GPU
#   ./setup.sh --profile cpu    # force CPU-only install
#   ./setup.sh --profile cuda   # force CUDA install

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE="auto"

usage() {
    echo "Usage: $0 [--profile auto|cuda|cpu]"
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --profile) PROFILE="$2"; shift 2 ;;
        -h|--help) usage ;;
        *) echo "Unknown option: $1"; usage ;;
    esac
done

if [[ "$PROFILE" != "auto" && "$PROFILE" != "cuda" && "$PROFILE" != "cpu" ]]; then
    echo "ERROR: --profile must be auto, cuda, or cpu." >&2
    exit 1
fi

# ── 1. Locate Python ──────────────────────────────────────────────────────
PYTHON=$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)
if [[ -z "$PYTHON" ]]; then
    echo "ERROR: Python not found in PATH. Install Python 3.11+ and try again." >&2
    exit 1
fi

PY_VER=$("$PYTHON" --version 2>&1)
echo "Found $PY_VER"

# ── 2. Create virtual environment if needed ───────────────────────────────
VENV_DIR="$SCRIPT_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"

if [[ ! -x "$VENV_PYTHON" ]]; then
    echo "Creating virtual environment at $VENV_DIR ..."
    "$PYTHON" -m venv "$VENV_DIR"
    echo "Virtual environment created."
else
    echo "Virtual environment already exists."
fi

# ── 3. Activate ───────────────────────────────────────────────────────────
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# ── 4. Resolve runtime profile ────────────────────────────────────────────
RESOLVED_PROFILE="$PROFILE"

if [[ "$PROFILE" == "auto" ]]; then
    echo "Detecting GPU..."
    if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null; then
        RESOLVED_PROFILE="cuda"
        echo "  NVIDIA GPU detected — selecting CUDA profile."
    else
        RESOLVED_PROFILE="cpu"
        echo "  nvidia-smi not found or failed — selecting CPU profile."
    fi
fi

# ── 5. Install dependencies ───────────────────────────────────────────────
if [[ "$RESOLVED_PROFILE" == "cuda" ]]; then
    REQ_FILE="requirements.runtime.txt"
else
    REQ_FILE="requirements.runtime-cpu.txt"
fi

REQ_PATH="$SCRIPT_DIR/$REQ_FILE"
echo ""
echo "Installing $REQ_FILE ($RESOLVED_PROFILE profile) ..."
pip install -r "$REQ_PATH"

# ── 6. Build landing page (optional; graceful skip if npm unavailable) ───
LANDING_DIR="$SCRIPT_DIR/landing"
if [[ -f "$LANDING_DIR/package.json" ]]; then
    echo ""
    echo "Building landing page ..."
    if ! command -v npm &>/dev/null; then
        echo "  npm not found on PATH — skipping landing build."
        echo "  Install Node.js 18+ from https://nodejs.org/ and re-run setup to build the landing page."
    else
        (
            cd "$LANDING_DIR"
            if [[ ! -d node_modules ]]; then
                echo "  Running npm install ..."
                npm install --no-audit --no-fund || { echo "  npm install failed — skipping landing build."; exit 0; }
            fi
            echo "  Running npm run build ..."
            if npm run build && [[ -f "$LANDING_DIR/dist/index.html" ]]; then
                echo "  Landing built: landing/dist/index.html"
            else
                echo "  Landing build did not produce dist/index.html."
            fi
        )
    fi
fi

# ── 7. Summary ────────────────────────────────────────────────────────────
echo ""
echo "========================================"
echo "  Setup complete: $RESOLVED_PROFILE profile"
echo ""
echo "  To start the application:"
echo "    ./start.sh"
echo ""
echo "  To expose publicly via ngrok:"
echo "    ./start-ngrok.sh"
echo ""
echo "  To force a specific profile:"
echo "    ./start.sh --profile $RESOLVED_PROFILE"
echo "========================================"
