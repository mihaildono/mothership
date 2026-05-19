#!/usr/bin/env bash
# install.sh — One-command Mothership child setup.
# Installs Nebula, sets up the agent, and registers both as boot services.
set -euo pipefail

BUNDLE_DIR="$(cd "$(dirname "$0")" && pwd)"
NEBULA_ETC="/etc/nebula"
NEBULA_BIN="/usr/local/bin/nebula"
AGENT_DIR="$HOME/mothership-child"
OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
ARCH="$(uname -m)"

echo "==> Mothership child setup"
echo ""

# ── 1. Install Nebula ─────────────────────────────────────────────────────────
BUNDLED_BINARY="$BUNDLE_DIR/nebula/nebula"
NEED_DOWNLOAD=false
if [[ -x "$BUNDLED_BINARY" ]] && "$BUNDLED_BINARY" --version &>/dev/null 2>&1; then
    INSTALL_SOURCE="$BUNDLED_BINARY"
else
    echo "  Bundled binary not compatible — downloading correct version..."
    NEED_DOWNLOAD=true
fi

if [[ "$NEED_DOWNLOAD" == true ]]; then
    NEBULA_VERSION="$(curl -fsSL --max-time 10 https://api.github.com/repos/slackhq/nebula/releases/latest \
        | python3 -c "import sys,json; print(json.load(sys.stdin)['tag_name'])" 2>/dev/null || echo 'v1.10.3')"
    TMP_DIR="$(mktemp -d)"
    case "$OS" in
        darwin)
            curl -fsSL "https://github.com/slackhq/nebula/releases/download/${NEBULA_VERSION}/nebula-darwin.zip" \
                -o "$TMP_DIR/nebula.zip"
            unzip -q "$TMP_DIR/nebula.zip" -d "$TMP_DIR"
            ;;
        linux)
            case "$ARCH" in
                x86_64|amd64)  ASSET="nebula-linux-amd64.tar.gz"  ;;
                arm64|aarch64) ASSET="nebula-linux-arm64.tar.gz"  ;;
                *) echo "Unsupported arch: $ARCH" >&2; exit 1 ;;
            esac
            curl -fsSL "https://github.com/slackhq/nebula/releases/download/${NEBULA_VERSION}/${ASSET}" \
                | tar -xz -C "$TMP_DIR"
            ;;
        *) echo "Unsupported OS: $OS" >&2; exit 1 ;;
    esac
    INSTALL_SOURCE="$TMP_DIR/nebula"
fi

echo "==> Installing Nebula..."
sudo install -m 755 "$INSTALL_SOURCE" "$NEBULA_BIN"
sudo mkdir -p "$NEBULA_ETC"
sudo install -m 644 "$BUNDLE_DIR/nebula/ca.crt"       "$NEBULA_ETC/ca.crt"
sudo install -m 600 "$BUNDLE_DIR/nebula/node.crt"     "$NEBULA_ETC/node.crt"
sudo install -m 600 "$BUNDLE_DIR/nebula/node.key"     "$NEBULA_ETC/node.key"
sudo install -m 644 "$BUNDLE_DIR/nebula/config.yml"   "$NEBULA_ETC/config.yml"

# ── 2. Install agent ──────────────────────────────────────────────────────────
echo "==> Installing child agent to $AGENT_DIR..."
mkdir -p "$AGENT_DIR"
cp -r "$BUNDLE_DIR/agent/." "$AGENT_DIR/"

# ── 3. Install uv + venv + deps ───────────────────────────────────────────────
echo "==> Setting up Python environment..."
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv &>/dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    source "$HOME/.local/bin/env"
fi

# Install Ollama if missing
if ! command -v ollama &>/dev/null; then
    echo "==> Installing Ollama..."
    if [[ "$OS" == "darwin" ]]; then
        if command -v brew &>/dev/null; then
            brew install ollama
        else
            echo "  Install Homebrew first: https://brew.sh" >&2
            exit 1
        fi
    else
        curl -fsSL https://ollama.ai/install.sh | sh
    fi
