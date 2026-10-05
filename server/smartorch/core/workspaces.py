"""
Workspaces registrados: carpetas que SmartOrch indexa y sobre las que busca.

El RAG solo consulta estos workspaces, asi un proyecto antiguo que ya no usas
no contamina las respuestas del actual. Se guardan en <carpeta de datos>/workspaces.json.
"""
import json
import os
import threading
import time
from contextvars import ContextVar
from pathlib import Path

from smartorch.core import datadir

_lock = threading.Lock()
_request_root: ContextVar[str | None] = ContextVar("smartorch_request_root", default=None)


def _file() -> str:
    return os.path.join(datadir.DATA_DIR, "workspaces.json")


def _load() -> dict:
    try:
        with open(_file(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def normalize(root: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.expanduser(root)))


def canonical(root: str) -> str:
    """Misma forma que guarda el chunker en la metadata de cada fragmento."""
    return str(Path(os.path.expanduser(root)).resolve())


def register(root: str) -> str:
    """Marca un workspace como activo y devuelve su ruta canonica."""
    key = normalize(root)
    with _lock:
        data = _load()
        data[key] = {"root": canonical(root), "last_used": time.time()}
        os.makedirs(datadir.DATA_DIR, exist_ok=True)
        with open(_file(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    return canonical(root)


def active_roots(limit: int = 5) -> list[str]:
    """Roots (tal como se guardaron en los chunks) de los workspaces recientes que aun existen."""
    with _lock:
        data = _load()
    items = [v for v in data.values() if os.path.isdir(v.get("root", ""))]
    items.sort(key=lambda v: v.get("last_used", 0), reverse=True)
    return [v["root"] for v in items[:limit]]


def is_registered(root: str) -> bool:
    with _lock:
        return normalize(root) in _load()


def use_root(root: str | None) -> None:
    """Fija el workspace de la peticion en curso (valido para el hilo/tarea actual)."""
    _request_root.set(canonical(root) if root and os.path.isdir(os.path.expanduser(root)) else None)


def current_root() -> str | None:
    """Workspace de la peticion; si no hay, el ultimo usado."""
    root = _request_root.get()
    if root and os.path.isdir(root):
        return root
    roots = active_roots(1)
    return roots[0] if roots else None
