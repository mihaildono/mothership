#!/usr/bin/env bash
# Mother setup script — macOS + Linux
# Installs uv, creates a venv with Python 3.12, and installs dependencies.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

echo "==> Mothership mother setup (macOS)"

# ── uv ────────────────────────────────────────────────────────────────────────
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv &>/dev/null; then
    echo "==> Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    source "$HOME/.local/bin/env"
else
    echo "==> uv already installed ($(uv --version)) — skipping"
fi

# ── Virtual environment ───────────────────────────────────────────────────────
if [[ ! -d "$VENV_DIR" ]] || ! "$VENV_DIR/bin/python3" -m pip --version &>/dev/null; then
    echo "==> Creating virtual environment with uv (Python 3.12)..."
    rm -rf "$VENV_DIR"
    uv venv "$VENV_DIR" --python 3.12 --seed
else
    echo "==> Virtual environment already exists — skipping"
fi

PYTHON="$VENV_DIR/bin/python3"

# ── Python dependencies ───────────────────────────────────────────────────────
echo "==> Installing Python dependencies..."
"$PYTHON" -m pip install --upgrade pip --quiet
"$PYTHON" -m pip install -r "$SCRIPT_DIR/requirements.txt"

echo ""
echo "==> Setup complete. Run ./start.sh to launch the mother."
