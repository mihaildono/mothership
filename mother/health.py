"""
health.py — periodic health-check loop.

Every PING_INTERVAL seconds the mother sends a PING to every connected child
and waits for a PONG. Children that miss PING_TIMEOUT consecutive pings are
marked offline and removed from the registry.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from child_registry import ChildRegistry

logger = logging.getLogger(__name__)

PING_INTERVAL = 60  # seconds between health checks
PING_TIMEOUT = 5  # seconds to wait for a PONG
MAX_MISSED = 2  # consecutive missed pings before declaring offline

_missed: dict[str, int] = {}  # child_id → consecutive missed ping count


async def run(registry: ChildRegistry) -> None:
    """Background loop — runs forever, call as asyncio.create_task()."""
    while True:
        await asyncio.sleep(PING_INTERVAL)
        children = registry.all()
        if not children:
            continue

        await asyncio.gather(
            *[_ping_child(registry, entry) for entry in children],
            return_exceptions=True,
        )


async def _ping_child(registry: ChildRegistry, entry) -> None:
    child_id = entry.child_id
    ws = entry.ws
    ping_payload = json.dumps({"type": "PING"})

    try:
        sent_at = time.monotonic()
        await asyncio.wait_for(ws.send_text(ping_payload), timeout=PING_TIMEOUT)
        latency_ms = (time.monotonic() - sent_at) * 1000
        await registry.record_pong(child_id, latency_ms)
        _missed[child_id] = 0
        logger.debug("PING → %s  (%.1f ms)", child_id, latency_ms)
    except Exception as e:
        _missed[child_id] = _missed.get(child_id, 0) + 1
        logger.warning(
            "PING failed for %s (%s) — missed: %d/%d",
            child_id,
            e,
            _missed[child_id],
            MAX_MISSED,
        )
        if _missed[child_id] >= MAX_MISSED:
            logger.error("Child %s unreachable — removing from registry", child_id)
            await registry.remove(child_id)
            _missed.pop(child_id, None)
