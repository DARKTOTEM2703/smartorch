"""
Grafo de codigo en SQLite: simbolos, quien llama a quien, quien importa a quien, duplicados y olores
(SOLID/DRY). Se calcula con analisis estatico (AST), sin gastar un solo token del modelo.

Sirve para que el agente pida "esta funcion y sus vecinos" en vez de leer archivos enteros: menos contexto,
mas precision. Es incremental (solo se re-analiza lo que cambio). Python se analiza con AST completo;
JS/TS/Go/etc. aportan simbolos e imports por expresiones regulares (aproximado, y asi se documenta).

Limite conocido: las llamadas se resuelven por NOMBRE, no por tipo; si dos funciones comparten nombre,
ambas aparecen como candidatas. Para un modelo es suficiente: ve los vecinos probables y lee el codigo.
"""
from __future__ import annotations

import ast
import hashlib
import os
import re
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from smartorch.core import analysis, datadir

MAX_FILE_BYTES = 400_000
DUP_MIN_LINES = 5          # funciones mas cortas no cuentan como duplicado
LONG_FUNCTION = 60
BIG_CLASS_METHODS = 15

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, hash TEXT, loc INTEGER, lang TEXT);
CREATE TABLE IF NOT EXISTS symbols(
    id INTEGER PRIMARY KEY, file TEXT, name TEXT, qual TEXT, kind TEXT, line INTEGER, end_line INTEGER,
    sig TEXT, doc TEXT, ast_hash TEXT, complexity INTEGER, parent TEXT);
CREATE TABLE IF NOT EXISTS edges(file TEXT, src TEXT, dst TEXT, kind TEXT, line INTEGER, via TEXT DEFAULT '');
CREATE INDEX IF NOT EXISTS sym_name ON symbols(name);
CREATE INDEX IF NOT EXISTS sym_file ON symbols(file);
CREATE INDEX IF NOT EXISTS edge_dst ON edges(dst);
CREATE INDEX IF NOT EXISTS edge_file ON edges(file);
"""

_JS_IMPORT = re.compile(r"""(?:import\s[^'"]*?from\s*|import\s*|require\()\s*['"]([^'"]+)['"]""")


def _db_path(root: str) -> str:
    digest = hashlib.sha1(os.path.normcase(root).encode("utf-8")).hexdigest()[:16]
    return os.path.join(datadir.DATA_DIR, "codegraph", f"{digest}.db")


def _connect(root: str) -> sqlite3.Connection:
    path = _db_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


# ── Analisis de Python ──────────────────────────────────────────────────────

def _complexity(node: ast.AST) -> int:
    branches = (ast.If, ast.For, ast.While, ast.Try, ast.With, ast.BoolOp, ast.IfExp, ast.ExceptHandler,
                ast.comprehension, ast.AsyncFor, ast.AsyncWith)
    return 1 + sum(isinstance(n, branches) for n in ast.walk(node))


class _Normalize(ast.NodeTransformer):
    """Para detectar duplicados: ignora nombres de variables, argumentos y constantes."""

    def visit_Name(self, node):
        return ast.copy_location(ast.Name(id="_", ctx=node.ctx), node)

    def visit_arg(self, node):
        return ast.copy_location(ast.arg(arg="_", annotation=None), node)

    def visit_Constant(self, node):
        return ast.copy_location(ast.Constant(value=type(node.value).__name__), node)


def _ast_hash(node: ast.AST) -> str:
    body = [n for n in node.body if not (isinstance(n, ast.Expr) and isinstance(getattr(n, "value", None), ast.Constant))]
    clone = ast.Module(body=[_Normalize().visit(ast.parse(ast.unparse(b))) for b in body], type_ignores=[])
    return hashlib.sha1(ast.dump(clone).encode()).hexdigest()[:16]


def _signature(node) -> str:
    try:
        args = ast.unparse(node.args)
    except Exception:  # noqa: BLE001
        args = "..."
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    returns = f" -> {ast.unparse(node.returns)}" if getattr(node, "returns", None) else ""
    return f"{prefix} {node.name}({args}){returns}"


def _receiver(func: ast.AST) -> str:
    """'re' en re.search(...): modulo importado desde el que se llama, si el receptor es un nombre simple."""
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return func.value.id
    return ""


def _call_name(func: ast.AST) -> Optional[str]:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _parse_python(rel: str, text: str) -> tuple[list[dict], list[dict]]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return [], []
    symbols: list[dict] = []
    edges: list[dict] = []
    aliases: dict[str, str] = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                aliases[a.asname or a.name.split(".")[0]] = a.name
        elif isinstance(n, ast.ImportFrom) and n.module:
            for a in n.names:
                aliases[a.asname or a.name] = n.module + "." + a.name

    def walk(body, prefix: str, parent: Optional[str]):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = f"{prefix}{node.name}"
                end = getattr(node, "end_lineno", node.lineno)
                try:
                    h = _ast_hash(node) if end - node.lineno + 1 >= DUP_MIN_LINES else ""
                except Exception:  # noqa: BLE001
                    h = ""
                symbols.append({"name": node.name, "qual": qual, "kind": "method" if parent else "function",
                                "line": node.lineno, "end": end, "sig": _signature(node),
                                "doc": " ".join((ast.get_docstring(node) or "").split())[:160],
                                "hash": h, "cx": _complexity(node), "parent": parent})
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call):
                        name = _call_name(sub.func)
                        if name:
                            via = aliases.get(_receiver(sub.func), "")
                            edges.append({"src": qual, "dst": name, "kind": "call", "line": sub.lineno, "via": via})
                walk(node.body, qual + ".", None)
            elif isinstance(node, ast.ClassDef):
                qual = f"{prefix}{node.name}"
                symbols.append({"name": node.name, "qual": qual, "kind": "class", "line": node.lineno,
                                "end": getattr(node, "end_lineno", node.lineno),
                                "sig": f"class {node.name}", "doc": " ".join((ast.get_docstring(node) or "").split())[:160],
                                "hash": "", "cx": 0, "parent": parent})
                for base in node.bases:
                    name = _call_name(base)
                    if name:
                        edges.append({"src": qual, "dst": name, "kind": "inherit", "line": node.lineno})
                walk(node.body, qual + ".", qual)

    walk(tree.body, "", None)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                edges.append({"src": "", "dst": a.name, "kind": "import", "line": node.lineno})
        elif isinstance(node, ast.ImportFrom):
            mod = ("." * node.level) + (node.module or "")
            edges.append({"src": "", "dst": mod, "kind": "import", "line": node.lineno})
    return symbols, edges


