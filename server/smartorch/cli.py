"""
SmartOrch CLI — usa el poder de SmartOrch desde cualquier terminal.

Uso:
  smartorch                         # REPL interactivo
  smartorch "pregunta"              # Pregunta rápida
  smartorch chat "pregunta"         # Chat mode explícito
  smartorch refactor archivo.py     # Refactorizar archivo
  smartorch review archivo.py       # Code review
  smartorch test archivo.py         # Generar tests
  smartorch solid archivo.py        # Aplicar SOLID/DRY
  smartorch yara archivo.py         # Generar regla YARA
  smartorch sigma "descripción"     # Generar regla Sigma
  smartorch c2 archivo.py           # Análisis C2/malware
  smartorch pentest "objetivo"      # Guía de pentest
  smartorch web "descripción"       # Desarrollar feature web
  smartorch mobile "descripción"    # Desarrollar feature mobile
  smartorch explain archivo.py      # Explicar código
  smartorch doc archivo.py          # Generar documentación
  smartorch status                  # Estado del servidor + métricas
  smartorch serve                   # Iniciar el servidor SmartOrch
  smartorch agent "tarea"           # Agente con herramientas (--plan, --effort, --web, -y)
  smartorch data [carpeta]          # Ver o cambiar dónde se guardan historial, índice y RAG
  smartorch history                 # Conversaciones guardadas (web · CLI · editor)
  smartorch resume [id]             # Retomar una conversación (la última por defecto)
"""
import sys
import os
import json
import time
import argparse
import subprocess
import threading

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── Constantes ───────────────────────────────────────────────────────────────

SERVER_URL   = os.environ.get("SMARTORCH_URL", "http://localhost:8080")
API_KEY      = os.environ.get("SMARTORCH_API_KEY", "smartorch-local-key")
SERVER_DIR   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HISTORY_FILE = os.path.join(os.path.expanduser("~"), ".smartorch_history")

# Conversacion activa en el historial compartido (web · CLI · editor)
_conv_id: str | None = None

# Nivel de esfuerzo de la sesion (rapido | normal | maximo)
_effort: list[str] = ["normal"]


def _new_conversation() -> str:
    global _conv_id  # noqa: PLW0603
    import uuid
    _conv_id = uuid.uuid4().hex[:12]
    return _conv_id

# ── Colores ANSI (sin dependencias externas) ──────────────────────────────────

class C:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    CYAN    = "\033[36m"
    GREEN   = "\033[32m"
    YELLOW  = "\033[33m"
    RED     = "\033[31m"
    BLUE    = "\033[34m"
    MAGENTA = "\033[35m"
    WHITE   = "\033[37m"
    GRAY    = "\033[90m"

    @staticmethod
    def enabled() -> bool:
        return sys.stdout.isatty() and os.name != 'nt' or _win_ansi_enabled()


def _win_ansi_enabled() -> bool:
    if os.name != 'nt':
        return True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        return True
    except Exception:
        return False


_USE_COLOR = _win_ansi_enabled()


def _c(code: str, text: str) -> str:
    return f"{code}{text}{C.RESET}" if _USE_COLOR else text


def _print_banner():
    print(_c(C.CYAN + C.BOLD, """
  ███████╗███╗   ███╗ █████╗ ██████╗ ████████╗ ██████╗ ██████╗  ██████╗██╗  ██╗
  ██╔════╝████╗ ████║██╔══██╗██╔══██╗╚══██╔══╝██╔═══██╗██╔══██╗██╔════╝██║  ██║
  ███████╗██╔████╔██║███████║██████╔╝   ██║   ██║   ██║██████╔╝██║     ███████║
  ╚════██║██║╚██╔╝██║██╔══██║██╔══██╗   ██║   ██║   ██║██╔══██╗██║     ██╔══██║
  ███████║██║ ╚═╝ ██║██║  ██║██║  ██║   ██║   ╚██████╔╝██║  ██║╚██████╗██║  ██║
  ╚══════╝╚═╝     ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝   ╚═╝    ╚═════╝ ╚═╝  ╚═╝ ╚═════╝╚═╝  ╚═╝
"""))
    print(_c(C.GRAY, "  AI local con Ollama · SOLID/DRY · YARA/Sigma · Anti-alucinaciones"))
    print(_c(C.GRAY, f"  Servidor: {SERVER_URL}"))
    print()


# ── HTTP helpers (sin dependencias) ──────────────────────────────────────────

