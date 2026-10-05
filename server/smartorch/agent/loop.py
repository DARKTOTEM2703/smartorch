"""
Bucle del agente SmartOrch.

El modelo local (Ollama, con soporte nativo de herramientas) decide que herramientas usar.
Leer, listar y buscar son automaticos; escribir, editar y ejecutar comandos esperan la
aprobacion del usuario, que ve antes un diff o el comando exacto.

Eventos que emite run() (diccionarios JSON):
  start, step, text, tool_call, approval_wait, tool_result, final, error, done
"""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Iterator, Optional

from smartorch.agent import tools as T
from smartorch.config import MODELS, OLLAMA_URL
from smartorch.core import analysis, datadir, gating

MAX_STEPS = int(os.environ.get("AGENT_MAX_STEPS", "10"))
APPROVAL_TIMEOUT = int(os.environ.get("AGENT_APPROVAL_TIMEOUT", "300"))
NUM_CTX = int(os.environ.get("AGENT_NUM_CTX", "12288"))
MODES = ("ask", "auto_edits", "readonly")

SYSTEM = """Eres SmartOrch, un agente de programación que corre en local. Trabajas dentro del proyecto «{name}».

Herramientas: list_files, read_file, search_text (automáticas) y write_file, edit_file, run_command (el usuario debe aprobarlas).

Reglas:
- Antes de contestar sobre el código, léelo con las herramientas. No adivines nombres de archivos, funciones ni rutas.
- Para modificar un archivo existente usa edit_file con un old_text exacto y único. write_file solo para archivos nuevos o reescrituras completas.
- Usa pocas herramientas por paso y no ejecutes comandos innecesarios. Nunca toques secretos (.env, llaves).
- Si el usuario menciona un archivo, ábrelo con read_file (list_files es solo para carpetas).
- Para preguntas generales sobre el proyecto, abre con read_file el README y los archivos principales antes de responder; nunca respondas con «probablemente» sobre un archivo que no abriste.
- Si una herramienta devuelve un error, léelo y corrige tu siguiente intento; nunca te rindas ni te disculpes sin haber intentado de nuevo.
- Al terminar, responde con un resumen breve de lo que encontraste o cambiaste, en el idioma del usuario.

Estructura del proyecto:
{overview}"""


class Approvals:
    """Decisiones del usuario pendientes, por id de llamada."""

    def __init__(self):
        self._lock = threading.Lock()
        self._pending: dict[str, dict] = {}

    def create(self, call_id: str) -> None:
        with self._lock:
            self._pending[call_id] = {"event": threading.Event(), "approved": None}

    def decide(self, call_id: str, approved: bool) -> bool:
        with self._lock:
            entry = self._pending.get(call_id)
            if not entry:
                return False
            entry["approved"] = approved
            entry["event"].set()
            return True

    def wait(self, call_id: str, timeout: float) -> Optional[bool]:
        with self._lock:
            entry = self._pending.get(call_id)
        if not entry:
            return None
        answered = entry["event"].wait(timeout)
        with self._lock:
            self._pending.pop(call_id, None)
        return entry["approved"] if answered else None


registry = Approvals()


# ── Utilidades ───────────────────────────────────────────────────────────────

def check_workspace(path: Optional[str]) -> Path:
    """El agente solo trabaja en la carpeta de un proyecto, nunca en toda la maquina."""
    if not path or not os.path.isdir(os.path.expanduser(path)):
        raise ValueError("Indica un workspace válido (carpeta del proyecto)")
    resolved = Path(os.path.expanduser(path)).resolve()
    home = Path.home().resolve()
    if resolved == Path(resolved.anchor) or resolved == home or resolved in home.parents:
        raise ValueError("El workspace es demasiado amplio; abre la carpeta de un proyecto")
    return resolved


