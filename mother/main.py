"""
main.py — mother entry point.

Runs two things:
  1. FastAPI HTTP + WebSocket server
       WS  /ws/child  — children connect here
       GET /children  — see who is connected + their status
       POST /send     — send a raw prompt to a specific child (for testing)

  2. Background health-check loop (health.py)

Usage:
    source .venv/bin/activate
    python main.py
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
import websockets
from fastapi import (
    FastAPI,
    WebSocket,
    WebSocketDisconnect,
    HTTPException,
    Query,
    Security,
    Depends,
)
from fastapi.responses import JSONResponse, FileResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, field_validator

from child_registry import ChildRegistry
import health as health_module

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("mother")

registry = ChildRegistry()

# ── API key auth for REST endpoints ──────────────────────────────────────────
# Set MOTHER_API_KEY env var before starting. If unset, REST endpoints are
# only accessible from localhost (enforced below).
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

_MOTHER_API_KEY = os.environ.get("MOTHER_API_KEY", "")


def _require_api_key(key: str | None = Security(_api_key_header)) -> None:
    if not _MOTHER_API_KEY:
        raise HTTPException(
            status_code=500, detail="Server misconfigured: MOTHER_API_KEY not set"
        )
    if not key or not secrets.compare_digest(key, _MOTHER_API_KEY):
        raise HTTPException(status_code=403, detail="Invalid or missing API key")


# ── Auth token registry for children ─────────────────────────────────────────
# Maps child_id → expected auth_token (hex string).
# Loaded from MOTHER_CHILD_TOKENS env var: "child-001=token1,child-002=token2"
def _load_child_tokens() -> dict[str, str]:
    raw = os.environ.get("MOTHER_CHILD_TOKENS", "")
    if not raw:
        return {}
    result: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if "=" in pair:
            cid, tok = pair.split("=", 1)
            result[cid.strip()] = tok.strip()
    return result


_CHILD_TOKENS: dict[str, str] = _load_child_tokens()

# ── Display name registry ─────────────────────────────────────────────────────
_NAMES_FILE = Path(__file__).parent / "child-names.json"


def _load_names() -> dict[str, str]:
    """Load child_id → display name mapping from child-names.json."""
    if not _NAMES_FILE.exists():
        return {}
    try:
        import json as _json

        return _json.loads(_NAMES_FILE.read_text())
    except Exception:
        return {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.results = {}
    if not _MOTHER_API_KEY:
        raise RuntimeError("MOTHER_API_KEY env var is required but not set")
    if not _CHILD_TOKENS:
        logger.warning("MOTHER_CHILD_TOKENS not set — no children can register!")
    logger.info("Mother starting up...")
    health_task = asyncio.create_task(health_module.run(registry))
    yield
    health_task.cancel()
    try:
        await health_task
    except asyncio.CancelledError:
        pass
    logger.info("Mother shut down.")


app = FastAPI(title="Mothership — Mother", lifespan=lifespan)


# ── WebSocket endpoint ────────────────────────────────────────────────────────


@app.websocket("/ws/child")
async def ws_child(ws: WebSocket):
    await ws.accept()
    child_id: str | None = None

    try:
        # First message must be CHILD_REGISTER
        raw = await asyncio.wait_for(ws.receive_text(), timeout=10)
        msg = json.loads(raw)

        if msg.get("type") != "CHILD_REGISTER" or not msg.get("child_id"):
            await ws.close(code=4001, reason="First message must be CHILD_REGISTER")
            return

        child_id = msg["child_id"]

        # Validate child_id format to prevent injection
        if not child_id.replace("-", "").replace("_", "").isalnum():
            await ws.close(code=4001, reason="Invalid child_id")
            return

        # Validate auth_token if we have a registry
        if _CHILD_TOKENS:
            supplied = msg.get("auth_token", "")
            expected = _CHILD_TOKENS.get(child_id, "")
            if not expected or not secrets.compare_digest(supplied, expected):
                logger.warning("Auth failed for child '%s' — rejecting", child_id)
                await ws.close(code=4003, reason="Invalid auth_token")
                return

        await registry.register(
            child_id,
            ws,
            model=msg.get("model", "unknown"),
            name=_load_names().get(child_id, child_id),
        )

        # Message loop
        async for raw in ws.iter_text():
            msg = json.loads(raw)
            await _handle_message(child_id, msg)

    except WebSocketDisconnect:
        pass
    except asyncio.TimeoutError:
        logger.warning("Child did not register in time — closing connection")
        await ws.close(code=4002, reason="Registration timeout")
    except Exception as e:
        logger.error("Unexpected error with child %s: %s", child_id, e)
    finally:
        if child_id:
            await registry.remove(child_id)


async def _handle_message(child_id: str, msg: dict) -> None:
    msg_type = msg.get("type")

    if msg_type == "PONG":
        # Health-check response — latency already recorded by health.py on send side.
        # Here we just log it; registry.record_pong is called from health.py.
        logger.debug("PONG from %s", child_id)

    elif msg_type == "CHILD_STATUS":
        status = msg.get("status", "idle")
        await registry.set_status(child_id, status)
        logger.info("Status update: %s → %s", child_id, status)

    elif msg_type == "TASK_RESULT":
        task_id = msg.get("task_id")
        result = msg.get("result")
        error = msg.get("error")
        if error:
            logger.error("Task %s failed on %s: %s", task_id, child_id, error)
        else:
            logger.info("Task %s result from %s: %.120s...", task_id, child_id, result)
        # Store in app state for polling by /send endpoint
        app.state.results[task_id] = {
            "child_id": child_id,
            "result": result,
            "error": error,
        }

    else:
        logger.debug("Unknown message type '%s' from %s", msg_type, child_id)


# ── REST endpoints ────────────────────────────────────────────────────────────


@app.get("/children", dependencies=[Depends(_require_api_key)])
async def list_children() -> JSONResponse:
    return JSONResponse(registry.snapshot())


_MAX_PROMPT_LEN = 10_000  # characters


class SendRequest(BaseModel):
    child_id: str
    task_id: str
    prompt: str

    @field_validator("prompt")
    @classmethod
    def prompt_length(cls, v: str) -> str:
        if len(v) > _MAX_PROMPT_LEN:
            raise ValueError(
                f"Prompt exceeds maximum length of {_MAX_PROMPT_LEN} characters"
            )
        return v

    @field_validator("child_id", "task_id")
    @classmethod
    def safe_id(cls, v: str) -> str:
        if not v.replace("-", "").replace("_", "").isalnum():
            raise ValueError("Invalid characters in id field")
        return v


@app.post("/send", dependencies=[Depends(_require_api_key)])
async def send_to_child(body: SendRequest) -> JSONResponse:
    entry = registry.get(body.child_id)
    if not entry:
        raise HTTPException(
            status_code=404, detail=f"Child '{body.child_id}' not connected"
        )

    payload = json.dumps(
        {
            "type": "TASK_REQUEST",
            "task_id": body.task_id,
            "payload": body.prompt,
        }
    )
    await entry.ws.send_text(payload)
    logger.info("Sent task %s to %s", body.task_id, body.child_id)
    return JSONResponse({"queued": True, "task_id": body.task_id})


@app.get("/result/{task_id}", dependencies=[Depends(_require_api_key)])
async def get_result(task_id: str) -> JSONResponse:
    result = app.state.results.get(task_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Result not ready yet")
    return JSONResponse(result)


# ── Bundle distribution ───────────────────────────────────────────────────────
# Serves pre-built child bundles for one-command child setup.
# Protected by a per-child token stored in nebula/bundles/<child_id>.token

_BUNDLES_DIR = Path(__file__).parent.parent / "nebula" / "bundles"


@app.get("/bundle/{child_id}")
async def get_bundle(child_id: str, token: str = Query(...)) -> FileResponse:
    # Validate child_id to prevent path traversal
    if not child_id.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(status_code=400, detail="Invalid child_id")

    token_file = _BUNDLES_DIR / f"{child_id}.token"
    bundle_file = _BUNDLES_DIR / f"{child_id}.tar.gz"

    if not bundle_file.exists():
        raise HTTPException(status_code=404, detail=f"No bundle found for '{child_id}'")

    if not token_file.exists():
        raise HTTPException(
            status_code=403, detail="Bundle token not configured or already used"
        )

    try:
        import json as _json

        token_data = _json.loads(token_file.read_text())
        expected = token_data["token"]
        expires_at = float(token_data["expires_at"])
    except Exception:
        raise HTTPException(status_code=403, detail="Malformed token file")

    if time.time() > expires_at:
        token_file.unlink(missing_ok=True)
        raise HTTPException(status_code=403, detail="Bundle token has expired")

    if not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=403, detail="Invalid token")

    # One-time use — delete token immediately before serving
    token_file.unlink(missing_ok=True)
    logger.info("Bundle served for %s — token invalidated", child_id)

    return FileResponse(
        path=bundle_file,
        filename=f"{child_id}.tar.gz",
        media_type="application/gzip",
    )


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8765, reload=False)
