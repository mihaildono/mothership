# Plan: Mothership — Distributed AI Hub Orchestration System

## TL;DR
Build a distributed system where multiple "hub" PCs each run Ollama+Gemma and are
managed by a single lightweight "orchestrator" PC. Users submit tasks to the
orchestrator, which queues them and dispatches to available hubs based on their
configured work-time windows. Results are persisted on the orchestrator.
Secure connectivity uses **Nebula** (single static binary, zero install, Win/Mac/Linux) as an encrypted overlay network + **WebSockets** for real-time bidirectional messaging. Stack: Python, FastAPI, SQLite, APScheduler, Ollama Python SDK.

---

## Architecture Overview

```
User CLI/API
     │ HTTP REST
     ▼
┌─────────────┐   WebSocket (over Nebula overlay)   ┌──────────────┐
│ Orchestrator│◄──────────────────────────────────►│  Hub Agent   │
│  (FastAPI)  │                                     │  (Python)    │
│  SQLite DB  │           ┌───────────┐             │  Ollama+Gemma│
│  nebula     │◄─────────►│Lighthouse │◄───────────►│  nebula      │
└─────────────┘           │ (VPS/orch)│             └──────────────┘
                          └───────────┘              (N of these)
```

**How Nebula works:**
- Nebula is a single static Go binary (`nebula` + `nebula-cert`) — no OS install, download and run. Works on Windows, macOS, Linux (x64/ARM).
- A CA is created once by the orchestrator admin: `nebula-cert ca -name "mothership"`
- Each node (orchestrator + every hub) gets a certificate signed by the CA and a virtual IP on a private overlay subnet (e.g. `10.10.0.0/24`)
- A **lighthouse** with a public IP bootstraps peer discovery and NAT traversal. Two options:
  - The orchestrator itself, if it has a public/static IP
  - A minimal VPS (any $3–5/mo instance) running only the nebula binary
- Once Nebula is running on both sides, the hub's WebSocket client connects directly to the orchestrator's Nebula IP: `ws://10.10.0.1:8765/ws/hub` — all traffic is encrypted end-to-end with Noise protocol (same as WireGuard)
- No port-forwarding, no tunnel subprocesses to manage in Python code — Nebula handles reconnection and NAT traversal automatically

**Why WebSockets over raw sockets or HTTP polling:**
- Persistent bidirectional connection — orchestrator can push tasks to hubs
- Hub connects outbound (firewall-friendly)
- FastAPI has native WebSocket support; works directly over Nebula IPs

---

## Project Structure

```
mothership/
├── orchestrator/
│   ├── main.py           # FastAPI app entry point + WS hub endpoint
│   ├── api.py            # User-facing REST API (submit task, get result)
│   ├── dispatcher.py     # Task routing: pick available hub, send via WS
│   ├── hub_manager.py    # Hub registry: track connected WS clients + status
│   ├── storage.py        # SQLite: tasks, results, hub configs
│   └── models.py         # Pydantic models: Task, Hub, Result, Message
├── hub/
│   ├── main.py           # Entry point: connect to orchestrator WS
│   ├── scheduler.py      # APScheduler: enforce work-time window
│   ├── ollama_runner.py  # Start/stop Ollama process + run inference
│   ├── ws_client.py      # Persistent WebSocket client with reconnect
│   └── config.py         # Config loader: orchestrator Nebula IP, ws port, work hours, auth token
├── nebula/
│   ├── README.md         # Setup instructions: CA creation, signing node certs
│   ├── orchestrator.yml  # Example Nebula config for orchestrator / lighthouse
│   └── hub.yml           # Example Nebula config template for a hub node
├── shared/
│   └── schemas.py        # Shared JSON message schemas (task request/response)
├── README.md
├── requirements-orchestrator.txt
└── requirements-hub.txt
```

---

## Phase 1 — Shared Foundations

**Steps (all parallel):**
1. Define `shared/schemas.py` — JSON message schemas for all WS messages:
   - `TASK_REQUEST`: `{type, task_id, payload, submitted_at}`
   - `TASK_RESULT`: `{type, task_id, hub_id, result, completed_at}`
   - `HUB_STATUS`: `{type, hub_id, status: "idle"|"busy"|"offline"}`
   - `HUB_REGISTER`: `{type, hub_id, auth_token}`
