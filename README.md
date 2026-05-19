# Mothership

A distributed AI hub where a single **mother** machine orchestrates multiple **child** machines, each running a local Ollama model. Tasks are submitted to the mother via REST API and dispatched to available children over an encrypted peer-to-peer overlay network (Nebula).

```
You / manage.py
    │  HTTP REST  (X-API-Key)
    ▼
┌──────────────┐   WebSocket over Nebula tunnel   ┌─────────────────┐
│   Mother     │◄────────────────────────────────►│  Child (×N)     │
│  FastAPI     │                                  │  Ollama (auto)  │
│  Port 8765   │                                  │  Port 8766      │
└──────────────┘                                  └─────────────────┘
       ▲
  Nebula lighthouse
  UDP 4242
```

---

## Requirements

| Machine | Requirements |
|---------|-------------|
| Mother  | macOS or Linux, Python 3.12+, public or LAN IP |
| Child   | macOS or Linux, Python 3.12+, 8 GB+ RAM (for Gemma) |

---

## Mother setup

### 1. Clone the repo

```bash
git clone <your-repo-url> mothership
cd mothership
```

### 2. Run the setup script

```bash
cd nebula
./setup-mother.sh
```

This will:
- Auto-detect your public IP (or pass it explicitly: `./setup-mother.sh 203.0.113.42`)
- Download the Nebula binary
- Generate a Certificate Authority and sign certificates
- Generate `mother/.env` with `MOTHER_API_KEY` and child auth tokens
- Start Nebula as a background service (launchd on macOS, systemd on Linux)
- Create a self-contained bundle per child in `nebula/bundles/`

For multiple children:

```bash
./setup-mother.sh --children child-001,child-002,child-003
```

### 3. Start the mother server

```bash
python3 manage.py mother start
```

The server starts on port **8765**.
Make sure **TCP 8765** and **UDP 4242** are reachable from child machines (open in firewall / forwarded on your router if behind NAT).

---

## Child setup — one command

After `setup-mother.sh` finishes it prints a ready-to-run command per child.
Copy it and run it on the child machine:

```bash
curl -fsSL "http://<MOTHER_PUBLIC_IP>:8765/bundle/child-001?token=<TOKEN>" \
  -o child-001.tar.gz \
  && tar -xzf child-001.tar.gz \
  && cd child-001 \
  && ./install.sh
```

`install.sh` does everything automatically:
1. Installs the Nebula binary (downloads correct arch if needed)
2. Installs certs + Nebula config into `/etc/nebula/`
3. Starts Nebula as a system service
4. Installs the child agent to `~/mothership-child/`
5. Sets up Python environment (via `uv`)
6. Installs and pulls the Ollama model
7. Registers the agent as a boot service

> **The download token is one-time use and expires after 10 minutes.**
> Regenerate it with `python3 manage.py token retoken child-001` if it expires.

### Start / stop the child agent

**macOS:**
```bash
launchctl start com.mothership.child   # start
launchctl stop  com.mothership.child   # stop
```

**Linux:**
```bash
sudo systemctl start mothership-child  # start
sudo systemctl stop  mothership-child  # stop
sudo systemctl status mothership-child # status
```

### View child logs

**macOS:**
```bash
tail -f ~/mothership-child/child.log
```

**Linux:**
```bash
journalctl -fu mothership-child
```

### Local dev (same machine)

```bash
python3 manage.py child start          # start child agent
python3 manage.py child stop           # stop it
python3 manage.py child detect-model   # run whichllm without starting
python3 manage.py child logs -n 100    # tail logs
```

---

## Networking

### Same local network (LAN)

Pass your LAN IP to the setup script:

```bash
./setup-mother.sh 192.168.1.50
```

Children use the same LAN IP in their bundle.
No port-forwarding needed — Nebula handles peer discovery on the local network.

### Different networks (internet)

Pass your public IP:

```bash
./setup-mother.sh 203.0.113.42
```

Open these ports on the mother's router/firewall:

| Port | Protocol | Purpose |
|------|----------|---------|
| 4242 | UDP | Nebula peer discovery + tunnel |
| 8765 | TCP | Mother REST API + bundle download |

Children connect outbound — no ports need to be open on child machines.

---

## manage.py — control CLI

`manage.py` is a zero-dependency Python CLI that works on macOS, Linux, and Windows.

```bash
# Mother
python3 manage.py mother start           # start orchestrator
python3 manage.py mother stop            # stop it
python3 manage.py mother status          # show connected children + models

# Child (local dev)
python3 manage.py child start            # start local child agent
python3 manage.py child stop             # stop it
python3 manage.py child detect-model     # run whichllm hardware detection
python3 manage.py child logs -n 100      # tail logs

# Child naming
python3 manage.py child list                          # list all named children
python3 manage.py child name child-001 "Gaming PC"    # set a display name
python3 manage.py child rename child-001 "Beast Box"  # rename
python3 manage.py child remove child-001              # remove + revoke token

# Setup
python3 manage.py nebula setup           # full mother + Nebula + bundle setup

# Bundles
python3 manage.py bundle list            # list available bundles

# Tokens
python3 manage.py token list             # list all child tokens
python3 manage.py token add child-002    # add a new child
python3 manage.py token revoke child-001 # revoke access immediately
python3 manage.py token retoken child-001 # regenerate token + download link

# Send a prompt (blocks until result)
python3 manage.py send child-001 "Explain quantum computing"
python3 manage.py send child-001 "Hello" --timeout 60 --host 10.10.0.1
```

