"""
Chunker inteligente de código.
Divide archivos en segmentos solapados, preservando contexto de archivo y línea.
"""
import os
import hashlib
from pathlib import Path

EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx",
    ".go", ".rs", ".java", ".c", ".cpp", ".h", ".cs",
    ".rb", ".php", ".sh", ".ps1", ".bat",
    ".md", ".yaml", ".yml", ".json", ".toml", ".ini", ".conf",
}

SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", "venv", ".venv",
    "dist", "build", "out", ".smartorch_db", ".next", "target",
    "vendor", "bower_components", ".idea", ".vscode",
}

CHUNK_LINES   = 60
OVERLAP_LINES = 15
MAX_FILE_SIZE = 400_000  # 400KB


def chunk_file(path: str, root: str = "") -> list[dict]:
    """
    Divide un archivo en chunks con overlap.
    Cada chunk incluye la ruta y número de línea para trazabilidad, y el
    workspace (root) al que pertenece para poder filtrar la búsqueda.
    """
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []

    lines = text.splitlines()
    if not lines:
        return []

    chunks = []
    step   = CHUNK_LINES - OVERLAP_LINES
    relpath = _short_path(path)

    for i in range(0, len(lines), step):
        segment = lines[i : i + CHUNK_LINES]
        # Saltar chunks vacíos
        if not any(l.strip() for l in segment):
            continue

        body       = "\n".join(segment)
        chunk_id   = hashlib.md5(f"{path}::{i}".encode()).hexdigest()
        chunk_text = f"# Archivo: {relpath} | Líneas {i+1}–{i+len(segment)}\n{body}"

        chunks.append({
            "id":   chunk_id,
            "text": chunk_text,
            "metadata": {
                "file":       relpath,
                "root":       root,
                "start_line": i + 1,
                "end_line":   i + len(segment),
            },
        })

    return chunks


def index_directory(root: str) -> list[dict]:
    """
    Recorre un directorio y devuelve todos los chunks de archivos de código.
    """
    all_chunks = []
    root_path  = Path(root).resolve()

    for dirpath, dirnames, filenames in os.walk(root_path):
        # Excluir directorios que no aportan
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]

        for fname in filenames:
            fpath = Path(dirpath) / fname
            if fpath.suffix.lower() not in EXTENSIONS:
                continue
            if fpath.stat().st_size > MAX_FILE_SIZE:
                continue
            all_chunks.extend(chunk_file(str(fpath), str(root_path)))

    return all_chunks


def _short_path(path: str) -> str:
    """Devuelve la parte más descriptiva de la ruta (últimos 3 componentes)."""
    parts = Path(path).parts
    return "/".join(parts[-3:]) if len(parts) >= 3 else path
