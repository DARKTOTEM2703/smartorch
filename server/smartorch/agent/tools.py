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
import os
import re
import subprocess
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
    return tool in ("write_file", "edit_file", "run_command")


# ── Implementaciones ─────────────────────────────────────────────────────────

def list_files(sb: Sandbox, path: str = ".") -> ToolOutcome:
    base = sb.resolve(path)
    if not base.is_dir():
        return ToolOutcome(False, f"'{path}' no es una carpeta")
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
    return ToolOutcome(True, "\n".join(hits) or "sin coincidencias")


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


def _edited(sb: Sandbox, path: str, old_text: str, new_text: str) -> tuple[Path, str, str]:
    p = sb.resolve(path)
    sb.check_not_sensitive(p)
    if not p.is_file():
        raise SandboxError(f"'{path}' no existe")
    current = p.read_text(encoding="utf-8", errors="replace")
    count = current.count(old_text)
    if not old_text:
        raise SandboxError("old_text no puede estar vacio")
    if count == 0:
        raise SandboxError("old_text no aparece en el archivo; lee el archivo y copia el texto exacto")
    if count > 1:
        raise SandboxError(f"old_text aparece {count} veces; incluye mas contexto para que sea unico")
    return p, current, current.replace(old_text, new_text, 1)


def preview_edit(sb: Sandbox, path: str, old_text: str, new_text: str) -> str:
    p, current, updated = _edited(sb, path, old_text, new_text)
    return _truncate(_diff(current, updated, sb.rel(p)), 6000)


def edit_file(sb: Sandbox, path: str, old_text: str, new_text: str) -> ToolOutcome:
    p, _, updated = _edited(sb, path, old_text, new_text)
    p.write_text(updated, encoding="utf-8", newline="")
    return ToolOutcome(True, f"Editado {sb.rel(p)}")


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

TOOLS = {
    "list_files": list_files, "read_file": read_file, "search_text": search_text,
    "write_file": write_file, "edit_file": edit_file, "run_command": run_command,
}


def preview(sb: Sandbox, name: str, args: dict) -> str:
    """Texto para mostrar al usuario antes de aprobar (diff o comando)."""
    if name == "write_file":
        return preview_write(sb, args.get("path", ""), args.get("content", ""))
    if name == "edit_file":
        return preview_edit(sb, args.get("path", ""), args.get("old_text", ""), args.get("new_text", ""))
    if name == "run_command":
        return str(args.get("command", ""))
    return ""
