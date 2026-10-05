"""
Mapa jerarquico del proyecto: el modelo lee cada archivo UNA vez y deja un resumen corto; luego se
resume cada carpeta a partir de los resumenes de sus hijos, y por ultimo el proyecto entero.

Es la forma de que un modelo chico "conozca" un proyecto grande sin tenerlo en contexto: consulta el mapa
en vez de recordar. Es incremental (solo se re-resume lo que cambio) y puede tardar, pero se hace una vez.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Callable, Optional

from smartorch.core import analysis, datadir

MAX_FILE_CHARS = 5000        # lo que ve el modelo de cada archivo
MAX_FILE_BYTES = 200_000     # archivos mayores se resumen por nombre y simbolos, no por contenido
MAX_CHILDREN = 40            # resumenes de hijos que entran en el prompt de una carpeta
SUMMARY_TOKENS = 110

_lock = threading.Lock()
_progress: dict[str, dict] = {}

Summarizer = Callable[[str], str]


def _store(root: str) -> str:
    digest = hashlib.sha1(os.path.normcase(root).encode("utf-8")).hexdigest()[:16]
    return os.path.join(datadir.DATA_DIR, "projectmap", f"{digest}.json")


def load(root: str) -> dict:
    try:
        with open(_store(root), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("files"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"files": {}, "dirs": {}, "project": "", "model": "", "built_at": 0}


def _save(root: str, data: dict) -> None:
    path = _store(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def _digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def _clean(text: str, limit: int = 300) -> str:
    return " ".join((text or "").split())[:limit]


def _default_summarizer(model: str) -> Summarizer:
    from smartorch.core import ollama_client

    def summarize(prompt: str) -> str:
        return ollama_client.chat_text(
            model, [{"role": "user", "content": prompt}], temperature=0.1, max_tokens=SUMMARY_TOKENS)
    return summarize


def _file_prompt(rel: str, text: str) -> str:
    return (f"Archivo: {rel}\n```\n{text[:MAX_FILE_CHARS]}\n```\n"
            "Resume en UNA sola frase (máximo 25 palabras, en español) qué responsabilidad tiene este archivo "
            "y qué expone (clases o funciones principales). Solo la frase, sin introducción.")


def _dir_prompt(rel: str, children: list[str]) -> str:
    body = "\n".join(children[:MAX_CHILDREN])
    return (f"Carpeta: {rel or '(raíz)'}\nContenido, ya resumido:\n{body}\n"
            "Resume en máximo 2 frases (en español) para qué sirve esta carpeta y cómo se relacionan sus partes. "
            "Solo el resumen, sin introducción.")


def _project_prompt(name: str, children: list[str]) -> str:
    body = "\n".join(children[:MAX_CHILDREN])
    return (f"Proyecto: {name}\nPartes principales, ya resumidas:\n{body}\n"
            "Describe en máximo 3 frases (en español) qué hace el proyecto y cómo está organizado. "
            "Solo la descripción, sin introducción.")


def build(root: str, model: Optional[str] = None, summarize: Optional[Summarizer] = None,
          max_files: int = 4000, cancel: Optional[threading.Event] = None) -> dict:
    """Construye o actualiza el mapa. Solo llama al modelo por lo que cambio. Devuelve estadisticas."""
    from smartorch.config import MODELS
    root_path = Path(root).resolve()
    key = str(root_path)
    if summarize is None:
        summarize = _default_summarizer(model or MODELS["agent"])

    data = load(key)
    old_files = data["files"]
    files: dict[str, dict] = {}
    paths = []
    for p in analysis._walk(root_path):
        paths.append(p)
        if len(paths) >= max_files:
            break

    state = {"phase": "archivos", "done": 0, "total": len(paths), "running": True, "error": ""}
    with _lock:
        _progress[key] = state
    stats = {"files": len(paths), "summarized": 0, "reused": 0, "failed": 0}
    try:
        for p in paths:
            if cancel is not None and cancel.is_set():
                raise InterruptedError("cancelado")
            rel = p.relative_to(root_path).as_posix()
            try:
                size = p.stat().st_size
                text = p.read_text(encoding="utf-8", errors="replace") if size <= MAX_FILE_BYTES else ""
            except OSError:
                state["done"] += 1
                continue
            sig = _digest(text or f"{rel}:{size}")
            prev = old_files.get(rel)
            if prev and prev.get("hash") == sig and prev.get("summary"):
                files[rel] = prev
                stats["reused"] += 1
            else:
                try:
                    summary = _clean(summarize(_file_prompt(rel, text or f"(archivo grande de {size} bytes)")))
                except ConnectionError:
                    raise
                except Exception:
                    summary, stats["failed"] = "", stats["failed"] + 1
                if summary:
                    files[rel] = {"hash": sig, "summary": summary}
                    stats["summarized"] += 1
            state["done"] += 1

        state.update(phase="carpetas", done=0)
        dirs = _summarize_dirs(files, data["dirs"], summarize, state, cancel)
        state.update(phase="proyecto")
        project, project_sig = _summarize_project(root_path.name, dirs, files, data, summarize)
        data = {"files": files, "dirs": dirs, "project": project, "project_sig": project_sig,
                "model": model or "", "built_at": __import__("time").time()}
        _save(key, data)
        return stats
    except Exception as e:
        state["error"] = str(e)
        if files:  # lo ya resumido no se pierde: la proxima corrida continua desde aqui
            partial = load(key)
            partial["files"] = {**partial["files"], **files}
            _save(key, partial)
        raise
    finally:
        state["running"] = False


def _summarize_dirs(files: dict, old_dirs: dict, summarize: Summarizer, state: dict,
                    cancel: Optional[threading.Event]) -> dict:
    """De las carpetas mas profundas a la raiz; cada una resume lo que ya resumieron sus hijos."""
    children: dict[str, list[str]] = {}
    for rel, info in files.items():
        parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
        children.setdefault(parent, []).append(f"- {rel.rsplit('/', 1)[-1]}: {info['summary']}")
    all_dirs = set(children)
    for d in list(all_dirs):  # las carpetas padre sin archivos propios tambien cuentan
        while "/" in d:
            d = d.rsplit("/", 1)[0]
            all_dirs.add(d)
    ordered = sorted((d for d in all_dirs if d), key=lambda d: -d.count("/"))
    dirs: dict[str, dict] = {}
    state["total"] = len(ordered)
    for d in ordered:
        if cancel is not None and cancel.is_set():
            raise InterruptedError("cancelado")
        lines = list(children.get(d, []))
        prefix = d + "/"
        lines += [f"- {k.rsplit('/', 1)[-1]}/ (carpeta): {v['summary']}" for k, v in dirs.items()
                  if k.startswith(prefix) and "/" not in k[len(prefix):]]
        sig = _digest("\n".join(lines))
        prev = old_dirs.get(d)
        if prev and prev.get("hash") == sig and prev.get("summary"):
            dirs[d] = prev
        elif lines:
            try:
                summary = _clean(summarize(_dir_prompt(d, lines)), 400)
            except ConnectionError:
                raise
            except Exception:
                summary = ""
            if summary:
                dirs[d] = {"hash": sig, "summary": summary}
        state["done"] += 1
    return dirs


def _summarize_project(name: str, dirs: dict, files: dict, data: dict, summarize: Summarizer) -> tuple[str, str]:
    top = [f"- {k}/: {v['summary']}" for k, v in dirs.items() if "/" not in k]
    top += [f"- {k}: {v['summary']}" for k, v in files.items() if "/" not in k]
    if not top:
        return "", ""
    sig = _digest("\n".join(top))
    if data.get("project") and data.get("project_sig") == sig:
        return data["project"], sig
    try:
        return _clean(summarize(_project_prompt(name, top)), 600), sig
    except ConnectionError:
        raise
    except Exception:
        return "", sig


def status(root: str) -> dict:
    key = str(Path(root).resolve())
    data = load(key)
    with _lock:
        prog = dict(_progress.get(key, {}))
    return {"summarized_files": len(data["files"]), "built_at": data.get("built_at", 0),
            "has_project_summary": bool(data.get("project")), "progress": prog}


def render(root: str, max_chars: int = 1800) -> str:
    """Texto para el contexto del modelo: resumen del proyecto y de sus carpetas principales."""
    data = load(str(Path(root).resolve()))
    if not data["project"] and not data["dirs"]:
        return ""
    parts = []
    if data["project"]:
        parts.append(data["project"])
    top = sorted(((k, v["summary"]) for k, v in data["dirs"].items() if k.count("/") <= 1))
    parts += [f"- {k}/: {s}" for k, s in top]
    text = "\n".join(parts)
    return text if len(text) <= max_chars else text[:max_chars].rsplit("\n", 1)[0]


def lookup(root: str, query: str, limit: int = 8) -> list[tuple[str, str]]:
    """Archivos cuyo resumen o nombre encaja con la consulta: sirve para decidir que leer, sin leer todo."""
    import re
    words = {w for w in re.findall(r"\w{4,}", analysis_plain(query))}
    if not words:
        return []
    data = load(str(Path(root).resolve()))
    scored = []
    for rel, info in data["files"].items():
        hay = analysis_plain(rel + " " + info["summary"])
        score = sum(1 for w in words if w in hay)
        if score:
            scored.append((score, rel, info["summary"]))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [(rel, summary) for _, rel, summary in scored[:limit]]


def analysis_plain(text: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")
