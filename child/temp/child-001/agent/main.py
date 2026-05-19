# -*- coding: utf-8 -*-
"""
main.py — child agent entry point.

Usage:
    ./start.sh
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
import model_detector
import ollama_runner
import ws_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("child")

_ws_task: asyncio.Task | None = None


async def _on_status(status: str) -> None:
    pass  # status changes are sent over WS; nothing extra needed here


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _ws_task
    cfg = cfg_loader.load()
    app.state.cfg = cfg

    # Auto-detect best model for this hardware if the configured model
    # is not yet pulled locally (first run or placeholder value).
    detected = await asyncio.get_event_loop().run_in_executor(
        None, lambda: model_detector.detect(ollama_bin=ollama_runner._OLLAMA_BIN)
    )
    if detected:
        if not await ollama_runner.is_model_available(cfg.ollama.model):
            logger.info(
                "Configured model '%s' not found locally — using detected model '%s'",
                cfg.ollama.model,
                detected.ollama_name,
            )
            cfg_loader.update_model(detected.ollama_name)
            cfg = cfg_loader.load()
            app.state.cfg = cfg
        else:
            logger.info(
                "Configured model '%s' is available. "
                "(Best for this hardware: %s, score %.1f)",
                cfg.ollama.model,
                detected.ollama_name,
                detected.score,
            )

    logger.info("Child '%s' starting — launching Ollama...", cfg.child_id)
    await ollama_runner.start()
    await ollama_runner.ensure_model(cfg.ollama.model)
    logger.info("Ollama ready. Connecting to mother at %s...", cfg.mother.ws_url)
    _ws_task = asyncio.create_task(ws_client.run(cfg, _on_status))
    yield
    if _ws_task:
        _ws_task.cancel()
        try:
            await _ws_task
        except asyncio.CancelledError:
            pass
    logger.info("Shutting down Ollama...")
    await ollama_runner.stop()


app = FastAPI(title="Mothership Child", lifespan=lifespan)


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
