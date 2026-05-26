"""history.py — SQLite-backed persistence for chat, swarm, and sandbox history.

Audit finding C-3: the portal forgot every interaction on tab close.
This module provides an idempotent init_db() + per-domain read/write helpers
using stdlib sqlite3 only (no new dependencies).

WAL journal mode + synchronous=NORMAL gives safe concurrent access without
the overhead of fsync on every write.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "history.db"


# ---------------------------------------------------------------------------
# Core DB helpers
# ---------------------------------------------------------------------------


def init_db() -> None:
    """Idempotent — create tables + WAL mode.  Called once at startup."""
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(_DB_PATH)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS chat_messages (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              conversation_id TEXT NOT NULL,
              role TEXT NOT NULL CHECK (role IN ('user','assistant','system')),
              content TEXT NOT NULL,
              deployment TEXT,
              ts INTEGER NOT NULL,
              exec_result_json TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_chat_conv
              ON chat_messages(conversation_id, ts DESC);

            CREATE TABLE IF NOT EXISTS swarm_runs (
              run_id TEXT PRIMARY KEY,
              n INTEGER NOT NULL,
              model TEXT NOT NULL,
              image TEXT NOT NULL,
              started_at INTEGER NOT NULL,
              ended_at INTEGER,
              state TEXT NOT NULL,
              summary_json TEXT,
              leaderboard_json TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_swarm_started
              ON swarm_runs(started_at DESC);

            CREATE TABLE IF NOT EXISTS sandbox_creations (
              sandbox_id TEXT PRIMARY KEY,
              created_at INTEGER NOT NULL,
              image TEXT NOT NULL,
              runtime_class TEXT NOT NULL,
              expired_at INTEGER,
              expiry_reason TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_sandbox_created
              ON sandbox_creations(created_at DESC);
            """
        )


def db() -> sqlite3.Connection:
    """Return a new connection per call.  WAL handles concurrent readers/writers."""
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


def record_chat_turn(
    conversation_id: str | None,
    role: str,
    content: str,
    deployment: str | None = None,
    exec_result: dict | None = None,
) -> str:
    """Insert one chat row.

    If *conversation_id* is None a new UUID4 is minted and returned.
    Returns the conversation_id so callers can thread subsequent turns.
    Content is stored as-is; nothing is logged to stdout.
    """
    cid = conversation_id if conversation_id else str(uuid.uuid4())
    exec_json = json.dumps(exec_result) if exec_result is not None else None
    with db() as conn:
        conn.execute(
            """
            INSERT INTO chat_messages
              (conversation_id, role, content, deployment, ts, exec_result_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (cid, role, content, deployment, int(time.time()), exec_json),
        )
    return cid


def list_chat_messages(conversation_id: str, limit: int = 100) -> list[dict]:
    """Return messages for a conversation, most-recent first, capped by limit."""
    with db() as conn:
        rows = conn.execute(
            """
            SELECT role, content, deployment, ts, exec_result_json
            FROM chat_messages
            WHERE conversation_id = ?
            ORDER BY ts DESC, id DESC
            LIMIT ?
            """,
            (conversation_id, limit),
        ).fetchall()
    result = []
    for r in rows:
        row_dict: dict[str, Any] = dict(r)
        raw_exec = row_dict.pop("exec_result_json", None)
        row_dict["exec_result"] = json.loads(raw_exec) if raw_exec else None
        result.append(row_dict)
    return result


def list_conversations() -> list[dict]:
    """One summary row per conversation_id ordered by most recent activity."""
    with db() as conn:
        rows = conn.execute(
            """
            SELECT
              conversation_id,
              MAX(ts) AS last_ts,
              COUNT(*) AS message_count,
              MAX(CASE WHEN role = 'user' THEN content END) AS last_user_text
            FROM chat_messages
            GROUP BY conversation_id
            ORDER BY last_ts DESC
            """
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Swarm
# ---------------------------------------------------------------------------


def record_swarm_run(
    run_id: str,
    n: int,
    model: str,
    image: str,
    started_at: int,
    ended_at: int | None,
    state: str,
    summary: dict | None,
    leaderboard: list | None,
) -> None:
    """Upsert a swarm run (INSERT OR REPLACE on run_id PK)."""
    with db() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO swarm_runs
              (run_id, n, model, image, started_at, ended_at, state,
               summary_json, leaderboard_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                n,
                model,
                image,
                started_at,
                ended_at,
                state,
                json.dumps(summary) if summary is not None else None,
                json.dumps(leaderboard) if leaderboard is not None else None,
            ),
        )


def list_swarm_runs(limit: int = 20) -> list[dict]:
    """Return swarm runs, most-recent first."""
    with db() as conn:
        rows = conn.execute(
            """
            SELECT run_id, n, model, image, started_at, ended_at,
                   state, summary_json, leaderboard_json
            FROM swarm_runs
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    result = []
    for r in rows:
        row_dict: dict[str, Any] = dict(r)
        raw_summary = row_dict.pop("summary_json", None)
        raw_lb = row_dict.pop("leaderboard_json", None)
        row_dict["summary"] = json.loads(raw_summary) if raw_summary else None
        row_dict["leaderboard"] = json.loads(raw_lb) if raw_lb else None
        result.append(row_dict)
    return result


# ---------------------------------------------------------------------------
# Sandbox
# ---------------------------------------------------------------------------


def record_sandbox_creation(
    sandbox_id: str,
    image: str,
    runtime_class: str,
    created_at: int,
) -> None:
    """Insert a sandbox creation row (no-op if sandbox_id already exists)."""
    with db() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO sandbox_creations
              (sandbox_id, image, runtime_class, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (sandbox_id, image, runtime_class, created_at),
        )


def record_sandbox_expiry(
    sandbox_id: str,
    expired_at: int,
    reason: str,
) -> None:
    """Record when a sandbox was deleted/expired.  No-op if sandbox_id unknown.

    reason: 'manual' | 'auto-expire' | 'pool-reclaim'
    """
    with db() as conn:
        conn.execute(
            """
            UPDATE sandbox_creations
            SET expired_at = ?, expiry_reason = ?
            WHERE sandbox_id = ? AND expired_at IS NULL
            """,
            (expired_at, reason, sandbox_id),
        )


def list_sandbox_creations(limit: int = 50) -> list[dict]:
    """Return sandbox rows, most-recent first."""
    with db() as conn:
        rows = conn.execute(
            """
            SELECT sandbox_id, image, runtime_class, created_at,
                   expired_at, expiry_reason
            FROM sandbox_creations
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]
