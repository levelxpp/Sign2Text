#!/usr/bin/env bash
# start_server.sh - Headless launcher for Linux GPU VM deployments.
#
# Usage:
#   bash start_server.sh            # direct ports (8000, 8001, 5500)
#   bash start_server.sh --proxy    # + reverse proxy on 0.0.0.0:8080 (recommended for Azure Linux VM public access)
#
# Starts all Sign2Text services in headless mode (no prompts, no browser).
# Ensures Ollama is running before launching the main application.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Activate virtual environment
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
else
    echo "ERROR: Virtual environment not found. Run bash setup_runpod.sh first."
    exit 1
fi

load_dotenv_value_if_unset() {
    local key="$1"
    local env_file=".env"
    local value

    if declare -p "$key" > /dev/null 2>&1 || [ ! -f "$env_file" ]; then
        return 0
    fi

    value="$(
        python - "$env_file" "$key" <<'PY'
from pathlib import Path
import sys

env_file = Path(sys.argv[1])
key = sys.argv[2]

for raw_line in env_file.read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue

    current_key, value = line.split("=", 1)
    if current_key.strip() != key:
        continue

    value = value.strip()
    if " #" in value:
        value = value.split(" #", 1)[0].rstrip()
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        value = value[1:-1]
    print(value)
    break
PY
    )"

    if [ -n "$value" ]; then
        printf -v "$key" '%s' "$value"
        export "$key"
    fi
}

load_dotenv_value_if_unset OLLAMA_MODE

# Ensure Ollama is running (skipped when OLLAMA_MODE=remote)
if [ "${OLLAMA_MODE:-local}" != "remote" ]; then
    if command -v ollama > /dev/null 2>&1; then
        if ! pgrep -x ollama > /dev/null 2>&1; then
            echo "Starting Ollama server..."
            ollama serve > /dev/null 2>&1 &
            sleep 2
        fi
    fi
else
    echo "OLLAMA_MODE=remote — skipping local Ollama startup"
fi

# Launch Sign2Text
exec python start.py --headless "$@"