def _parse_other(rel: str, text: str, suffix: str) -> tuple[list[dict], list[dict]]:
    symbols = []
    for s in analysis._regex_symbols(text.splitlines(), suffix):
        symbols.append({"name": s["name"], "qual": s["name"], "kind": s.get("kind", "function"), "line": s.get("line", 1),
                        "end": s.get("line", 1), "sig": s["name"], "doc": "", "hash": "", "cx": 0, "parent": None})
    edges = [{"src": "", "dst": m, "kind": "import", "line": 0} for m in _JS_IMPORT.findall(text)]
    return symbols, edges


# ── Construccion incremental ────────────────────────────────────────────────

def build(root: str) -> dict:
    """Crea o actualiza el grafo. Solo re-analiza los archivos cuyo contenido cambio."""
    root_path = Path(root).resolve()
    stats = {"files": 0, "parsed": 0, "reused": 0, "removed": 0}
    with _lock, _connect(str(root_path)) as conn:
        known = {r["path"]: r["hash"] for r in conn.execute("SELECT path, hash FROM files")}
        seen: set[str] = set()
        for p in analysis._walk(root_path):
            rel = p.relative_to(root_path).as_posix()
            try:
                if p.stat().st_size > MAX_FILE_BYTES:
                    continue
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            seen.add(rel)
            stats["files"] += 1
            digest = hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()[:16]
            if known.get(rel) == digest:
                stats["reused"] += 1
                continue
            _drop(conn, rel)
            suffix = p.suffix.lower()
            symbols, edges = _parse_python(rel, text) if suffix == ".py" else _parse_other(rel, text, suffix)
            conn.execute("INSERT INTO files VALUES(?,?,?,?)", (rel, digest, text.count("\n") + 1, suffix.lstrip(".")))
            conn.executemany(
                "INSERT INTO symbols(file,name,qual,kind,line,end_line,sig,doc,ast_hash,complexity,parent) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                [(rel, s["name"], s["qual"], s["kind"], s["line"], s["end"], s["sig"], s["doc"], s["hash"], s["cx"], s["parent"])
                 for s in symbols])
            conn.executemany("INSERT INTO edges VALUES(?,?,?,?,?,?)",
                             [(rel, e["src"], e["dst"], e["kind"], e["line"], e.get("via", "")) for e in edges])
            stats["parsed"] += 1
        for gone in set(known) - seen:
            _drop(conn, gone)
            stats["removed"] += 1
    return stats


def _drop(conn: sqlite3.Connection, rel: str) -> None:
    for table in ("files", "symbols", "edges"):
        col = "path" if table == "files" else "file"
        conn.execute(f"DELETE FROM {table} WHERE {col}=?", (rel,))


def exists(root: str) -> bool:
    return os.path.isfile(_db_path(str(Path(root).resolve())))


