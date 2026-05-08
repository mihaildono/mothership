# -*- coding: utf-8 -*-
"""
main.py — hub agent entry point.

Usage:
    source .venv/bin/activate
    python main.py

# ── Orchestrator / Nebula mode (disabled for now) ─────────────────────────────
# When the orchestrator and Nebula overlay are ready, replace the standalone
# HTTP server below with the scheduler + WebSocket client:
#
# import ws_client
# import scheduler as sched_module
#
# async def main() -> None:
#     cfg = cfg_loader.load()
#     scheduler = sched_module.build(cfg)
#     scheduler.start()
#     if sched_module.is_within_window(cfg):
#         await ollama_runner.start()
#         ws_task = asyncio.create_task(ws_client.run(cfg, sched_module._on_status))
#     try:
#         while True:
#             await asyncio.sleep(60)
#     except (KeyboardInterrupt, asyncio.CancelledError):
#         scheduler.shutdown(wait=False)
#         await ollama_runner.stop()
# ─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import config as cfg_loader
import ollama_runner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("hub")


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = cfg_loader.load()
    app.state.cfg = cfg
    logger.info("Hub '%s' starting — launching Ollama...", cfg.hub_id)
    await ollama_runner.start()
    logger.info("Ollama ready. Listening for requests.")
    yield
    logger.info("Shutting down Ollama...")
    await ollama_runner.stop()


app = FastAPI(title="Mothership Hub", lifespan=lifespan)


class PromptRequest(BaseModel):
    prompt: str


@app.post("/run")
async def run_prompt(body: PromptRequest) -> JSONResponse:
    cfg = app.state.cfg
    logger.info("Received prompt (%d chars)", len(body.prompt))
    result = await ollama_runner.run_inference(
        prompt=body.prompt,
        model=cfg.ollama.model,
        host=cfg.ollama.host,
    )
    return JSONResponse({"result": result})


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8766, reload=False)
