"""
Indexer — indexa código fuente para RAG local
Sin dependencias externas: usa TF-IDF simple con json para persistencia
"""
import json
import math
import os
import re
import hashlib
from pathlib import Path
from smartorch.config import AGENT_WORK_DIR

from smartorch.core.datadir import INDEX_FILE, migrate_legacy

migrate_legacy()

from smartorch.rag.chunker import SKIP_DIRS as _CHUNKER_SKIP_DIRS, is_indexable

# Mismas reglas que el RAG semantico: una sola fuente de verdad sobre que se indexa
SKIP_DIRS = _CHUNKER_SKIP_DIRS | {'.build', '.dist', 'compilados', 'onefile-build'}

CHUNK_SIZE = 60   # líneas por chunk
CHUNK_OVERLAP = 10


class CodeIndex:
    def __init__(self):
        self.chunks: list[dict] = []   # {id, path, start_line, content, tokens}
        self.idf: dict[str, float] = {}
        self._dirty = False

    # ── Chunking ─────────────────────────────────────────────────────────

    def _chunk_file(self, path: str, content: str) -> list[dict]:
        lines = content.splitlines()
        chunks = []
        i = 0
        while i < len(lines):
            end = min(i + CHUNK_SIZE, len(lines))
            chunk_lines = lines[i:end]
            chunk_text = "\n".join(chunk_lines)
            if chunk_text.strip():
                chunks.append({
                    "id": hashlib.md5(f"{path}:{i}".encode()).hexdigest()[:12],
                    "path": path,
                    "start_line": i + 1,
                    "content": chunk_text,
                    "tokens": self._tokenize(chunk_text),
                })
            i += CHUNK_SIZE - CHUNK_OVERLAP
        return chunks

    def _tokenize(self, text: str) -> list[str]:
        text = text.lower()
        tokens = re.findall(r'[a-z][a-z0-9_]{2,}', text)
        return list(set(tokens))

    # ── Indexing ──────────────────────────────────────────────────────────

    def index_directory(self, root: str, verbose: bool = True) -> int:
        root = os.path.abspath(root)
        new_chunks = []

        for dirpath, dirnames, filenames in os.walk(root):
            # Filtrar directorios ignorados
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith('.')]

            for fname in filenames:
                if not is_indexable(Path(fname)):
                    continue
                fpath = os.path.join(dirpath, fname)
                rel   = os.path.relpath(fpath, root)
                try:
                    with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                    if len(content) > 500_000:  # skip archivos gigantes
                        continue
                    file_chunks = self._chunk_file(rel, content)
                    new_chunks.extend(file_chunks)
                    if verbose:
                        print(f"  [+] {rel} → {len(file_chunks)} chunks")
                except Exception as e:
                    if verbose:
                        print(f"  [!] {rel}: {e}")

        self.chunks = new_chunks
        self._build_idf()
        self._dirty = True

        if verbose:
            print(f"\n[INDEX] {len(self.chunks)} chunks de {root}")
        return len(self.chunks)

    def _build_idf(self):
        """Calcula IDF para TF-IDF simple."""
        N = len(self.chunks)
        if N == 0:
            return
        df: dict[str, int] = {}
        for chunk in self.chunks:
            for tok in set(chunk["tokens"]):
                df[tok] = df.get(tok, 0) + 1
        self.idf = {tok: math.log((N + 1) / (count + 1)) for tok, count in df.items()}

    # ── Search ────────────────────────────────────────────────────────────

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """Busca chunks relevantes por TF-IDF."""
        if not self.chunks:
            return []

        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []

        scores: list[tuple[float, dict]] = []
        for chunk in self.chunks:
            score = 0.0
            chunk_token_set = set(chunk["tokens"])
            chunk_len = max(len(chunk["tokens"]), 1)
            for qt in query_tokens:
                if qt in chunk_token_set:
                    tf = chunk["tokens"].count(qt) / chunk_len
                    idf = self.idf.get(qt, 0)
                    score += tf * idf
            if score > 0:
                scores.append((score, chunk))

        scores.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in scores[:top_k]]

    def search_formatted(self, query: str, top_k: int = 4, max_chars: int = 3000) -> str:
        """Devuelve contexto formateado listo para inyectar en el prompt."""
        results = self.search(query, top_k)
        if not results:
            return ""

        parts = []
        total = 0
        for r in results:
            snippet = f"# {r['path']} (línea {r['start_line']})\n{r['content']}"
            if total + len(snippet) > max_chars:
                break
            parts.append(snippet)
            total += len(snippet)

        return "\n\n---\n\n".join(parts)

    # ── Persistencia ──────────────────────────────────────────────────────

    def save(self, path: str = INDEX_FILE):
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({"chunks": self.chunks, "idf": self.idf}, f)
        self._dirty = False

    def load(self, path: str = INDEX_FILE) -> bool:
        if not os.path.isfile(path):
            return False
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.chunks = data.get("chunks", [])
            self.idf    = data.get("idf", {})
            return True
        except Exception:
            return False

    @property
    def size(self) -> int:
        return len(self.chunks)


# Instancia global (singleton)
_index = CodeIndex()


def get_index() -> CodeIndex:
    return _index


def load_or_create(root: str | None = None) -> CodeIndex:
    """Carga el índice existente o crea uno nuevo si se pasa root."""
    if _index.load():
        print(f"[INDEX] Cargado: {_index.size} chunks")
    elif root:
        reindex(root)
    return _index


def reindex(root: str) -> int:
    """Reindexa un directorio completo."""
    n = _index.index_directory(root, verbose=True)
    _index.save()
    return n
