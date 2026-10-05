"""
Cache semántico de respuestas — como lo hacen OpenAI/Anthropic internamente.

Lógica:
  1. Calcular embedding de la query del usuario
  2. Buscar en cache si hay una respuesta con similitud >= umbral
  3. Si hay hit: devolver respuesta en <1ms
  4. Si no: ejecutar pipeline completo y guardar en cache

LRU de 256 entradas. Umbral de similitud configurable (default 0.92).
"""
import hashlib
import logging
import time
from collections import OrderedDict
from typing import Optional
import numpy as np

logger = logging.getLogger(__name__)

_CACHE_MAX   = int(__import__("os").environ.get("CACHE_MAX", "256"))
_SIMILARITY  = float(__import__("os").environ.get("CACHE_SIMILARITY", "0.92"))

# Estructura: OrderedDict[str, {"vec": np.ndarray, "result": dict, "ts": float, "hits": int}]
_cache: OrderedDict = OrderedDict()


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _embed_query(text: str) -> Optional[np.ndarray]:
    """Intenta generar embedding. Devuelve None si no hay sentence-transformers."""
    try:
        from smartorch.rag.embedder import embed_one
        return embed_one(text)
    except Exception:
        return None


def _extract_user_text(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            c = m.get("content", "")
            # Quitar bloque RAG si ya se inyectó
            if "[CONTEXTO DEL PROYECTO]" in c:
                c = c.split("[CONTEXTO DEL PROYECTO]")[0]
            return c.strip()
    return ""


def lookup(messages: list[dict]) -> Optional[dict]:
    """
    Busca un hit en el cache semántico.
    Devuelve el resultado guardado o None si no hay match.
    """
    if not _cache:
        return None

    user_text = _extract_user_text(messages)
    if len(user_text) < 10:
        return None

    vec = _embed_query(user_text)
    if vec is None:
        return None

    best_sim  = 0.0
    best_key  = None
    for key, entry in _cache.items():
        sim = _cosine(vec, entry["vec"])
        if sim > best_sim:
            best_sim = sim
            best_key = key

    if best_sim >= _SIMILARITY and best_key:
        entry = _cache[best_key]
        _cache.move_to_end(best_key)
        entry["hits"] += 1
        logger.info(f"[CACHE] HIT sim={best_sim:.3f} hits={entry['hits']} — '{user_text[:50]}'")
        result = dict(entry["result"])
        result["cache_hit"]  = True
        result["cache_sim"]  = round(best_sim, 4)
        return result

    return None


def store(messages: list[dict], result: dict) -> None:
    """Guarda un resultado en el cache. No guarda errores ni respuestas muy cortas."""
    if result.get("error"):
        return
    content = result.get("content", "")
    if len(content) < 20:
        return

    user_text = _extract_user_text(messages)
    if len(user_text) < 10:
        return

    vec = _embed_query(user_text)
    if vec is None:
        return

    key = hashlib.md5(user_text.encode(), usedforsecurity=False).hexdigest()
    _cache[key] = {
        "vec":    vec,
        "result": result,
        "ts":     time.time(),
        "hits":   0,
    }
    _cache.move_to_end(key)
    if len(_cache) > _CACHE_MAX:
        evicted = _cache.popitem(last=False)
        logger.debug(f"[CACHE] Evicted: {evicted[0][:8]}…")

    logger.debug(f"[CACHE] Stored: '{user_text[:50]}' (size={len(_cache)})")


def stats() -> dict:
    total_hits = sum(e["hits"] for e in _cache.values())
    return {
        "entries":    len(_cache),
        "max":        _CACHE_MAX,
        "similarity_threshold": _SIMILARITY,
        "total_hits": total_hits,
    }


def clear() -> int:
    n = len(_cache)
    _cache.clear()
    logger.info(f"[CACHE] Limpiado: {n} entradas eliminadas")
    return n
