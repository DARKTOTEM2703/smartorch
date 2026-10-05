"""
Herramientas del agente SmartOrch.

Seguridad (por diseno):
  - Todo ocurre dentro de UN workspace (Sandbox): no se puede salir con `..` ni por symlinks.
  - Archivos sensibles (.env, llaves, credenciales) no se leen ni se escriben.
  - Leer/buscar son automaticos; escribir, editar y ejecutar comandos requieren aprobacion.
  - Hay comandos que se rechazan siempre, aunque el usuario apruebe.
"""
import difflib
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

MAX_READ_BYTES = 200_000
MAX_OUTPUT_CHARS = 8_000
MAX_LIST_ENTRIES = 300
MAX_SEARCH_HITS = 60
MAX_COMMAND_SECONDS = 120

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next", "target", ".idea"}
SENSITIVE_PATTERNS = (".env", ".env.*", "*.pem", "*.key", "id_rsa*", "id_ed25519*", "*.pfx", "*.p12",
                      "credentials*", "*.kdbx", "*.keystore", ".npmrc", ".pypirc", ".netrc")

BLOCKED_COMMANDS = [
    r"\brm\s+(-[a-z]*\s+)*(/|~|\*|\.\.)(\s|$)",
    r"\brm\s+-[a-z]*r[a-z]*f?[a-z]*\s+/",
    r"\bdel\s+(/[a-z]\s+)*[a-z]:\\",
    r"\brd\s+/s\b.*[a-z]:\\",
    r"remove-item\b.*-recurse.*([a-z]:\\|\\\\|~)",
    r"\bformat\s+[a-z]:",
    r"\b(mkfs|diskpart|shutdown|reboot|halt|poweroff)\b",
    r"\breg\s+delete\b",
    r":\(\)\s*\{",
    r"\b(curl|wget|iwr|invoke-webrequest)\b[^|]*\|\s*(sh|bash|zsh|iex|invoke-expression|powershell|pwsh)",
    r"-enc(odedcommand)?\s",
    r"\bchmod\s+-r\s+777\s+/",
    r">\s*/dev/sd[a-z]",
]


class SandboxError(Exception):
    """Operacion fuera de los limites permitidos."""


@dataclass
class ToolOutcome:
    ok: bool
    output: str


def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[... recortado, {len(text) - limit} caracteres mas]"


class Sandbox:
    def __init__(self, root: str):
        self.root = Path(root).resolve()

    def resolve(self, rel: str) -> Path:
        candidate = (self.root / (rel or ".")).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise SandboxError(f"'{rel}' esta fuera del workspace")
        return candidate

    def rel(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.root)).replace("\\", "/") or "."
        except ValueError:
            return str(path)

    def check_not_sensitive(self, path: Path) -> None:
        name = path.name.lower()
        if any(fnmatch.fnmatch(name, pat) for pat in SENSITIVE_PATTERNS):
            raise SandboxError(f"'{path.name}' parece contener secretos; el agente no lo toca")
        if ".git" in path.parts:
            raise SandboxError("el agente no modifica ni lee el interior de .git")


def check_command(command: str) -> Optional[str]:
    """Devuelve el motivo si el comando se rechaza siempre, o None si puede pasar a aprobacion."""
    low = command.lower()
    for pattern in BLOCKED_COMMANDS:
        if re.search(pattern, low):
            return "comando bloqueado por seguridad (destructivo o de ejecucion remota)"
    if not command.strip():
        return "comando vacio"
    return None


def is_mutating(tool: str) -> bool:
    """Herramientas que modifican archivos o ejecutan codigo: piden aprobacion y no corren en solo lectura."""
    return tool in ("write_file", "edit_file", "append_file", "replace_in_files", "run_command", "run_tests")


def is_network(tool: str) -> bool:
    """Herramientas que salen a internet: siempre piden aprobacion (se muestra la consulta o la URL)."""
    return tool in ("web_search", "web_fetch")


# ── Implementaciones ─────────────────────────────────────────────────────────