def _request(method: str, path: str, body: dict | None = None, timeout: int = 120) -> dict:
    """HTTP sin requests/httpx — solo stdlib."""
    import urllib.request
    import urllib.error

    url  = SERVER_URL.rstrip("/") + path
    data = json.dumps(body).encode() if body else None
    req  = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {API_KEY}",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            body_txt = e.read().decode()
        except Exception:
            body_txt = ""
        raise RuntimeError(f"HTTP {e.code}: {body_txt[:300]}")


def _stream_request(path: str, body: dict, on_token, on_done, on_status=None, timeout: int = 300):
    """
    SSE streaming — llama on_token(text) por cada fragmento,
    on_status(step) para eventos de pipeline,
    on_done(stats_dict) al terminar.
    """
    import urllib.request
    import urllib.error

    url  = SERVER_URL.rstrip("/") + path
    data = json.dumps(body).encode()
    req  = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {API_KEY}",
            "Accept":        "text/event-stream",
        },
        method="POST",
    )

    stats = {}
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            buffer = b""
            while True:
                chunk = resp.read(1)
                if not chunk:
                    break
                buffer += chunk
                if b"\n" in buffer:
                    lines = buffer.split(b"\n")
                    buffer = lines[-1]
                    for line in lines[:-1]:
                        line = line.decode("utf-8", errors="replace").rstrip("\r")
                        if line.startswith("data: "):
                            payload = line[6:]
                            if payload == "[DONE]":
                                break
                            try:
                                obj = json.loads(payload)
                                # OpenAI streaming chunk
                                if "choices" in obj:
                                    delta = obj["choices"][0].get("delta", {})
                                    text  = delta.get("content", "")
                                    if text:
                                        on_token(text)
                                # Pipeline status event
                                elif obj.get("type") == "status" and on_status:
                                    on_status(obj.get("step", ""))
                                # Token stats event
                                elif obj.get("type") == "token_stats":
                                    stats = obj
                            except json.JSONDecodeError:
                                pass
    except urllib.error.URLError as e:
        raise RuntimeError(f"No se puede conectar a SmartOrch en {SERVER_URL}: {e.reason}")
    on_done(stats)


def _server_alive() -> bool:
    try:
        _request("GET", "/health", timeout=3)
        return True
    except Exception:
        return False


def _auto_start_server() -> bool:
    """Intenta iniciar el servidor si no está corriendo."""
    if _server_alive():
        return True

    print(_c(C.YELLOW, "  Servidor no encontrado. Iniciando SmartOrch..."))
    run_py = os.path.join(SERVER_DIR, "run.py")
    if not os.path.isfile(run_py):
        print(_c(C.RED, f"  No se encontró {run_py}"))
        return False

    try:
        proc = subprocess.Popen(
            [sys.executable, run_py],
            cwd=SERVER_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0,
        )
        print(_c(C.GRAY, f"  PID {proc.pid} — esperando arranque..."), end="", flush=True)
        for _ in range(20):
            time.sleep(0.5)
            print(".", end="", flush=True)
            if _server_alive():
                print(_c(C.GREEN, " listo!"))
                return True
        print(_c(C.RED, "\n  Timeout al iniciar el servidor"))
        return False
    except Exception as e:
        print(_c(C.RED, f"  Error al iniciar servidor: {e}"))
        return False


# ── Chat / send message ───────────────────────────────────────────────────────

