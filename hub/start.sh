#!/usr/bin/env bash
# Start the hub agent, running any missing setup steps first.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

# ── Homebrew ──────────────────────────────────────────────────────────────────
if ! command -v brew &>/dev/null; then
    echo "==> Homebrew not found — running setup first..."
    bash "$SCRIPT_DIR/setup.sh"
    exec bash "$0" "$@"   # re-exec so PATH includes brew
fi

# ── Python ────────────────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "==> Python not found — installing..."
    brew install python
fi

# ── Ollama ────────────────────────────────────────────────────────────────────
if ! command -v ollama &>/dev/null; then
    echo "==> Ollama not found — installing..."
    brew install ollama
fi

# ── Virtual environment ───────────────────────────────────────────────────────
if [[ ! -d "$VENV_DIR" ]]; then
    echo "==> Virtual environment missing — creating..."
    python3 -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

# ── Python dependencies ───────────────────────────────────────────────────────
# Install/sync quietly; only prints if something is actually installed
pip install --upgrade pip --quiet
pip install -r "$SCRIPT_DIR/requirements.txt" --quiet

# ── config.toml ───────────────────────────────────────────────────────────────
if [[ ! -f "$SCRIPT_DIR/config.toml" ]]; then
    echo "==> No config.toml found — launching configurator..."
    python "$SCRIPT_DIR/configure.py"
fi

# ── Launch ────────────────────────────────────────────────────────────────────
echo "==> Starting hub agent..."
exec python "$SCRIPT_DIR/main.py"
