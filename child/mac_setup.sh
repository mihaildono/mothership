#!/usr/bin/env bash
# Hub setup script — macOS
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Mothership hub setup (macOS)"

# ── Homebrew ─────────────────────────────────────────────────────────────────
if ! command -v brew &>/dev/null; then
    echo "==> Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

    # Add brew to PATH for Apple Silicon
    if [[ -f /opt/homebrew/bin/brew ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
else
    echo "==> Homebrew already installed — upgrading packages..."
    brew update
    brew upgrade
fi

# ── Python ────────────────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "==> Installing Python..."
    brew install python
else
    PYTHON_VERSION=$(python3 --version 2>&1)
    echo "==> Python already installed ($PYTHON_VERSION) — skipping"
fi

# ── Ollama ────────────────────────────────────────────────────────────────────
if ! command -v ollama &>/dev/null; then
    echo "==> Installing Ollama..."
    brew install ollama
else
    OLLAMA_VERSION=$(ollama --version 2>&1)
    echo "==> Ollama already installed ($OLLAMA_VERSION) — checking for updates..."
    brew upgrade ollama || true
fi

# ── Python virtual environment ────────────────────────────────────────────────
VENV_DIR="$SCRIPT_DIR/.venv"
if [[ ! -d "$VENV_DIR" ]]; then
    echo "==> Creating virtual environment at child/.venv ..."
    "$(brew --prefix python3)/bin/python3" -m venv "$VENV_DIR"
else
    echo "==> Virtual environment already exists — skipping"
fi

PYTHON="$VENV_DIR/bin/python3"

echo "==> Installing Python dependencies..."
"$PYTHON" -m pip install --upgrade pip --quiet
"$PYTHON" -m pip install -r "$SCRIPT_DIR/requirements.txt"

# ── Gemma model ───────────────────────────────────────────────────────────────
# Read model name from config.toml if present, fall back to gemma
MODEL="gemma4:e2b"
if [[ -f "$SCRIPT_DIR/config.toml" ]]; then
    PARSED=$(grep -E '^model\s*=' "$SCRIPT_DIR/config.toml" | head -1 | cut -d'"' -f2)
    [[ -n "$PARSED" ]] && MODEL="$PARSED"
fi

echo "==> Pulling Ollama model: $MODEL (this may take a while)..."
ollama pull "$MODEL"

echo ""
echo "==> Setup complete."
