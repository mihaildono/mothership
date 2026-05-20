"""
database.py — SQLite persistence layer for the mother.

Tables
──────
  children        — one row per known child (upserted on connect)
  events          — audit log: connect, disconnect, register, kick
  tasks           — every TASK_REQUEST dispatched + its result
  commands        — operator commands issued via the REST API

SQLite is used with WAL mode + a single shared connection (thread-safe via
the asyncio wrapper below).  No external ORM dependency — only stdlib.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DB_PATH = Path(__file__).parent / "mothership.db"
_conn: sqlite3.Connection | None = None
_lock: asyncio.Lock | None = None


# ── Schema ────────────────────────────────────────────────────────────────────

_SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS children (
    child_id        TEXT PRIMARY KEY,
    name            TEXT NOT NULL DEFAULT '',
    model           TEXT NOT NULL DEFAULT 'unknown',
    first_seen      REAL NOT NULL,          -- Unix timestamp
    last_seen       REAL NOT NULL,
    total_connects  INTEGER NOT NULL DEFAULT 0,
    total_tasks     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL,           -- Unix timestamp
    child_id    TEXT    NOT NULL,
    event_type  TEXT    NOT NULL,           -- connect | disconnect | register | kick | auth_fail
    detail      TEXT    DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_events_child ON events (child_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_events_ts    ON events (ts DESC);

CREATE TABLE IF NOT EXISTS tasks (
    task_id     TEXT    PRIMARY KEY,
    child_id    TEXT    NOT NULL,
    prompt      TEXT    NOT NULL,
    result      TEXT,
    error       TEXT,
    status      TEXT    NOT NULL DEFAULT 'pending',   -- pending | ok | error
    queued_at   REAL    NOT NULL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS idx_tasks_child ON tasks (child_id, queued_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_ts    ON tasks (queued_at DESC);

CREATE TABLE IF NOT EXISTS commands (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL,
    command     TEXT    NOT NULL,   -- e.g. 'kick', 'send', 'token_revoke'
    child_id    TEXT,
    operator    TEXT    DEFAULT 'api',
    detail      TEXT    DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_commands_ts ON commands (ts DESC);
"""


# ── Lifecycle ─────────────────────────────────────────────────────────────────