def list_files(sb: Sandbox, path: str = ".") -> ToolOutcome:
    base = sb.resolve(path)
    if base.is_file():  # error tipico del modelo: orientarlo en vez de fallar
        lines = len(base.read_text(encoding="utf-8", errors="replace").splitlines()) if base.stat().st_size <= MAX_READ_BYTES else "muchas"
        return ToolOutcome(True, f"{sb.rel(base)} es un archivo ({lines} líneas), no una carpeta. Usa read_file para ver su contenido.")
    if not base.is_dir():
        return ToolOutcome(False, f"'{path}' no existe")
    entries: list[str] = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        depth = len(Path(dirpath).relative_to(base).parts)
        if depth >= 3:
            dirnames[:] = []
        for name in sorted(filenames):
            if name.startswith(".") and name not in (".gitignore", ".env.example"):
                continue
            entries.append(sb.rel(Path(dirpath) / name))
            if len(entries) >= MAX_LIST_ENTRIES:
                entries.append("[... lista recortada]")
                return ToolOutcome(True, "\n".join(entries))
    return ToolOutcome(True, "\n".join(entries) or "(carpeta vacia)")


def read_file(sb: Sandbox, path: str, start_line: int = 1, end_line: int = 0) -> ToolOutcome:
    p = sb.resolve(path)
    sb.check_not_sensitive(p)
    if not p.is_file():
        return ToolOutcome(False, f"'{path}' no existe o no es un archivo")
    if p.stat().st_size > MAX_READ_BYTES:
        return ToolOutcome(False, f"'{path}' pesa {p.stat().st_size} bytes; pide un rango con start_line/end_line o usa search_text")
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(1, int(start_line or 1))
    end = int(end_line) if end_line and int(end_line) >= start else len(lines)
    numbered = [f"{i}: {line}" for i, line in enumerate(lines[start - 1:end], start=start)]
    return ToolOutcome(True, _truncate("\n".join(numbered)) or "(archivo vacio)")


def _iter_files(base: Path):
    if base.is_file():
        yield base
        return
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            yield Path(dirpath) / name