def ensure(root: str) -> None:
    """Primera vez: construye el grafo (rapido, solo AST). Despues se mantiene con build()."""
    if not exists(root):
        build(root)


# ── Consultas ───────────────────────────────────────────────────────────────

def find(root: str, name: str, limit: int = 10) -> list[dict]:
    """Definiciones por nombre; acepta 'Clase.metodo' o solo 'metodo'."""
    key = str(Path(root).resolve())
    with _connect(key) as conn:
        rows = conn.execute(
            "SELECT * FROM symbols WHERE name=? OR qual=? ORDER BY (kind='class') DESC, file LIMIT ?",
            (name.split(".")[-1], name, limit)).fetchall()
        if name.count(".") and not any(r["qual"] == name for r in rows):
            rows = [r for r in rows if r["qual"].endswith(name)]
    return [dict(r) for r in rows]


def _via_matches(via: str, file: str) -> bool:
    """Una llamada tipo `re.search` solo apunta a un simbolo del proyecto si `re` es ese modulo del proyecto."""
    if not via:
        return True
    module = Path(file).with_suffix("").as_posix().replace("/", ".")
    parts = via.split(".")
    return module == via or module.endswith("." + via) or module.split(".")[-1] in parts


def callers(root: str, name: str, limit: int = 12, def_file: Optional[str] = None) -> list[dict]:
    key = str(Path(root).resolve())
    short = name.split(".")[-1]
    with _connect(key) as conn:
        rows = conn.execute(
            "SELECT file, src, line, via FROM edges WHERE kind='call' AND dst=? AND src<>'' ORDER BY file, line",
            (short,)).fetchall()
    out = [dict(r) for r in rows if def_file is None or _via_matches(r["via"], def_file)]
    return out[:limit]


def callees(root: str, qual: str, file: Optional[str] = None, limit: int = 15) -> list[dict]:
    """Funciones del proyecto que `qual` llama (solo las que existen como simbolo)."""
    key = str(Path(root).resolve())
    with _connect(key) as conn:
        params: list = [qual]
        extra = ""
        if file:
            extra, params = " AND e.file=?", [qual, file]
        rows = conn.execute(
            "SELECT DISTINCT s.file, s.qual, s.sig, s.line, e.via FROM edges e JOIN symbols s ON s.name=e.dst "
            "WHERE e.kind='call' AND e.src=?" + extra + " AND s.kind IN ('function','method','class')",
            tuple(params)).fetchall()
    return [dict(r) for r in rows if _via_matches(r["via"], r["file"])][:limit]


def importers(root: str, rel: str) -> list[str]:
    """Archivos que importan al modulo `rel` (por coincidencia del nombre del modulo)."""
    module = Path(rel).with_suffix("").as_posix().replace("/", ".")
    leaf = module.split(".")[-1]
    key = str(Path(root).resolve())
    with _connect(key) as conn:
        rows = conn.execute("SELECT DISTINCT file, dst FROM edges WHERE kind='import'").fetchall()
    out = []
    for r in rows:
        mod = r["dst"].lstrip(".")
        if r["file"] != rel and (mod == module or mod.endswith("." + module) or mod.split(".")[-1] == leaf):
            out.append(r["file"])
    return sorted(set(out))


def slice_for(root: str, name: str, max_chars: int = 3500) -> str:
    """Todo lo que un modelo necesita para tocar `name`: su codigo, quien lo llama y que llama. Nada mas."""
    defs = find(root, name, limit=3)
    if not defs:
        return f"No hay ningún símbolo llamado «{name}» en el grafo del proyecto."
    root_path = Path(root).resolve()
    parts: list[str] = []
    budget = max_chars
    for d in defs:
        try:
            lines = (root_path / d["file"]).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        body = "\n".join(f"{n}: {lines[n - 1]}" for n in range(d["line"], min(d["end_line"], len(lines)) + 1))
        if len(body) > budget // 2:
            body = body[: budget // 2].rsplit("\n", 1)[0] + "\n…(recortado; usa read_file con start_line/end_line)"
        parts.append(f"── {d['qual']} · {d['file']}:{d['line']}-{d['end_line']}\n{body}")
        budget -= len(body)
        near_callers = callers(root, d["name"], limit=6, def_file=d["file"])
        if near_callers:
            parts.append("Lo llaman: " + ", ".join(f"{c['src']} ({c['file']}:{c['line']})" for c in near_callers))
        near_callees = callees(root, d["qual"], file=d["file"], limit=8)
        if near_callees:
            parts.append("Llama a: " + "; ".join(f"{c['sig']} ({c['file']}:{c['line']})" for c in near_callees))
        if d["kind"] == "class":
            methods = [m for m in find_children(root, d["qual"], d["file"])]
            if methods:
                parts.append("Métodos: " + "; ".join(m["sig"] for m in methods[:20]))
    return "\n".join(parts)[:max_chars + 1200]


def find_children(root: str, qual: str, file: str) -> list[dict]:
    with _connect(str(Path(root).resolve())) as conn:
        rows = conn.execute("SELECT * FROM symbols WHERE parent=? AND file=? ORDER BY line", (qual, file)).fetchall()
    return [dict(r) for r in rows]


# ── Olores: DRY y SOLID ─────────────────────────────────────────────────────

def duplicates(root: str) -> list[list[dict]]:
    """Grupos de funciones con el mismo AST normalizado (copiar y pegar con otros nombres)."""
    with _connect(str(Path(root).resolve())) as conn:
        rows = conn.execute(
            "SELECT * FROM symbols WHERE ast_hash<>'' AND ast_hash IN "
            "(SELECT ast_hash FROM symbols WHERE ast_hash<>'' GROUP BY ast_hash HAVING COUNT(*)>1) "
            "ORDER BY ast_hash, file, line").fetchall()
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["ast_hash"], []).append(dict(r))
    return list(groups.values())