> **Names** are stored in `mother/child-names.json`. They are display-only — renaming never touches Nebula certs or the child's config.
> Restart the mother after naming or renaming for the change to appear in `mother status`.

---

## API reference

All endpoints (except `/bundle`) require the `X-API-Key` header.
The key is in `mother/.env` as `MOTHER_API_KEY`.

### Check connected children

```bash
curl http://localhost:8765/children \
  -H "X-API-Key: <MOTHER_API_KEY>"
```

Example response:
```json
[
  {
    "child_id": "child-001",
    "status": "idle",
    "model": "qwen3:14b",
    "connected_at": "2026-05-14T14:00:00",
    "last_ping_ms": 3.2
  }
]
```

### Submit a task

```bash
curl -X POST http://localhost:8765/send \
  -H "X-API-Key: <MOTHER_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{
    "child_id": "child-001",
    "task_id":  "task-42",
    "prompt":   "Write a LinkedIn post about distributed AI"
  }'
```

Response:
```json
{"queued": true, "task_id": "task-42"}
```

### Poll for result

```bash
curl http://localhost:8765/result/task-42 \
  -H "X-API-Key: <MOTHER_API_KEY>"
```

Returns `404` while the task is still running. When done:
```json
{
  "child_id": "child-001",
  "result":   "The future of AI is distributed...",
  "error":    null
}
```

---

## Token management

All token operations work from the repo root via `manage.py` (or directly via `nebula/manage-child.sh` on bash systems).

### List all children

```bash
python3 manage.py token list
```

### Add a new child

```bash
python3 manage.py token add child-002
```

Generates a Nebula cert, an auth token, and a bundle with a one-time download link.
Restart the mother to apply the new token.

### Revoke a child immediately

```bash
python3 manage.py token revoke child-001
```

Removes the auth token from `mother/.env` and deletes its bundle.
Restart the mother — the child will be rejected on its next reconnect attempt.

```bash
python3 manage.py mother start
```

### Rotate a child's auth token

```bash
python3 manage.py token retoken child-001
```

Generates a new auth token + a fresh 10-minute bundle download link.
Prints the new one-liner to send to the child operator.
Restart the mother to apply.

---

## Security model

| Layer | Mechanism |
|-------|-----------|
| Transport | Nebula (Noise protocol — same crypto as WireGuard). All traffic encrypted end-to-end. |
| REST API | `X-API-Key` header — 64-char random hex, required on all endpoints |
| Child registration | `auth_token` — 64-char random hex per child, validated on WebSocket connect |
| Bundle download | One-time token, expires in 10 minutes, deleted on first use |
| Prompt injection | Prompt size capped at 10,000 characters |
| Path traversal | `child_id` and `task_id` validated to alphanumeric + `-_` only |

Keys are stored in `mother/.env` (gitignored). Never commit this file.

---

## Project structure

```
mothership/
├── manage.py              # Cross-platform control CLI (macOS/Linux/Windows)
│
├── mother/
│   ├── main.py            # FastAPI server — WS endpoint + REST API
│   ├── child_registry.py  # In-memory registry of connected children (+ model)
│   ├── health.py          # PING/PONG health-check loop
│   ├── requirements.txt
│   ├── start.sh           # Start the mother (loads .env automatically)
│   ├── setup.sh           # One-time dependency setup
│   └── .env               # Generated by setup-mother.sh — KEEP SECRET
│
├── child/
│   ├── main.py            # FastAPI + lifespan: starts Ollama + WS client
│   ├── ws_client.py       # Persistent WebSocket client with reconnect
│   ├── ollama_runner.py   # Start/stop Ollama subprocess + run inference
│   ├── model_detector.py  # whichllm hardware detection + HF→Ollama mapping
│   ├── config.py          # TOML config loader + update_model()
│   ├── config.toml        # Generated by setup — KEEP SECRET
│   ├── config.toml.example
│   ├── requirements.txt
│   └── start.sh
│
├── nebula/
│   ├── setup-mother.sh    # One-command mother setup (certs + keys + bundles)
│   ├── manage-child.sh    # Add / revoke / rotate child tokens
│   ├── mother.yml         # Nebula config template for mother
│   ├── child.yml          # Nebula config template for child
│   ├── README.md          # Nebula-specific setup details
│   ├── certs/             # CA + node certs (gitignored)
│   ├── bundles/           # Child bundles + tokens (gitignored)
│   └── bin/               # Downloaded Nebula binaries (gitignored)
```

---

## Message protocol (WebSocket)

| Type | Direction | Description |
|------|-----------|-------------|
| `CHILD_REGISTER` | child → mother | First message on connect. Includes `child_id`, `auth_token`, and `model`. |
| `CHILD_STATUS` | child → mother | Status update: `idle` or `busy` |
| `TASK_REQUEST` | mother → child | `{type, task_id, payload}` |
| `TASK_RESULT` | child → mother | `{type, task_id, result, error}` |
| `PING` | mother → child | Health check sent every 60 seconds |
| `PONG` | child → mother | Response to PING |