2. Define `orchestrator/models.py` — SQLite table-mapped Pydantic models
3. Write `orchestrator/storage.py` — SQLite via `aiosqlite`:
   - `tasks` table: `(id, payload, status, assigned_hub, submitted_at, completed_at)`
   - `results` table: `(task_id, hub_id, result_text, stored_at)`
   - `hubs` table: `(hub_id, last_seen, work_start, work_end)`
4. Write `hub/config.py` — loads from `config.toml`:
   - `orchestrator_nebula_ip` — orchestrator's Nebula overlay IP (e.g. `10.10.0.1`)
   - `orchestrator_ws_port` (default 8765)
   - `hub_id`, `auth_token`, `work_start`, `work_end`

---

## Phase 2 — Orchestrator

**Steps (sequential):**
5. Write `orchestrator/hub_manager.py`:
   - In-memory dict of `hub_id → {websocket, status, work_hours}`
   - Methods: `register(hub_id, ws)`, `set_status(hub_id, status)`, `get_available()` (checks current time against work window), `disconnect(hub_id)`
6. Write `orchestrator/dispatcher.py`:
   - `dispatch_pending_tasks()`: query SQLite for `status=pending` tasks, call `hub_manager.get_available()`, send `TASK_REQUEST` over WS
   - Background asyncio task running every 5 seconds
   - If no hub available, tasks remain queued (no drop, no timeout by default)
7. Write `orchestrator/main.py`:
   - FastAPI app with:
     - `WebSocket /ws/hub` endpoint: authenticate token, register hub, listen for `HUB_STATUS` and `TASK_RESULT` messages
     - Mount `api.py` router
   - On startup: init SQLite, start dispatcher background task
8. Write `orchestrator/api.py` — REST endpoints:
   - `POST /tasks` — submit a new task, persist to SQLite, return `task_id`
   - `GET /tasks/{task_id}` — check status + result
   - `GET /tasks` — list all tasks with statuses
   - `GET /hubs` — list registered hubs and their current status

---

## Phase 3 — Hub Agent

**Steps (sequential):**
9. Write `hub/ollama_runner.py`:
   - `start_ollama()`: subprocess `ollama serve`
   - `stop_ollama()`: kill the process
   - `run_inference(prompt)`: call Ollama HTTP API (`localhost:11434`) with `gemma` model, return text
   - Use `httpx` async client for inference calls
11. Write `hub/scheduler.py`:
    - APScheduler `AsyncIOScheduler`
    - On schedule start time → `ollama_runner.start_ollama()`, connect WS, set status `idle`
    - On schedule end time → finish current task, set status `offline`, disconnect WS, `ollama_runner.stop_ollama()`
    - Nebula runs as a persistent OS-level process (separate from the hub agent) — no start/stop needed in scheduler
12. Write `hub/ws_client.py`:
    - Connect to `ws://<orchestrator_nebula_ip>:<ws_port>/ws/hub` with `Authorization: Bearer <auth_token>` header
    - Send `HUB_REGISTER` on connect
    - Listen loop: on `TASK_REQUEST` → run inference → send `TASK_RESULT`
    - During inference: send `HUB_STATUS busy` before, `HUB_STATUS idle` after
    - Auto-reconnect with exponential backoff on disconnect
13. Write `hub/main.py`:
    - Wire scheduler + ws_client together
    - If current time is within work window at startup: immediately start Ollama and connect

---

## Phase 4 — Security

14. Authentication: each hub has a pre-shared `auth_token` (UUID) in `config.toml`
    - Orchestrator validates token on WS handshake (HTTP header `Authorization: Bearer <token>`)
    - Tokens stored in orchestrator's SQLite `hubs` table
    - No hub can register without a valid token
15. Transport security: Nebula encrypts all traffic with the **Noise protocol** (same cryptographic foundation as WireGuard). Certificate management:
    - **One-time CA setup** (run on orchestrator admin machine):
      ```
      nebula-cert ca -name "mothership"
      nebula-cert sign -name "orchestrator" -ip "10.10.0.1/24"
      nebula-cert sign -name "hub-001" -ip "10.10.0.2/24"
      ```
    - Distribute `ca.crt` + the node's `<name>.crt` / `<name>.key` to each machine
    - Nodes can only join the overlay if their cert is signed by the CA — no unsigned node can connect
    - Nebula config files live in `nebula/` directory; example configs provided for orchestrator and hub