def search_text(sb: Sandbox, pattern: str, path: str = ".") -> ToolOutcome:
    base = sb.resolve(path)
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return ToolOutcome(False, f"expresion regular invalida: {e}")
    hits: list[str] = []
    for fp in _iter_files(base):
        try:
            sb.check_not_sensitive(fp)
            if fp.stat().st_size > MAX_READ_BYTES:
                continue
            for n, line in enumerate(fp.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
                if rx.search(line):
                    hits.append(f"{sb.rel(fp)}:{n}: {line.strip()[:200]}")
                    if len(hits) >= MAX_SEARCH_HITS:
                        return ToolOutcome(True, "\n".join(hits) + "\n[... mas resultados omitidos]")
        except (SandboxError, OSError):
            continue
    if hits and all(h.split(":", 1)[0].lower().endswith((".md", ".txt", ".rst")) for h in hits):
        hits.append("(Solo hay coincidencias en documentación, no en código. El código suele estar en inglés: prueba también "
                    "con el término en inglés, p. ej. 'discount' en vez de 'descuento'.)")
    return ToolOutcome(True, "\n".join(hits) or
                       "sin coincidencias. Prueba con otro término (el código suele estar en inglés: discount, price, user…), "
                       "uno más corto, o usa glob/list_files para ver los archivos.")


def _diff(old: str, new: str, name: str) -> str:
    return "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"a/{name}", tofile=f"b/{name}", n=2))


def preview_write(sb: Sandbox, path: str, content: str) -> str:
    p = sb.resolve(path)
    sb.check_not_sensitive(p)
    if p.exists():
        return _truncate(_diff(p.read_text(encoding="utf-8", errors="replace"), content, sb.rel(p)) or "(sin cambios)", 6000)
    return _truncate(f"Archivo nuevo: {sb.rel(p)} ({len(content.splitlines())} lineas)\n\n{content}", 6000)


def write_file(sb: Sandbox, path: str, content: str) -> ToolOutcome:
    p = sb.resolve(path)
    sb.check_not_sensitive(p)
    if p.is_dir():
        return ToolOutcome(False, f"'{path}' es una carpeta")
    existed = p.exists()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8", newline="")
    return ToolOutcome(True, f"{'Sobrescrito' if existed else 'Creado'} {sb.rel(p)} ({len(content)} caracteres)")


_NUMBERED = re.compile(r"^\s*\d+[:|]\s?")


def _strip_line_numbers(text: str) -> str:
    """read_file muestra '9: codigo'; si el modelo copia esos prefijos, se quitan."""
    lines = text.split("\n")
    body = [l for l in lines if l.strip()]
    if body and all(_NUMBERED.match(l) for l in body):
        return "\n".join(_NUMBERED.sub("", l, count=1) for l in lines)
    return text


def _indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def _closest_hint(current: str, old_text: str) -> str:
    """Lo que el modelo necesita para reintentar: el archivo completo si es chico, o las lineas parecidas."""
    file_lines = current.splitlines()
    if not file_lines:
        return ""
    if len(current) <= 3500:
        shown = "\n".join(f"{n + 1}: {line}" for n, line in enumerate(file_lines))
        return f"\nContenido actual del archivo (copia el texto exacto, sin los números de línea):\n{shown}"
    target = next((l.strip() for l in old_text.splitlines() if l.strip()), "")
    best, score = 0, 0.0
    for i, line in enumerate(file_lines):
        ratio = difflib.SequenceMatcher(None, target, line.strip()).ratio()
        if ratio > score:
            best, score = i, ratio
    if not target or score < 0.5:
        return "\nLee el archivo con read_file y copia el texto exacto."
    lo, hi = max(0, best - 2), min(len(file_lines), best + 4)
    shown = "\n".join(f"{n + 1}: {file_lines[n]}" for n in range(lo, hi))
    return f"\nTexto parecido en el archivo (copia exactamente, sin los números de línea):\n{shown}"


def _replace_unique(current: str, old_text: str, new_text: str) -> str:
    if not old_text or not old_text.strip():
        raise SandboxError("old_text no puede estar vacío")

    # 1) coincidencia exacta (tambien tras quitar prefijos de numero de linea)
    for candidate in dict.fromkeys((old_text, _strip_line_numbers(old_text))):
        count = current.count(candidate)
        if count == 1:
            replacement = new_text if candidate == old_text else _strip_line_numbers(new_text)
            return current.replace(candidate, replacement, 1)
        if count > 1:
            raise SandboxError(f"old_text aparece {count} veces; incluye más contexto para que sea único")

    # 2) mismo bloque de lineas ignorando indentacion y espacios finales
    wanted = [l.strip() for l in _strip_line_numbers(old_text).strip("\n").split("\n")]
    lines = current.split("\n")
    stripped = [l.strip() for l in lines]
    hits = [i for i in range(len(lines) - len(wanted) + 1) if stripped[i : i + len(wanted)] == wanted]
    if len(hits) > 1:
        raise SandboxError(f"old_text coincide con {len(hits)} bloques; incluye más contexto para que sea único")
    if len(hits) == 1:
        i = hits[0]
        actual_indent = _indent(lines[i])
        given_first = _indent(next((l for l in _strip_line_numbers(old_text).split("\n") if l.strip()), ""))
        replacement = _strip_line_numbers(new_text).strip("\n").split("\n")
        if actual_indent != given_first:  # el modelo omitio o cambio la indentacion: se ajusta
            delta = actual_indent[len(given_first):] if actual_indent.startswith(given_first) else actual_indent
            replacement = [(delta + l) if l.strip() else l for l in replacement]
        return "\n".join(lines[:i] + replacement + lines[i + len(wanted):])

    raise SandboxError("old_text no aparece en el archivo; lee el archivo y copia el texto exacto." + _closest_hint(current, old_text))


def _edited(sb: Sandbox, path: str, old_text: str, new_text: str) -> tuple[Path, str, str]:
    p = sb.resolve(path)
    sb.check_not_sensitive(p)
    if p.is_dir():
        raise SandboxError(f"'{path}' es una carpeta; edit_file necesita la ruta de un archivo. Usa search_text para ver en qué "
                           "archivos aparece el texto, o replace_in_files para cambiarlo en todo el proyecto.")
    if not p.is_file():
        raise SandboxError(f"'{path}' no existe")
    current = p.read_text(encoding="utf-8", errors="replace")
    return p, current, _replace_unique(current, old_text, new_text)


def preview_edit(sb: Sandbox, path: str, old_text: str, new_text: str) -> str:
    p, current, updated = _edited(sb, path, old_text, new_text)
    return _truncate(_diff(current, updated, sb.rel(p)), 6000)


def edit_file(sb: Sandbox, path: str, old_text: str, new_text: str) -> ToolOutcome:
    p, _, updated = _edited(sb, path, old_text, new_text)
    p.write_text(updated, encoding="utf-8", newline="")
    return ToolOutcome(True, f"Editado {sb.rel(p)}")


MAX_REPLACE_FILES = 60


def _replace_plan(sb: Sandbox, old: str, new: str, whole_word: bool, path: str) -> list[tuple[Path, str, str, int]]:
    if not old:
        raise SandboxError("'old' no puede estar vacío")
    base = sb.resolve(path or ".")
    use_word = whole_word and re.fullmatch(r"\w+", old) is not None
    rx = re.compile(r"\b" + re.escape(old) + r"\b" if use_word else re.escape(old))
    from smartorch.rag.chunker import is_indexable
    changes: list[tuple[Path, str, str, int]] = []
    for fp in _iter_files(base):
        try:
            sb.check_not_sensitive(fp)
            if not is_indexable(fp) or fp.stat().st_size > MAX_READ_BYTES:
                continue
            text = fp.read_text(encoding="utf-8", errors="replace")
        except (SandboxError, OSError):
            continue
        updated, count = rx.subn(lambda _m: new, text)
        if count:
            changes.append((fp, text, updated, count))
        if len(changes) > MAX_REPLACE_FILES:
            raise SandboxError(f"Afecta a más de {MAX_REPLACE_FILES} archivos; acota con 'path'.")
    return changes


def preview_replace(sb: Sandbox, old: str, new: str, whole_word: bool = True, path: str = ".") -> str:
    changes = _replace_plan(sb, old, new, whole_word, path)
    if not changes:
        return f"No hay ninguna coincidencia de '{old}'."
    total = sum(c[3] for c in changes)
    lines = [f"Reemplazar '{old}' por '{new}': {total} ocurrencias en {len(changes)} archivos"]
    lines += [f"  {sb.rel(fp)} ({n})" for fp, _, _, n in changes]
    diffs = "\n".join(_diff(before, after, sb.rel(fp)) for fp, before, after, _ in changes[:6])
    return _truncate("\n".join(lines) + "\n\n" + diffs, 6000)


def replace_in_files(sb: Sandbox, old: str, new: str, whole_word: bool = True, path: str = ".") -> ToolOutcome:
    """Reemplaza un texto en todos los archivos de codigo del proyecto (renombrar funciones, variables...)."""
    changes = _replace_plan(sb, old, new, whole_word, path)
    if not changes:
        return ToolOutcome(False, f"No encontré '{old}' en ningún archivo. Usa search_text para ver cómo está escrito.")
    notes: list[str] = []
    for fp, _, updated, _ in changes:
        fp.write_text(updated, encoding="utf-8", newline="")
        problem = check_syntax(fp)
        if problem:
            notes.append(f"⚠ Error de sintaxis en {sb.rel(fp)}: {problem}")
    summary = ", ".join(f"{sb.rel(fp)} ({n})" for fp, _, _, n in changes)
    total = sum(c[3] for c in changes)
    out = f"Reemplazadas {total} ocurrencias en {len(changes)} archivos: {summary}"
    return ToolOutcome(True, out + ("\n" + "\n".join(notes) if notes else "\n✔ Sintaxis válida en los archivos modificados."))


def _appended(sb: Sandbox, path: str, content: str) -> tuple[Path, str, str]:
    p = sb.resolve(path)
    sb.check_not_sensitive(p)
    if p.is_dir():
        raise SandboxError(f"'{path}' es una carpeta; indica la ruta de un archivo.")
    if not content or not content.strip():
        raise SandboxError("content no puede estar vacío")
    current = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""
    sep = ""
    if current:
        sep = ("" if current.endswith("\n") else "\n") + ("" if current.endswith("\n\n") else "\n")
    addition = content if content.endswith("\n") else content + "\n"
    return p, current, current + sep + addition


def preview_append(sb: Sandbox, path: str, content: str) -> str:
    p, current, updated = _appended(sb, path, content)
    return _truncate(_diff(current, updated, sb.rel(p)), 6000)


def append_file(sb: Sandbox, path: str, content: str) -> ToolOutcome:
    """Agrega codigo al final de un archivo (o lo crea). No requiere old_text."""
    p, _, updated = _appended(sb, path, content)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(updated, encoding="utf-8", newline="")
    return ToolOutcome(True, f"Agregado al final de {sb.rel(p)}")


def run_command(sb: Sandbox, command: str, timeout: int = 60) -> ToolOutcome:
    reason = check_command(command)
    if reason:
        return ToolOutcome(False, reason)
    seconds = max(1, min(int(timeout or 60), MAX_COMMAND_SECONDS))
    try:
        r = subprocess.run(command, shell=True, cwd=str(sb.root), capture_output=True, text=True,
                           timeout=seconds, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return ToolOutcome(False, f"el comando excedio {seconds}s y se detuvo")
    combined = r.stdout
    if r.stderr.strip():
        combined += "\n[stderr]\n" + r.stderr
    out = _truncate(combined.strip() or "(sin salida)")
    return ToolOutcome(r.returncode == 0, f"codigo de salida {r.returncode}\n{out}")


def glob_files(sb: Sandbox, pattern: str) -> ToolOutcome:
    """Busca archivos por patron (p. ej. '*.py', 'src/**/*.ts', 'test_*')."""
    if not pattern or not pattern.strip():
        return ToolOutcome(False, "indica un patron, por ejemplo '*.py'")
    pat = pattern.strip().strip("'\"`").strip().replace("\\", "/").lstrip("./")
    hits: list[str] = []
    for fp in _iter_files(sb.root):
        rel = sb.rel(fp)
        if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(rel, "*/" + pat) or fnmatch.fnmatch(fp.name, pat):
            hits.append(rel)
            if len(hits) >= 200:
                hits.append("[... mas resultados omitidos]")
                break
    if not hits:
        sample = [sb.rel(fp) for fp in _iter_files(sb.root)][:15]
        return ToolOutcome(True, "sin coincidencias. Archivos del proyecto:\n" + "\n".join(sample))
    return ToolOutcome(True, "\n".join(sorted(hits)))


def detect_test_command(root: Path) -> Optional[list[str]]:
    """Comando de tests del proyecto, o None. No se acepta ninguno que el modelo invente."""
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            if "test" in (json.loads(pkg.read_text(encoding="utf-8", errors="ignore")).get("scripts") or {}):
                return ["npm", "test", "--silent"]
        except ValueError:
            pass
    if (root / "go.mod").is_file():
        return ["go", "test", "./..."]
    has_py_tests = any(
        (root / d).is_dir() for d in ("tests", "test")
    ) or any(root.glob("test_*.py")) or any(root.glob("*_test.py"))
    if has_py_tests or (root / "pytest.ini").is_file() or (root / "pyproject.toml").is_file() and "pytest" in (root / "pyproject.toml").read_text(encoding="utf-8", errors="ignore"):
        try:
            __import__("pytest")
            return [sys.executable, "-m", "pytest", "-q", "-x", "--no-header"]
        except ImportError:
            return [sys.executable, "-m", "unittest", "discover", "-q"]
    return None


def preview_tests(sb: Sandbox) -> str:
    cmd = detect_test_command(sb.root)
    return " ".join(cmd) if cmd else "(este proyecto no tiene tests detectables)"


def run_tests(sb: Sandbox) -> ToolOutcome:
    cmd = detect_test_command(sb.root)
    if not cmd:
        return ToolOutcome(False, "No detecté tests en este proyecto (ni carpeta tests/, ni script npm test, ni go.mod).")
    if shutil.which(cmd[0]) is None and cmd[0] != sys.executable:
        return ToolOutcome(False, f"No encuentro '{cmd[0]}' para correr los tests.")
    try:
        # sin bytecode: una edicion y su prueba en el mismo segundo y con igual tamano reutilizarian un .pyc viejo
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        r = subprocess.run(cmd, cwd=str(sb.root), capture_output=True, text=True, timeout=MAX_COMMAND_SECONDS,
                           encoding="utf-8", errors="replace", env=env, shell=(os.name == "nt" and cmd[0] == "npm"))
    except subprocess.TimeoutExpired:
        return ToolOutcome(False, f"los tests excedieron {MAX_COMMAND_SECONDS}s y se detuvieron")
    text = (r.stdout + ("\n" + r.stderr if r.stderr.strip() else "")).strip()
    # lo importante de una falla suele estar al final
    if len(text) > MAX_OUTPUT_CHARS:
        text = "[... recortado al inicio]\n" + text[-MAX_OUTPUT_CHARS:]
    verdict = "TESTS OK" if r.returncode == 0 else "TESTS FALLARON"
    return ToolOutcome(r.returncode == 0, f"{verdict} (codigo {r.returncode}) — {' '.join(cmd)}\n{text or '(sin salida)'}")


def check_syntax(path: Path) -> Optional[str]:
    """None si el archivo esta sintacticamente bien (o no se puede comprobar); si no, el error."""
    suffix = path.suffix.lower()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        if suffix == ".py":
            compile(text, str(path), "exec")
        elif suffix == ".json":
            json.loads(text)
        elif suffix in (".js", ".mjs", ".cjs") and shutil.which("node"):
            r = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True, timeout=15,
                               encoding="utf-8", errors="replace")
            if r.returncode != 0:
                return (r.stderr.strip().splitlines() or ["error de sintaxis"])[-1][:200]
    except SyntaxError as e:
        return f"{e.msg} (linea {e.lineno})"
    except ValueError as e:
        return str(e)[:200]
    except (OSError, subprocess.SubprocessError):
        return None
    return None


def proposed_content(sb: Sandbox, name: str, args: dict) -> Optional[dict]:
    """Contenido final propuesto por write_file/edit_file, para mostrar un diff nativo en el editor."""
    try:
        if name == "write_file":
            content = args.get("content", "")
            path = sb.resolve(args.get("path", ""))
        elif name == "edit_file":
            path, _, content = _edited(sb, args.get("path", ""), args.get("old_text", ""), args.get("new_text", ""))
        elif name == "append_file":
            path, _, content = _appended(sb, args.get("path", ""), args.get("content", ""))
        else:
            return None
    except (SandboxError, OSError):
        return None
    if len(content) > 200_000:
        return None
    return {"path": sb.rel(path), "content": content}


# ── Catalogo para el modelo ──────────────────────────────────────────────────

def _spec(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required}}}