fi

VENV="$AGENT_DIR/.venv"
uv venv "$VENV" --python 3.12 --seed
"$VENV/bin/python3" -m pip install --upgrade pip --quiet
"$VENV/bin/python3" -m pip install -r "$AGENT_DIR/requirements.txt" --quiet

# Auto-detect best model for this hardware using whichllm, then pull it.
# Falls back to the model already written in config.toml if detection fails.
echo "==> Detecting best model for this hardware..."
# Capture whichllm output to a temp file to avoid pipe+heredoc stdin conflict.
_WL_TMP="$(mktemp)"
"$VENV/bin/whichllm" --json --top 5 > "$_WL_TMP" 2>/dev/null || true

DETECTED_MODEL=""
if [[ -s "$_WL_TMP" ]]; then
    DETECTED_MODEL="$("$VENV/bin/python3" << PYEOF
import json, re, pathlib, sys

FAMILY_MAP = [
    (r"qwen3\.6",     "qwen3.6"),
    (r"qwen3",        "qwen3"),
    (r"qwen2\.5",     "qwen2.5"),
    (r"qwen2",        "qwen2"),
    (r"qwen",         "qwen"),
    (r"llama-?4",     "llama4"),
    (r"llama-?3\.3",  "llama3.3"),
    (r"llama-?3\.2",  "llama3.2"),
    (r"llama-?3\.1",  "llama3.1"),
    (r"llama-?3",     "llama3"),
    (r"llama-?2",     "llama2"),
    (r"mistral-nemo", "mistral-nemo"),
    (r"mistral",      "mistral"),
    (r"mixtral",      "mixtral"),
    (r"phi-?4",       "phi4"),
    (r"phi-?3\.5",    "phi3.5"),
    (r"phi-?3",       "phi3"),
    (r"gemma-?3",     "gemma3"),
    (r"gemma-?2",     "gemma2"),
    (r"gemma",        "gemma"),
    (r"deepseek-r2",  "deepseek-r2"),
    (r"deepseek-r1",  "deepseek-r1"),
    (r"deepseek-v3",  "deepseek-v3"),
    (r"deepseek-v2",  "deepseek-v2"),
    (r"deepseek",     "deepseek"),
    (r"command-?r",   "command-r"),
]

def param_tag(s):
    m = re.search(r"(\d+\.?\d*)[xX](\d+)[bB]", s)
    if m: return f"{m.group(1)}x{m.group(2)}b"
    m = re.search(r"(\d+\.?\d*)[bB]", s)
    return f"{m.group(1)}b" if m else None

def hf_to_ollama(model_id):
    s = model_id.lower()
    if "/" in s: s = s.split("/", 1)[1]
    for suf in ("-instruct","-chat","-hf","-gguf","-awq","-gptq","-bf16","-fp16","-it","-base"):
        s = s.replace(suf, "")
    tag = param_tag(s)
    for pat, base in FAMILY_MAP:
        if re.search(pat, s):
            return f"{base}:{tag}" if tag else base
    return None

try:
    data = json.loads(pathlib.Path("$_WL_TMP").read_text())
    for m in data.get("models", []):
        name = hf_to_ollama(m.get("model_id", ""))
        if name:
            print(name)
            sys.exit(0)
except Exception:
    pass
PYEOF
    )"
fi
rm -f "$_WL_TMP"

if [[ -n "$DETECTED_MODEL" ]]; then
    echo "==> Best model for this hardware: $DETECTED_MODEL"
    # Update config.toml with the detected model
    "$VENV/bin/python3" -c "
import re, pathlib
p = pathlib.Path('$AGENT_DIR/config.toml')
text = p.read_text()
new = re.sub(r'(?m)(^\s*model\s*=\s*)\"[^\"]*\"', r'\g<1>\"$DETECTED_MODEL\"', text)
p.write_text(new)
"
    MODEL="$DETECTED_MODEL"
else
    # Fall back to the model in config.toml (written during bundle creation)
    MODEL="$("$VENV/bin/python3" -c "
