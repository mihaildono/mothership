#!/usr/bin/env bash
# nebula/setup-mother.sh — Fully automated Nebula setup for the MOTHER machine.
#
# What this does:
#   1. Downloads the Nebula binary (no manual install needed)
#   2. Creates the CA and signs certs for mother + all children
#   3. Installs certs + config into /etc/nebula/
#   4. Starts Nebula (and optionally registers it as a boot service)
#   5. Creates a self-contained bundle for each child (drop-and-run tarball)
#
# Usage:
#   chmod +x setup-mother.sh
#   ./setup-mother.sh <YOUR_PUBLIC_IP>
#
# Example:
#   ./setup-mother.sh 203.0.113.42
#
# To add more children later, re-run with --children:
#   ./setup-mother.sh 203.0.113.42 --children child-001,child-002,child-003

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
# Fetch latest Nebula version tag at runtime
NEBULA_VERSION="$(curl -fsSL --max-time 10 https://api.github.com/repos/slackhq/nebula/releases/latest | python3 -c "import sys,json; print(json.load(sys.stdin)['tag_name'])" 2>/dev/null || echo 'v1.10.3')"
OVERLAY_SUBNET="10.10.0.0/24"
MOTHER_IP="10.10.0.1"
BASE_CHILD_IP="10.10.0"   # children get .2, .3, .4 ...
NEBULA_PORT=4242

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="$SCRIPT_DIR/bin"
CERTS_DIR="$SCRIPT_DIR/certs"
BUNDLES_DIR="$SCRIPT_DIR/bundles"
NEBULA_ETC="/etc/nebula"

# ── Args ──────────────────────────────────────────────────────────────────────
PUBLIC_IP="${1:-}"
CHILDREN="child-001"

for arg in "$@"; do
    if [[ "$arg" == --children=* ]]; then
        CHILDREN="${arg#--children=}"
    elif [[ "$arg" == --children ]]; then
        shift; CHILDREN="${1:-child-001}"
    fi
done

if [[ -z "$PUBLIC_IP" ]]; then
    echo "==> No PUBLIC_IP provided — detecting automatically..."
    PUBLIC_IP="$(curl -fsSL --max-time 5 https://api.ipify.org 2>/dev/null || true)"
    if [[ -z "$PUBLIC_IP" ]]; then
        echo ""
        echo "  Could not auto-detect public IP."
        echo "  Usage: $0 <PUBLIC_IP> [--children child-001,child-002,...]"
        echo ""
        echo "  Run 'curl https://api.ipify.org' to find your public IP."
        exit 1
    fi
    echo "    Detected public IP: $PUBLIC_IP"
    echo ""
fi

echo "==> Mothership Nebula setup"
echo "    Public IP  : $PUBLIC_IP"
echo "    Children   : $CHILDREN"
echo "    Nebula port: $NEBULA_PORT"
echo ""

# ── Detect OS / arch ──────────────────────────────────────────────────────────
# macOS ships a single universal zip; Linux is split by arch
detect_download() {
    local os arch
    os="$(uname -s | tr '[:upper:]' '[:lower:]')"
    arch="$(uname -m)"

    case "$os" in
        darwin)
            echo "nebula-darwin.zip" "zip"
            return
            ;;
        linux)  ;;
        *)
            echo "Unsupported OS: $os" >&2
            exit 1
        ;;
    esac

    case "$arch" in
        x86_64|amd64)  echo "nebula-linux-amd64.tar.gz" "tgz" ;;
        arm64|aarch64) echo "nebula-linux-arm64.tar.gz" "tgz" ;;
        *)
            echo "Unsupported architecture: $arch" >&2
            exit 1
        ;;
    esac
}

read -r ASSET FORMAT <<< "$(detect_download)"
DOWNLOAD_URL="https://github.com/slackhq/nebula/releases/download/${NEBULA_VERSION}/${ASSET}"

# ── Download Nebula binaries ──────────────────────────────────────────────────
mkdir -p "$BIN_DIR"

if [[ -x "$BIN_DIR/nebula" && -x "$BIN_DIR/nebula-cert" ]]; then
    echo "==> Nebula binaries already downloaded — skipping."