def _send_chat(
    messages: list[dict],
    stream: bool = True,
    task_hint: str | None = None,
) -> str:
    """
    Envía mensajes al endpoint /v1/chat/completions.
    Con stream=True muestra tokens en tiempo real y devuelve el texto completo.
    """
    model = "hermes3:8b"
    if task_hint in ("code", "refactor", "test", "solid", "web", "mobile", "explain", "doc", "optimize"):
        model = "qwen2.5-coder:7b"
    elif task_hint in ("yara", "sigma", "c2", "pentest", "security-review"):
        model = "hermes3:8b"

    body = {
        "model":     model,
        "messages":  messages,
        "stream":    stream,
        "max_tokens": 2048,
        "effort":    _effort[0],
    }
    if _conv_id:
        body.update({"conversation_id": _conv_id, "source": "cli", "workspace": os.getcwd()})

    if not stream:
        result = _request("POST", "/v1/chat/completions", body, timeout=120)
        return result["choices"][0]["message"]["content"]

    # Streaming
    full_text  = []
    start      = time.time()
    _pipeline_step = [None]

    _STEP_LABELS = {
        "cache":   _c(C.BLUE, "[cache]"),
        "route":   _c(C.GRAY, "[router]"),
        "rag":     _c(C.MAGENTA, "[RAG]"),
        "cot":     _c(C.CYAN, "[CoT]"),
        "thinking":_c(C.YELLOW, "[💭 pensando]"),
        "llm":     _c(C.GREEN, "[LLM]"),
        "verify":  _c(C.YELLOW, "[verificando]"),
    }

    def on_status(step: str):
        label = _STEP_LABELS.get(step, f"[{step}]")
        print(f"\r  {label}          ", end="", flush=True)
        _pipeline_step[0] = step

    def on_token(text: str):
        if _pipeline_step[0]:
            print(f"\r{' ' * 30}\r", end="", flush=True)
            _pipeline_step[0] = None
        print(text, end="", flush=True)
        full_text.append(text)

    def on_done(stats: dict):
        elapsed = time.time() - start
        ptok = stats.get("prompt_tokens", 0)
        ctok = stats.get("completion_tokens", 0)
        tps  = stats.get("tokens_per_sec", 0)
        print()
        meta_parts = []
        if ptok:
            meta_parts.append(_c(C.GRAY, f"{ptok}↑"))
        if ctok:
            meta_parts.append(_c(C.GRAY, f"{ctok}↓"))
        if tps:
            meta_parts.append(_c(C.GRAY, f"{tps:.1f} tok/s"))
        meta_parts.append(_c(C.GRAY, f"{elapsed:.1f}s"))
        if meta_parts:
            print(_c(C.DIM, "  " + " · ".join(meta_parts)))

    try:
        _stream_request("/v1/chat/completions", body, on_token, on_done, on_status)
    except RuntimeError as e:
        print(_c(C.RED, f"\n  Error: {e}"))
        return ""

    return "".join(full_text)


# ── Lectura de archivos ───────────────────────────────────────────────────────

def _read_input(args_input: str | None, file_path: str | None) -> str:
    """Lee texto desde argumento o archivo."""
    if file_path:
        if not os.path.isfile(file_path):
            print(_c(C.RED, f"  Archivo no encontrado: {file_path}"))
            sys.exit(1)
        with open(file_path, encoding="utf-8", errors="replace") as f:
            content = f.read()
        return content
    return args_input or ""


# ── Comandos slash ────────────────────────────────────────────────────────────

# Tabla: comando → (template_text, task_hint)
_COMMANDS: dict[str, tuple[str, str]] = {
    "explain": (
        "Explica el siguiente código de forma detallada y clara:\n\n{code}",
        "explain",
    ),
    "refactor": (
        "Refactoriza el siguiente código aplicando SOLID, DRY, nombres descriptivos "
        "y clean architecture. Muestra el código refactorizado completo:\n\n{code}",
        "refactor",
    ),
    "solid": (
        "Aplica los principios SOLID y DRY al siguiente código. "
        "Para cada principio violado, muestra qué cambias y por qué:\n\n{code}",
        "refactor",
    ),
    "test": (
        "Escribe tests unitarios completos para el siguiente código. "
        "Cubre happy path, casos borde y casos de error:\n\n{code}",
        "test",
    ),
    "review": (
        "Haz un code review exhaustivo. Identifica bugs, problemas de SOLID/DRY, "
        "seguridad y rendimiento. Usa número de línea cuando sea posible:\n\n{code}",
        "refactor",
    ),
    "doc": (
        "Genera documentación profesional (docstrings/JSDoc/javadoc) para:\n\n{code}",
        "doc",
    ),
    "optimize": (
        "Analiza la complejidad actual O(?) y optimiza el siguiente código. "
        "Indica la mejora de complejidad conseguida:\n\n{code}",
        "refactor",
    ),
    "yara": (
        "Analiza el siguiente código/muestra y genera una regla YARA precisa "
        "(strings únicos, condición robusta, metadata completa con MITRE ATT&CK):\n\n{code}",
        "yara",
    ),
    "sigma": (
        "Genera una regla Sigma para detectar el siguiente comportamiento/IOC. "
        "Incluye logsource correcto, detection precisa y nivel justificado:\n\n{code}",
        "sigma",
    ),
    "c2": (
        "Analiza el siguiente código/tráfico C2 (contexto educativo/defensivo). "
        "Mapea a MITRE ATT&CK, extrae IoCs, documenta persistencia y evasión:\n\n{code}",
        "c2",
    ),
    "pentest": (
        "Analiza este objetivo/código desde perspectiva de pentesting "
        "(entorno controlado/educativo). Superficie de ataque, vectores, metodología:\n\n{code}",
        "pentest",
    ),
    "web": (
        "Desarrolla el siguiente requerimiento web (frontend/backend/fullstack) "
        "aplicando arquitectura limpia, SOLID/DRY y buenas prácticas del stack:\n\n{code}",
        "web",
    ),
    "mobile": (
        "Desarrolla el siguiente requerimiento para aplicación móvil "
        "(Flutter/React Native). Aplica SOLID, manejo de estado correcto, performance:\n\n{code}",
        "mobile",
    ),
}


