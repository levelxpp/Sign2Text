#!/usr/bin/env bash
# Start Sign2Text locally (all services on localhost).
#
# Usage:
#   ./start.sh                        # auto profile, with Ollama
#   ./start.sh --profile cuda         # force CUDA
#   ./start.sh --no-ollama            # skip Ollama translation
#   ./start.sh --tunnel               # Cloudflare Quick Tunnel

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE="auto"
NO_OLLAMA=0
TUNNEL=0

usage() {
    echo "Usage: $0 [--profile auto|cuda|cpu] [--no-ollama] [--tunnel]"
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --profile)   PROFILE="$2"; shift 2 ;;
        --no-ollama) NO_OLLAMA=1; shift ;;
        --tunnel)    TUNNEL=1; shift ;;
        -h|--help)   usage ;;
        *) echo "Unknown option: $1"; usage ;;
    esac
done

export ASL_RUNTIME_PROFILE="$PROFILE"
export ASL_DISABLE_TRANSLATION=$([ "$NO_OLLAMA" -eq 1 ] && echo "1" || echo "0")

PYTHON="$SCRIPT_DIR/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
    PYTHON=$(command -v python3 2>/dev/null || command -v python)
fi

echo "========================================"
echo "  Sign2Text"
echo "  ASL Runtime Profile: $PROFILE"
[ "$NO_OLLAMA" -eq 1 ] && echo "  Ollama Translation:  DISABLED"
[ "$TUNNEL"    -eq 1 ] && echo "  Tunnel Mode:         ENABLED"
echo "========================================"

ARGS=()
[ "$TUNNEL" -eq 1 ] && ARGS+=("--tunnel")

exec "$PYTHON" "$SCRIPT_DIR/start.py" "${ARGS[@]}"
