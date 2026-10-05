"""
Retriever — interfaz unificada de búsqueda RAG.
Usa ChromaDB semántico si está disponible, TF-IDF como fallback.
"""
from .store import search as semantic_search, collection_ready
from smartorch.core.gating import MIN_RAG_SCORE


def search(query: str, top_k: int = 5) -> list[dict]:
    """Busca contexto relevante. Devuelve lista de {"text", "file", "score"}."""
    return semantic_search(query, n_results=top_k)


def search_formatted(query: str, top_k: int = 5, max_chars: int = 3000) -> str:
    """
    Busca y formatea el contexto para inyectar en el prompt.
    Devuelve string vacío si no hay resultados.
    """
    if not collection_ready():
        return ""

    results = [r for r in search(query, top_k=top_k) if r["score"] >= MIN_RAG_SCORE]
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
