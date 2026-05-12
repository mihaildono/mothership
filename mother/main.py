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
import json
import logging
from contextlib import asynccontextmanager

import uvicorn
import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from child_registry import ChildRegistry
import health as health_module

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("mother")

registry = ChildRegistry()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.results = {}
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
        await registry.register(child_id, ws)

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


@app.get("/children")
async def list_children() -> JSONResponse:
    return JSONResponse(registry.snapshot())


class SendRequest(BaseModel):
    child_id: str
    task_id: str
    prompt: str


@app.post("/send")
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


@app.get("/result/{task_id}")
async def get_result(task_id: str) -> JSONResponse:
    result = app.state.results.get(task_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Result not ready yet")
    return JSONResponse(result)


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8765, reload=False)
