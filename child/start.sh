#!/usr/bin/env bash
# Start the child agent (macOS + Linux).
# Installs missing dependencies automatically on first run.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
OS="$(uname -s)"

# ── Ollama ────────────────────────────────────────────────────────────────────
if ! command -v ollama &>/dev/null && [[ -z "$(find /opt/homebrew/bin /usr/local/bin /usr/bin -name ollama 2>/dev/null)" ]]; then
    echo "==> Ollama not found — installing..."
    if [[ "$OS" == "Darwin" ]]; then
        if command -v brew &>/dev/null; then
            brew install ollama
        else
            echo "  Homebrew not found. Install Ollama from https://ollama.ai or install Homebrew first."
            exit 1
        fi
    else
        # Linux — official install script
        curl -fsSL https://ollama.ai/install.sh | sh
    fi
fi

# ── uv ────────────────────────────────────────────────────────────────────────
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv &>/dev/null; then
    echo "==> Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    source "$HOME/.local/bin/env" 2>/dev/null || export PATH="$HOME/.local/bin:$PATH"
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