def _run_command(cmd: str, content: str) -> str:
    template, hint = _COMMANDS[cmd]
    prompt = template.format(code=content)
    messages = [{"role": "user", "content": prompt}]

    print()
    print(_c(C.CYAN + C.BOLD, f"  /{cmd}"), _c(C.GRAY, f"— {len(content)} chars"))
    print(_c(C.GRAY, "  " + "─" * 60))
    print()

    return _send_chat(messages, stream=True, task_hint=hint)


# ── REPL interactivo ──────────────────────────────────────────────────────────

def _list_conversations(limit: int = 15) -> list[dict]:
    data = _request("GET", f"/smartorch/conversations?limit={limit}", timeout=10)
    return data.get("conversations", [])


def _print_conversations(convs: list[dict]) -> None:
    if not convs:
        print(_c(C.GRAY, "  Aún no hay conversaciones guardadas."))
        return
    print()
    for c in convs:
        when = time.strftime("%d/%m %H:%M", time.localtime(c.get("updated_at", 0)))
        src = _c(C.MAGENTA, f"{c.get('source', '?'):<6}")
        print(f"  {_c(C.CYAN, c['id'])}  {src} {_c(C.GRAY, when)}  {c['title']}")
    print()
    print(_c(C.GRAY, "  Continúa una con: smartorch resume <id>   (o /resume <id> en el chat)"))


def _load_conversation(conv_id: str) -> list[dict] | None:
    """Carga una conversación del historial compartido y la deja activa."""
    global _conv_id  # noqa: PLW0603
    if conv_id == "latest":
        convs = _list_conversations(1)
        if not convs:
            return None
        conv_id = convs[0]["id"]
    try:
        conv = _request("GET", f"/smartorch/conversations/{conv_id}", timeout=10)
    except Exception:
        return None
    _conv_id = conv["id"]
    print(_c(C.GREEN, f"  Retomando: {conv['title']}"), _c(C.GRAY, f"({conv.get('message_count', 0)} mensajes)"))
    return [{"role": m["role"], "content": m["content"]}
            for m in conv.get("messages", []) if m["role"] in ("user", "assistant")]


