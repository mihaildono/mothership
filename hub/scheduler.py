"""
scheduler.py — enforces the hub's work-time window using APScheduler.
Starts Ollama + WebSocket connection at work_start; shuts everything down at work_end.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import HubConfig
import ollama_runner
import ws_client

logger = logging.getLogger(__name__)

_ws_task: asyncio.Task | None = None
_status: str = "offline"


async def _on_status(status: str) -> None:
    global _status
    _status = status


async def _start_work(cfg: HubConfig) -> None:
    global _ws_task
    logger.info("Work window started — launching Ollama and connecting...")
    await ollama_runner.start()
    _ws_task = asyncio.create_task(ws_client.run(cfg, _on_status))


async def _stop_work() -> None:
    global _ws_task
    logger.info("Work window ended — shutting down...")
    if _ws_task and not _ws_task.done():
        _ws_task.cancel()
        try:
            await _ws_task
        except asyncio.CancelledError:
            pass
    _ws_task = None
    await ollama_runner.stop()
    logger.info("Hub offline")


def _parse_cron(time_str: str) -> tuple[int, int]:
    """Parse "HH:MM" into (hour, minute)."""
    h, m = time_str.split(":")
    return int(h), int(m)


def build(cfg: HubConfig) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()

    start_h, start_m = _parse_cron(cfg.work_start)
    end_h, end_m = _parse_cron(cfg.work_end)

    scheduler.add_job(
        _start_work,
        CronTrigger(hour=start_h, minute=start_m),
        args=[cfg],
        id="work_start",
    )
    scheduler.add_job(
        _stop_work,
        CronTrigger(hour=end_h, minute=end_m),
        id="work_stop",
    )
    return scheduler


def is_within_window(cfg: HubConfig) -> bool:
    """Return True if the current local time falls inside the work window."""
    now = datetime.now().time()
    start_h, start_m = _parse_cron(cfg.work_start)
    end_h, end_m = _parse_cron(cfg.work_end)

    from datetime import time

    start = time(start_h, start_m)
    end = time(end_h, end_m)

    if start <= end:
        return start <= now < end
    # overnight window (e.g. 22:00 → 05:00)
    return now >= start or now < end
