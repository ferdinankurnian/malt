"""SQLite schema + CRUD operations."""

import sqlite3
import uuid
import secrets
from pathlib import Path

DB_PATH = Path.home() / ".local" / "share" / "malt" / "malt.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    root_path TEXT NOT NULL,
    permission TEXT NOT NULL DEFAULT 'read',
    allowed_commands TEXT,
    token TEXT NOT NULL UNIQUE,
    mcp_port INTEGER DEFAULT 3100,
    tunnel_enabled INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    arguments TEXT,
    response TEXT,
    success INTEGER DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);
"""


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    conn = _connect()
    conn.executescript(SCHEMA)
    conn.close()


def create_project(name: str, root_path: str, permission: str = "read") -> dict:
    project_id = uuid.uuid4().hex[:12]
    token = secrets.token_hex(16)
    conn = _connect()
    conn.execute(
        "INSERT INTO projects (id, name, root_path, permission, token) VALUES (?, ?, ?, ?, ?)",
        (project_id, name, root_path, permission, token),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    conn.close()
    return dict(row)


def get_project(project_id: str) -> dict | None:
    conn = _connect()
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_project_by_token(token: str) -> dict | None:
    conn = _connect()
    row = conn.execute("SELECT * FROM projects WHERE token = ?", (token,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_projects() -> list[dict]:
    conn = _connect()
    rows = conn.execute("SELECT * FROM projects ORDER BY name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_project(project_id: str, **fields) -> dict | None:
    allowed = {
        "name",
        "root_path",
        "permission",
        "allowed_commands",
        "mcp_port",
        "tunnel_enabled",
    }
    filtered = {k: v for k, v in fields.items() if k in allowed}
    if not filtered:
        return get_project(project_id)
    filtered["updated_at"] = "CURRENT_TIMESTAMP"
    sets = ", ".join(f"{k} = ?" for k in filtered)
    values = list(filtered.values()) + [project_id]
    conn = _connect()
    conn.execute(f"UPDATE projects SET {sets} WHERE id = ?", values)
    conn.commit()
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_project(project_id: str) -> bool:
    conn = _connect()
    cursor = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    conn.commit()
    deleted = cursor.rowcount > 0
    conn.close()
    return deleted


def regenerate_token(project_id: str) -> str | None:
    new_token = secrets.token_hex(16)
    conn = _connect()
    conn.execute(
        "UPDATE projects SET token = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (new_token, project_id),
    )
    conn.commit()
    conn.close()
    return new_token


def add_log(
    project_id: str,
    tool_name: str,
    arguments: str = "",
    response: str = "",
    success: bool = True,
) -> None:
    conn = _connect()
    conn.execute(
        "INSERT INTO logs (project_id, tool_name, arguments, response, success) VALUES (?, ?, ?, ?, ?)",
        (project_id, tool_name, arguments, response, int(success)),
    )
    conn.commit()
    conn.close()


def get_logs(project_id: str, limit: int = 100) -> list[dict]:
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM logs WHERE project_id = ? ORDER BY created_at DESC LIMIT ?",
        (project_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def clear_logs(project_id: str) -> None:
    conn = _connect()
    conn.execute("DELETE FROM logs WHERE project_id = ?", (project_id,))
    conn.commit()
    conn.close()