def _repl(resume_id: str | None = None):
    """REPL interactivo con historial de conversación."""
    _print_banner()

    if not _auto_start_server():
        print(_c(C.RED, "  No se puede conectar al servidor SmartOrch."))
        print(_c(C.GRAY, "  Inicia el servidor con: smartorch serve"))
        sys.exit(1)

    print(_c(C.GREEN, "  Servidor conectado."))
    print(_c(C.GRAY, "  Comandos: /yara /sigma /c2 /pentest /solid /refactor /review /test"))
    print(_c(C.GRAY, "  Escribe /help para ver todos los comandos · /exit para salir"))
    print()

    conversation: list[dict] = []
    if resume_id:
        loaded = _load_conversation(resume_id)
        if loaded is None:
            print(_c(C.YELLOW, "  No encontré esa conversación; empiezo una nueva."))
            _new_conversation()
        else:
            conversation = loaded
    else:
        _new_conversation()
    _load_readline()

    while True:
        try:
            raw = input(_c(C.CYAN + C.BOLD, "  >> ")).strip()
        except (EOFError, KeyboardInterrupt):
            print("\n" + _c(C.GRAY, "  Hasta luego."))
            break

        if not raw:
            continue

        # Comandos de control del REPL
        if raw in ("/exit", "/quit", "exit", "quit"):
            print(_c(C.GRAY, "  Hasta luego."))
            break

        if raw == "/clear":
            conversation.clear()
            _new_conversation()
            print(_c(C.GRAY, "  Conversación nueva."))
            continue

        if raw == "/list":
            _print_conversations(_list_conversations())
            continue

        if raw.startswith("/resume"):
            target = raw.partition(" ")[2].strip() or "latest"
            loaded = _load_conversation(target)
            if loaded is None:
                print(_c(C.YELLOW, "  No encontré esa conversación (usa /list)."))
            else:
                conversation = loaded
            continue

        if raw == "/open":
            import webbrowser
            webbrowser.open(f"{SERVER_URL}/?c={_conv_id}")
            print(_c(C.GRAY, "  Abierta en el navegador."))
            continue

        if raw == "/help":
            _print_help()
            continue

        if raw == "/status":
            _cmd_status()
            continue

        if raw == "/history":
            for i, m in enumerate(conversation):
                role = _c(C.CYAN, "tú") if m["role"] == "user" else _c(C.GREEN, "SmartOrch")
                print(f"  [{i}] {role}: {m['content'][:80]}...")
            continue

        if raw.startswith(("/agent ", "/plan ")):
            from smartorch.cli_agent import agent_command
            task = raw.split(" ", 1)[1].strip()
            if task:
                agent_command(task, plan=raw.startswith("/plan "), effort=_effort[0], approval="ask",
                              web=False, workspace=None)
            continue

        if raw.startswith("/effort"):
            level = raw.partition(" ")[2].strip().lower()
            if level in ("rapido", "normal", "maximo"):
                _effort[0] = level
                print(_c(C.GRAY, f"  Esfuerzo: {level}"))
            else:
                print(_c(C.YELLOW, f"  Uso: /effort rapido|normal|maximo   (actual: {_effort[0]})"))
            continue

        # Slash commands que reciben input del REPL
        if raw.startswith("/") and " " in raw:
            parts = raw.split(" ", 1)
            cmd   = parts[0].lstrip("/")
            rest  = parts[1].strip()
            if cmd in _COMMANDS:
                # Si es ruta de archivo, leerlo
                if os.path.isfile(rest):
                    with open(rest, encoding="utf-8", errors="replace") as f:
                        content = f.read()
                else:
                    content = rest
                _run_command(cmd, content)
                continue
            print(_c(C.YELLOW, f"  Comando desconocido: /{cmd}"))
            continue

        if raw.startswith("/") and raw.lstrip("/") in _COMMANDS:
            cmd = raw.lstrip("/")
            print(_c(C.GRAY, f"  Pega el código para /{cmd} (termina con EOF en línea sola):"))
            lines = []
            try:
                while True:
                    line = input()
                    if line == "EOF":
                        break
                    lines.append(line)
            except EOFError:
                pass
            if lines:
                _run_command(cmd, "\n".join(lines))
            continue

        # Conversación normal
        conversation.append({"role": "user", "content": raw})
        print()
        response = _send_chat(list(conversation), stream=True, task_hint="chat")
        print()
        if response:
            conversation.append({"role": "assistant", "content": response})


def _load_readline():
    """Activa historial de readline si está disponible (Linux/Mac)."""
    try:
        import readline
        if os.path.isfile(HISTORY_FILE):
            readline.read_history_file(HISTORY_FILE)
        import atexit
        atexit.register(readline.write_history_file, HISTORY_FILE)
        readline.set_history_length(200)
    except ImportError:
        pass


def _print_help():
    cmds = [
        ("/clear",       "Nueva conversación"),
        ("/history",     "Ver mensajes de esta conversación"),
        ("/list",        "Conversaciones guardadas (web · CLI · editor)"),
        ("/resume [id]", "Retomar una conversación (la última si no hay id)"),
        ("/open",        "Abrir esta conversación en la web"),
        ("/agent <t>",   "Agente con herramientas (pide aprobación para editar)"),
        ("/plan <t>",    "Investigar y proponer un plan, sin modificar nada"),
        ("/effort <n>",  "Esfuerzo: rapido, normal o maximo"),
        ("/status",      "Estado del servidor y métricas"),
        ("/help",        "Este mensaje"),
        ("/exit",        "Salir"),
        ("",             ""),
        ("/explain <f>", "Explicar código de archivo o texto"),
        ("/refactor <f>","Refactorizar con SOLID/DRY"),
        ("/solid <f>",   "Aplicar principios SOLID explícitamente"),
        ("/review <f>",  "Code review exhaustivo"),
        ("/test <f>",    "Generar tests unitarios"),
        ("/doc <f>",     "Generar documentación"),
        ("/optimize <f>","Optimizar complejidad"),
        ("",             ""),
        ("/yara <f>",    "Generar regla YARA"),
        ("/sigma <f>",   "Generar regla Sigma"),
        ("/c2 <f>",      "Analizar código C2/malware"),
        ("/pentest <f>", "Guía de pentest"),
        ("/web <f>",     "Feature web (React/FastAPI/etc)"),
        ("/mobile <f>",  "Feature mobile (Flutter/RN)"),
    ]
    print()
    print(_c(C.BOLD, "  Comandos SmartOrch CLI"))
    print()
    for cmd, desc in cmds:
        if not cmd:
            continue
        print(f"  {_c(C.CYAN, cmd):<22} {_c(C.GRAY, desc)}")
    print()
    print(_c(C.GRAY, "  <f> puede ser un archivo o texto inline"))
    print()


