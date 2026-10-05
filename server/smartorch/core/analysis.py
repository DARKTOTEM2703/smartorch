"""
Analisis estructural de un proyecto.

No depende del LLM: recorre TODO el workspace con las mismas reglas que el indexador,
mide lenguajes, carpetas y tamanos, extrae clases y funciones de cada archivo, detecta
puntos de entrada y dependencias, y junta TODO/FIXME. El resultado alimenta:
  - un resumen compacto que se inyecta al contexto (el modelo conoce la estructura real),
  - un informe en Markdown que el usuario puede pedir.
"""
import ast
import hashlib
import json
import os
import re
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

from smartorch.core import datadir
from smartorch.rag.chunker import IgnoreRules, is_indexable, skip_dir

MAX_FILES = 6000          # tope de seguridad para repos enormes
MAX_FILE_BYTES = 600_000
MAX_MODULES = 400
MAX_TODOS = 60
FINGERPRINT_TTL = 10      # segundos entre comprobaciones de cambios en disco

LANGUAGES = {
    ".py": "Python", ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript", ".ts": "TypeScript",
    ".tsx": "TypeScript", ".jsx": "JavaScript", ".go": "Go", ".rs": "Rust", ".java": "Java", ".kt": "Kotlin",
    ".kts": "Kotlin", ".scala": "Scala", ".c": "C", ".h": "C/C++", ".cpp": "C++", ".cc": "C++", ".hpp": "C/C++",
    ".cs": "C#", ".rb": "Ruby", ".php": "PHP", ".swift": "Swift", ".dart": "Dart", ".lua": "Lua", ".sh": "Shell",
    ".bash": "Shell", ".ps1": "PowerShell", ".bat": "Batch", ".sql": "SQL", ".html": "HTML", ".css": "CSS",
    ".scss": "CSS", ".vue": "Vue", ".svelte": "Svelte", ".md": "Markdown", ".json": "JSON", ".yaml": "YAML",
    ".yml": "YAML", ".toml": "TOML",
}
CODE_LANGS = {l for l in LANGUAGES.values()} - {"Markdown", "JSON", "YAML", "TOML", "HTML", "CSS"}

ENTRY_NAMES = {"main.py", "run.py", "app.py", "manage.py", "cli.py", "server.py", "__main__.py", "index.js",
               "main.js", "server.js", "index.ts", "main.ts", "main.go", "main.rs", "program.cs", "main.dart"}
_TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec)(/|$)|(^|/)(test_[^/]+|[^/]+_test\.\w+|[^/]+\.(test|spec)\.\w+)$")

_JS_SYMBOLS = [
    (re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)"), "function"),
    (re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)"), "class"),
    (re.compile(r"^\s*(?:export\s+)?(?:const|let)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*(?::[^=]+)?=>"), "function"),
    (re.compile(r"^\s*(?:export\s+)?interface\s+([A-Za-z_$][\w$]*)"), "interface"),
]
_GO_FUNC = re.compile(r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)")
_TODO = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b[:\s-]*(.*)", re.IGNORECASE)

_lock = threading.Lock()
_memory: dict[str, dict] = {}


def _cache_file(root: str) -> str:
    digest = hashlib.sha1(os.path.normcase(root).encode("utf-8")).hexdigest()[:16]
    return os.path.join(datadir.DATA_DIR, "analysis", f"{digest}.json")


def _walk(root: Path):
    count = 0
    rules = IgnoreRules(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not skip_dir(d) and not rules.ignored(Path(dirpath) / d, True))
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if is_indexable(path) and not rules.ignored(path):
                yield path
                count += 1
                if count >= MAX_FILES:
                    return


def _fingerprint(root: Path) -> str:
    n, newest = 0, 0.0
    for path in _walk(root):
        try:
            newest = max(newest, path.stat().st_mtime)
        except OSError:
            continue
        n += 1
    return f"{n}:{int(newest)}"


# ── Extraccion de simbolos ───────────────────────────────────────────────────

def _first_line(doc: Optional[str]) -> str:
    return " ".join((doc or "").strip().splitlines()[:1])[:110]


def _python_symbols(text: str) -> tuple[str, list[dict], bool]:
    """(resumen del modulo, simbolos, tiene bloque __main__)"""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return "", [], False
    symbols: list[dict] = []
    has_main = False
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            methods = [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("_")]
            symbols.append({"kind": "class", "name": node.name, "line": node.lineno,
                            "doc": _first_line(ast.get_docstring(node)), "methods": methods[:12]})
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append({"kind": "def", "name": node.name, "line": node.lineno, "doc": _first_line(ast.get_docstring(node))})
        elif isinstance(node, ast.If):
            test = ast.unparse(node.test) if hasattr(ast, "unparse") else ""
            if "__name__" in test and "__main__" in test:
                has_main = True
    return _first_line(ast.get_docstring(tree)), symbols, has_main