16. Input sanitisation: validate all task payloads with Pydantic on the orchestrator boundary before queuing. Reject oversized payloads (configurable max length, default 10,000 chars).

---

## Phase 5 — CLI for Users

17. Write a simple `cli.py` (uses `httpx`) with commands:
    - `python cli.py submit "Create a LinkedIn post about AI trends"`
    - `python cli.py status <task_id>`
    - `python cli.py results` — list completed tasks
    - `python cli.py hubs` — show connected hubs and their status

---

## Relevant Files (all new)

- `orchestrator/main.py` — FastAPI app, WS endpoint, startup
- `orchestrator/api.py` — REST task API
- `orchestrator/dispatcher.py` — background task routing loop
- `orchestrator/hub_manager.py` — in-memory hub registry
- `orchestrator/storage.py` — aiosqlite persistence
- `orchestrator/models.py` — Pydantic + SQLite models
- `hub/main.py` — hub entry point
- `hub/scheduler.py` — APScheduler work-time control
- `hub/ollama_runner.py` — Ollama process + inference
- `hub/ws_client.py` — WebSocket client with reconnect
- `hub/config.py` — TOML config loader
- `nebula/orchestrator.yml` — Nebula config for orchestrator/lighthouse
- `nebula/hub.yml` — Nebula config template for hub nodes
- `nebula/README.md` — CA setup and node cert signing instructions
- `shared/schemas.py` — shared message schemas
- `cli.py` — user CLI

---

## Verification

1. Run orchestrator locally: `uvicorn orchestrator.main:app --reload` — confirm startup, SQLite created
2. Run hub agent with a test config pointing to `localhost` — confirm it registers and appears in `GET /hubs`
3. Submit a test task via CLI: `python cli.py submit "Write a tweet about cats"` — confirm task appears in DB with `status=pending`
4. Confirm hub receives task, runs Ollama inference (gemma model), returns result
5. Confirm result is stored in orchestrator SQLite and retrievable via `GET /tasks/{id}`
6. Test work-time window: set window 1 minute ahead, confirm hub connects and Ollama starts automatically, then stops at end of window
7. Test reconnect: kill and restart hub, confirm it re-registers and picks up pending tasks
8. Test auth: attempt WS connection with wrong token, confirm 403 rejection

---

## Decisions

- **Nebula** preferred over SSH tunnels: no tunnel subprocess to manage in Python, built-in NAT traversal via lighthouse, automatic reconnection, certificate-based access control. Single static binary — no OS-level install on Win/Mac/Linux.
- **WebSockets** preferred over raw TCP sockets: easier to work with in Python async, built into FastAPI; connects directly to orchestrator's Nebula IP
- **SQLite** (not Redis/Postgres): keeps orchestrator zero-dependency and lightweight; sufficient for this use case
- **APScheduler** for work-time windows: more reliable than cron on Windows/Mac
- **Gemma via Ollama**: hub uses `ollama pull gemma` before first run; model selection is configurable in `config.toml`
- Hubs pull tasks (via WS push from orchestrator) — orchestrator never makes outbound connections to hubs
- Scope excludes: web UI (CLI only), multi-model support per hub (single model per hub config), task priorities (FIFO only)

---

## Further Considerations

1. **Nebula onboarding UX**: The one-time setup steps (run `nebula-cert` to sign a node cert, distribute files) are the only manual steps per hub. Consider a `python cli.py register-hub <hub_id>` command that runs `nebula-cert sign` automatically and outputs the files to copy to the hub. The `nebula/README.md` should include copy-paste commands for each OS.
2. **Hub overload protection**: If a hub is already busy and receives a second task push (race condition), the hub should respond with a `HUB_STATUS busy` reject and the orchestrator should re-queue. This edge case should be handled in dispatcher retry logic.
3. **Result delivery UX**: Since tasks take time, consider adding a webhook/callback URL field to `POST /tasks` so the orchestrator can notify the user when done, rather than requiring polling.
