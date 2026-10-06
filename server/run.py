#!/usr/bin/env python3
"""
SmartOrch v2.0 — Inicia el servidor con todo automático.

Uso:
  python run.py                          # auto-detecta workspace VS Code
  python run.py Z:\CYBERRANGE_V2        # workspace explícito
  python run.py --reindex Z:\proyecto   # fuerza reindexar
  python run.py --no-watch              # sin file watcher
  python run.py --no-rag                # solo TF-IDF, sin embeddings
"""
import sys, os, logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)


def main():
    args          = sys.argv[1:]
    force_reindex = "--reindex" in args
    no_watch      = "--no-watch" in args
    no_rag        = "--no-rag"   in args

    workspace  = next((a for a in args if not a.startswith("--")), None)

    # ── Una sola instancia: si ya hay un servidor, se reutiliza en vez de arrancar otro ───────
    from smartorch import singleton
    existing = singleton.running_server()
    if existing:
        print(f"[*] SmartOrch ya está corriendo ({singleton._url('')}); reutilizo ese servidor.")
        if workspace:
            root = os.path.abspath(workspace)
            try:
                r = singleton.register_workspace(root)
                print(f"[*] Proyecto registrado en el servidor existente: {root} ({r.get('tfidf_chunks', 0)} fragmentos)")
            except Exception as e:  # noqa: BLE001
                print(f"[!] No pude registrar {root} en el servidor existente: {e}")
        return

    # ── Auto-detectar workspace ───────────────────────────────────────────────
    from smartorch.core.watcher import detect_vscode_workspace
    from smartorch.core.indexer import get_index, reindex, INDEX_FILE

    if not workspace:
        workspace = detect_vscode_workspace()
        if workspace:
            logger.info(f"[AUTO] Workspace detectado: {workspace}")
        else:
            logger.info("[AUTO] Sin workspace — RAG desactivado hasta indexar")
            logger.info("       Usa: python run.py <ruta>")

    # ── Indexado TF-IDF ───────────────────────────────────────────────────────
    idx = get_index()
    if force_reindex and workspace:
        logger.info(f"[INDEX] Reindexando {workspace}...")
        n = reindex(workspace)
        logger.info(f"[INDEX] TF-IDF: {n} chunks")
    elif os.path.isfile(INDEX_FILE):
        idx.load()
        logger.info(f"[INDEX] TF-IDF: {idx.size} chunks (caché)")
    elif workspace:
        logger.info(f"[INDEX] Primer uso — indexando {workspace}...")
        n = reindex(workspace)
        logger.info(f"[INDEX] TF-IDF: {n} chunks")

    # ── Indexado RAG semántico (opcional) ─────────────────────────────────────
    if not no_rag and workspace:
        _build_rag(workspace, force=force_reindex)

    # ── File watcher ──────────────────────────────────────────────────────────
    watcher = None
    if workspace and not no_watch:
        from smartorch.core.watcher import WorkspaceWatcher
        watcher = WorkspaceWatcher(workspace)
        watcher.start()
        logger.info(f"[WATCH] Monitoreando {workspace}")

    # ── Servidor FastAPI ──────────────────────────────────────────────────────
    from smartorch.api.server import start
    try:
        start()
    except KeyboardInterrupt:
        print("\n[*] SmartOrch detenido.")
    finally:
        if watcher:
            watcher.stop()


def _purge_unscoped_once(purge):
    """Borra una sola vez los fragmentos de versiones anteriores, que no guardaban su workspace."""
    from smartorch.core import datadir
    marker = os.path.join(datadir.CHROMA_DIR, ".scoped-v1")
    if os.path.exists(marker):
        return
    removed = purge()
    os.makedirs(datadir.CHROMA_DIR, exist_ok=True)
    with open(marker, "w", encoding="utf-8") as f:
        f.write("ok")
    logger.info(f"[RAG] Limpieza única: {removed} fragmentos antiguos sin workspace eliminados")


def _build_rag(workspace: str, force: bool = False):
    """Construye el índice semántico RAG si las deps están disponibles."""
    try:
        from smartorch.core import workspaces
        from smartorch.rag.store import chunk_count, purge_unscoped, root_chunk_count, sync_root
        from smartorch.rag.chunker import index_directory

        root = workspaces.register(workspace)
        _purge_unscoped_once(purge_unscoped)

        if root_chunk_count(root) > 0 and not force:
            logger.info(f"[RAG] ChromaDB: {chunk_count()} chunks, {workspace} ya indexado (caché)")
            return

        logger.info(f"[RAG] Construyendo índice semántico para {workspace}...")
        logger.info("[RAG] Cargando sentence-transformers (primera vez: ~10s)...")

        chunks = index_directory(workspace)
        removed = sync_root(root, chunks)
        if chunks:
            logger.info(f"[RAG] Semántico: {len(chunks)} chunks indexados ({removed} obsoletos eliminados)")
        else:
            logger.info("[RAG] Sin archivos para indexar")

    except ImportError:
        logger.info("[RAG] sentence-transformers/chromadb no instalados — solo TF-IDF")
        logger.info("      Para activar RAG semántico:")
        logger.info("      pip install sentence-transformers chromadb")
    except Exception as e:
        logger.warning(f"[RAG] Error al indexar: {e} — usando TF-IDF")


if __name__ == "__main__":
    main()