else
    echo "==> Downloading Nebula ${NEBULA_VERSION} (${ASSET})..."
    TMP_DL="$(mktemp -d)"
    curl -fsSL "$DOWNLOAD_URL" -o "$TMP_DL/$ASSET"
    if [[ "$FORMAT" == "zip" ]]; then
        unzip -q "$TMP_DL/$ASSET" -d "$TMP_DL"
    else
        tar -xz -C "$TMP_DL" -f "$TMP_DL/$ASSET"
    fi
    install -m 755 "$TMP_DL/nebula"      "$BIN_DIR/nebula"
    install -m 755 "$TMP_DL/nebula-cert" "$BIN_DIR/nebula-cert"
    rm -rf "$TMP_DL"
    echo "    Done."
fi

NEBULA="$BIN_DIR/nebula"
NEBULA_CERT="$BIN_DIR/nebula-cert"

# ── Generate CA ───────────────────────────────────────────────────────────────
mkdir -p "$CERTS_DIR"

if [[ -f "$CERTS_DIR/ca.crt" ]]; then
    echo "==> CA already exists — skipping CA creation."
else
    echo "==> Creating Certificate Authority..."
    "$NEBULA_CERT" ca -name "mothership" \
        -out-crt "$CERTS_DIR/ca.crt" \
        -out-key "$CERTS_DIR/ca.key"
    echo "    CA created."
fi

# ── Sign mother cert ──────────────────────────────────────────────────────────
if [[ -f "$CERTS_DIR/mother.crt" ]]; then
    echo "==> mother.crt already exists — skipping."
else
    echo "==> Signing certificate for mother ($MOTHER_IP)..."
    "$NEBULA_CERT" sign \
        -ca-crt "$CERTS_DIR/ca.crt" \
        -ca-key "$CERTS_DIR/ca.key" \
        -name "mother" \
        -ip "${MOTHER_IP}/24" \
        -out-crt "$CERTS_DIR/mother.crt" \
        -out-key "$CERTS_DIR/mother.key"
fi

# ── Sign child certs + build bundles ─────────────────────────────────────────
mkdir -p "$BUNDLES_DIR"

IFS=',' read -ra CHILD_LIST <<< "$CHILDREN"
CHILD_INDEX=2   # .2, .3, .4 ...

# ── Generate / load MOTHER_API_KEY ────────────────────────────────────────────
MOTHER_ENV="$SCRIPT_DIR/../mother/.env"

if [[ -f "$MOTHER_ENV" ]] && grep -q "MOTHER_API_KEY=" "$MOTHER_ENV"; then
    MOTHER_API_KEY="$(grep "^MOTHER_API_KEY=" "$MOTHER_ENV" | cut -d= -f2)"
    echo "==> Existing MOTHER_API_KEY loaded from mother/.env"
else
    MOTHER_API_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
    echo "MOTHER_API_KEY=${MOTHER_API_KEY}" >> "$MOTHER_ENV"
    chmod 600 "$MOTHER_ENV"
    echo "==> Generated MOTHER_API_KEY → saved to mother/.env"
fi

for child_id in "${CHILD_LIST[@]}"; do
    child_ip="${BASE_CHILD_IP}.${CHILD_INDEX}"

    # ── Generate / load per-child auth token ──────────────────────────────────
    if grep -q "# child:${child_id}:token=" "$MOTHER_ENV" 2>/dev/null; then
        CHILD_AUTH_TOKEN="$(grep "^# child:${child_id}:token=" "$MOTHER_ENV" | sed 's/^.*token=//')"
        echo "==> Existing auth token for ${child_id} loaded."
    else
        CHILD_AUTH_TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
        echo "" >> "$MOTHER_ENV"
        echo "# child:${child_id}: auth token — delete this line to revoke" >> "$MOTHER_ENV"
        echo "# child:${child_id}:token=${CHILD_AUTH_TOKEN}" >> "$MOTHER_ENV"
        echo "==> Generated auth token for ${child_id} → saved to mother/.env"
    fi

    # ── Rebuild MOTHER_CHILD_TOKENS line in .env ──────────────────────────────
    # Collect all child tokens from .env and rebuild the env var
    ALL_TOKENS="$(grep "^# child:.*:token=" "$MOTHER_ENV" | sed 's/^# child:\(.*\):token=\(.*\)/\1=\2/' | paste -sd ',' -)"
    if grep -q "^MOTHER_CHILD_TOKENS=" "$MOTHER_ENV"; then
        # Update existing line
        python3 -c "