def _audit(workspace: Path, name: str, args: dict, ok: bool) -> None:
    try:
        os.makedirs(datadir.DATA_DIR, exist_ok=True)
        short = {k: (v[:200] if isinstance(v, str) else v) for k, v in args.items()}
        entry = {"ts": time.time(), "workspace": str(workspace), "tool": name, "args": short, "ok": ok}
        with open(os.path.join(datadir.DATA_DIR, "agent-audit.log"), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _chat(model: str, messages: list[dict]) -> dict:
    payload = json.dumps({
        "model": model, "messages": messages, "tools": T.TOOL_SPECS, "stream": False,
        "options": {"temperature": 0.2, "num_ctx": NUM_CTX, "num_predict": 1024},
    }).encode()
    req = urllib.request.Request(f"{OLLAMA_URL}/api/chat", data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read().decode())["message"]
    except urllib.error.URLError as e:
        raise ConnectionError(f"Ollama no disponible en {OLLAMA_URL}: {e}") from e


def _extract_calls(message: dict) -> list[dict]:
    """Llamadas a herramientas del mensaje; acepta tambien el formato <tool_call> en texto."""
    calls: list[dict] = []
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function", {})
        args = fn.get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        if fn.get("name"):
            calls.append({"name": fn["name"], "args": args if isinstance(args, dict) else {}})
    if not calls:
        for m in re.finditer(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", message.get("content") or "", re.DOTALL):
            try:
                data = json.loads(m.group(1))
                calls.append({"name": data.get("name"), "args": data.get("arguments") or {}})
            except ValueError:
                continue
    return calls


def _clean_args(name: str, args: dict) -> dict:
    allowed = next((set(s["function"]["parameters"]["properties"]) for s in T.TOOL_SPECS
                    if s["function"]["name"] == name), set())
    return {k: v for k, v in args.items() if k in allowed}


def _describe(name: str, args: dict) -> str:
    icons = {"list_files": "📂", "read_file": "📖", "search_text": "🔎", "write_file": "📝",
             "edit_file": "✏️", "run_command": "⚙️"}
    main = args.get("path") or args.get("pattern") or args.get("command") or ""
    return f"{icons.get(name, '🔧')} {name}({str(main)[:80]})"


MAX_NUDGES = 2
_FILE_TOKEN = re.compile(r"[\w./\\-]+\.[A-Za-z0-9]{1,6}")


def _norm(path: str) -> str:
    return str(path).replace("\\", "/").lstrip("./").lower()


def _unread(user_text: str, root: Path, read: set[str], listing: dict) -> list[str]:
    """Archivos que el usuario nombro (o el README, en preguntas generales) y el agente aun no abrio."""
    if "files" not in listing:
        listing["files"] = {}
        try:
            for p in analysis._walk(root):
                listing["files"][p.relative_to(root).as_posix()] = p.name.lower()
        except Exception:
            pass
    files: dict[str, str] = listing["files"]
    wanted: list[str] = []
    for token in _FILE_TOKEN.findall(user_text):
        t = _norm(token)
        exact = [f for f in files if f.lower() == t]
        by_name = [f for f, n in files.items() if n == t.split("/")[-1]]
        match = exact or (by_name if len(by_name) == 1 else [])
        wanted += [m for m in match if m not in wanted]
    if not wanted and not read and gating.wants_project_context(user_text):
        readme = next((f for f in files if f.lower() in ("readme.md", "readme.rst", "readme.txt")), None)
        if readme:
            wanted.append(readme)
    return [f for f in wanted if _norm(f) not in read][:4]


# ── Bucle ────────────────────────────────────────────────────────────────────

def run(messages: list[dict], workspace: str, model: Optional[str] = None,
        mode: str = "ask", max_steps: Optional[int] = None) -> Iterator[dict]:
    mode = mode if mode in MODES else "ask"
    model = model or MODELS["agent"]
    limit = max_steps or MAX_STEPS
    started = time.time()

    try:
        root = check_workspace(workspace)
    except ValueError as e:
        yield {"type": "error", "message": str(e)}
        return

    sandbox = T.Sandbox(str(root))
    try:
        overview = analysis.overview(analysis.get_profile(str(root)), 1800)
    except Exception:
        overview = "(sin análisis disponible)"
    convo = [{"role": "system", "content": SYSTEM.format(name=root.name, overview=overview)}]
    convo += [{"role": m["role"], "content": m["content"]} for m in messages if m.get("role") != "system"]

    yield {"type": "start", "run_id": uuid.uuid4().hex[:10], "workspace": str(root), "model": model, "mode": mode}
    log: list[str] = []
    final = ""
    step = 0
    user_text = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
    read_paths: set[str] = set()
    listing: dict = {}
    nudges = 0

    for step in range(1, limit + 1):
        yield {"type": "step", "n": step}
        try:
            message = _chat(model, convo)
        except ConnectionError as e:
            yield {"type": "error", "message": str(e)}
            return

        calls = _extract_calls(message)
        content = (message.get("content") or "").strip()
        if not calls:
            missing = _unread(user_text, root, read_paths, listing)
            if missing and nudges < MAX_NUDGES:
                nudges += 1
                convo.append({"role": "assistant", "content": content})
                convo.append({"role": "user", "content": "Aún no abriste " + ", ".join(missing)
                              + ". Ábrelos con read_file antes de responder y no supongas su contenido."})
                yield {"type": "text", "content": f"Verificando {', '.join(missing)} antes de responder…"}
                continue
            final = content
            yield {"type": "final", "content": final}
            break

        if content:
            yield {"type": "text", "content": content}
        convo.append({"role": "assistant", "content": content, "tool_calls": message.get("tool_calls") or []})

        for call in calls:
            name = call["name"] or ""
            args = _clean_args(name, call["args"])
            result = yield from _execute(sandbox, root, name, args, mode)
            if name == "read_file" and result["ok"] and args.get("path"):
                read_paths.add(_norm(args["path"]))
            log.append(_describe(name, args) + ("" if result["ok"] else " — falló"))
            hint = "" if result["ok"] else "\n(La herramienta falló. Corrige los argumentos o usa otra herramienta y vuelve a intentarlo antes de responder.)"
            convo.append({"role": "tool", "content": result["output"] + hint, "tool_name": name})
    else:
        final = f"Llegué al límite de {limit} pasos sin terminar. Pídeme que continúe o acota la tarea."
        yield {"type": "final", "content": final}

    yield {"type": "done", "steps": step, "model": model, "elapsed": round(time.time() - started, 1),
           "tool_log": log, "final": final}


def _execute(sandbox: "T.Sandbox", root: Path, name: str, args: dict, mode: str) -> Iterator[dict]:
    """Ejecuta una herramienta, pidiendo aprobacion si modifica. Emite eventos y devuelve {ok, output}."""
    call_id = uuid.uuid4().hex[:10]
    fn = T.TOOLS.get(name)

    def result(ok: bool, output: str) -> dict:
        return {"type": "tool_result", "id": call_id, "name": name, "ok": ok, "output": output}

    if fn is None:
        yield result(False, f"herramienta desconocida: {name}")
        return {"ok": False, "output": f"Error: no existe la herramienta '{name}'."}

    mutating = T.is_mutating(name)
    needs_approval = mutating and not (mode == "auto_edits" and name in ("write_file", "edit_file"))
    preview = ""
    if mutating:
        try:
            preview = T.preview(sandbox, name, args)
            reason = T.check_command(args.get("command", "")) if name == "run_command" else None
            if reason:
                raise T.SandboxError(reason)
        except T.SandboxError as e:
            yield {"type": "tool_call", "id": call_id, "name": name, "args": args, "mutating": True,
                   "needs_approval": False, "preview": ""}
            yield result(False, str(e))
            return {"ok": False, "output": f"Error: {e}"}

    denied = mutating and mode == "readonly"
    yield {"type": "tool_call", "id": call_id, "name": name, "args": args, "mutating": mutating,
           "needs_approval": needs_approval and not denied, "preview": preview}

    if denied:
        msg = "Denegado: el agente está en modo solo lectura."
        yield result(False, msg)
        return {"ok": False, "output": msg}

    if needs_approval:
        registry.create(call_id)
        yield {"type": "approval_wait", "id": call_id, "timeout": APPROVAL_TIMEOUT}
        decision = registry.wait(call_id, APPROVAL_TIMEOUT)
        if decision is not True:
            msg = "El usuario rechazó la acción." if decision is False else "Sin respuesta del usuario a tiempo: acción cancelada."
            yield result(False, msg)
            return {"ok": False, "output": msg}

    try:
        outcome = fn(sandbox, **args)
    except (T.SandboxError, TypeError, ValueError, OSError) as e:
        outcome = T.ToolOutcome(False, f"Error: {e}")
    if mutating:
        _audit(root, name, args, outcome.ok)
    yield result(outcome.ok, outcome.output)
    return {"ok": outcome.ok, "output": outcome.output}