# ── Subcomandos CLI ───────────────────────────────────────────────────────────

def _cmd_status():
    """Muestra estado del servidor y métricas."""
    try:
        health  = _request("GET", "/health", timeout=5)
        metrics = _request("GET", "/smartorch/metrics", timeout=5)
        usage   = _request("GET", "/smartorch/usage", timeout=5)
    except Exception as e:
        print(_c(C.RED, f"  Error conectando: {e}"))
        return

    print()
    print(_c(C.BOLD, "  SmartOrch — Estado"))
    print(_c(C.GRAY, "  " + "─" * 40))

    status_color = C.GREEN if health.get("status") == "ok" else C.RED
    print(f"  Estado:      {_c(status_color, health.get('status', '?'))}")
    print(f"  Uptime:      {_c(C.GRAY, health.get('uptime', '?'))}")
    print(f"  Modelos:     {_c(C.CYAN, ', '.join(health.get('models', [])))}")

    print()
    print(_c(C.BOLD, "  Sesión"))
    print(f"  Requests:    {_c(C.WHITE, str(usage.get('total_requests', 0)))}")
    print(f"  Tokens ↑:    {_c(C.BLUE, str(usage.get('total_prompt_tokens', 0)))}")
    print(f"  Tokens ↓:    {_c(C.GREEN, str(usage.get('total_completion_tokens', 0)))}")
    avg_p = usage.get('avg_prompt_tokens', 0)
    avg_c = usage.get('avg_completion_tokens', 0)
    print(f"  Promedio:    {_c(C.GRAY, f'{avg_p}↑ / {avg_c}↓ tok por request')}")

    cache = usage.get("cache_stats", {})
    if cache:
        hit_rate = cache.get("hit_rate", 0)
        hr_color = C.GREEN if hit_rate > 30 else C.YELLOW
        print()
        print(_c(C.BOLD, "  Cache Semántico"))
        hits  = cache.get("hits", 0)
        total = cache.get("total", 0)
        print(f"  Hits/Total:  {_c(C.WHITE, f'{hits}/{total}')}")
        print(f"  Hit rate:    {_c(hr_color, f'{hit_rate:.1f}%')}")
        print(f"  Entradas:    {_c(C.GRAY, str(cache.get('entries', 0)))}")

    print()


def _cmd_data(new_path: str | None, move: bool, reset: bool):
    """Muestra o cambia la carpeta donde SmartOrch guarda historial, indice y RAG."""
    from smartorch.core import datadir

    if reset or new_path:
        if _server_alive():
            print(_c(C.YELLOW, "  Detén el servidor antes de cambiar la carpeta de datos (Ctrl+C en su terminal)."))
            sys.exit(1)
        try:
            if reset:
                datadir.reset_data_dir()
                print(_c(C.GREEN, f"  Carpeta de datos restablecida: {datadir.BOOTSTRAP_DIR}"))
                return
            result = datadir.set_data_dir(new_path, move=move)
        except (OSError, PermissionError) as e:
            print(_c(C.RED, f"  {e}"))
            sys.exit(1)
        if not result["changed"]:
            print(_c(C.GRAY, "  Ya se usa esa carpeta."))
        else:
            print(_c(C.GREEN + C.BOLD, f"  Datos en: {result['data_dir']}"))
            if result["copied"]:
                print(_c(C.GRAY, f"  Copiado: {', '.join(result['copied'])}"))
                print(_c(C.GRAY, f"  Lo anterior sigue en {result['previous']} (bórralo cuando confirmes que todo funciona)."))
            print(_c(C.GRAY, "  Inicia el servidor de nuevo: smartorch serve"))
        return

    data = datadir.info()
    print()
    print(_c(C.BOLD, "  Datos de SmartOrch"))
    print(_c(C.GRAY, "  " + "─" * 50))
    print(f"  Carpeta:       {_c(C.CYAN, data['data_dir'])} {_c(C.GRAY, '(personalizada)' if data['custom'] else '(por defecto)')}")
    if data["free_gb"] is not None:
        print(f"  Espacio libre: {_c(C.WHITE, str(data['free_gb']) + ' GB')}")
    for item in data["items"]:
        mark = _c(C.GREEN, "✔") if item["exists"] else _c(C.GRAY, "·")
        print(f"  {mark} {item['label']:<28} {_c(C.GRAY, str(item['mb']) + ' MB')}")
    print()
    print(_c(C.GRAY, "  Cambiar:    smartorch data <carpeta>      (copia lo existente)"))
    print(_c(C.GRAY, "  Restablecer: smartorch data --reset"))
    print()


