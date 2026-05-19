"""
child_registry.py — in-memory registry of connected child nodes.

Tracks each child's WebSocket connection, status, and last heartbeat time.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from websockets import WebSocketServerProtocol

logger = logging.getLogger(__name__)


@dataclass
class ChildEntry:
    child_id: str
    ws: "WebSocketServerProtocol"
    status: str = "idle"  # idle | busy | offline
    model: str = "unknown"  # Ollama model reported at registration
    name: str = ""  # human-friendly display name (from mother/child-names.json)
    connected_at: datetime = field(default_factory=datetime.utcnow)
    last_seen: datetime = field(default_factory=datetime.utcnow)
    last_ping_ms: float | None = None  # round-trip latency of last health check


class ChildRegistry:
    def __init__(self) -> None:
        self._children: dict[str, ChildEntry] = {}
        self._lock = asyncio.Lock()

    async def register(
        self,
        child_id: str,
        ws: "WebSocketServerProtocol",
        model: str = "unknown",
        name: str = "",
    ) -> None:
        async with self._lock:
            self._children[child_id] = ChildEntry(
                child_id=child_id, ws=ws, model=model, name=name or child_id
            )
        logger.info(
            "Child registered: %s  (total active: %d)", child_id, len(self._children)
        )

    async def remove(self, child_id: str) -> None:
        async with self._lock:
            self._children.pop(child_id, None)
        logger.info(
            "Child disconnected: %s  (total active: %d)", child_id, len(self._children)
        )

    async def set_status(self, child_id: str, status: str) -> None:
        async with self._lock:
            if child_id in self._children:
                self._children[child_id].status = status
                self._children[child_id].last_seen = datetime.utcnow()

    async def record_pong(self, child_id: str, latency_ms: float) -> None:
        async with self._lock:
            if child_id in self._children:
                self._children[child_id].last_seen = datetime.utcnow()
                self._children[child_id].last_ping_ms = latency_ms

    def snapshot(self) -> list[dict]:
        """Return a serialisable list of all connected children."""
        return [
            {
                "child_id": e.child_id,
                "name": e.name,
                "status": e.status,
                "model": e.model,
                "connected_at": e.connected_at.isoformat(),
                "last_seen": e.last_seen.isoformat(),
                "last_ping_ms": e.last_ping_ms,
            }
            for e in self._children.values()
        ]

    def get(self, child_id: str) -> ChildEntry | None:
        return self._children.get(child_id)

    def all(self) -> list[ChildEntry]:
        return list(self._children.values())
