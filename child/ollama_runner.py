"""
ollama_runner.py — start/stop the Ollama server process and run inference.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from typing import AsyncIterator

import httpx

_process: subprocess.Popen | None = None


# Resolve ollama binary at import time — covers Homebrew (Intel + Apple Silicon),
# system installs, and anything on PATH.
def _find_ollama() -> str:
    found = shutil.which("ollama")
    if found:
        return found
    for candidate in (
        "/opt/homebrew/bin/ollama",
        "/usr/local/bin/ollama",
        "/usr/bin/ollama",
    ):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    raise FileNotFoundError(
        "ollama binary not found. Install it from https://ollama.ai or brew install ollama"
    )


_OLLAMA_BIN = _find_ollama()


async def start() -> None:
    """Launch `ollama serve` as a background subprocess."""
    global _process
    if _process and _process.poll() is None:
        return  # already running
    _process = subprocess.Popen(
        [_OLLAMA_BIN, "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Give the server a moment to be ready
    await _wait_ready()


async def stop() -> None:
    """Terminate the Ollama subprocess."""
    global _process
    if _process and _process.poll() is None:
        _process.terminate()
        try:
            _process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _process.kill()
    _process = None


async def is_model_available(model: str, host: str = "http://localhost:11434") -> bool:
    """Return True if the model is already pulled in the local Ollama instance."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{host}/api/tags")
            r.raise_for_status()
            names = [m["name"] for m in r.json().get("models", [])]
            base = model.split(":")[0].lower()
            return any(base in n.lower() for n in names)
    except Exception:
        return False


async def ensure_model(model: str, host: str = "http://localhost:11434") -> None:
    """Pull the model if it is not already available locally."""
    if await is_model_available(model, host):
        return
    import logging

    logger = logging.getLogger("ollama_runner")
    logger.info("Pulling model '%s' — this may take a while...", model)
    proc = await asyncio.create_subprocess_exec(
        _OLLAMA_BIN,
        "pull",
        model,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"Failed to pull model '{model}': {stdout.decode()[:500]}")
    logger.info("Model '%s' pulled successfully.", model)


async def run_inference(prompt: str, model: str, host: str) -> str:
    """Send a prompt to Ollama and return the full response text."""
    url = f"{host}/api/generate"
    async with httpx.AsyncClient(timeout=300) as client:
        response = await client.post(
            url,
            json={"model": model, "prompt": prompt, "stream": False},
        )
        response.raise_for_status()
        return response.json()["response"]


async def _wait_ready(host: str = "http://localhost:11434", retries: int = 20) -> None:
    """Poll until Ollama HTTP server responds."""
    async with httpx.AsyncClient() as client:
        for _ in range(retries):
            try:
                r = await client.get(f"{host}/api/tags", timeout=2)
                if r.status_code == 200:
                    return
            except httpx.TransportError:
                pass
            await asyncio.sleep(1)
    raise RuntimeError("Ollama did not start in time")