TOOL_SPECS = [
    _spec("list_files", "Lista los archivos del proyecto (hasta 3 niveles).",
          {"path": {"type": "string", "description": "Carpeta relativa, '.' por defecto"}}, []),
    _spec("read_file", "Lee un archivo con numeros de linea.",
          {"path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}, ["path"]),
    _spec("search_text", "Busca texto o una expresion regular en el proyecto.",
          {"pattern": {"type": "string"}, "path": {"type": "string"}}, ["pattern"]),
    _spec("write_file", "Crea o sobrescribe un archivo completo. Requiere aprobacion del usuario.",
          {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
    _spec("edit_file", "Reemplaza un fragmento exacto y unico de un archivo. Requiere aprobacion del usuario.",
          {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}},
          ["path", "old_text", "new_text"]),
    _spec("run_command", "Ejecuta un comando de shell en la raiz del proyecto. Requiere aprobacion del usuario.",
          {"command": {"type": "string"}, "timeout": {"type": "integer", "description": "segundos, maximo 120"}}, ["command"]),
]

EXTRA_SPECS = {
    "symbol_context": _spec("symbol_context",
                            "Dado el nombre de una función, método o clase, devuelve SOLO su código con números de línea, quién la llama y a quién llama. Úsalo antes de leer archivos enteros.",
                            {"name": {"type": "string", "description": "p. ej. 'calc_total' o 'Cart.add'"}}, ["name"]),
    "glob": _spec("glob", "Busca archivos por patron de nombre (por ejemplo '*.py' o 'src/**/*.ts').",
                  {"pattern": {"type": "string"}}, ["pattern"]),
    "append_file": _spec("append_file",
                         "Agrega código al FINAL de un archivo existente (o lo crea). Úsalo para añadir una función o un test nuevo: no necesitas old_text. Requiere aprobación.",
                         {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
    "replace_in_files": _spec("replace_in_files",
                              "Reemplaza un texto en TODOS los archivos de código del proyecto en una sola llamada (renombrar una función o variable). Requiere aprobación.",
                              {"old": {"type": "string"}, "new": {"type": "string"},
                               "whole_word": {"type": "boolean", "description": "true (por defecto): solo palabras completas"},
                               "path": {"type": "string", "description": "carpeta o archivo a limitar; '.' por defecto"}}, ["old", "new"]),
    "run_tests": _spec("run_tests", "Ejecuta los tests del proyecto (el comando se detecta solo). Requiere aprobacion.", {}, []),
    "todo_write": _spec("todo_write", "Crea o actualiza tu lista de tareas del trabajo actual. Reescribe la lista completa cada vez.",
                        {"todos": {"type": "array", "items": {"type": "object", "properties": {
                            "text": {"type": "string"}, "done": {"type": "boolean"}}, "required": ["text"]}}}, ["todos"]),
    "ask_user": _spec("ask_user", "Hace una pregunta al usuario cuando falta informacion o hay que elegir entre opciones. No adivines.",
                      {"question": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}}}, ["question"]),
    "explore": _spec("explore", "Delega una pregunta de investigacion a un explorador con contexto propio; devuelve un resumen con rutas y lineas. Util para no llenar tu contexto.",
                     {"question": {"type": "string"}}, ["question"]),
    "web_search": _spec("web_search", "Busca en internet (documentacion, versiones, errores). El usuario ve y aprueba la consulta. No incluyas codigo ni rutas privadas.",
                        {"query": {"type": "string"}}, ["query"]),
    "web_fetch": _spec("web_fetch", "Lee el texto de una pagina publica. El usuario aprueba la URL.",
                       {"url": {"type": "string"}}, ["url"]),
}

READ_TOOLS = ["list_files", "glob", "read_file", "search_text", "symbol_context", "todo_write"]
EDIT_TOOLS = ["write_file", "edit_file", "append_file", "replace_in_files", "run_command", "run_tests"]


def specs_for(plan: bool = False, explore: bool = False, web: bool = False) -> list[dict]:
    """Herramientas ofrecidas al modelo segun el modo (plan = solo investigar), esfuerzo y si hay web."""
    by_name = {s["function"]["name"]: s for s in TOOL_SPECS}
    by_name.update(EXTRA_SPECS)
    # ask_user solo en modo plan: en modo agente un modelo chico lo usa para esquivar el trabajo
    names = list(READ_TOOLS) + (["ask_user"] if plan else EDIT_TOOLS)
    if explore:
        names.append("explore")
    if web:
        names += ["web_search", "web_fetch"]
    return [by_name[n] for n in names]


_graph_built: dict[str, float] = {}
GRAPH_REFRESH_SECONDS = 20


def symbol_context(sb: Sandbox, name: str) -> ToolOutcome:
    """Codigo de un simbolo y sus vecinos, desde el grafo (se actualiza solo, es incremental)."""
    import time
    from smartorch.core import codegraph
    name = (name or "").strip()
    if not name:
        raise SandboxError("name no puede estar vacío")
    root = str(sb.root)
    if time.time() - _graph_built.get(root, 0) > GRAPH_REFRESH_SECONDS:
        codegraph.build(root)
        _graph_built[root] = time.time()
    text = codegraph.slice_for(root, name)
    return ToolOutcome(not text.startswith("No hay ningún símbolo"), text)


TOOLS = {
    "symbol_context": symbol_context,
    "list_files": list_files, "read_file": read_file, "search_text": search_text,
    "write_file": write_file, "edit_file": edit_file, "run_command": run_command,
    "glob": glob_files, "run_tests": run_tests, "replace_in_files": replace_in_files, "append_file": append_file,
}


def preview(sb: Sandbox, name: str, args: dict) -> str:
    """Texto para mostrar al usuario antes de aprobar (diff o comando)."""
    if name == "write_file":
        return preview_write(sb, args.get("path", ""), args.get("content", ""))
    if name == "edit_file":
        return preview_edit(sb, args.get("path", ""), args.get("old_text", ""), args.get("new_text", ""))
    if name == "run_command":
        return str(args.get("command", ""))
    if name == "run_tests":
        return preview_tests(sb)
    if name == "append_file":
        return preview_append(sb, args.get("path", ""), args.get("content", ""))
    if name == "replace_in_files":
        return preview_replace(sb, str(args.get("old", "")), str(args.get("new", "")),
                               bool(args.get("whole_word", True)), str(args.get("path", ".") or "."))
    return ""
