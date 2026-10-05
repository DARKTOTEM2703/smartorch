"""
Historial unico de conversaciones (SQLite).

Lo comparten la web, la CLI y VS Code: una conversacion iniciada en un lado
aparece en los demas. La ubicacion la decide smartorch.core.datadir.
"""
import os
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Optional

from smartorch.core import datadir

DATA_DIR = datadir.DATA_DIR
DB_PATH = datadir.HISTORY_DB

_lock = threading.Lock()
_ready = False

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id         TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT 'api',
    workspace  TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    conv_id    TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT 'api',
    ts         REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conv_id, id);
CREATE INDEX IF NOT EXISTS idx_conv_updated ON conversations(updated_at DESC);
"""


def _connect() -> sqlite3.Connection:
    global _ready
    if not os.path.isdir(os.path.dirname(DB_PATH)):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if not _ready:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(_SCHEMA)
        _ready = True
    return conn


@contextmanager
def _db():
    with _lock:
        conn = _connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


_ATTACHED = re.compile(r"^Archivo `([^`]+)`[^\n]*:\n```[\s\S]*?```\s*", re.MULTILINE)


def _title_from(text: str) -> str:
    """Titulo legible: ignora el contexto adjunto (archivo + codigo) y usa lo que el usuario escribio."""
    cleaned = _ATTACHED.sub("", text or "", count=1).strip()
    if not cleaned:
        m = re.match(r"^Archivo `([^`]+)`", text or "")
        cleaned = f"Archivo {m.group(1)}" if m else ""
    one_line = " ".join(cleaned.split())
    return (one_line[:60] + "…") if len(one_line) > 60 else (one_line or "Conversación")


def _conv_row(row: sqlite3.Row, count: Optional[int] = None) -> dict:
    d = dict(row)
    if count is not None:
        d["message_count"] = count
    return d


def create_conversation(title: str = "", source: str = "api", workspace: Optional[str] = None,
                        conv_id: Optional[str] = None) -> dict:
    now = time.time()
    cid = conv_id or uuid.uuid4().hex[:12]
    with _db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO conversations (id, title, source, workspace, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (cid, title or "Nueva conversación", source, workspace, now, now),
        )
        row = conn.execute("SELECT * FROM conversations WHERE id = ?", (cid,)).fetchone()
    return _conv_row(row)


def add_message(conv_id: str, role: str, content: str, source: str = "api") -> None:
    now = time.time()
    with _db() as conn:
        conn.execute(
            "INSERT INTO messages (conv_id, role, content, source, ts) VALUES (?, ?, ?, ?, ?)",
            (conv_id, role, content, source, now),
        )
        conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conv_id))


def save_turn(conv_id: str, source: str, user_text: str, assistant_text: str,
              workspace: Optional[str] = None, title: Optional[str] = None) -> None:
    """Guarda un intercambio, creando la conversacion si aun no existe."""
    with _db() as conn:
        exists = conn.execute("SELECT 1 FROM conversations WHERE id = ?", (conv_id,)).fetchone()
    if not exists:
        create_conversation(_title_from(title) if title else _title_from(user_text), source, workspace, conv_id)
    if user_text:
        add_message(conv_id, "user", user_text, source)
    if assistant_text:
        add_message(conv_id, "assistant", assistant_text, source)


def list_conversations(limit: int = 100, query: str = "") -> list[dict]:
    limit = max(1, min(limit, 500))
    with _db() as conn:
        if query:
            like = f"%{query}%"
            rows = conn.execute(
                "SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conv_id = c.id) AS message_count "
                "FROM conversations c WHERE c.title LIKE ? OR c.id IN "
                "(SELECT conv_id FROM messages WHERE content LIKE ?) "
                "ORDER BY c.updated_at DESC LIMIT ?",
                (like, like, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conv_id = c.id) AS message_count "
                "FROM conversations c ORDER BY c.updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


def get_conversation(conv_id: str) -> Optional[dict]:
    with _db() as conn:
        row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conv_id,)).fetchone()
        if not row:
            return None
        msgs = conn.execute(
            "SELECT role, content, source, ts FROM messages WHERE conv_id = ? ORDER BY id",
            (conv_id,),
        ).fetchall()
    d = _conv_row(row, len(msgs))
    d["messages"] = [dict(m) for m in msgs]
    return d


def rename_conversation(conv_id: str, title: str) -> bool:
    with _db() as conn:
        cur = conn.execute("UPDATE conversations SET title = ? WHERE id = ?", (title.strip() or "Conversación", conv_id))
    return cur.rowcount > 0


def delete_conversation(conv_id: str) -> bool:
    with _db() as conn:
        cur = conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
    return cur.rowcount > 0


def export_markdown(conv_id: str) -> Optional[str]:
    conv = get_conversation(conv_id)
    if not conv:
        return None
    names = {"user": "Tú", "assistant": "SmartOrch", "system": "Sistema"}
    parts = [f"# {conv['title']}", ""]
    for m in conv["messages"]:
        parts += [f"## {names.get(m['role'], m['role'])} ({m['source']})", "", m["content"], ""]
    return "\n".join(parts)