def smells(root: str) -> dict:
    """Indicios (no veredictos) de problemas de diseno, para que el modelo sepa donde mirar."""
    key = str(Path(root).resolve())
    with _connect(key) as conn:
        long_fns = [dict(r) for r in conn.execute(
            "SELECT file, qual, line, end_line-line+1 AS lines, complexity FROM symbols "
            "WHERE kind IN ('function','method') AND (end_line-line+1>? OR complexity>15) "
            "ORDER BY lines DESC LIMIT 15", (LONG_FUNCTION,))]
        big_classes = [dict(r) for r in conn.execute(
            "SELECT p.file, p.qual, p.line, COUNT(c.id) AS methods FROM symbols p JOIN symbols c "
            "ON c.parent=p.qual AND c.file=p.file WHERE p.kind='class' GROUP BY p.file, p.qual "
            "HAVING methods>? ORDER BY methods DESC LIMIT 10", (BIG_CLASS_METHODS,))]
        big_files = [dict(r) for r in conn.execute(
            "SELECT f.path AS file, f.loc, COUNT(s.id) AS symbols FROM files f JOIN symbols s ON s.file=f.path "
            "GROUP BY f.path HAVING symbols>40 ORDER BY symbols DESC LIMIT 10")]
    return {"long_functions": long_fns, "big_classes": big_classes, "big_files": big_files,
            "duplicates": [[(d["file"], d["qual"], d["line"]) for d in g] for g in duplicates(root)[:10]],
            "cycles": import_cycles(root)}


def import_cycles(root: str, limit: int = 5) -> list[list[str]]:
    """Ciclos de importacion entre archivos del proyecto (acoplamiento circular)."""
    key = str(Path(root).resolve())
    with _connect(key) as conn:
        files = [r["path"] for r in conn.execute("SELECT path FROM files WHERE lang='py'")]
        edges = conn.execute("SELECT file, dst FROM edges WHERE kind='import'").fetchall()
    by_module: dict[str, str] = {}
    for f in files:
        mod = Path(f).with_suffix("").as_posix().replace("/", ".")
        by_module[mod] = f
        if mod.endswith(".__init__"):
            by_module[mod[: -len(".__init__")]] = f
    graph: dict[str, set[str]] = {f: set() for f in files}
    for e in edges:
        target = by_module.get(e["dst"].lstrip("."))
        if target is None:
            target = next((f for m, f in by_module.items() if m.endswith("." + e["dst"].lstrip("."))), None)
        if target and target != e["file"] and e["file"] in graph:
            graph[e["file"]].add(target)
    cycles: list[list[str]] = []
    state: dict[str, int] = {}
    stack: list[str] = []

    def dfs(node: str):
        state[node] = 1
        stack.append(node)
        for nxt in sorted(graph[node]):
            if state.get(nxt, 0) == 0:
                dfs(nxt)
            elif state[nxt] == 1 and len(cycles) < limit:
                cycles.append(stack[stack.index(nxt):] + [nxt])
        stack.pop()
        state[node] = 2

    import sys
    old = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old, len(files) + 200))
    try:
        for f in sorted(graph):
            if state.get(f, 0) == 0:
                dfs(f)
    finally:
        sys.setrecursionlimit(old)
    return cycles


def stats(root: str) -> dict:
    with _connect(str(Path(root).resolve())) as conn:
        return {
            "files": conn.execute("SELECT COUNT(*) FROM files").fetchone()[0],
            "symbols": conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0],
            "edges": conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0],
        }