def _cmd_serve():
    """Inicia el servidor SmartOrch en primer plano."""
    run_py = os.path.join(SERVER_DIR, "run.py")
    if not os.path.isfile(run_py):
        print(_c(C.RED, f"  No se encontró {run_py}"))
        sys.exit(1)
    if _server_alive():
        print(_c(C.GREEN + C.BOLD, "  SmartOrch ya está corriendo ✔"))
        print(_c(C.GRAY, f"  URL: {SERVER_URL}  (usa 'smartorch' para chatear aquí en la terminal)"))
        return
    print(_c(C.CYAN + C.BOLD, "  Iniciando SmartOrch Server..."))
    print(_c(C.GRAY, f"  Directorio: {SERVER_DIR}"))
    print(_c(C.GRAY, f"  URL:        http://localhost:8080"))
    print(_c(C.GRAY, "  Ctrl+C para detener"))
    print()
    os.chdir(SERVER_DIR)
    os.execv(sys.executable, [sys.executable, run_py])


# ── Entry point ───────────────────────────────────────────────────────────────

def _cmd_map(folder: "str | None") -> None:
    """smartorch map [carpeta]: el modelo lee cada archivo una vez y resume el proyecto (incremental)."""
    import threading
    import time
    from smartorch.core import projectmap

    root = os.path.abspath(folder or os.getcwd())
    if not os.path.isdir(root):
        print(_c(C.RED, f"  No existe la carpeta: {root}"))
        sys.exit(1)
    print(_c(C.CYAN, f"  Construyendo el mapa de {root} (puede tardar; solo se re-resume lo que cambió)…"))
    result: dict = {}

    def work():
        try:
            result["stats"] = projectmap.build(root)
        except Exception as e:  # noqa: BLE001
            result["error"] = str(e)

    th = threading.Thread(target=work, daemon=True)
    th.start()
    key = os.path.abspath(root)
    try:
        while th.is_alive():
            p = projectmap.status(key).get("progress") or {}
            if p:
                print(f"\r  {p.get('phase', '')}: {p.get('done', 0)}/{p.get('total', 0)}   ", end="", flush=True)
            time.sleep(0.5)
    except KeyboardInterrupt:
        print(_c(C.YELLOW, "\n  Interrumpido: lo ya resumido queda guardado; vuelve a correr el comando para continuar."))
        sys.exit(130)
    print()
    if "error" in result:
        print(_c(C.RED, f"  {result['error']}"))
        sys.exit(1)
    s = result["stats"]
    print(_c(C.GREEN, f"  Listo: {s['summarized']} resumidos, {s['reused']} sin cambios, {s['failed']} fallidos."))
    print(projectmap.render(root, 1800))


