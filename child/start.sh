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

# ── uv (Python toolchain manager) ────────────────────────────────────────────
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv &>/dev/null; then
    echo "==> Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    source "$HOME/.local/bin/env"
fi

# ── Virtual environment ───────────────────────────────────────────────────────
# Recreate if missing or if pip is broken inside it.
# uv bundles its own Python 3.12 — avoids broken system/Homebrew Pythons.
if [[ ! -d "$VENV_DIR" ]] || ! "$VENV_DIR/bin/python3" -m pip --version &>/dev/null; then
    echo "==> (Re)creating virtual environment with uv (Python 3.12)..."
    rm -rf "$VENV_DIR"
    uv venv "$VENV_DIR" --python 3.12 --seed
fi

PYTHON="$VENV_DIR/bin/python3"

# ── Python dependencies ───────────────────────────────────────────────────────
"$PYTHON" -m pip install --upgrade pip --quiet
"$PYTHON" -m pip install -r "$SCRIPT_DIR/requirements.txt" --quiet

# ── config.toml ───────────────────────────────────────────────────────────────
if [[ ! -f "$SCRIPT_DIR/config.toml" ]]; then
    echo "==> No config.toml found — launching configurator..."
    "$PYTHON" "$SCRIPT_DIR/configure.py"
fi

# ── Launch ────────────────────────────────────────────────────────────────────
echo "==> Starting child agent..."
exec "$PYTHON" "$SCRIPT_DIR/main.py"