def _regex_symbols(lines: list[str], suffix: str) -> list[dict]:
    out: list[dict] = []
    for n, line in enumerate(lines, start=1):
        if suffix == ".go":
            m = _GO_FUNC.match(line)
            if m:
                out.append({"kind": "func", "name": m.group(1), "line": n})
            continue
        for rx, kind in _JS_SYMBOLS:
            m = rx.match(line)
            if m:
                out.append({"kind": kind, "name": m.group(1), "line": n})
                break
        if len(out) >= 40:
            break
    return out


# ── Dependencias ─────────────────────────────────────────────────────────────

def _dependencies(root: Path) -> dict[str, list[str]]:
    deps: dict[str, list[str]] = {}
    req = root / "requirements.txt"
    if not req.exists():
        req = root / "server" / "requirements.txt"
    if req.exists():
        names = []
        for line in req.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.split("#")[0].strip()
            if line and not line.startswith("-"):
                names.append(re.split(r"[<>=!~\[; ]", line)[0])
        deps["Python (requirements)"] = names[:40]
    pyproject = next((p for p in (root / "pyproject.toml", root / "server" / "pyproject.toml") if p.exists()), None)
    if pyproject:
        text = pyproject.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"dependencies\s*=\s*\[(.*?)\]", text, re.DOTALL)
        if m:
            deps["Python (pyproject)"] = [re.split(r"[<>=!~\[; ]", d.strip().strip('"\''))[0]
                                          for d in m.group(1).replace("\n", " ").split(",") if d.strip()][:40]
    for pkg in (root / "package.json", root / "extension" / "package.json"):
        if pkg.exists():
            try:
                data = json.loads(pkg.read_text(encoding="utf-8", errors="ignore"))
            except ValueError:
                continue
            names = list((data.get("dependencies") or {}).keys())[:40]
            if names:
                deps[f"Node ({pkg.relative_to(root).as_posix()})"] = names
    go = root / "go.mod"
    if go.exists():
        deps["Go"] = re.findall(r"^\s*([\w./-]+)\s+v", go.read_text(encoding="utf-8", errors="ignore"), re.MULTILINE)[:40]
    return {k: v for k, v in deps.items() if v}


# ── Analisis ─────────────────────────────────────────────────────────────────

