"""
Chunker inteligente de código.
Divide archivos en segmentos solapados, preservando contexto de archivo y línea.
"""
import fnmatch
import os
import hashlib
from pathlib import Path

EXTENSIONS = {
    # lenguajes
    ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt", ".kts", ".scala",
    ".c", ".cpp", ".cc", ".h", ".hpp", ".cs", ".rb", ".php", ".swift", ".dart", ".lua", ".r", ".ex", ".exs",
    ".hs", ".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd", ".sql",
    # web
    ".html", ".htm", ".css", ".scss", ".sass", ".less", ".vue", ".svelte", ".astro",
    # configuracion y documentacion
    ".md", ".rst", ".txt", ".yaml", ".yml", ".json", ".toml", ".ini", ".conf", ".cfg", ".xml", ".gradle",
    ".proto", ".graphql", ".tf",
}

# Archivos sin extension (o con nombre especial) que si aportan contexto
SPECIAL_NAMES = {
    "dockerfile", "makefile", "procfile", "gemfile", "rakefile", "jenkinsfile", "vagrantfile",
    ".gitignore", ".dockerignore", ".editorconfig", ".env.example",
}

SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", "venv", ".venv", "env",
    "dist", "build", "out", ".smartorch_db", ".next", ".nuxt", "target", "coverage",
    "vendor", "bower_components", ".idea", ".vscode", ".pytest_cache", ".mypy_cache", ".tox",
    "site-packages", ".gradle", ".dart_tool", "Pods", "DerivedData", ".git_old",
}

# Archivos generados o enormes que solo meten ruido
SKIP_FILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "pipfile.lock",
    "composer.lock", "cargo.lock", "gemfile.lock", "go.sum",
}
SKIP_SUFFIXES = (".min.js", ".min.css", ".map", ".lock", ".bundle.js")


def skip_dir(name: str) -> bool:
    """Carpetas que nunca se recorren: dependencias, builds, ocultas y artefactos de empaquetado."""
    return name in SKIP_DIRS or name.startswith(".") or name.endswith((".egg-info", ".dist-info"))


class IgnoreRules:
    """
    Respeta los .gitignore del workspace, incluidos los de subcarpetas (patrones simples),
    y un .smartorchignore opcional en la raiz.
    """

    def __init__(self, root):
        self.root = Path(root).resolve()
        self._cache: dict[Path, list[str]] = {}

    def _patterns(self, directory: Path) -> list[str]:
        if directory in self._cache:
            return self._cache[directory]
        patterns: list[str] = []
        names = (".gitignore", ".smartorchignore") if directory == self.root else (".gitignore",)
        for name in names:
            try:
                for line in (directory / name).read_text(encoding="utf-8", errors="ignore").splitlines():
                    line = line.strip()
                    if line and not line.startswith(("#", "!")):
                        patterns.append(line)
            except OSError:
                continue
        self._cache[directory] = patterns
        return patterns

    @staticmethod
    def _match(patterns: list[str], rel: str, is_dir: bool) -> bool:
        name = rel.split("/")[-1]
        for pat in patterns:
            dir_only = pat.endswith("/")
            core = pat.strip("/")
            if not core or (dir_only and not is_dir):
                continue
            if "/" in core:  # anclado a la carpeta del .gitignore o con subcarpeta
                if fnmatch.fnmatch(rel, core.lstrip("/")) or fnmatch.fnmatch(rel, "*/" + core.lstrip("/")):
                    return True
            elif fnmatch.fnmatch(name, core):
                return True
        return False

    def ignored(self, path, is_dir: bool = False) -> bool:
        try:
            full = Path(path).resolve()
            parts = full.relative_to(self.root).parts
        except ValueError:
            return False
        ancestors = [self.root] + [self.root.joinpath(*parts[:i]) for i in range(1, len(parts))]
        for ancestor in ancestors:
            patterns = self._patterns(ancestor)
            if patterns and self._match(patterns, full.relative_to(ancestor).as_posix(), is_dir):
                return True
        return False


def is_indexable(path: Path) -> bool:
    name = path.name.lower()
    if name in SKIP_FILES or name.endswith(SKIP_SUFFIXES):
        return False
    return path.suffix.lower() in EXTENSIONS or name in SPECIAL_NAMES

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
    relpath = _relative_path(path, root)

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

    rules = IgnoreRules(root_path)
    for dirpath, dirnames, filenames in os.walk(root_path):
        # Excluir directorios que no aportan
        dirnames[:] = [d for d in dirnames if not skip_dir(d) and not rules.ignored(Path(dirpath) / d, True)]

        for fname in filenames:
            fpath = Path(dirpath) / fname
            if not is_indexable(fpath) or rules.ignored(fpath):
                continue
            try:
                if fpath.stat().st_size > MAX_FILE_SIZE:
                    continue
            except OSError:
                continue
            all_chunks.extend(chunk_file(str(fpath), str(root_path)))

    return all_chunks


def _relative_path(path: str, root: str) -> str:
    """Ruta relativa al workspace (con /); sin root cae en los ultimos 3 componentes."""
    if root:
        try:
            return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
        except ValueError:
            pass
    return _short_path(path)


def _short_path(path: str) -> str:
    """Devuelve la parte más descriptiva de la ruta (últimos 3 componentes)."""
    parts = Path(path).parts
    return "/".join(parts[-3:]) if len(parts) >= 3 else path
