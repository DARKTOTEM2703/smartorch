"""
Retriever — interfaz unificada de búsqueda RAG.
Usa ChromaDB semántico si está disponible, TF-IDF como fallback.
"""
from .store import search as semantic_search, collection_ready
import re

from smartorch.core import workspaces
from smartorch.core.gating import MIN_RAG_SCORE

PATH_BOOST = 0.15
_STOPWORDS = {
    "donde", "esta", "este", "esta", "como", "para", "pero", "cual", "cuales", "sobre", "hace", "hacer",
    "archivo", "archivos", "codigo", "proyecto", "workspace", "funcion", "clase", "metodo", "that", "this",
    "what", "where", "which", "file", "code", "with", "from", "have", "tiene", "tengo", "puedo", "quiero",
}


def search(query: str, top_k: int = 5, roots: list[str] | None = None) -> list[dict]:
    """
    Busca contexto relevante. Devuelve lista de {"text", "file", "score"}.
    Sin `roots` usa los workspaces registrados (workspaces.active_roots).
    """
    if roots is None:
        roots = workspaces.active_roots()
    # Se pide de mas y se reordena: una palabra de la pregunta en el nombre del archivo
    # ("watcher" -> watcher.py) es una senal fuerte que los embeddings suelen perder.
    pool = semantic_search(query, n_results=max(top_k * 3, top_k), roots=roots)
    terms = {w for w in re.findall(r"[a-zA-Z_]{4,}", query.lower()) if w not in _STOPWORDS}
    for r in pool:
        path = r["file"].lower()
        if any(t in path for t in terms):
            r["score"] = round(min(1.0, r["score"] + PATH_BOOST), 3)
    pool.sort(key=lambda r: r["score"], reverse=True)
    return pool[:top_k]


def search_formatted(query: str, top_k: int = 5, max_chars: int = 3000, roots: list[str] | None = None) -> str:
    """
    Busca y formatea el contexto para inyectar en el prompt.
    Devuelve string vacío si no hay resultados.
    """
    if not collection_ready():
        return ""

    results = [r for r in search(query, top_k=top_k, roots=roots) if r["score"] >= MIN_RAG_SCORE]
    if not results:
        return ""

    parts    = []
    consumed = 0

    for r in results:
        if consumed >= max_chars:
            break
        snippet = r["text"]
        # Truncar si excede el espacio restante
        remaining = max_chars - consumed
        if len(snippet) > remaining:
            snippet = snippet[:remaining] + "\n[...truncado]"

        score_pct = int(r["score"] * 100)
        parts.append(f"[{r['file']} — relevancia {score_pct}%]\n{snippet}")
        consumed += len(snippet)

    return "\n\n---\n\n".join(parts)
