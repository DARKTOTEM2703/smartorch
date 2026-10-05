"""
Embeddings semánticos con sentence-transformers.
Modelo: all-MiniLM-L6-v2 — 80MB, rápido en CPU.
Cache LRU en memoria para queries repetidas.
"""
import hashlib
import logging
import numpy as np
from collections import OrderedDict

logger = logging.getLogger(__name__)

_model     = None
MODEL_NAME = "all-MiniLM-L6-v2"

# Cache LRU — evita recalcular embeddings de queries repetidas
_CACHE_MAX = 512
_cache: OrderedDict = OrderedDict()


def _cache_key(text: str) -> str:
    return hashlib.md5(text.encode(), usedforsecurity=False).hexdigest()


def get_model():
    global _model
    if _model is None:
        logger.info(f"[EMBED] Cargando modelo {MODEL_NAME}...")
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(MODEL_NAME)
        logger.info("[EMBED] Modelo listo")
    return _model


def embed(texts: list[str]) -> np.ndarray:
    """Genera embeddings normalizados para una lista de textos."""
    if not texts:
        return np.array([])
    return get_model().encode(
        texts,
        normalize_embeddings=True,
        show_progress_bar=False,
        batch_size=32,
    )


def embed_one(text: str) -> np.ndarray:
    """Embedding de un solo texto con cache LRU."""
    key = _cache_key(text)
    if key in _cache:
        _cache.move_to_end(key)
        logger.debug("[EMBED] Cache hit")
        return _cache[key]

    vec = embed([text])[0]

    _cache[key] = vec
    _cache.move_to_end(key)
    if len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)

    return vec


def cache_stats() -> dict:
    return {"size": len(_cache), "max": _CACHE_MAX}
