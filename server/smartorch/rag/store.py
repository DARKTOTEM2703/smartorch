"""
Base de datos vectorial con ChromaDB.
Persiste en disco dentro de la carpeta de datos de SmartOrch (ver core/datadir.py).
"""
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

from smartorch.core import datadir

datadir.migrate_legacy()
DB_DIR = Path(datadir.CHROMA_DIR)
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


def search(query: str, n_results: int = 5, roots: list[str] | None = None) -> list[dict]:
    """
    Busca los n_results chunks más relevantes para la query.
    Con `roots` solo considera fragmentos de esos workspaces.
    Devuelve lista de {"text", "file", "score"}.
    """
    col = _get_collection()
    if col.count() == 0:
        return []
    if roots is not None and not roots:
        return []

    from .embedder import embed_one
    q_embed = embed_one(query).tolist()

    kwargs = {"where": {"root": {"$in": list(roots)}}} if roots else {}
    n = min(n_results, col.count())
    results = col.query(query_embeddings=[q_embed], n_results=n, **kwargs)

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


def root_chunk_count(root: str) -> int:
    col = _get_collection()
    return len(col.get(where={"root": root}, include=[], limit=1_000_000)["ids"])


def sync_root(root: str, chunks: list[dict]) -> int:
    """
    Deja en el almacen exactamente los fragmentos de `chunks` para ese workspace:
    sube los nuevos y borra los obsoletos (archivos cambiados o eliminados) sin
    dejar el RAG vacio mientras se calculan los embeddings. Devuelve cuantos borro.
    """
    upsert_chunks(chunks)
    keep = {c["id"] for c in chunks}
    col = _get_collection()
    existing = col.get(where={"root": root}, include=[], limit=1_000_000)["ids"]
    stale = [i for i in existing if i not in keep]
    for i in range(0, len(stale), 5000):
        col.delete(ids=stale[i : i + 5000])
    return len(stale)


def delete_root(root: str) -> int:
    """Borra todos los fragmentos de un workspace."""
    col = _get_collection()
    before = col.count()
    col.delete(where={"root": root})
    return before - col.count()


def purge_unscoped() -> int:
    """Elimina fragmentos antiguos sin workspace asociado; no se pueden atribuir a ningun proyecto."""
    col = _get_collection()
    stale: list[str] = []
    offset, page = 0, 5000
    while True:
        got = col.get(include=["metadatas"], limit=page, offset=offset)
        ids, metas = got["ids"], got["metadatas"]
        if not ids:
            break
        stale += [i for i, m in zip(ids, metas) if not (m or {}).get("root")]
        offset += page
    for i in range(0, len(stale), 5000):
        col.delete(ids=stale[i : i + 5000])
    return len(stale)


def clear():
    """Elimina todos los vectores (para reindexar desde cero)."""
    col = _get_collection()
    col.delete(where={"file": {"$ne": ""}})
    logger.info("[STORE] Colección limpiada")
