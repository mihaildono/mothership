#!/usr/bin/env bash
# Start the mother, running any missing setup steps first.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

# ── uv ────────────────────────────────────────────────────────────────────────
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv &>/dev/null; then
    echo "==> uv not found — running setup first..."
    bash "$SCRIPT_DIR/setup.sh"
fi

# ── Virtual environment ───────────────────────────────────────────────────────
if [[ ! -d "$VENV_DIR" ]] || ! "$VENV_DIR/bin/python3" -m pip --version &>/dev/null; then
    echo "==> (Re)creating virtual environment with uv (Python 3.12)..."
    rm -rf "$VENV_DIR"
    uv venv "$VENV_DIR" --python 3.12 --seed
fi

PYTHON="$VENV_DIR/bin/python3"

# ── Python dependencies ───────────────────────────────────────────────────────
"$PYTHON" -m pip install --upgrade pip --quiet
"$PYTHON" -m pip install -r "$SCRIPT_DIR/requirements.txt" --quiet

# ── Launch ────────────────────────────────────────────────────────────────────
echo "==> Starting mother..."
exec "$PYTHON" "$SCRIPT_DIR/main.py"