def analyze(root: str) -> dict:
    base = Path(root).resolve()
    langs_files: Counter = Counter()
    langs_loc: Counter = Counter()
    dirs_files: Counter = Counter()
    dirs_loc: Counter = Counter()
    modules: list[dict] = []
    entry_points: list[str] = []
    todos: list[dict] = []
    tests = 0
    total_loc = 0
    files = 0

    for path in _walk(base):
        rel = path.relative_to(base).as_posix()
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        lines = text.splitlines()
        loc = sum(1 for l in lines if l.strip())
        suffix = path.suffix.lower()
        lang = LANGUAGES.get(suffix, path.suffix.lstrip(".").upper() or "otros")

        files += 1
        total_loc += loc
        langs_files[lang] += 1
        langs_loc[lang] += loc
        parts = rel.split("/")
        for depth in range(1, min(len(parts), 4)):
            key = "/".join(parts[:depth])
            dirs_files[key] += 1
            dirs_loc[key] += loc

        is_test = bool(_TEST_PATH.search(rel))
        if is_test:
            tests += 1

        summary, symbols, has_main = "", [], False
        if suffix == ".py":
            summary, symbols, has_main = _python_symbols(text)
        elif suffix in (".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".go"):
            symbols = _regex_symbols(lines, suffix)

        if not is_test and (path.name.lower() in ENTRY_NAMES or has_main):
            entry_points.append(rel)

        if lang in CODE_LANGS or suffix == ".md":
            for n, line in enumerate(lines, start=1):
                m = _TODO.search(line)
                if m and len(todos) < MAX_TODOS:
                    todos.append({"path": rel, "line": n, "tag": m.group(1).upper(), "text": m.group(2).strip()[:100]})

        if lang in CODE_LANGS:
            modules.append({"path": rel, "language": lang, "loc": loc, "summary": summary, "symbols": symbols})

    modules.sort(key=lambda m: m["loc"], reverse=True)
    languages = [{"name": name, "files": langs_files[name], "loc": langs_loc[name]}
                 for name, _ in langs_loc.most_common(12)]
    top_dirs = [{"path": d, "files": dirs_files[d], "loc": dirs_loc[d]}
                for d, _ in dirs_loc.most_common(60)]
    top_dirs.sort(key=lambda d: d["path"])

    return {
        "root": str(base),
        "name": base.name,
        "generated": time.time(),
        "files": files,
        "loc": total_loc,
        "tests": tests,
        "languages": languages,
        "dirs": top_dirs,
        "entry_points": sorted(set(entry_points))[:30],
        "dependencies": _dependencies(base),
        "modules": modules[:MAX_MODULES],
        "todos": todos,
        "truncated": files >= MAX_FILES,
    }


def get_profile(root: str, force: bool = False) -> dict:
    """Perfil del proyecto, reutilizando el calculo previo mientras los archivos no cambien."""
    base = Path(root).resolve()
    key = str(base)
    now = time.time()
    with _lock:
        recent = _memory.get(key)
        if recent and not force and now - recent.get("_checked", 0) < FINGERPRINT_TTL:
            return recent  # se verifico hace instantes: no volver a recorrer el disco
    fingerprint = _fingerprint(base)
    with _lock:
        cached = _memory.get(key)
        if cached is None and not force:
            try:
                with open(_cache_file(key), encoding="utf-8") as f:
                    cached = json.load(f)
                _memory[key] = cached
            except (OSError, ValueError):
                cached = None
        if cached and not force and cached.get("fingerprint") == fingerprint:
            cached["_checked"] = now
            return cached
    profile = analyze(key)
    profile["fingerprint"] = fingerprint
    profile["_checked"] = time.time()
    with _lock:
        _memory[key] = profile
        try:
            os.makedirs(os.path.dirname(_cache_file(key)), exist_ok=True)
            with open(_cache_file(key), "w", encoding="utf-8") as f:
                json.dump(profile, f, ensure_ascii=False)
        except OSError:
            pass
    return profile


# ── Presentacion ─────────────────────────────────────────────────────────────

def _symbol_text(sym: dict) -> str:
    if sym["kind"] == "class":
        methods = f" [{', '.join(sym.get('methods', [])[:6])}]" if sym.get("methods") else ""
        return f"{sym['name']}{methods}"
    return f"{sym['name']}()" if sym["kind"] in ("def", "function", "func") else sym["name"]


def overview(profile: dict, max_chars: int = 2600) -> str:
    """Resumen compacto para el contexto del modelo: estructura real, no fragmentos sueltos."""
    langs = ", ".join(f"{l['name']} {l['files']} arch./{l['loc']} lín." for l in profile["languages"][:5])
    out = [f"Proyecto «{profile['name']}»: {profile['files']} archivos, {profile['loc']} líneas de contenido. {langs}."]
    if profile["entry_points"]:
        out.append("Puntos de entrada: " + ", ".join(profile["entry_points"][:8]) + ".")
    if profile["dependencies"]:
        out.append("Dependencias: " + "; ".join(f"{k}: {', '.join(v[:10])}" for k, v in list(profile["dependencies"].items())[:3]) + ".")
    out.append("Carpetas (archivos/líneas):")
    for d in profile["dirs"]:
        if d["path"].count("/") <= 1:
            out.append(f"  {d['path']}/ ({d['files']}/{d['loc']})")
    out.append("Módulos principales:")
    for m in profile["modules"][:40]:
        syms = ", ".join(_symbol_text(s) for s in m["symbols"][:5])
        doc = f" — {m['summary']}" if m["summary"] else ""
        out.append(f"  {m['path']} ({m['loc']} lín.){doc}" + (f": {syms}" if syms else ""))
    text = "\n".join(out)
    return text if len(text) <= max_chars else text[:max_chars].rsplit("\n", 1)[0] + "\n  [... resumen recortado]"


def report_markdown(profile: dict) -> str:
    p = profile
    lines = [f"# Análisis de {p['name']}", "",
             f"- **Ruta:** `{p['root']}`",
             f"- **Archivos analizados:** {p['files']}{' (límite alcanzado)' if p.get('truncated') else ''}",
             f"- **Líneas de contenido:** {p['loc']}",
             f"- **Archivos de prueba:** {p['tests']}", "", "## Lenguajes", "",
             "| Lenguaje | Archivos | Líneas |", "|---|---:|---:|"]
    lines += [f"| {l['name']} | {l['files']} | {l['loc']} |" for l in p["languages"]]
    lines += ["", "## Estructura", ""]
    lines += [f"- `{d['path']}/` — {d['files']} archivos, {d['loc']} líneas" for d in p["dirs"] if d["path"].count("/") <= 2]
    if p["entry_points"]:
        lines += ["", "## Puntos de entrada", ""] + [f"- `{e}`" for e in p["entry_points"]]
    if p["dependencies"]:
        lines += ["", "## Dependencias", ""]
        for source, names in p["dependencies"].items():
            lines.append(f"- **{source}:** {', '.join(names)}")
    lines += ["", "## Módulos más grandes", ""]
    for m in p["modules"][:25]:
        syms = ", ".join(_symbol_text(s) for s in m["symbols"][:8])
        doc = f" — {m['summary']}" if m["summary"] else ""
        lines.append(f"- `{m['path']}` ({m['loc']} líneas){doc}")
        if syms:
            lines.append(f"  - {syms}")
    if p["todos"]:
        lines += ["", "## Pendientes marcados en el código", ""]
        lines += [f"- `{t['path']}:{t['line']}` **{t['tag']}** {t['text']}" for t in p["todos"][:30]]
    return "\n".join(lines) + "\n"


def symbol_files(profile: dict, query: str, limit: int = 4) -> list[str]:
    """Archivos que definen un simbolo mencionado en la consulta (refuerza el RAG)."""
    words = {w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", query)}
    hits: dict[str, int] = defaultdict(int)
    for m in profile["modules"]:
        for s in m["symbols"]:
            if s["name"].lower() in words:
                hits[m["path"]] += 2
        stem = Path(m["path"]).stem.lower()
        if stem in words:
            hits[m["path"]] += 1
    return [p for p, _ in sorted(hits.items(), key=lambda kv: kv[1], reverse=True)[:limit]]