import re, sys
content = open('$MOTHER_ENV').read()
content = re.sub(r'^MOTHER_CHILD_TOKENS=.*$', 'MOTHER_CHILD_TOKENS=${ALL_TOKENS}', content, flags=re.MULTILINE)
open('$MOTHER_ENV', 'w').write(content)
"
    else
        echo "MOTHER_CHILD_TOKENS=${ALL_TOKENS}" >> "$MOTHER_ENV"
    fi

    if [[ -f "$CERTS_DIR/${child_id}.crt" ]]; then
        echo "==> ${child_id}.crt already exists — skipping cert signing."
    else
        echo "==> Signing certificate for ${child_id} (${child_ip})..."
        "$NEBULA_CERT" sign \
            -ca-crt "$CERTS_DIR/ca.crt" \
            -ca-key "$CERTS_DIR/ca.key" \
            -name "$child_id" \
            -ip "${child_ip}/24" \
            -out-crt "$CERTS_DIR/${child_id}.crt" \
            -out-key "$CERTS_DIR/${child_id}.key"
    fi

    # ── Build child bundle ────────────────────────────────────────────────────
    BUNDLE_STAGING="$BUNDLES_DIR/${child_id}"
    rm -rf "$BUNDLE_STAGING"
    mkdir -p "$BUNDLE_STAGING/nebula" "$BUNDLE_STAGING/agent"

    # Nebula certs + config + binary
    cp "$CERTS_DIR/ca.crt"           "$BUNDLE_STAGING/nebula/"
    cp "$CERTS_DIR/${child_id}.crt"  "$BUNDLE_STAGING/nebula/node.crt"
    cp "$CERTS_DIR/${child_id}.key"  "$BUNDLE_STAGING/nebula/node.key"
    cp "$NEBULA"                     "$BUNDLE_STAGING/nebula/nebula"

    cat > "$BUNDLE_STAGING/nebula/config.yml" << YAMLEOF
pki:
  ca: /etc/nebula/ca.crt
  cert: /etc/nebula/node.crt
  key: /etc/nebula/node.key

static_host_map:
  "${MOTHER_IP}": ["${PUBLIC_IP}:${NEBULA_PORT}"]

lighthouse:
  am_lighthouse: false
  interval: 60
  hosts:
    - "${MOTHER_IP}"

listen:
  host: 0.0.0.0
  port: ${NEBULA_PORT}

punchy:
  punch: true

tun:
  disabled: false
  dev: nebula1
  drop_local_broadcast: false
  drop_multicast: false
  tx_queue: 500
  mtu: 1300

firewall:
  outbound:
    - port: any
      proto: any
      host: any
  inbound:
    - port: any
      proto: icmp
      host: any

logging:
  level: info
  format: text
YAMLEOF

    # Copy child agent code (excluding venv, pycache, config)
    CHILD_SRC="$SCRIPT_DIR/../child"
    rsync -a --exclude='.venv' --exclude='__pycache__' --exclude='*.pyc' \
              --exclude='config.toml' "$CHILD_SRC/" "$BUNDLE_STAGING/agent/"

    # Write pre-filled config.toml
    cat > "$BUNDLE_STAGING/agent/config.toml" << TOMLEOF
child_id   = "${child_id}"
auth_token = "${CHILD_AUTH_TOKEN}"
work_start = "00:00"
work_end   = "23:59"

[mother]
host    = "${MOTHER_IP}"
ws_port = 8765

[ollama]
model = "gemma3:4b"
host  = "http://localhost:11434"
TOMLEOF
    chmod 600 "$BUNDLE_STAGING/agent/config.toml"

    # ── Generate install.sh ───────────────────────────────────────────────────
    cat > "$BUNDLE_STAGING/install.sh" << 'INSTALLEOF'
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
INSTALLEOF

    chmod +x "$BUNDLE_STAGING/install.sh"

    # Pack the bundle
    BUNDLE_FILE="$BUNDLES_DIR/${child_id}.tar.gz"
    tar -czf "$BUNDLE_FILE" -C "$BUNDLES_DIR" "$child_id"
    rm -rf "$BUNDLE_STAGING"

    # Generate a one-time download token valid for 10 minutes
    TOKEN_FILE="$BUNDLES_DIR/${child_id}.token"
    python3 - "$TOKEN_FILE" << 'PYEOF'