def init(path: Path | None = None) -> None:
    """Open the database and apply the schema. Call once at startup."""
    global _conn, _lock
    db_path = path or _DB_PATH
    _conn = sqlite3.connect(str(db_path), check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.executescript(_SCHEMA)
    _conn.commit()
    _lock = asyncio.Lock()
    logger.info("Database opened: %s", db_path)


def close() -> None:
    global _conn
    if _conn:
        _conn.close()
        _conn = None
        logger.info("Database closed.")


# ── Internal helpers ──────────────────────────────────────────────────────────


def _db() -> sqlite3.Connection:
    if _conn is None:
        raise RuntimeError("database.init() has not been called")
    return _conn


def _now() -> float:
    return time.time()


async def _execute(sql: str, params: tuple = ()) -> None:
    assert _lock is not None
    async with _lock:
        _db().execute(sql, params)
        _db().commit()


async def _fetchall(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    assert _lock is not None
    async with _lock:
        cur = _db().execute(sql, params)
        return cur.fetchall()


async def _fetchone(sql: str, params: tuple = ()) -> sqlite3.Row | None:
    assert _lock is not None
    async with _lock:
        cur = _db().execute(sql, params)
        return cur.fetchone()


# ── Children ──────────────────────────────────────────────────────────────────


async def upsert_child(child_id: str, name: str, model: str) -> None:
    """Insert or update a child's record. Increments total_connects on each call."""
    now = _now()
    await _execute(
        """
        INSERT INTO children (child_id, name, model, first_seen, last_seen, total_connects)
        VALUES (?, ?, ?, ?, ?, 1)
        ON CONFLICT(child_id) DO UPDATE SET
            name           = excluded.name,
            model          = excluded.model,
            last_seen      = excluded.last_seen,
            total_connects = total_connects + 1
        """,
        (child_id, name, model, now, now),
    )


async def touch_child(child_id: str) -> None:
    """Update last_seen timestamp for a child (e.g. on PONG)."""
    await _execute(
        "UPDATE children SET last_seen = ? WHERE child_id = ?",
        (_now(), child_id),
    )


async def increment_task_count(child_id: str) -> None:
    await _execute(
        "UPDATE children SET total_tasks = total_tasks + 1 WHERE child_id = ?",
        (child_id,),
    )


async def list_children() -> list[dict]:
    rows = await _fetchall("SELECT * FROM children ORDER BY last_seen DESC")
    return [dict(r) for r in rows]


async def get_child(child_id: str) -> dict | None:
    row = await _fetchone("SELECT * FROM children WHERE child_id = ?", (child_id,))
    return dict(row) if row else None


# ── Events ────────────────────────────────────────────────────────────────────


async def log_event(child_id: str, event_type: str, detail: str = "") -> None:
    await _execute(
        "INSERT INTO events (ts, child_id, event_type, detail) VALUES (?, ?, ?, ?)",
        (_now(), child_id, event_type, detail),
    )


async def get_events(
    child_id: str | None = None,
    limit: int = 100,
    event_type: str | None = None,
) -> list[dict]:
    clauses: list[str] = []
    params: list[Any] = []
    if child_id:
        clauses.append("child_id = ?")
        params.append(child_id)
    if event_type:
        clauses.append("event_type = ?")
        params.append(event_type)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = await _fetchall(
        f"SELECT * FROM events {where} ORDER BY ts DESC LIMIT ?",
        tuple(params) + (limit,),
    )
    return [dict(r) for r in rows]


# ── Tasks ─────────────────────────────────────────────────────────────────────


async def record_task(task_id: str, child_id: str, prompt: str) -> None:
    await _execute(
        """
        INSERT OR IGNORE INTO tasks (task_id, child_id, prompt, queued_at)
        VALUES (?, ?, ?, ?)
        """,
        (task_id, child_id, prompt, _now()),
    )
    await increment_task_count(child_id)


async def complete_task(task_id: str, result: str | None, error: str | None) -> None:
    status = "error" if error else "ok"
    await _execute(
        """
        UPDATE tasks
        SET result = ?, error = ?, status = ?, finished_at = ?
        WHERE task_id = ?
        """,
        (result, error, status, _now(), task_id),
    )


async def get_tasks(
    child_id: str | None = None,
    limit: int = 50,
    status: str | None = None,
) -> list[dict]:
    clauses: list[str] = []
    params: list[Any] = []
    if child_id:
        clauses.append("child_id = ?")
        params.append(child_id)
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = await _fetchall(
        f"SELECT task_id, child_id, status, queued_at, finished_at, "
        f"SUBSTR(prompt, 1, 120) AS prompt_preview, "
        f"SUBSTR(result, 1, 200) AS result_preview, error "
        f"FROM tasks {where} ORDER BY queued_at DESC LIMIT ?",
        tuple(params) + (limit,),
    )
    return [dict(r) for r in rows]


async def get_task(task_id: str) -> dict | None:
    row = await _fetchone("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
    return dict(row) if row else None


# ── Commands ──────────────────────────────────────────────────────────────────


async def log_command(
    command: str,
    child_id: str | None = None,
    detail: str = "",
    operator: str = "api",
) -> None:
    await _execute(
        "INSERT INTO commands (ts, command, child_id, operator, detail) VALUES (?, ?, ?, ?, ?)",
        (_now(), command, child_id, operator, detail),
    )


async def get_commands(limit: int = 50, child_id: str | None = None) -> list[dict]:
    clauses: list[str] = []
    params: list[Any] = []
    if child_id:
        clauses.append("child_id = ?")
        params.append(child_id)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = await _fetchall(
        f"SELECT * FROM commands {where} ORDER BY ts DESC LIMIT ?",
        tuple(params) + (limit,),
    )
    return [dict(r) for r in rows]


# ── Stats ─────────────────────────────────────────────────────────────────────


async def get_stats() -> dict:
    """Return aggregate statistics across all children."""
    row = await _fetchone("SELECT COUNT(*) AS total_children FROM children")
    total_children = row["total_children"] if row else 0

    row = await _fetchone(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END) AS ok, "
        "SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS errors, "
        "SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending "
        "FROM tasks"
    )
    task_stats = dict(row) if row else {}

    row = await _fetchone("SELECT COUNT(*) AS total FROM events")
    total_events = row["total"] if row else 0

    return {
        "total_children": total_children,
        "tasks": task_stats,
        "total_events": total_events,
    }
