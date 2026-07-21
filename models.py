import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "vfs_admin.db"
_db_lock = threading.Lock()


def _connect():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _db_lock, _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS admin_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS bot_cycles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                duration_seconds INTEGER,
                results_json TEXT,
                status TEXT NOT NULL DEFAULT 'running'
            );
        """)


def create_admin(username: str, password_hash: str) -> None:
    with _db_lock, _connect() as conn:
        conn.execute(
            "INSERT INTO admin_users (username, password_hash) VALUES (?, ?)",
            (username, password_hash),
        )


def get_admin_by_username(username: str):
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM admin_users WHERE username = ?", (username,)
        ).fetchone()


def start_cycle() -> int:
    with _db_lock, _connect() as conn:
        cur = conn.execute(
            "INSERT INTO bot_cycles (started_at, status) VALUES (?, 'running')",
            (datetime.now(timezone.utc).isoformat(),),
        )
        return cur.lastrowid


def end_cycle(cycle_id: int, results: dict, status: str = "completed") -> None:
    ended_at = datetime.now(timezone.utc).isoformat()
    with _db_lock, _connect() as conn:
        row = conn.execute(
            "SELECT started_at FROM bot_cycles WHERE id = ?", (cycle_id,)
        ).fetchone()
        duration = None
        if row:
            try:
                start = datetime.fromisoformat(row["started_at"])
                end_dt = datetime.fromisoformat(ended_at)
                duration = int((end_dt - start).total_seconds())
            except Exception:
                pass
        conn.execute(
            "UPDATE bot_cycles SET ended_at=?, duration_seconds=?, results_json=?, status=? WHERE id=?",
            (ended_at, duration, json.dumps(results), status, cycle_id),
        )


def get_recent_cycles(limit: int = 100) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM bot_cycles ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