import sys, json, secrets, time
data = {"token": secrets.token_hex(24), "expires_at": time.time() + 600}
with open(sys.argv[1], "w") as f:
    json.dump(data, f)
PYEOF
    chmod 600 "$TOKEN_FILE"

    echo "    Bundle created: $BUNDLE_FILE"
    CHILD_INDEX=$((CHILD_INDEX + 1))
done

# ── Install Nebula on mother ──────────────────────────────────────────────────
echo ""
echo "==> Installing Nebula on mother..."
sudo install -m 755 "$NEBULA" /usr/local/bin/nebula

sudo mkdir -p "$NEBULA_ETC"
sudo install -m 644 "$CERTS_DIR/ca.crt"     "$NEBULA_ETC/ca.crt"
sudo install -m 600 "$CERTS_DIR/mother.crt" "$NEBULA_ETC/mother.crt"
sudo install -m 600 "$CERTS_DIR/mother.key" "$NEBULA_ETC/mother.key"

# Write mother config to /etc/nebula/config.yml
sudo tee "$NEBULA_ETC/config.yml" > /dev/null << YAMLEOF
pki:
  ca: /etc/nebula/ca.crt
  cert: /etc/nebula/mother.crt
  key: /etc/nebula/mother.key

static_host_map: {}

lighthouse:
  am_lighthouse: true
  interval: 60

listen:
  host: 0.0.0.0
  port: ${NEBULA_PORT}

punchy:
  punch: true

tun:
  disabled: false
  dev: nebula1
  drop_local_broadcast: false
  drop_multicast: false
  tx_queue: 500
  mtu: 1300

firewall:
  outbound:
    - port: any
      proto: any
      host: any
  inbound:
    - port: any
      proto: icmp
      host: any
    - port: 8765
      proto: tcp
      host: any

logging:
  level: info
  format: text
YAMLEOF

# ── Start Nebula on mother ─────────────────────────────────────────────────────
echo "==> Starting Nebula on mother..."
OS="$(uname -s | tr '[:upper:]' '[:lower:]')"

if [[ "$OS" == "darwin" ]]; then
    PLIST=/Library/LaunchDaemons/com.mothership.nebula.plist
    sudo tee "$PLIST" > /dev/null << PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.mothership.nebula</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/nebula</string>
        <string>-config</string>
        <string>/etc/nebula/config.yml</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardErrorPath</key><string>/var/log/nebula.log</string>
    <key>StandardOutPath</key><string>/var/log/nebula.log</string>
</dict>
</plist>
PLISTEOF
    sudo launchctl load "$PLIST" 2>/dev/null || sudo launchctl kickstart -k "system/com.mothership.nebula"

elif command -v systemctl &>/dev/null; then
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
    sudo systemctl daemon-reload
    sudo systemctl enable --now nebula

else
    sudo nohup /usr/local/bin/nebula -config "$NEBULA_ETC/config.yml" > /var/log/nebula.log 2>&1 &
    echo "  Nebula started in background. Logs: /var/log/nebula.log"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "=========================================="
echo "  Mother setup complete!"
echo "=========================================="
echo ""
echo "  Nebula is running. Mother overlay IP: $MOTHER_IP"
echo ""
echo "  On each child machine, run this ONE command:"
echo ""

CHILD_INDEX=2
IFS=',' read -ra CHILD_LIST <<< "$CHILDREN"
for child_id in "${CHILD_LIST[@]}"; do
    child_ip="${BASE_CHILD_IP}.${CHILD_INDEX}"
    TOKEN="$(python3 -c "import json; print(json.load(open('$BUNDLES_DIR/${child_id}.token'))['token'])")"
    echo "  $child_id ($child_ip):"
    echo "    curl -fsSL \"http://${PUBLIC_IP}:8765/bundle/${child_id}?token=${TOKEN}\" -o ${child_id}.tar.gz && tar -xzf ${child_id}.tar.gz && cd ${child_id} && ./install.sh"
    echo ""
    CHILD_INDEX=$((CHILD_INDEX + 1))
done

echo "  Make sure UDP port $NEBULA_PORT is open on this machine's firewall/router."
echo "  Make sure TCP port 8765 is reachable from child machines (needed to fetch the bundle)."
echo ""