def main():
    global SERVER_URL  # noqa: PLW0603

    parser = argparse.ArgumentParser(
        prog="smartorch",
        description="SmartOrch — AI local con SOLID/DRY, YARA/Sigma y anti-alucinaciones",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "command",
        nargs="?",
        help="Comando o pregunta directa",
    )
    parser.add_argument(
        "input",
        nargs="?",
        help="Archivo o texto de entrada",
    )
    parser.add_argument(
        "--file", "-f",
        help="Archivo de entrada (alternativo)",
        metavar="FILE",
    )
    parser.add_argument(
        "--no-stream",
        action="store_true",
        help="Desactivar streaming (esperar respuesta completa)",
    )
    parser.add_argument("--no-move", action="store_true", help="smartorch data: no copiar los datos existentes")
    parser.add_argument("--reset", action="store_true", help="smartorch data: volver a la carpeta por defecto")
    parser.add_argument("--plan", action="store_true", help="smartorch agent: investigar y proponer un plan, sin modificar nada")
    parser.add_argument("--effort", choices=("rapido", "normal", "maximo"), default="normal", help="esfuerzo: rapido, normal o maximo")
    parser.add_argument("--approval", choices=("ask", "auto_edits", "readonly"), default="ask", help="smartorch agent: permisos")
    parser.add_argument("--web", action="store_true", help="smartorch agent: permitir buscar en internet (cada consulta se aprueba)")
    parser.add_argument("--cwd", default=None, help="smartorch agent: carpeta del proyecto (por defecto la actual)")
    parser.add_argument("--yes", "-y", action="store_true", help="smartorch agent: aplicar ediciones de archivos sin preguntar")
    parser.add_argument(
        "--url",
        default=None,
        help=f"URL del servidor SmartOrch (default: {SERVER_URL})",
    )

    args = parser.parse_args()

    # Actualizar URL global si se pasa por argumento
    if args.url:
        SERVER_URL = args.url.rstrip("/")

    # Sin comando → REPL
    if not args.command:
        _repl()
        return

    # Comandos especiales que no necesitan el servidor para arrancar
    if args.command == "serve":
        _cmd_serve()
        return

    if args.command == "status":
        _cmd_status()
        return

    if args.command == "data":
        _cmd_data(args.input, move=not args.no_move, reset=args.reset)
        return

    if args.command == "map":
        _cmd_map(args.cwd or args.input)
        return

    # Auto-arrancar servidor para todo lo demás
    if not _auto_start_server():
        print(_c(C.RED, "  No se puede conectar al servidor SmartOrch."))
        print(_c(C.GRAY, "  Usa 'smartorch serve' para iniciarlo."))
        sys.exit(1)

    if args.command == "history":
        _print_conversations(_list_conversations(30))
        return

    if args.command == "resume":
        _repl(resume_id=args.input or "latest")
        return

    if args.command == "agent":
        from smartorch.cli_agent import agent_command
        task = args.input or (sys.stdin.read().strip() if not sys.stdin.isatty() else "")
        if not task:
            print(_c(C.YELLOW, '  Uso: smartorch agent "tarea" [--plan] [--effort rapido|normal|maximo] [--web] [-y]'))
            sys.exit(1)
        _new_conversation()
        agent_command(task, plan=args.plan, effort=args.effort, approval=args.approval, web=args.web,
                      workspace=args.cwd, yes=args.yes)
        return

    _new_conversation()  # cada llamada suelta queda guardada en el historial compartido

    cmd   = args.command
    fpath = args.file or args.input

    # Si el comando es un slash command conocido
    if cmd in _COMMANDS:
        content = _read_input(None, fpath) if fpath else ""
        if not content:
            # Leer de stdin si hay datos
            if not sys.stdin.isatty():
                content = sys.stdin.read()
            else:
                print(_c(C.YELLOW, f"  Uso: smartorch {cmd} <archivo> | <texto>"))
                print(_c(C.GRAY,   f"  Ejemplo: smartorch {cmd} mi_archivo.py"))
                print(_c(C.GRAY,   "  O pega código (termina con EOF en línea sola):"))
                lines = []
                try:
                    while True:
                        line = input()
                        if line == "EOF":
                            break
                        lines.append(line)
                except EOFError:
                    pass
                content = "\n".join(lines)
        if not content:
            print(_c(C.RED, "  Sin contenido para procesar."))
            sys.exit(1)
        _run_command(cmd, content)
        return

    # Si el comando parece un archivo existente → chat sobre ese archivo
    if os.path.isfile(cmd):
        with open(cmd, encoding="utf-8", errors="replace") as f:
            file_content = f.read()
        extra = args.input or ""
        prompt = f"{extra}\n\n```\n{file_content}\n```" if extra else f"Explica este código:\n\n```\n{file_content}\n```"
        messages = [{"role": "user", "content": prompt}]
        print()
        print(_c(C.CYAN, f"  Analizando: {cmd}"))
        print()
        _send_chat(messages, stream=not args.no_stream, task_hint="explain")
        return

    # Pregunta directa en lenguaje natural
    # e.g.: smartorch "cómo hago X"
    question = cmd
    if fpath:
        if os.path.isfile(fpath):
            with open(fpath, encoding="utf-8", errors="replace") as f:
                question += "\n\n```\n" + f.read() + "\n```"
        else:
            question += " " + fpath

    messages = [{"role": "user", "content": question}]
    print()
    _send_chat(messages, stream=not args.no_stream)
    print()


if __name__ == "__main__":
    main()
