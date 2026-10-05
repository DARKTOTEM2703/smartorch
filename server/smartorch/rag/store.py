"""
Base de datos vectorial con ChromaDB.
Persiste en disco en .smartorch_db/ junto al proyecto.
"""
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DB_DIR = Path(__file__).parent.parent.parent / ".smartorch_db"
_collection = None


def _get_collection():
    global _collection
    if _collection is not None:
        return _collection

    import chromadb
    DB_DIR.mkdir(exist_ok=True)
    client = chromadb.PersistentClient(path=str(DB_DIR))
    _collection = client.get_or_create_collection(
        name="codebase",
        metadata={"hnsw:space": "cosine"},
    )
    return _collection


def collection_ready() -> bool:
    """True si ChromaDB está disponible y tiene datos."""
    try:
        return _get_collection().count() > 0
    except Exception:
        return False


def chunk_count() -> int:
    try:
        return _get_collection().count()
    except Exception:
        return 0


def upsert_chunks(chunks: list[dict]):
    """
    Indexa chunks en ChromaDB con embeddings semánticos.
    chunks: [{"id": str, "text": str, "metadata": dict}]
    """
    if not chunks:
        return

    from .embedder import embed

    col        = _get_collection()
    batch_size = 64

    for i in range(0, len(chunks), batch_size):
        batch      = chunks[i : i + batch_size]
        texts      = [c["text"] for c in batch]
        embeddings = embed(texts).tolist()

        col.upsert(
            ids        = [c["id"] for c in batch],
            documents  = texts,
            embeddings = embeddings,
            metadatas  = [c.get("metadata", {}) for c in batch],
        )
        logger.debug(f"[STORE] Upserted {i+len(batch)}/{len(chunks)} chunks")

    logger.info(f"[STORE] {len(chunks)} chunks indexados en ChromaDB")


def search(query: str, n_results: int = 5) -> list[dict]:
    """
    Busca los n_results chunks más relevantes para la query.
    Devuelve lista de {"text", "file", "score"}.
    """
    col = _get_collection()
    if col.count() == 0:
        return []

    from .embedder import embed_one
    q_embed = embed_one(query).tolist()

    n = min(n_results, col.count())
    results = col.query(query_embeddings=[q_embed], n_results=n)

    out = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        out.append({
            "text":  doc,
            "file":  meta.get("file", ""),
            "score": round(1.0 - dist, 3),
        })

    return out


def clear():
    """Elimina todos los vectores (para reindexar desde cero)."""
    col = _get_collection()
    col.delete(where={"file": {"$ne": ""}})
    logger.info("[STORE] Colección limpiada")
