"""
Memoria de experiencias: lo que SmartOrch aprendio resolviendo tareas en ESTE proyecto.

Solo se guardan tareas verificadas (los tests pasaron despues de editar). Cada entrada es una receta
("para pedir X se tocaron estos archivos y asi quedo") y, si hubo un fallo antes del exito, una leccion
("fallo con este error y se resolvio asi"). Antes de una tarea parecida se recuperan como pistas.

Es la forma de que un modelo chico mejore con el uso sin reentrenarse: la experiencia vive en SQLite.
La recuperacion es lexica (palabras en comun), sin embeddings: no gasta modelo ni memoria.
"""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import time
import unicodedata
from pathlib import Path
from typing import Optional

from smartorch.core import datadir

MAX_PER_PROJECT = 300
MIN_SCORE = 0.12

SCHEMA = """
CREATE TABLE IF NOT EXISTS experiences(
    id INTEGER PRIMARY KEY, root TEXT, kind TEXT, task TEXT, summary TEXT, files TEXT, lesson TEXT,
    created REAL, uses INTEGER DEFAULT 0, last_used REAL DEFAULT 0);
CREATE INDEX IF NOT EXISTS exp_root ON experiences(root);
"""


def _db() -> sqlite3.Connection:
    path = os.path.join(datadir.DATA_DIR, "experience.db")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _key(root: str) -> str:
    return hashlib.sha1(os.path.normcase(str(Path(root).resolve())).encode("utf-8")).hexdigest()[:16]


def _words(text: str) -> set[str]:
    plain = "".join(c for c in unicodedata.normalize("NFD", (text or "").lower()) if unicodedata.category(c) != "Mn")
    return {w for w in re.findall(r"[a-z0-9_]{4,}", plain)}


def record(root: str, task: str, files: list[str], summary: str = "", lesson: str = "") -> Optional[int]:
    """Guarda una experiencia verificada. Devuelve su id (o None si la tarea no tiene contenido)."""
    task = " ".join((task or "").split())[:400]
    if not task or not files:
        return None
    key = _key(root)
    files_text = ", ".join(files[:12])
    with _db() as conn:
        dup = conn.execute("SELECT id FROM experiences WHERE root=? AND task=? AND files=?", (key, task, files_text)).fetchone()
        if dup:
            conn.execute("UPDATE experiences SET summary=?, lesson=?, created=? WHERE id=?",
                         (" ".join(summary.split())[:400], lesson[:500], time.time(), dup["id"]))
            return dup["id"]
        cur = conn.execute(
            "INSERT INTO experiences(root, kind, task, summary, files, lesson, created) VALUES(?,?,?,?,?,?,?)",
            (key, "lesson" if lesson else "recipe", task, " ".join(summary.split())[:400], files_text, lesson[:500], time.time()))
        conn.execute(
            "DELETE FROM experiences WHERE root=? AND id NOT IN "
            "(SELECT id FROM experiences WHERE root=? ORDER BY uses DESC, created DESC LIMIT ?)", (key, key, MAX_PER_PROJECT))
        return cur.lastrowid


def recall(root: str, task: str, limit: int = 3) -> list[dict]:
    """Experiencias parecidas a la tarea, mejores primero. Marca como usadas las que devuelve."""
    query = _words(task)
    if not query:
        return []
    with _db() as conn:
        rows = conn.execute("SELECT * FROM experiences WHERE root=?", (_key(root),)).fetchall()
        scored = []
        for r in rows:
            doc = _words(" ".join([r["task"], r["summary"], r["files"], r["lesson"]]))
            if not doc:
                continue
            overlap = len(query & doc)
            score = overlap / len(query | doc)
            if overlap >= 2 and score >= MIN_SCORE:
                scored.append((score * (1 + 0.1 * min(r["uses"], 5)), dict(r)))
        scored.sort(key=lambda t: -t[0])
        chosen = [r for _, r in scored[:limit]]
        for r in chosen:
            conn.execute("UPDATE experiences SET uses=uses+1, last_used=? WHERE id=?", (time.time(), r["id"]))
    return chosen


def render(items: list[dict]) -> str:
    if not items:
        return ""
    lines = []
    for e in items:
        line = f"- Tarea parecida: «{e['task']}». Se tocó: {e['files']}."
        if e["summary"]:
            line += f" Resultado: {e['summary']}"
        if e["lesson"]:
            line += f" Lección: {e['lesson']}"
        lines.append(line)
    return ("[Experiencias previas en este proyecto (tareas verificadas con tests; úsalas como pista, no como verdad absoluta)]\n"
            + "\n".join(lines))


def forget(root: str, exp_id: Optional[int] = None) -> int:
    """Borra una experiencia (o todas las de este proyecto). El usuario manda sobre lo que se recuerda."""
    with _db() as conn:
        if exp_id is None:
            return conn.execute("DELETE FROM experiences WHERE root=?", (_key(root),)).rowcount
        return conn.execute("DELETE FROM experiences WHERE root=? AND id=?", (_key(root), exp_id)).rowcount


def listing(root: str, limit: int = 50) -> list[dict]:
    with _db() as conn:
        rows = conn.execute("SELECT id, kind, task, summary, files, lesson, uses, created FROM experiences "
                            "WHERE root=? ORDER BY created DESC LIMIT ?", (_key(root), limit)).fetchall()
    return [dict(r) for r in rows]
