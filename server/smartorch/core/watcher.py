"""
Watcher — monitorea cambios en el workspace y reindexia automáticamente
Sin dependencias externas: usa threading + os.stat polling
"""
import os
import threading
import time
import logging
from smartorch.core.indexer import get_index, INDEX_FILE

logger = logging.getLogger(__name__)

POLL_INTERVAL = 8   # segundos entre polls
DEBOUNCE      = 3   # esperar N segundos sin cambios antes de reindexar

WATCH_EXTENSIONS = {
    '.py', '.js', '.ts', '.tsx', '.jsx', '.go', '.rs', '.java',
    '.c', '.cpp', '.h', '.cs', '.rb', '.php', '.md', '.yaml',
    '.yml', '.json', '.toml', '.env', '.sh', '.ps1', '.bat',
    '.html', '.css', '.scss', '.vue', '.svelte', '.kt', '.swift',
}

SKIP_DIRS = {
    '__pycache__', '.git', 'node_modules', 'venv', '.venv',
    'dist', 'build', '.build', '.dist', 'compilados',
    'onefile-build', '.idea', '.vscode',
}


class WorkspaceWatcher:
    def __init__(self, root: str):
        self.root      = os.path.abspath(root)
        self._snapshots: dict[str, float] = {}
        self._running  = False
        self._thread: threading.Thread | None = None
        self._pending_reindex = False
        self._last_change = 0.0

    def _scan(self) -> dict[str, float]:
        """Toma snapshot de mtimes de todos los archivos relevantes."""
        snapshot = {}
        own_index = os.path.abspath(INDEX_FILE)
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith('.')]
            for fname in filenames:
                if os.path.splitext(fname)[1].lower() in WATCH_EXTENSIONS:
                    fpath = os.path.join(dirpath, fname)
                    if os.path.abspath(fpath) == own_index:
                        continue  # el indice propio no debe disparar reindexados
                    try:
                        snapshot[fpath] = os.stat(fpath).st_mtime
                    except OSError:
                        pass
        return snapshot

    def _detect_changes(self, new_snap: dict[str, float]) -> list[str]:
        """Detecta archivos nuevos, modificados o eliminados."""
        changed = []
        for path, mtime in new_snap.items():
            if path not in self._snapshots or self._snapshots[path] != mtime:
                changed.append(path)
        for path in self._snapshots:
            if path not in new_snap:
                changed.append(path)
        return changed

    def _reindex(self):
        """Reindexia el workspace — TF-IDF + ChromaDB semántico."""
        idx = get_index()
        logger.info(f"[WATCHER] Reindexando TF-IDF: {self.root}...")
        n = idx.index_directory(self.root, verbose=False)
        idx.save()
        logger.info(f"[WATCHER] TF-IDF actualizado: {n} chunks")

        # Reindexar también ChromaDB semántico
        try:
            from smartorch.rag.chunker import index_directory
            from smartorch.rag.store import upsert_chunks
            chunks = index_directory(self.root)
            if chunks:
                upsert_chunks(chunks)
                logger.info(f"[WATCHER] RAG semántico actualizado: {len(chunks)} chunks")
        except Exception as e:
            logger.warning(f"[WATCHER] RAG semántico no actualizado: {e}")

    def _loop(self):
        logger.info(f"[WATCHER] Monitoreando: {self.root}")
        self._snapshots = self._scan()

        while self._running:
            time.sleep(POLL_INTERVAL)
            new_snap = self._scan()
            changed  = self._detect_changes(new_snap)

            if changed:
                self._snapshots  = new_snap
                self._last_change = time.time()
                self._pending_reindex = True
                logger.info(f"[WATCHER] {len(changed)} archivo(s) cambiado(s)")

            # Reindexar después del debounce (N segundos sin cambios)
            if self._pending_reindex and (time.time() - self._last_change) >= DEBOUNCE:
                self._pending_reindex = False
                try:
                    self._reindex()
                except Exception as e:
                    logger.error(f"[WATCHER] Error reindexando: {e}")

    def start(self):
        self._running = True
        self._thread  = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info(f"[WATCHER] Iniciado (poll cada {POLL_INTERVAL}s, debounce {DEBOUNCE}s)")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("[WATCHER] Detenido")


# ── Auto-detección de workspace ──────────────────────────────────────

def detect_vscode_workspace() -> str | None:
    """
    Intenta detectar el workspace abierto en VS Code buscando
    archivos .vscode/settings.json o .git en directorios recientes.
    """
    # 1. Variable de entorno (si VS Code la expone al terminal integrado)
    ws = os.environ.get("VSCODE_WORKSPACE_FOLDER") or os.environ.get("WORKSPACE_FOLDER")
    if ws and os.path.isdir(ws):
        return ws

    # 2. Buscar en directorios comunes de proyectos
    candidates = []
    home = os.path.expanduser("~")
    bases = [os.getcwd(), os.path.dirname(os.getcwd())]
    bases += [os.path.join(home, d) for d in ("Documents", "projects", "dev", "code", "src", "repos")]
    if os.name == "nt":
        bases += [f"{letter}:\\" for letter in "CDEFGHZ"]
    for base in dict.fromkeys(bases):
        if not os.path.isdir(base):
            continue
        for entry in os.scandir(base):
            if entry.is_dir() and not entry.name.startswith('.'):
                vscode_dir = os.path.join(entry.path, ".vscode")
                git_dir    = os.path.join(entry.path, ".git")
                if os.path.isdir(vscode_dir) or os.path.isdir(git_dir):
                    try:
                        mtime = os.stat(entry.path).st_mtime
                        candidates.append((mtime, entry.path))
                    except OSError:
                        pass

    if candidates:
        candidates.sort(reverse=True)
        return candidates[0][1]

    return None
