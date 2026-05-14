"""
ws_client.py — persistent WebSocket client that connects to the mother,
processes TASK_REQUEST messages, and sends back TASK_RESULT messages.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Callable, Awaitable

import websockets
from websockets.exceptions import ConnectionClosed

from config import ChildConfig
import ollama_runner

logger = logging.getLogger(__name__)

# Callback type: receives (status: str) to notify the rest of the agent
StatusCallback = Callable[[str], Awaitable[None]]

_INITIAL_BACKOFF = 2  # seconds
_MAX_BACKOFF = 60  # seconds


async def run(cfg: ChildConfig, on_status: StatusCallback) -> None:
    """Connect to the mother and maintain the connection indefinitely."""
    backoff = _INITIAL_BACKOFF
    headers = {"Authorization": f"Bearer {cfg.auth_token}"}

    while True:
        try:
            async with websockets.connect(
                cfg.mother.ws_url, additional_headers=headers
            ) as ws:
                logger.info("Connected to mother at %s", cfg.mother.ws_url)
                backoff = _INITIAL_BACKOFF  # reset on successful connect

                # Register this child
                await ws.send(
                    json.dumps(
                        {
                            "type": "CHILD_REGISTER",
                            "child_id": cfg.child_id,
                            "auth_token": cfg.auth_token,
                        }
                    )
                )
                await on_status("idle")

                async for raw in ws:
                    message = json.loads(raw)
                    await _handle_message(ws, cfg, message, on_status)

        except ConnectionClosed as e:
            logger.warning("WebSocket closed: %s — reconnecting in %ss", e, backoff)
        except OSError as e:
            logger.warning("Connection failed: %s — reconnecting in %ss", e, backoff)

        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, _MAX_BACKOFF)


async def _handle_message(
    ws, cfg: ChildConfig, message: dict, on_status: StatusCallback
) -> None:
    msg_type = message.get("type")

    if msg_type == "PING":
        await ws.send(json.dumps({"type": "PONG", "child_id": cfg.child_id}))
        logger.debug("PING → PONG")
        return

    if msg_type != "TASK_REQUEST":
        return

    task_id = message["task_id"]
    payload = message["payload"]
    logger.info("Received task %s", task_id)

    # Signal busy before starting inference
    await ws.send(
        json.dumps({"type": "CHILD_STATUS", "child_id": cfg.child_id, "status": "busy"})
    )
    await on_status("busy")

    try:
        result = await ollama_runner.run_inference(
            prompt=payload,
            model=cfg.ollama.model,
            host=cfg.ollama.host,
        )
        await ws.send(
            json.dumps(
                {
                    "type": "TASK_RESULT",
                    "task_id": task_id,
                    "child_id": cfg.child_id,
                    "result": result,
                }
            )
        )
        logger.info("Completed task %s", task_id)
    except Exception as e:
        logger.error("Inference failed for task %s: %s", task_id, e)
        await ws.send(
            json.dumps(
                {
                    "type": "TASK_RESULT",
                    "task_id": task_id,
                    "child_id": cfg.child_id,
                    "result": None,
                    "error": str(e),
                }
            )
        )
    finally:
        await ws.send(
            json.dumps(
                {"type": "CHILD_STATUS", "child_id": cfg.child_id, "status": "idle"}
            )
        )
        await on_status("idle")