import sys
try:
    import tomllib
except ImportError:
    import tomli as tomllib
with open('$AGENT_DIR/config.toml', 'rb') as f:
    cfg = tomllib.load(f)
print(cfg.get('ollama', {}).get('model', 'gemma3:4b'))
")"
    echo "==> whichllm detection skipped — using model: $MODEL"
fi

echo "==> Pulling model: $MODEL (this may take a while)..."
ollama pull "$MODEL" || echo "  Warning: model pull failed — you can retry: ollama pull $MODEL"

# ── 4. Install as a service ───────────────────────────────────────────────────
echo "==> Installing services..."
LAUNCH_CMD="$VENV/bin/python3 $AGENT_DIR/main.py"

if [[ "$OS" == "darwin" ]]; then
    # Nebula service
    sudo tee /Library/LaunchDaemons/com.mothership.nebula.plist > /dev/null << PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
    <key>Label</key><string>com.mothership.nebula</string>
    <key>ProgramArguments</key><array>
        <string>/usr/local/bin/nebula</string><string>-config</string><string>/etc/nebula/config.yml</string>
    </array>
    <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
    <key>StandardErrorPath</key><string>/var/log/nebula.log</string>
    <key>StandardOutPath</key><string>/var/log/nebula.log</string>
</dict></plist>
PLISTEOF
    sudo launchctl load /Library/LaunchDaemons/com.mothership.nebula.plist 2>/dev/null \
        || sudo launchctl kickstart -k system/com.mothership.nebula

    # Agent service (user launchd, no sudo)
    mkdir -p "$HOME/Library/LaunchAgents"
    tee "$HOME/Library/LaunchAgents/com.mothership.child.plist" > /dev/null << PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
    <key>Label</key><string>com.mothership.child</string>
    <key>ProgramArguments</key><array>
        <string>$VENV/bin/python3</string><string>$AGENT_DIR/main.py</string>
    </array>
    <key>WorkingDirectory</key><string>$AGENT_DIR</string>
    <key>EnvironmentVariables</key><dict>
        <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
    <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
    <key>StandardErrorPath</key><string>$AGENT_DIR/child.log</string>
    <key>StandardOutPath</key><string>$AGENT_DIR/child.log</string>
</dict></plist>
PLISTEOF
    launchctl load "$HOME/Library/LaunchAgents/com.mothership.child.plist" 2>/dev/null \
        || launchctl kickstart -k "gui/$(id -u)/com.mothership.child"

elif command -v systemctl &>/dev/null; then
    # Nebula
    sudo tee /etc/systemd/system/nebula.service > /dev/null << SVCEOF
[Unit]
Description=Nebula overlay network
After=network.target
[Service]
ExecStart=/usr/local/bin/nebula -config /etc/nebula/config.yml
Restart=always
[Install]
WantedBy=multi-user.target
SVCEOF

    # Agent
    sudo tee /etc/systemd/system/mothership-child.service > /dev/null << SVCEOF
[Unit]
Description=Mothership child agent
After=network.target nebula.service
Wants=nebula.service
[Service]
ExecStart=$LAUNCH_CMD
WorkingDirectory=$AGENT_DIR
Restart=always
User=$(whoami)
[Install]
WantedBy=multi-user.target
SVCEOF
    sudo systemctl daemon-reload
    sudo systemctl enable --now nebula mothership-child
fi

# ── 5. Summary ────────────────────────────────────────────────────────────────
echo ""
echo "==========================================="
echo "  Child setup complete!"
echo "==========================================="
echo ""
if [[ "$OS" == "darwin" ]]; then
    echo "  Start:  launchctl start com.mothership.child"
    echo "  Stop:   launchctl stop com.mothership.child"
    echo "  Logs:   tail -f $AGENT_DIR/child.log"
else
    echo "  Start:  sudo systemctl start mothership-child"
    echo "  Stop:   sudo systemctl stop mothership-child"
    echo "  Logs:   journalctl -fu mothership-child"
fi
echo ""
